"""Gesture recognition: a rotation-limited $1 Unistroke matcher plus geometric
heuristics for shapes that template matching handles poorly (lines, spirals)."""

import math
import numpy as np

N = 64
SIZE = 250.0
HALF_DIAG = 0.5 * math.sqrt(2 * SIZE * SIZE)
ANGLE_RANGE = math.radians(25)
ANGLE_PREC = math.radians(2)
PHI = 0.5 * (-1 + math.sqrt(5))


def path_length(pts):
    a = np.asarray(pts, float)
    return float(np.linalg.norm(np.diff(a, axis=0), axis=1).sum()) if len(a) > 1 else 0.0


def resample(points, n=N):
    pts = [tuple(p) for p in points]
    interval = path_length(pts) / (n - 1)
    if interval <= 0:
        return np.repeat(np.asarray(pts[:1], float), n, axis=0)
    out = [pts[0]]
    acc = 0.0
    i = 1
    while i < len(pts):
        (x0, y0), (x1, y1) = pts[i - 1], pts[i]
        d = math.hypot(x1 - x0, y1 - y0)
        if d > 0 and acc + d >= interval:
            t = (interval - acc) / d
            q = (x0 + t * (x1 - x0), y0 + t * (y1 - y0))
            out.append(q)
            pts.insert(i, q)
            acc = 0.0
        else:
            acc += d
        i += 1
    while len(out) < n:
        out.append(pts[-1])
    return np.asarray(out[:n], float)


def _normalize(raw):
    # Uniform scaling keeps aspect ratio, so thin and wide shapes stay distinct.
    r = resample(raw)
    w, h = np.ptp(r, axis=0)
    s = SIZE / max(w, h, 1e-6)
    return (r - r.mean(axis=0)) * s


def _rotate(pts, a):
    c, s = math.cos(a), math.sin(a)
    return pts @ np.array([[c, s], [-s, c]])


def _path_distance(a, b):
    return float(np.linalg.norm(a - b, axis=1).mean())


def _distance_at_best_angle(pts, tpl):
    a, b = -ANGLE_RANGE, ANGLE_RANGE
    x1 = PHI * a + (1 - PHI) * b
    f1 = _path_distance(_rotate(pts, x1), tpl)
    x2 = (1 - PHI) * a + PHI * b
    f2 = _path_distance(_rotate(pts, x2), tpl)
    while abs(b - a) > ANGLE_PREC:
        if f1 < f2:
            b, x2, f2 = x2, x1, f1
            x1 = PHI * a + (1 - PHI) * b
            f1 = _path_distance(_rotate(pts, x1), tpl)
        else:
            a, x1, f1 = x1, x2, f2
            x2 = (1 - PHI) * a + PHI * b
            f2 = _path_distance(_rotate(pts, x2), tpl)
    return min(f1, f2)


# ---- Shape generators (unit box, y down) ----
def polyline(verts, per=20):
    out = []
    for (x0, y0), (x1, y1) in zip(verts, verts[1:]):
        for k in range(per):
            out.append((x0 + (x1 - x0) * k / per, y0 + (y1 - y0) * k / per))
    out.append(tuple(verts[-1]))
    return out


def arc(cx, cy, r, start, direction, turns=1.0, n=64, r_end=None):
    r_end = r if r_end is None else r_end
    out = []
    for i in range(n + 1):
        t = i / n
        a = start + direction * t * turns * math.pi * 2
        rr = r + (r_end - r) * t
        out.append((cx + math.cos(a) * rr, cy + math.sin(a) * rr))
    return out


BOLTS = [
    [(0.7, 0), (0.3, 0.5), (0.7, 0.5), (0.3, 1)],
    [(0.3, 0), (0.7, 0.5), (0.3, 0.5), (0.7, 1)],
    [(0, 0), (1, 0), (0, 1), (1, 1)],
]
VEE = [(0, 0), (0.5, 1), (1, 0)]
TRI = [(0.5, 0), (1, 0.87), (0, 0.87)]

# Glyphs shown in the spellbook (direction matters for the arrowheads).
GLYPHS = {
    'circle': arc(0.5, 0.5, 0.5, -math.pi / 2, 1, 0.92),
    'bolt': polyline(BOLTS[0]),
    'flick': polyline([(0.1, 0.9), (0.9, 0.1)]),
    'spiral': arc(0.5, 0.5, 0.06, 0, 1, 2.3, 90, 0.5),
    'up': polyline([(0.5, 1), (0.5, 0)]),
    'triangle': polyline(TRI + [TRI[0]]),
    'vee': polyline(VEE),
}


def _builtin_templates():
    t = []
    for k in range(8):
        for d in (1, -1):
            t.append(('circle', arc(0.5, 0.5, 0.5, k * math.pi / 4, d)))
    for s in range(3):
        for d in (1, -1):
            t.append(('triangle', polyline([TRI[(s + d * i) % 3] for i in range(4)])))
    for b in BOLTS:
        t.append(('bolt', polyline(b)))
        t.append(('bolt', polyline(b[::-1])))
    t.append(('vee', polyline(VEE)))
    t.append(('vee', polyline(VEE[::-1])))
    return t


class Recognizer:
    def __init__(self):
        self.templates = [dict(name=n, pts=_normalize(p), builtin=True) for n, p in _builtin_templates()]

    def add_custom(self, name, samples):
        for s in samples:
            if len(s) < 4:
                continue
            self.templates.append(dict(name=name, pts=_normalize(s), builtin=False))
            self.templates.append(dict(name=name, pts=_normalize(s[::-1]), builtin=False))

    def remove_custom(self, name):
        self.templates = [t for t in self.templates if t['builtin'] or t['name'] != name]

    def recognize(self, raw):
        if not raw or len(raw) < 4:
            return dict(name=None, score=0.0, reason='Too short')
        length = path_length(raw)
        r = resample(raw)
        w, h = np.ptp(r, axis=0)
        size = max(w, h, 1e-6)
        chord = float(np.linalg.norm(r[-1] - r[0]))
        straight = chord / max(length, 1e-6)

        seg = np.diff(r, axis=0)
        ang = np.arctan2(seg[:, 1], seg[:, 0])
        d = (np.diff(ang) + math.pi) % (2 * math.pi) - math.pi
        turn = float(d.sum())
        abs_turn = float(np.abs(d).sum())
        # Spirals/circles turn steadily one way; scribbles flail both ways.
        steady = abs(turn) / max(abs_turn, 1e-6)
        radii = np.linalg.norm(r - r.mean(axis=0), axis=1)
        cv = float(radii.std() / max(radii.mean(), 1e-6))
        loops = abs(turn) / (2 * math.pi)
        info = dict(straight=straight, loops=loops, cv=cv)

        n = _normalize(raw)
        best, best_d, best_c, best_cd = None, math.inf, None, math.inf
        for t in self.templates:
            dist = _distance_at_best_angle(n, t['pts'])
            if t['builtin']:
                if dist < best_d:
                    best_d, best = dist, t
            elif dist < best_cd:
                best_cd, best_c = dist, t

        def score(dist):
            return max(0.0, 1 - dist / HALF_DIAG)

        if best_c and score(best_cd) > 0.84:
            return dict(name=best_c['name'], score=score(best_cd), custom=True, info=info)

        if straight > 0.8:
            a = math.degrees(math.atan2(r[-1][1] - r[0][1], r[-1][0] - r[0][0]))
            sc = min(1.0, 0.72 + (straight - 0.8) * 1.4)
            if -125 < a < -55:
                return dict(name='up', score=sc, info=info)
            aa = abs(a)
            if 12 <= aa <= 78 or 102 <= aa <= 168:
                return dict(name='flick', score=sc, info=info)
            return dict(name=None, score=0.0, info=info, reason='A flat line: flick diagonally or swipe up')

        if loops > 1.45 and steady > 0.7:
            if cv > 0.2:
                return dict(name='spiral', score=min(1.0, 0.78 + cv * 0.5), info=info)
            return dict(name='circle', score=0.9, info=info)
        if loops > 0.75 and cv < 0.13 and chord / size < 0.4:
            return dict(name='circle', score=max(0.85, score(best_d)), info=info)

        sc = score(best_d)
        if best and sc >= 0.68:
            return dict(name=best['name'], score=sc, info=info)
        if best_c and score(best_cd) >= 0.75:
            return dict(name=best_c['name'], score=score(best_cd), custom=True, info=info)
        return dict(name=None, score=sc, guess=best['name'] if best else None, info=info, reason='The form falters')
