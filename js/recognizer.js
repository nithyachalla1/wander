// Gesture recognition: a rotation-limited $1 Unistroke matcher plus geometric
// heuristics for shapes that template matching handles poorly (lines, spirals).

const N = 64;
const SIZE = 250;
const HALF_DIAG = 0.5 * Math.sqrt(2 * SIZE * SIZE);
const ANGLE_RANGE = (25 * Math.PI) / 180;
const ANGLE_PREC = (2 * Math.PI) / 180;
const PHI = 0.5 * (-1 + Math.sqrt(5));

const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);

export function pathLength(pts) {
  let d = 0;
  for (let i = 1; i < pts.length; i++) d += dist(pts[i - 1], pts[i]);
  return d;
}

export function resample(points, n = N) {
  const pts = points.map((p) => ({ x: p.x, y: p.y }));
  const I = pathLength(pts) / (n - 1);
  if (!(I > 0)) return Array.from({ length: n }, () => ({ ...pts[0] }));
  let D = 0;
  const out = [{ ...pts[0] }];
  for (let i = 1; i < pts.length; i++) {
    const d = dist(pts[i - 1], pts[i]);
    if (D + d >= I && d > 0) {
      const t = (I - D) / d;
      const q = { x: pts[i - 1].x + t * (pts[i].x - pts[i - 1].x), y: pts[i - 1].y + t * (pts[i].y - pts[i - 1].y) };
      out.push(q);
      pts.splice(i, 0, q);
      D = 0;
    } else D += d;
  }
  while (out.length < n) out.push({ ...pts[pts.length - 1] });
  return out.slice(0, n);
}

function centroid(pts) {
  let x = 0, y = 0;
  for (const p of pts) { x += p.x; y += p.y; }
  return { x: x / pts.length, y: y / pts.length };
}

function bbox(pts) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const p of pts) {
    minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x);
    minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y);
  }
  return { minX, minY, maxX, maxY, w: maxX - minX, h: maxY - minY };
}

// Uniform scaling keeps aspect ratio, so thin and wide shapes stay distinct.
function normalize(raw) {
  const r = resample(raw);
  const b = bbox(r);
  const s = SIZE / Math.max(b.w, b.h, 1e-6);
  const c = centroid(r);
  return r.map((p) => ({ x: (p.x - c.x) * s, y: (p.y - c.y) * s }));
}

function rotate(pts, a) {
  const c = Math.cos(a), s = Math.sin(a);
  return pts.map((p) => ({ x: p.x * c - p.y * s, y: p.x * s + p.y * c }));
}

function pathDistance(a, b) {
  let d = 0;
  for (let i = 0; i < a.length; i++) d += dist(a[i], b[i]);
  return d / a.length;
}

function distanceAtBestAngle(pts, tpl) {
  let a = -ANGLE_RANGE, b = ANGLE_RANGE;
  let x1 = PHI * a + (1 - PHI) * b, f1 = pathDistance(rotate(pts, x1), tpl);
  let x2 = (1 - PHI) * a + PHI * b, f2 = pathDistance(rotate(pts, x2), tpl);
  while (Math.abs(b - a) > ANGLE_PREC) {
    if (f1 < f2) { b = x2; x2 = x1; f2 = f1; x1 = PHI * a + (1 - PHI) * b; f1 = pathDistance(rotate(pts, x1), tpl); }
    else { a = x1; x1 = x2; f1 = f2; x2 = (1 - PHI) * a + PHI * b; f2 = pathDistance(rotate(pts, x2), tpl); }
  }
  return Math.min(f1, f2);
}

// ---- Shape generators (unit box, y down) ----
function polyline(verts, per = 20) {
  const out = [];
  for (let i = 0; i < verts.length - 1; i++) {
    const [x0, y0] = verts[i], [x1, y1] = verts[i + 1];
    for (let k = 0; k < per; k++) out.push({ x: x0 + ((x1 - x0) * k) / per, y: y0 + ((y1 - y0) * k) / per });
  }
  const [lx, ly] = verts[verts.length - 1];
  out.push({ x: lx, y: ly });
  return out;
}

function arc(cx, cy, r, start, dir, turns = 1, n = 64, rEnd = r) {
  const out = [];
  for (let i = 0; i <= n; i++) {
    const t = i / n;
    const a = start + dir * t * turns * Math.PI * 2;
    const rr = r + (rEnd - r) * t;
    out.push({ x: cx + Math.cos(a) * rr, y: cy + Math.sin(a) * rr });
  }
  return out;
}

const BOLTS = [
  [[0.7, 0], [0.3, 0.5], [0.7, 0.5], [0.3, 1]],
  [[0.3, 0], [0.7, 0.5], [0.3, 0.5], [0.7, 1]],
  [[0, 0], [1, 0], [0, 1], [1, 1]],
];
const VEE = [[0, 0], [0.5, 1], [1, 0]];
const TRI = [[0.5, 0], [1, 0.87], [0, 0.87]];

// Glyphs shown in the spellbook (direction matters for the arrowheads).
export const GLYPHS = {
  circle: arc(0.5, 0.5, 0.5, -Math.PI / 2, 1, 0.92),
  bolt: polyline(BOLTS[0]),
  flick: polyline([[0.1, 0.9], [0.9, 0.1]]),
  spiral: arc(0.5, 0.5, 0.06, 0, 1, 2.3, 90, 0.5),
  up: polyline([[0.5, 1], [0.5, 0]]),
  triangle: polyline([...TRI, TRI[0]]),
  vee: polyline(VEE),
};

function builtinTemplates() {
  const T = [];
  for (let k = 0; k < 8; k++) for (const dir of [1, -1]) T.push(['circle', arc(0.5, 0.5, 0.5, (k * Math.PI) / 4, dir)]);
  for (let s = 0; s < 3; s++) {
    for (const dir of [1, -1]) {
      const v = [];
      for (let i = 0; i <= 3; i++) v.push(TRI[(((s + dir * i) % 3) + 3) % 3]);
      T.push(['triangle', polyline(v)]);
    }
  }
  for (const b of BOLTS) { T.push(['bolt', polyline(b)]); T.push(['bolt', polyline([...b].reverse())]); }
  T.push(['vee', polyline(VEE)]);
  T.push(['vee', polyline([...VEE].reverse())]);
  return T;
}

export class Recognizer {
  constructor() {
    this.templates = builtinTemplates().map(([name, pts]) => ({ name, pts: normalize(pts), builtin: true }));
  }

  addCustom(name, samples) {
    for (const s of samples) {
      if (s.length < 4) continue;
      this.templates.push({ name, pts: normalize(s), builtin: false });
      this.templates.push({ name, pts: normalize([...s].reverse()), builtin: false });
    }
  }

  removeCustom(name) {
    this.templates = this.templates.filter((t) => t.builtin || t.name !== name);
  }

  recognize(raw) {
    if (!raw || raw.length < 4) return { name: null, score: 0, reason: 'Too short' };
    const len = pathLength(raw);
    const r = resample(raw);
    const b = bbox(r);
    const size = Math.max(b.w, b.h, 1e-6);
    const chord = dist(r[0], r[N - 1]);
    const straight = chord / Math.max(len, 1e-6);

    let turn = 0, absTurn = 0;
    for (let i = 2; i < N; i++) {
      const a1 = Math.atan2(r[i - 1].y - r[i - 2].y, r[i - 1].x - r[i - 2].x);
      const a2 = Math.atan2(r[i].y - r[i - 1].y, r[i].x - r[i - 1].x);
      let d = a2 - a1;
      while (d > Math.PI) d -= 2 * Math.PI;
      while (d < -Math.PI) d += 2 * Math.PI;
      turn += d;
      absTurn += Math.abs(d);
    }
    // Spirals/circles turn steadily one way; scribbles flail both ways.
    const steady = Math.abs(turn) / Math.max(absTurn, 1e-6);
    const c = centroid(r);
    const radii = r.map((p) => dist(p, c));
    const mean = radii.reduce((a, v) => a + v, 0) / N;
    const cv = Math.sqrt(radii.reduce((a, v) => a + (v - mean) ** 2, 0) / N) / Math.max(mean, 1e-6);
    const loops = Math.abs(turn) / (2 * Math.PI);
    const info = { straight, loops, cv };

    const n = normalize(raw);
    let best = null, bestD = Infinity, bestC = null, bestCD = Infinity;
    for (const t of this.templates) {
      const d = distanceAtBestAngle(n, t.pts);
      if (t.builtin) { if (d < bestD) { bestD = d; best = t; } }
      else if (d < bestCD) { bestCD = d; bestC = t; }
    }
    const score = (d) => Math.max(0, 1 - d / HALF_DIAG);

    if (bestC && score(bestCD) > 0.84) return { name: bestC.name, score: score(bestCD), custom: true, info };

    if (straight > 0.8) {
      const ang = (Math.atan2(r[N - 1].y - r[0].y, r[N - 1].x - r[0].x) * 180) / Math.PI;
      const sc = Math.min(1, 0.72 + (straight - 0.8) * 1.4);
      if (ang > -125 && ang < -55) return { name: 'up', score: sc, info };
      const a = Math.abs(ang);
      if ((a >= 12 && a <= 78) || (a >= 102 && a <= 168)) {
        return { name: 'flick', score: sc, info, dir: { x: (r[N - 1].x - r[0].x) / chord, y: (r[N - 1].y - r[0].y) / chord } };
      }
      return { name: null, score: 0, info, reason: 'A flat line — flick diagonally or swipe up' };
    }

    if (loops > 1.45 && steady > 0.7) {
      if (cv > 0.2) return { name: 'spiral', score: Math.min(1, 0.78 + cv * 0.5), info };
      return { name: 'circle', score: 0.9, info };
    }
    if (loops > 0.75 && cv < 0.13 && chord / size < 0.4) return { name: 'circle', score: Math.max(0.85, score(bestD)), info };

    const sc = score(bestD);
    if (best && sc >= 0.68) return { name: best.name, score: sc, info };
    if (bestC && score(bestCD) >= 0.75) return { name: bestC.name, score: score(bestCD), custom: true, info };
    return { name: null, score: sc, guess: best?.name, info, reason: 'The form falters' };
  }
}
