"""The Hollow Archon: an adaptive duelist. It keeps a running model of the
player (spell frequencies, a Markov chain of spell transitions, block and
interrupt reflexes) and scores its options against that model each turn."""

import math
import random

import pygame

from .fx import blit_glow, dim
from .spells import SPELLS, rgb

ENEMY_SPELLS = {
    'hex': dict(name='Hex Bolt', color='#b04cff', charge=1.1, dmg=9, kind='proj'),
    'breaker': dict(name='Wardbreaker', color='#ff3355', charge=1.7, dmg=14, kind='proj', pierce=True),
    'veil': dict(name='Umbral Veil', color='#5a3aa6', charge=1.4, kind='curse'),
    'ward': dict(name='Obsidian Ward', color='#6b5cff', charge=0.9, kind='self', absorb=24),
    'mend': dict(name='Hollow Mend', color='#4dffa6', charge=2.0, kind='self', heal=24),
    'barrage': dict(name='Hollow Barrage', color='#d14cff', charge=1.6, dmg=7, kind='multi', count=3),
}

DIFF = [
    dict(name='Apprentice', hp=120, idle=(2.6, 3.8), charge=1.3, temp=1.1, dmg=0.7),
    dict(name='Adept', hp=170, idle=(1.7, 2.7), charge=1.0, temp=0.55, dmg=1.0),
    dict(name='Archmage', hp=230, idle=(1.0, 1.8), charge=0.8, temp=0.28, dmg=1.3),
]


def pct(v):
    return f'{round(v * 100)}%'


class PlayerModel:
    def __init__(self, prior=None):
        self.freq = {k: v * 0.5 for k, v in (prior or {}).items()}
        self.trans = {}
        self.last = None
        self.faced = 0
        self.blocked = 0
        self.casts = 0
        self.interrupts = 0

    def record(self, sid):
        for k in self.freq:
            self.freq[k] *= 0.88
        self.freq[sid] = self.freq.get(sid, 0) + 1
        if self.last:
            row = self.trans.setdefault(self.last, {})
            row[sid] = row.get(sid, 0) + 1
        self.last = sid

    @staticmethod
    def total(d):
        return sum(d.values())

    def share(self, sid):
        s = self.total(self.freq)
        return self.freq.get(sid, 0) / s if s else 0.0

    def favorite(self):
        if not self.freq:
            return None
        k = max(self.freq, key=self.freq.get)
        return (k, self.share(k)) if self.freq[k] > 0 else None

    def predict(self):
        row = self.trans.get(self.last) if self.last else None
        src = row if row and self.total(row) >= 2 else self.freq
        s = self.total(src)
        if not s:
            return None
        k = max(src, key=src.get)
        return dict(id=k, p=src[k] / s, markov=src is row)

    def block_rate(self):
        return (self.blocked + 1) / (self.faced + 3)

    def interrupt_rate(self):
        return (self.interrupts + 0.3) / (self.casts + 2)

    def style(self):
        s = self.share
        return dict(
            aggression=min(1, s('fulmina') + s('inferna') + 0.5 * s('glacia') + 0.5 * s('ascenda')),
            defense=min(1, s('aegis') + 0.5 * s('solaris')),
            control=min(1, s('dissolvo') + 0.5 * s('glacia') + 0.5 * s('ascenda')),
            reflex=self.block_rate(),
        )


class Enemy:
    def __init__(self, diff, model, dummy=False):
        self.d = DIFF[diff]
        self.model = model
        self.dummy = dummy
        self.max_hp = 300 if dummy else self.d['hp']
        self.hp = self.max_hp
        self.ward = 0.0
        self.frozen = self.burn = self.disarmed = self.stasis = 0.0
        self.blind = False
        self.cast = None
        self.cool = 2.2
        self.phase = 1
        self.last = None
        self.mend_cd = 0.0
        self.thoughts = []
        self.options = []
        self.flash = 0.0
        self.t = 0.0
        self.since_hit = 0.0
        self.dead = False
        self.dead_t = 0.0
        self.staff = dict(x=0.36, y=0.0, a=0.0, vx=0.0, vy=0.0, va=0.0, away=False)

    @property
    def hp_frac(self):
        return max(0.0, self.hp / self.max_hp)

    def think(self, s):
        self.thoughts.insert(0, s)
        del self.thoughts[5:]

    def disarm(self, t):
        self.cast = None
        self.disarmed = max(self.disarmed, t)
        d = random.choice((-1, 1))
        self.staff.update(x=0.36, y=0.0, a=0.0, vx=d * random.uniform(1.2, 1.8), vy=-2.2, va=d * 14, away=True)

    def interrupt(self):
        if not self.cast:
            return False
        self.model.interrupts += 1
        self.disarm(2.6)
        return True

    def update(self, dt, g):
        self.t += dt
        self.flash = max(0.0, self.flash - dt * 4)
        self.since_hit += dt
        if self.dead:
            self.dead_t += dt
            return
        if self.burn > 0:
            self.burn -= dt
            g.damage_enemy(4 * dt, dot=True)
        self.frozen = max(0.0, self.frozen - dt)
        self.mend_cd -= dt

        st = self.staff
        if st['away']:
            if self.disarmed > 0:
                st['vy'] += 6 * dt
                st['x'] += st['vx'] * dt
                st['y'] += st['vy'] * dt
                st['a'] += st['va'] * dt
                if st['y'] > 0.55:
                    st['y'] = 0.55
                    st['vy'] *= -0.3
                    st['vx'] *= 0.6
                    st['va'] *= 0.5
            else:
                k = min(1.0, dt * 8)
                st['x'] += (0.36 - st['x']) * k
                st['y'] += -st['y'] * k
                st['a'] *= math.exp(-dt * 10)
                if abs(st['x'] - 0.36) < 0.01 and abs(st['y']) < 0.01:
                    st['away'] = False

        if not self.dummy:
            ph = 1 if self.hp_frac > 0.66 else 2 if self.hp_frac > 0.33 else 3
            if ph > self.phase:
                self.phase = ph
                g.on_enemy_phase(ph)

        if self.stasis > 0:
            self.stasis -= dt
            return
        if self.disarmed > 0:
            self.disarmed -= dt
            return
        if self.dummy:
            if self.since_hit > 3:
                self.hp = min(self.max_hp, self.hp + 60 * dt)
            return
        if g.intro_t > 0:
            return

        speed = (0.5 if self.frozen > 0 else 1.0) * (1 + (self.phase - 1) * 0.12)
        if self.cast:
            self.cast['t'] += dt * speed
            if self.cast['t'] >= self.cast['dur']:
                sid = self.cast['id']
                self.cast = None
                self.last = sid
                g.enemy_release(sid)
                self.cool = random.uniform(*self.d['idle']) / (1 + (self.phase - 1) * 0.18)
            return
        self.cool -= dt * speed
        if self.cool <= 0:
            self.decide(g)

    def decide(self, g):
        m = self.model
        st, br, ir, pred = m.style(), m.block_rate(), m.interrupt_rate(), m.predict()
        p = g.player
        opts = []

        def add(sid, u, why):
            opts.append([sid, max(0.02, u), why])

        pred_name = SPELLS[pred['id']]['name'] if pred and pred['id'] in SPELLS else None
        add('hex', 1.0 - 0.7 * br + (0.25 if p['shield'] <= 0 else 0),
            f'Your ward rises {pct(br)} of the time... a feint, then.' if br > 0.55 else 'Testing your guard.')
        add('breaker', 0.3 + 1.5 * br + (1.3 if p['shield'] > 0 else 0) + 0.6 * st['defense'],
            'A ward? It will shatter.' if p['shield'] > 0 else f'You block {pct(br)} of my hexes. Wardbreaker.')
        if self.ward <= 0:
            pred_atk = 0.9 * pred['p'] if pred and pred['id'] in ('fulmina', 'inferna') else 0
            add('ward', 0.25 + 1.6 * st['aggression'] + pred_atk - 0.9 * m.share('dissolvo'),
                f'I foresee {pred_name} ({pct(pred["p"])}). Obsidian Ward.' if pred_atk > 0.3
                else f'You lean on offense ({pct(st["aggression"])}). Raising my ward.')
        if p['dark'] <= 0:
            add('veil', 0.35 + (1 - m.share('solaris')) * 0.55,
                'You rarely call the light. Darkness.' if m.share('solaris') < 0.08 else 'Let the Veil fall.')
        if self.phase >= 2 and self.hp_frac < 0.6 and self.mend_cd <= 0:
            add('mend', 0.5 + (1 - self.hp_frac) * 1.6 - ir * 1.4,
                f'You seldom interrupt ({pct(ir)}). I will mend.' if ir < 0.25 else 'Wounded... I must mend.')
        if self.phase >= 3:
            add('barrage', 0.9 + 0.6 * (1 - br), 'Enough. Hollow Barrage!')

        for o in opts:
            if o[0] == self.last:
                o[1] *= 0.55
        temp = self.d['temp']
        mx = max(o[1] for o in opts)
        ws = [math.exp((o[1] - mx) / temp) for o in opts]
        tot = sum(ws)
        r = random.random() * tot
        pick = opts[0]
        for o, w in zip(opts, ws):
            r -= w
            if r <= 0:
                pick = o
                break
        self.options = sorted(((o[0], w / tot) for o, w in zip(opts, ws)), key=lambda x: -x[1])

        if pick[0] == 'mend':
            self.mend_cd = 12
        self.cast = dict(id=pick[0], t=0.0, dur=ENEMY_SPELLS[pick[0]]['charge'] * self.d['charge'])
        m.casts += 1
        self.think(pick[2])
        fav = m.favorite()
        if fav and m.total(m.freq) > 4 and random.random() < 0.15 and fav[0] in SPELLS:
            self.think(f'({SPELLS[fav[0]]["name"]}: {pct(fav[1])} of your casts.)')
        g.on_enemy_cast_start(pick[0], self.cast['dur'])


# ---------------------------------------------------------------- drawing
def _bezier(p0, p1, p2, p3, n=10):
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        out.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    return out


def _robe(t):
    pts = [(0, -0.64)]
    pts += _bezier((0, -0.64), (0.22, -0.62), (0.27, -0.36), (0.2, -0.22))
    pts += _bezier((0.2, -0.22), (0.36, -0.1), (0.42, 0.3), (0.46, 0.62))
    for i in range(9):
        pts.append((0.46 - i * 0.115, 0.62 + (0.07 if i % 2 else 0) + math.sin(t * 3 + i) * 0.02))
    pts += _bezier(pts[-1], (-0.42, 0.3), (-0.36, -0.1), (-0.2, -0.22))
    pts += _bezier((-0.2, -0.22), (-0.27, -0.36), (-0.22, -0.62), (0, -0.64))
    return pts


def _alpha_poly(surf, pts, color, alpha):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, y0 = int(min(xs)) - 1, int(min(ys)) - 1
    w, h = int(max(xs)) - x0 + 2, int(max(ys)) - y0 + 2
    tmp = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.polygon(tmp, (*rgb(color), int(alpha * 255)), [(x - x0, y - y0) for x, y in pts])
    surf.blit(tmp, (x0, y0))


def draw_archon(surf, layer, e, x, y, s, u=1.0, eye=None):
    t = e.t
    y += math.sin(t * 1.6) * 0.03 * s
    sx = sy = s
    if e.dead:
        k = min(1.0, e.dead_t / 1.5)
        if k >= 1:
            return
        y += k * 0.3 * s
        sx, sy = s * (1 + k * 0.3), s * (1 - k * 0.5)

    def P(ux, uy):
        return (x + ux * sx, y + uy * sy)

    eye = eye or ['#c070ff', '#c070ff', '#ff3b8a', '#ff2a2a'][e.phase]
    lw, lw1 = max(1, round(2 * u)), max(1, round(u))
    blit_glow(surf, eye, x, y, 0.9 * s, 2 + 1 * math.sin(t * 3))

    robe = [P(*p) for p in _robe(t)]
    pygame.draw.polygon(surf, (20, 12, 38), robe)
    pygame.draw.polygon(layer, dim(eye, 0.35), robe, lw)

    # rune trim
    pygame.draw.line(layer, dim(eye, 0.45), P(0, -0.18), P(0, 0.6), lw)
    for i in range(4):
        yy = -0.05 + i * 0.16
        pygame.draw.lines(layer, dim(eye, 0.45), False, [P(-0.03, yy), P(0, yy + 0.04), P(0.03, yy)], lw)

    # face void + eyes
    fw, fh = 0.23 * sx, 0.28 * sy
    pygame.draw.ellipse(surf, (0, 0, 0), pygame.Rect(x - fw / 2, y - 0.38 * sy - fh / 2, fw, fh))
    if not e.blind:
        blink = 0.25 if math.sin(t * 0.7) > 0.97 else 1.0
        for ex in (-0.045, 0.045):
            blit_glow(surf, eye, *P(ex, -0.4), 0.065 * s * blink + 2)

    # sleeves
    for ux in (0.3, -0.3):
        cx, cy = P(ux, 0.02)
        pygame.draw.ellipse(surf, (28, 18, 51), pygame.Rect(cx - 0.07 * sx, cy - 0.05 * sy, 0.14 * sx, 0.1 * sy))

    # staff
    st = e.staff
    ox, oy, ang = (st['x'], st['y'], st['a']) if st['away'] else (0.36, 0.0, 0.0)
    c, sn = math.cos(ang), math.sin(ang)

    def S(ux, uy):
        return P(ox + ux * c - uy * sn, oy + ux * sn + uy * c)

    pygame.draw.line(surf, (59, 42, 26), S(0, -0.42), S(0, 0.6), max(2, int(0.03 * s)))
    orb = ENEMY_SPELLS[e.cast['id']]['color'] if e.cast else eye
    blit_glow(surf, orb, *S(0, -0.5), 0.12 * s)

    # casting rune circle
    if e.cast:
        k = e.cast['t'] / e.cast['dur']
        col = ENEMY_SPELLS[e.cast['id']]['color']
        R = 0.25 + 0.3 * k
        rot = t * 2

        def ring_pt(a, rr):
            return P(math.cos(a + rot) * rr, 0.08 + math.sin(a + rot) * rr * 0.45)

        pts = [ring_pt(i / 40 * math.tau, R) for i in range(41)]
        pygame.draw.lines(layer, dim(col, 0.4 + 0.6 * k), False, pts, lw)
        arcn = max(2, int(40 * k))
        pygame.draw.lines(layer, dim(col, 0.4 + 0.6 * k), False, [ring_pt(i / 40 * math.tau, R * 0.75) for i in range(arcn + 1)], lw)
        for i in range(6):
            a = i / 6 * math.tau
            pygame.draw.line(layer, dim(col, 0.5 + 0.5 * k), ring_pt(a, R), ring_pt(a + 2.1, R), lw1)

    # status overlays
    if e.frozen > 0 or e.stasis > 0:
        _alpha_poly(surf, robe, '#9fd8ff', 0.55 if e.stasis > 0 else 0.35)
        n = 12 if e.stasis > 0 else 6
        for i in range(n):
            a = i / n * math.tau
            cx, cy = math.cos(a) * 0.42, 0.05 + math.sin(a) * 0.55
            pygame.draw.polygon(layer, (150, 200, 220), [P(cx, cy - 0.08), P(cx + 0.035, cy), P(cx, cy + 0.08), P(cx - 0.035, cy)], lw1)
        if e.stasis > 0:
            pygame.draw.rect(layer, (110, 160, 170), pygame.Rect(*P(-0.55, -0.75), 1.1 * sx, 1.5 * sy), lw)
    if e.flash > 0:
        _alpha_poly(surf, robe, '#ffffff', e.flash * 0.8)


def draw_effigy(surf, e, x, y, s, u=1.0):
    def P(ux, uy):
        return (x + ux * s, y + uy * s)

    def R(ux, uy, w, h):
        return pygame.Rect(x + ux * s, y + uy * s, w * s, h * s)

    pygame.draw.rect(surf, (74, 52, 32), R(-0.03, -0.3, 0.06, 1.0))
    pygame.draw.rect(surf, (74, 52, 32), R(-0.38, -0.12, 0.76, 0.05))
    pygame.draw.ellipse(surf, (138, 112, 72), R(-0.22, -0.22, 0.44, 0.64))
    pygame.draw.circle(surf, (138, 112, 72), P(0, -0.38), 0.13 * s)
    for i in range(6):
        pygame.draw.line(surf, (90, 68, 40), P(-0.2, -0.1 + i * 0.07), P(0.2, -0.08 + i * 0.07), max(1, round(2 * u)))
    for r, c in ((0.16, (194, 59, 59)), (0.11, (239, 226, 196)), (0.06, (194, 59, 59))):
        pygame.draw.circle(surf, c, P(0, 0.08), r * s)
    pygame.draw.rect(surf, (42, 26, 12), R(-0.06, -0.42, 0.03, 0.03))
    pygame.draw.rect(surf, (42, 26, 12), R(0.03, -0.42, 0.03, 0.03))
    body = [P(math.cos(a / 20 * math.tau) * 0.32, math.sin(a / 20 * math.tau) * 0.6) for a in range(20)]
    if e.frozen > 0 or e.stasis > 0:
        _alpha_poly(surf, body, '#a0dcff', 0.4)
    if e.flash > 0:
        _alpha_poly(surf, body, '#ffffff', e.flash * 0.7)
