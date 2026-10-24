// Webcam hand tracking via MediaPipe Tasks Vision, with pose classification
// (point / open / fist / pinch) and a One Euro filter for jitter-free wand tips.

const VER = '0.10.14';
const CDN = `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${VER}`;
const MODEL = 'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task';

export const HAND_EDGES = [
  [0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8], [5, 9], [9, 10], [10, 11], [11, 12],
  [9, 13], [13, 14], [14, 15], [15, 16], [13, 17], [17, 18], [18, 19], [19, 20], [0, 17],
];

class LowPass {
  constructor() { this.y = null; }
  filter(x, a) { this.y = this.y === null ? x : a * x + (1 - a) * this.y; return this.y; }
}

export class OneEuro {
  constructor(minCutoff = 1.2, beta = 0.02, dCutoff = 1.0) {
    Object.assign(this, { minCutoff, beta, dCutoff });
    this.x = new LowPass(); this.dx = new LowPass(); this.last = null; this.prev = null;
  }
  alpha(cutoff, dt) { const tau = 1 / (2 * Math.PI * cutoff); return 1 / (1 + tau / dt); }
  reset() { this.x = new LowPass(); this.dx = new LowPass(); this.last = null; this.prev = null; }
  filter(v, t) {
    if (this.last === null) { this.last = t; this.prev = v; return this.x.filter(v, 1); }
    const dt = Math.max(1e-3, t - this.last);
    this.last = t;
    const dv = (v - this.prev) / dt;
    this.prev = v;
    const edx = this.dx.filter(dv, this.alpha(this.dCutoff, dt));
    const cutoff = this.minCutoff + this.beta * Math.abs(edx);
    return this.x.filter(v, this.alpha(cutoff, dt));
  }
}

function classify(lm) {
  const d = (a, b) => Math.hypot(lm[a].x - lm[b].x, lm[a].y - lm[b].y);
  const ext = (tip, pip) => d(0, tip) > d(0, pip) * 1.12;
  const index = ext(8, 6), middle = ext(12, 10), ring = ext(16, 14), pinky = ext(20, 18);
  const handSize = d(0, 9);
  const pinch = d(4, 8) < handSize * 0.38;
  const n = index + middle + ring + pinky;
  let pose = 'other';
  if (index && !middle && !ring && !pinky) pose = 'point';
  else if (n >= 4) pose = 'open';
  else if (n === 0) pose = 'fist';
  else if (index && middle && !ring && !pinky) pose = 'peace';
  return { pose, pinch, handSize };
}

export class HandTracker {
  constructor(video) {
    this.video = video;
    this.ready = false;
    this.landmarker = null;
    this.lastVideoTime = -1;
    this.result = { present: false };
  }

  async start(onStatus = () => {}) {
    onStatus('Requesting camera…');
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'user' }, audio: false,
    });
    this.video.srcObject = stream;
    await this.video.play();
    onStatus('Summoning hand model…');
    const { FilesetResolver, HandLandmarker } = await import(`${CDN}/vision_bundle.mjs`);
    const fileset = await FilesetResolver.forVisionTasks(`${CDN}/wasm`);
    const opts = (delegate) => ({
      baseOptions: { modelAssetPath: MODEL, delegate },
      runningMode: 'VIDEO', numHands: 1,
      minHandDetectionConfidence: 0.6, minHandPresenceConfidence: 0.5, minTrackingConfidence: 0.5,
    });
    try { this.landmarker = await HandLandmarker.createFromOptions(fileset, opts('GPU')); }
    catch { this.landmarker = await HandLandmarker.createFromOptions(fileset, opts('CPU')); }
    this.ready = true;
    onStatus('Wand attuned');
  }

  detect(now) {
    if (!this.ready || this.video.readyState < 2) return this.result;
    if (this.video.currentTime === this.lastVideoTime) return this.result;
    this.lastVideoTime = this.video.currentTime;
    const res = this.landmarker.detectForVideo(this.video, now);
    if (!res.landmarks || !res.landmarks.length) { this.result = { present: false, fresh: true }; return this.result; }
    const lm = res.landmarks[0];
    const { pose, pinch, handSize } = classify(lm);
    const tipIdx = pinch ? null : 8;
    const tip = tipIdx === null
      ? { x: 1 - (lm[4].x + lm[8].x) / 2, y: (lm[4].y + lm[8].y) / 2 }
      : { x: 1 - lm[8].x, y: lm[8].y };
    const palm = { x: 1 - (lm[0].x + lm[9].x) / 2, y: (lm[0].y + lm[9].y) / 2 };
    this.result = { present: true, fresh: true, lm, pose, pinch, handSize, tip, palm };
    return this.result;
  }
}
