"""IR wand tracking: the webcam follows the bright LED at the wand's tip (see ../../wand/wand.ino).

It plugs into the game exactly like HandTracker (same snapshot() shape), so spells, voice,
fusions, the Arena and the Forge all work with the wand. Drawing is gated by the wand's
ARMED button (or Space), not by a hand pose.

Detection, per frame:
  * brightness = the max of B/G/R (an IR LED shows up white-violet, a white LED white)
  * keep blobs that are much brighter than the frame and small (a lamp or window is large)
  * ignore spots learned as static background during calibration
  * prefer the blob closest to where the wand was, so a second bright dot can't steal it
  * sub-pixel centroid from the blob's intensity-weighted moments
  * brief dropouts coast on the last position, like the hand tracker
"""

import math
import threading
import time

from .hands import COAST_FRAMES, HandTracker

PROC_WIDTH = 640        # frames are downscaled to this width for blob detection
MIN_PEAK = 170          # a dot must reach at least this brightness (0-255)
PEAK_MARGIN = 45        # pixels within this much of the peak belong to the dot
MAX_AREA_FRAC = 0.004   # blobs bigger than 0.4% of the frame are lamps/windows, not a wand tip
MASK_RADIUS = 0.03      # learned background spots block a circle of this size (fraction of width)


def find_dot(frame_bgr, last=None, mask=(), cv2=None, np=None):
    """Locate the wand tip in a BGR frame.

    Returns (x, y, info) with x, y normalized to 0..1, or None. `last` is the previous
    normalized position (for continuity); `mask` lists normalized background spots to skip."""
    if cv2 is None:
        import cv2
    if np is None:
        import numpy as np
    h, w = frame_bgr.shape[:2]
    b, g, r = cv2.split(frame_bgr)
    bright = cv2.GaussianBlur(cv2.max(cv2.max(b, g), r), (5, 5), 0)
    _, peak, _, _ = cv2.minMaxLoc(bright)
    if peak < MIN_PEAK:
        return None
    _, bw = cv2.threshold(bright, max(MIN_PEAK - 20, peak - PEAK_MARGIN), 255, cv2.THRESH_BINARY)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(bw)
    best, best_score = None, -math.inf
    for i in range(1, n):
        x, y, bw_, bh, area = stats[i]
        if area > MAX_AREA_FRAC * w * h or bw_ > 0.12 * w or bh > 0.12 * h:
            continue
        roi = bright[y:y + bh, x:x + bw_].astype(np.float32) * (labels[y:y + bh, x:x + bw_] == i)
        m = roi.sum()
        if m <= 0:
            continue
        ys, xs = np.mgrid[0:bh, 0:bw_]
        cx, cy = float((x + (xs * roi).sum() / m) / w), float((y + (ys * roi).sum() / m) / h)
        if any(math.hypot(cx - mx, (cy - my) * h / w) < MASK_RADIUS for mx, my in mask):
            continue
        local_peak = float(roi.max())
        score = local_peak - 0.15 * area ** 0.5
        if last:
            score -= 400 * math.hypot(cx - last[0], cy - last[1])  # strongly prefer continuity
        if score > best_score:
            best, best_score = (cx, cy, dict(area=int(area), peak=round(local_peak))), score
    return best


def bright_spots(frame_bgr, cv2=None, np=None):
    """All bright small spots in a frame (used to learn static background lights)."""
    spots, mask = [], []
    for _ in range(6):
        hit = find_dot(frame_bgr, None, mask, cv2, np)
        if not hit:
            break
        spots.append(hit[:2])
        mask.append(hit[:2])
    return spots


class IRTracker(HandTracker):
    """Same interface as HandTracker; reports the IR dot as the wand tip."""

    def __init__(self, cam_index=0, low_exposure=True, mask=()):
        super().__init__(cam_index)
        self.low_exposure = low_exposure
        self.mask = [tuple(p) for p in mask]
        self._learn_until = 0.0
        self._learned = []
        self._learned_frames = 0
        self._last_pos = None
        self._mask_lock = threading.Lock()

    def _release(self, cap):
        if self.low_exposure:  # don't leave the webcam dark for hand mode or other apps
            try:
                import cv2
                self.auto_exposure(cap, cv2)
            except Exception:
                pass
        cap.release()

    def learn_background(self, seconds=1.5):
        """Record bright spots for a moment (wand hidden) and ignore them from now on."""
        with self._mask_lock:
            self._learned = []
            self._learn_until = time.monotonic() + seconds

    @property
    def learning(self):
        return time.monotonic() < self._learn_until

    def _finish_learning(self):
        # Keep spots seen in several frames, merged when close together.
        merged = []
        for x, y in self._learned:
            for m in merged:
                if math.hypot(x - m[0], y - m[1]) < MASK_RADIUS:
                    m[2] += 1
                    break
            else:
                merged.append([x, y, 1])
        frames = max(1, self._learned_frames)
        self.mask = [(x, y) for x, y, c in merged if c >= max(2, frames * 0.3)]

    def _run(self):
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.error = 'Install opencv-python for the IR wand'
            return
        self.status = 'Opening camera...'
        cap = self._open_camera(cv2)
        if cap is None:
            self.error = 'No camera found - the wand needs a webcam'
            return
        if self.low_exposure:
            # Darken the scene so the LED is the brightest thing in view (not every webcam obeys).
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
            cap.set(cv2.CAP_PROP_EXPOSURE, -7)
        threading.Thread(target=self._capture_loop, args=(cap,), daemon=True).start()
        self.ready = True
        self.status = ''
        seen = -1
        missing = 0
        learning_was = False
        self._learned_frames = 0
        t_fps, n_fps = time.monotonic(), 0
        while not self._stop:
            with self._frame_lock:
                while self._frame_id == seen and not self._stop:
                    self._frame_lock.wait(0.1)
                frame, seen = self._frame, self._frame_id
            if frame is None:
                continue
            frame = cv2.flip(frame, 1)  # mirror so moving right moves the cursor right
            h, w = frame.shape[:2]
            if w > PROC_WIDTH:
                frame = cv2.resize(frame, (PROC_WIDTH, int(h * PROC_WIDTH / w)), interpolation=cv2.INTER_AREA)

            learning = self.learning
            if learning:
                with self._mask_lock:
                    self._learned += bright_spots(frame, cv2, np)
                    self._learned_frames += 1
            elif learning_was:
                with self._mask_lock:
                    self._finish_learning()
                    self._learned_frames = 0
            learning_was = learning

            hit = None if learning else find_dot(frame, self._last_pos, self.mask, cv2, np)
            if hit:
                x, y, info = hit
                self._last_pos = (x, y)
                missing = 0
                result = dict(present=True, lm=None, pose='point', pinch=False, tip=(x, y), coasting=False, ir=info)
                self._last = result
            elif self._last and missing < COAST_FRAMES:
                missing += 1
                result = dict(self._last, coasting=True)
            else:
                self._last = None
                self._last_pos = None
                result = dict(present=False)

            n_fps += 1
            now = time.monotonic()
            if now - t_fps >= 1.0:
                self.fps, t_fps, n_fps = n_fps / (now - t_fps), now, 0
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            small = cv2.resize(rgb, (320, int(320 * rgb.shape[0] / rgb.shape[1])))
            sh, sw = small.shape[:2]
            for mx, my in self.mask:  # show ignored background lights
                cv2.drawMarker(small, (int(mx * sw), int(my * sh)), (120, 120, 120), cv2.MARKER_TILTED_CROSS, 10, 1)
            if result.get('present'):
                tx, ty = result['tip']
                cv2.circle(small, (int(tx * sw), int(ty * sh)), 9, (255, 217, 138), 2)
            with self._lock:
                self._result, self._small, self._full = result, small, rgb
                self._seq += 1
