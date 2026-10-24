"""Webcam hand tracking with MediaPipe on a background thread, plus pose
classification (point / open / fist / pinch) and a One Euro filter."""

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


def classify(lm):
    def d(a, b):
        return math.hypot(lm[a][0] - lm[b][0], lm[a][1] - lm[b][1])

    def ext(tip, pip):
        return d(0, tip) > d(0, pip) * 1.12

    index, middle, ring, pinky = ext(8, 6), ext(12, 10), ext(16, 14), ext(20, 18)
    pinch = d(4, 8) < d(0, 9) * 0.38
    n = index + middle + ring + pinky
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
        self._lock = threading.Lock()
        self._result = dict(present=False)
        self._small = None
        self._full = None
        self._seq = 0
        self._stop = False
        self._thread = None

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

    def _make_detector(self, mp):
        """Prefer the classic Solutions API; fall back to the Tasks API."""
        if hasattr(mp, 'solutions') and hasattr(mp.solutions, 'hands'):
            hands = mp.solutions.hands.Hands(max_num_hands=1, model_complexity=0,
                                             min_detection_confidence=0.6, min_tracking_confidence=0.5)

            def detect(rgb):
                res = hands.process(rgb)
                if not res.multi_hand_landmarks:
                    return None
                return [(p.x, p.y) for p in res.multi_hand_landmarks[0].landmark]
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
            running_mode=vision.RunningMode.VIDEO, num_hands=1,
            min_hand_detection_confidence=0.6, min_tracking_confidence=0.5))
        clock = [0]

        def detect(rgb):
            clock[0] = max(clock[0] + 1, int(time.monotonic() * 1000))
            res = lmk.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), clock[0])
            if not res.hand_landmarks:
                return None
            return [(p.x, p.y) for p in res.hand_landmarks[0]]
        return detect

    def _run(self):
        try:
            import cv2
            import mediapipe as mp
        except ImportError:
            self.error = 'Install opencv-python and mediapipe for webcam casting'
            return
        self.status = 'Opening camera...'
        cap = cv2.VideoCapture(self.cam_index, cv2.CAP_DSHOW) if sys.platform == 'win32' else cv2.VideoCapture(self.cam_index)
        if not cap.isOpened():
            self.error = 'No camera found - draw with the mouse'
            return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        try:
            self.status = 'Loading hand model...'
            detect = self._make_detector(mp)
        except Exception as exc:  # model download / init failure
            self.error = f'Hand model failed: {exc}'
            cap.release()
            return
        self.ready = True
        self.status = ''
        while not self._stop:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.01)
                continue
            frame = cv2.flip(frame, 1)  # mirror, so landmark x already matches the screen
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            lm = detect(rgb)
            if lm:
                pose, pinch = classify(lm)
                tip = ((lm[4][0] + lm[8][0]) / 2, (lm[4][1] + lm[8][1]) / 2) if pinch else lm[8]
                result = dict(present=True, lm=lm, pose=pose, pinch=pinch, tip=tip)
            else:
                result = dict(present=False)
            small = cv2.resize(rgb, (200, 150))
            with self._lock:
                self._result, self._small, self._full = result, small, rgb
                self._seq += 1
        cap.release()


if os.environ.get('WANDER_NO_CAMERA'):
    HandTracker.start = lambda self: setattr(self, 'error', 'Camera disabled')
