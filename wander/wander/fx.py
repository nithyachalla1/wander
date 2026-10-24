"""Effects: additive glow particles, procedural lightning, rings, floating
combat text, screen flashes and shake."""

import math
import random

import numpy as np
import pygame

from .spells import rgb

ADD = pygame.BLEND_ADD
_glow_cache = {}


def glow(color, r, level=8):
    """Additive radial glow sprite. `level` (1-8) scales brightness, which
    stands in for alpha because additive blits have none."""
    if isinstance(color, str):
        color = rgb(color)
    r = max(2, int(r) if r < 12 else int(r) // 4 * 4)  # bucket sizes so the cache stays small
    level = max(1, min(8, int(level)))
    key = (color, r, level)
    s = _glow_cache.get(key)
    if s is None:
        if len(_glow_cache) > 3000:
            _glow_cache.clear()
        d = 2 * r
        y, x = np.mgrid[0:d, 0:d]
        dist = np.sqrt((x - r + 0.5) ** 2 + (y - r + 0.5) ** 2) / r
        inten = np.clip(1 - dist, 0, 1) ** 2
        core = np.clip(1 - dist / 0.22, 0, 1) ** 2
        arr = (inten[..., None] * np.array(color, float) + core[..., None] * 200.0) * (level / 8)
        s = pygame.surfarray.make_surface(np.clip(arr, 0, 255).astype(np.uint8).transpose(1, 0, 2))
        _glow_cache[key] = s
    return s


def blit_glow(surf, color, x, y, r, level=8):
    g = glow(color, r, level)
    surf.blit(g, (x - g.get_width() / 2, y - g.get_height() / 2), special_flags=ADD)


def dim(color, k):
    if isinstance(color, str):
        color = rgb(color)
    return tuple(max(0, min(255, int(c * k))) for c in color)


def _jag(x1, y1, x2, y2, disp, depth, out):
    if depth == 0:
        out.append((x2, y2))
        return
    mx = (x1 + x2) / 2 + (random.random() - 0.5) * disp
    my = (y1 + y2) / 2 + (random.random() - 0.5) * disp
    _jag(x1, y1, mx, my, disp / 2, depth - 1, out)
    _jag(mx, my, x2, y2, disp / 2, depth - 1, out)


def lightning_points(x1, y1, x2, y2, depth=6, rough=0.22):
    pts = [(x1, y1)]
    _jag(x1, y1, x2, y2, math.hypot(x2 - x1, y2 - y1) * rough, depth, pts)
    return pts


class FX:
    MAX_PARTS = 1100

    def __init__(self):
        self.parts, self.rings, self.bolts, self.texts, self.flashes = [], [], [], [], []
        self.shake = 0.0

    def clear(self):
        self.parts.clear()
        self.rings.clear()
        self.bolts.clear()
        self.texts.clear()
        self.flashes.clear()

    def spawn(self, x, y, vx=0.0, vy=0.0, life=1.0, size=10.0, color='#ffffff', drag=2.0, grav=0.0, grow=0.0):
        if len(self.parts) >= self.MAX_PARTS:
            self.parts.pop(0)
        self.parts.append([x, y, vx, vy, life, life, size, color, drag, grav, grow])

    def burst(self, x, y, color, n=30, speed=300, life=0.7, size=10, drag=2.5, grav=0.0, grow=0.0):
        colors = color if isinstance(color, (list, tuple)) and isinstance(color[0], str) else [color]
        for _ in range(n):
            a = random.random() * math.tau
            s = speed * (0.25 + random.random() * 0.75)
            self.spawn(x, y, math.cos(a) * s, math.sin(a) * s, life * (0.6 + random.random() * 0.7),
                       size * (0.5 + random.random()), random.choice(colors), drag, grav, grow)

    def ring(self, x, y, color, r0, r1, dur=0.5, width=4):
        self.rings.append([x, y, color, r0, r1, dur, width, 0.0])

    def lightning(self, x1, y1, x2, y2, color, dur=0.3, width=3):
        self.bolts.append([x1, y1, x2, y2, color, dur, width, 0.0])

    def text(self, x, y, s, color='#ffffff', size=28, dur=1.1):
        self.texts.append([x, y, s, color, size, dur, 0.0])

    def flash(self, color, a=0.4, dur=0.3):
        self.flashes.append([color, a, dur, 0.0])

    def add_shake(self, a):
        self.shake = min(30.0, self.shake + a)

    def update(self, dt):
        alive = []
        for p in self.parts:
            k = math.exp(-p[8] * dt)
            p[2] *= k
            p[3] = p[3] * k + p[9] * dt
            p[0] += p[2] * dt
            p[1] += p[3] * dt
            p[6] += p[10] * dt
            p[4] -= dt
            if p[4] > 0 and p[6] > 0.5:
                alive.append(p)
        self.parts = alive
        for group, idx_dur in ((self.rings, 5), (self.bolts, 5), (self.texts, 5), (self.flashes, 2)):
            for o in group:
                o[-1] += dt
            group[:] = [o for o in group if o[-1] < o[idx_dur]]
        self.shake *= math.exp(-8 * dt)

    def draw_particles(self, surf):
        for x, y, _, _, life, mx, size, color, *_ in self.parts:
            a = max(0.0, life / mx)
            blit_glow(surf, color, x, y, size * (0.6 + 0.4 * a), 1 + a * 7)

    def draw_lines(self, layer):
        """Rings and lightning, drawn onto an additive layer."""
        for x, y, color, r0, r1, dur, width, t in self.rings:
            k = t / dur
            e = 1 - (1 - k) ** 3
            rad = int(r0 + (r1 - r0) * e)
            if rad > 1:
                pygame.draw.circle(layer, dim(color, 1 - k), (int(x), int(y)), rad, max(1, int(width * (1 - k * 0.6))))
        for x1, y1, x2, y2, color, dur, width, t in self.bolts:
            a = 1 - t / dur
            pts = lightning_points(x1, y1, x2, y2)
            pygame.draw.lines(layer, dim(color, 0.3 * a), False, pts, max(1, int(width * 5)))
            pygame.draw.lines(layer, dim(color, 0.85 * a), False, pts, max(1, int(width * 2)))
            pygame.draw.lines(layer, dim('#ffffff', a), False, pts, max(1, int(width * 0.8)))
            length = math.hypot(x2 - x1, y2 - y1)
            for _ in range(2):
                px, py = random.choice(pts)
                ang = random.random() * math.tau
                bl = length * (0.08 + random.random() * 0.15)
                bp = lightning_points(px, py, px + math.cos(ang) * bl, py + math.sin(ang) * bl, 3, 0.4)
                pygame.draw.lines(layer, dim(color, 0.6 * a), False, bp, max(1, int(width)))

    def draw_texts(self, surf, render_text):
        for x, y, s, color, size, dur, t in self.texts:
            k = t / dur
            alpha = 1.0 if k < 0.7 else (1 - k) / 0.3
            render_text(surf, s, (x, y - k * 50), size=int(size), color=color, serif=True, anchor='center',
                        alpha=alpha, outline=True)

    def draw_flashes(self, surf, overlay):
        for color, a, dur, t in self.flashes:
            overlay.fill((*rgb(color), int(255 * a * (1 - t / dur))))
            surf.blit(overlay, (0, 0))
