"""Webcam hand tracking with MediaPipe on background threads.

Reliability features:
  * capture runs on its own thread so detection always sees the newest frame
  * dim frames get a CLAHE contrast boost before detection
  * up to two hands are detected; we lock onto the casting hand by continuity
  * finger poses come from 3D joint angles (world landmarks), so a hand tilted
    toward the camera still reads correctly; pinch is measured in centimeters
  * poses are debounced by a short majority vote
  * brief dropouts (a few frames) coast on the last pose instead of ending a stroke
"""

import collections
import math
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

HAND_EDGES = [
    (0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17),
]
MODEL_URL = ('https://storage.googleapis.com/mediapipe-models/hand_landmarker/'
             'hand_landmarker/float16/1/hand_landmarker.task')
DETECT_WIDTH = 960      # frames are downscaled to this width for detection
COAST_FRAMES = 6        # ~0.2s of dropout tolerated before the hand counts as gone
POSE_WINDOW = 5         # majority vote over this many frames
FINGERS = ((5, 6, 7, 8), (9, 10, 11, 12), (13, 14, 15, 16), (17, 18, 19, 20))


class OneEuro:
    def __init__(self, min_cutoff=1.4, beta=0.015, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.reset()

    def reset(self):
        self.x = self.dx = self.last = self.prev = None

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1 / (2 * math.pi * cutoff)
        return 1 / (1 + tau / dt)

    def __call__(self, v, t):
        if self.last is None:
            self.last, self.prev, self.x, self.dx = t, v, v, 0.0
            return v
        dt = max(1e-3, t - self.last)
        self.last = t
        dv = (v - self.prev) / dt
        self.prev = v
        a_d = self._alpha(self.d_cutoff, dt)
        self.dx = a_d * dv + (1 - a_d) * self.dx
        a = self._alpha(self.min_cutoff + self.beta * abs(self.dx), dt)
        self.x = a * v + (1 - a) * self.x
        return self.x


def _angle(a, b, c):
    """Angle at b (degrees) between segments b->a and b->c, in 3D."""
    v1 = [a[i] - b[i] for i in range(3)]
    v2 = [c[i] - b[i] for i in range(3)]
    n1 = math.sqrt(sum(x * x for x in v1)) or 1e-9
    n2 = math.sqrt(sum(x * x for x in v2)) or 1e-9
    cos = sum(x * y for x, y in zip(v1, v2)) / (n1 * n2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def classify(lm, world=None):
    """Pose from 3D world landmarks when available (rotation-robust), else 2D."""
    if world:
        def ext(mcp, pip, dip, tip):
            straight = _angle(world[mcp], world[pip], world[dip]) + _angle(world[pip], world[dip], world[tip])
            return straight > 300  # both joints nearly straight (each > ~150 degrees)
        extended = [ext(*f) for f in FINGERS]
        pinch = math.dist(world[4], world[8]) < 0.035  # meters
    else:
        def d(a, b):
            return math.hypot(lm[a][0] - lm[b][0], lm[a][1] - lm[b][1])
        extended = [d(0, f[3]) > d(0, f[1]) * 1.12 for f in FINGERS]
        pinch = d(4, 8) < d(0, 9) * 0.38
    index, middle, ring, pinky = extended
    n = sum(extended)
    if index and not (middle or ring or pinky):
        pose = 'point'
    elif n >= 4:
        pose = 'open'
    elif n == 0:
        pose = 'fist'
    elif index and middle and not (ring or pinky):
        pose = 'peace'
    else:
        pose = 'other'
    return pose, pinch


class HandTracker:
    """Runs capture + detection off the main thread. Read `snapshot()`."""

    def __init__(self, cam_index=0):
        self.cam_index = cam_index
        self.ready = False
        self.status = ''
        self.error = None
        self.fps = 0.0
        self._lock = threading.Lock()
        self._result = dict(present=False)
        self._small = None
        self._full = None
        self._seq = 0
        self._stop = False
        self._thread = None
        self._frame = None
        self._frame_id = 0
        self._frame_lock = threading.Condition()
        self._poses = collections.deque(maxlen=POSE_WINDOW)
        self._pinches = collections.deque(maxlen=POSE_WINDOW)
        self._last = None
        self._missing = 0

    def start(self):
        if self._thread:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True

    def snapshot(self):
        with self._lock:
            return self._seq, self._result, self._small, self._full

    # -------------------------------------------------------------- detector
    def _make_detector(self, mp):
        """Returns detect(rgb) -> list of (landmarks2d, world3d, score) per hand."""
        if hasattr(mp, 'solutions') and hasattr(mp.solutions, 'hands'):
            hands = mp.solutions.hands.Hands(max_num_hands=2, model_complexity=1,
                                             min_detection_confidence=0.5, min_tracking_confidence=0.5)

            def detect(rgb):
                res = hands.process(rgb)
                out = []
                for i, h in enumerate(res.multi_hand_landmarks or []):
                    world = res.multi_hand_world_landmarks[i].landmark if res.multi_hand_world_landmarks else None
                    score = res.multi_handedness[i].classification[0].score if res.multi_handedness else 1.0
                    out.append(([(p.x, p.y) for p in h.landmark], [(p.x, p.y, p.z) for p in world] if world else None, score))
                return out
            return detect

        from mediapipe.tasks import python as mpt
        from mediapipe.tasks.python import vision
        model = Path.home() / '.wander' / 'hand_landmarker.task'
        if not model.exists():
            self.status = 'Downloading hand model...'
            model.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(MODEL_URL, model)
        lmk = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
            base_options=mpt.BaseOptions(model_asset_path=str(model)),
            running_mode=vision.RunningMode.VIDEO, num_hands=2,
            min_hand_detection_confidence=0.5, min_hand_presence_confidence=0.5, min_tracking_confidence=0.5))
        clock = [0]

        def detect(rgb):
            clock[0] = max(clock[0] + 1, int(time.monotonic() * 1000))
            res = lmk.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), clock[0])
            out = []
            for i, h in enumerate(res.hand_landmarks or []):
                world = res.hand_world_landmarks[i] if res.hand_world_landmarks else None
                score = res.handedness[i][0].score if res.handedness and res.handedness[i] else 1.0
                out.append(([(p.x, p.y) for p in h], [(p.x, p.y, p.z) for p in world] if world else None, score))
            return out
        return detect

    # -------------------------------------------------------------- threads
    def _capture_loop(self, cap):
        while not self._stop:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.01)
                continue
            with self._frame_lock:
                self._frame = frame
                self._frame_id += 1
                self._frame_lock.notify()
        self._release(cap)

    @staticmethod
    def auto_exposure(cap, cv2):
        """Hand the camera's exposure back to its auto mode. The IR wand switches it to a dark manual
        setting, and many Windows webcam drivers keep that even after the app closes."""
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.75 if sys.platform == 'win32' else 3)  # DirectShow: 0.75 = auto

    def _release(self, cap):
        cap.release()

    def _open_camera(self, cv2):
        api = cv2.CAP_DSHOW if sys.platform == 'win32' else cv2.CAP_ANY
        cap = None
        for _ in range(4):  # after switching hand <-> wand, the previous tracker may still be releasing the camera
            cap = cv2.VideoCapture(self.cam_index, api)
            if not cap.isOpened():
                cap = cv2.VideoCapture(self.cam_index)
            if cap.isOpened():
                break
            time.sleep(0.5)
        if not cap.isOpened():
            return None
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))  # lets most webcams do 720p at 30fps
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        cap.set(cv2.CAP_PROP_FPS, 30)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.auto_exposure(cap, cv2)  # undo any dark IR-wand setting the driver remembered
        return cap

    def _pick(self, hands):
        """Lock onto the casting hand: closest to where it was, else the most confident."""
        if not hands:
            return None
        if self._last and len(hands) > 1:
            lx, ly = self._last['lm'][9]
            return min(hands, key=lambda h: math.hypot(h[0][9][0] - lx, h[0][9][1] - ly))
        return max(hands, key=lambda h: h[2])

    def _vote(self, pose, pinch):
        self._poses.append(pose)
        self._pinches.append(pinch)
        best = collections.Counter(self._poses).most_common(1)[0][0]
        return best, sum(self._pinches) * 2 > len(self._pinches)

    def _run(self):
        try:
            import cv2
            import mediapipe as mp
        except ImportError:
            self.error = 'Install opencv-python and mediapipe for webcam casting'
            return
        self.status = 'Opening camera...'
        cap = self._open_camera(cv2)
        if cap is None:
            self.error = 'No camera found - draw with the mouse'
            return
        try:
            self.status = 'Loading hand model...'
            detect = self._make_detector(mp)
        except Exception as exc:  # model download / init failure
            self.error = f'Hand model failed: {exc}'
            cap.release()
            return
        threading.Thread(target=self._capture_loop, args=(cap,), daemon=True).start()
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        self.ready = True
        self.status = ''
        seen = -1
        t_fps, n_fps = time.monotonic(), 0
        while not self._stop:
            with self._frame_lock:
                while self._frame_id == seen and not self._stop:
                    self._frame_lock.wait(0.1)
                frame, seen = self._frame, self._frame_id
            if frame is None:
                continue
            frame = cv2.flip(frame, 1)  # mirror, so landmark x already matches the screen
            h, w = frame.shape[:2]
            if w > DETECT_WIDTH:
                frame = cv2.resize(frame, (DETECT_WIDTH, int(h * DETECT_WIDTH / w)), interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            det = rgb
            lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
            if lab[..., 0].mean() < 95:  # dim room: boost local contrast before detection
                lab[..., 0] = clahe.apply(lab[..., 0])
                det = cv2.cvtColor(cv2.cvtColor(lab, cv2.COLOR_LAB2BGR), cv2.COLOR_BGR2RGB)

            hand = self._pick(detect(det))
            if hand:
                lm, world, _ = hand
                pose, pinch = self._vote(*classify(lm, world))
                tip = ((lm[4][0] + lm[8][0]) / 2, (lm[4][1] + lm[8][1]) / 2) if pinch else lm[8]
                result = dict(present=True, lm=lm, pose=pose, pinch=pinch, tip=tip, coasting=False)
                self._last = result
                self._missing = 0
            elif self._last and self._missing < COAST_FRAMES:
                self._missing += 1
                result = dict(self._last, coasting=True)
            else:
                self._last = None
                self._poses.clear()
                self._pinches.clear()
                result = dict(present=False)

            n_fps += 1
            now = time.monotonic()
            if now - t_fps >= 1.0:
                self.fps, t_fps, n_fps = n_fps / (now - t_fps), now, 0
            small = cv2.resize(rgb, (320, int(320 * rgb.shape[0] / rgb.shape[1])))
            with self._lock:
                self._result, self._small, self._full = result, small, rgb
                self._seq += 1


if os.environ.get('WANDER_NO_CAMERA'):
    HandTracker.start = lambda self: setattr(self, 'error', 'Camera disabled')
