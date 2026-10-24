"""Wander game orchestration: input (hand / mouse / voice / keys), spell
resolution and fusion, the duel loop, rendering and UI."""

import json
import math
import random
import time
from pathlib import Path

import numpy as np
import pygame

from .audio import Audio
from .enemy import DIFF, ENEMY_SPELLS, Enemy, PlayerModel, draw_archon, draw_effigy, pct
from .fx import ADD, FX, blit_glow, dim
from .hands import HAND_EDGES, HandTracker, OneEuro
from .recognizer import GLYPHS, Recognizer, path_length
from .spells import (BASE_COLORS, COMBO_WINDOW, COMBOS, GESTURE_LABEL, GESTURE_TO_SPELL, SPELL_ORDER,
                     SPELLS, rgb)
from .voice import Voice

W, H = 1280, 720
SAVE = Path.home() / '.wander' / 'save.json'
INK = (236, 230, 255)
MUTED = (169, 155, 196)
GOLD = '#ffd98a'
EDGE = (180, 150, 255, 60)
TRIGGER_LABEL = {'point': 'Pointing finger', 'pinch': 'Pinch'}


def clamp(v, a, b):
    return max(a, min(b, v))


def lerp(a, b, t):
    return a + (b - a) * t


def now():
    return time.perf_counter()


class Game:
    def __init__(self, camera=True, voice=True, cam_index=0, fullscreen=False):
        pygame.mixer.pre_init(22050, -16, 2, 512)
        pygame.init()
        flags = pygame.SCALED | pygame.RESIZABLE | (pygame.FULLSCREEN if fullscreen else 0)
        self.screen = pygame.display.set_mode((W, H), flags)
        pygame.display.set_caption('Wander - Spellcasting Duel')
        self.clock = pygame.time.Clock()
        self.running = True

        self.fonts, self.text_cache, self.panel_cache, self.glyph_cache = {}, {}, {}, {}
        self.layer = pygame.Surface((W, H))
        self.overlay = pygame.Surface((W, H), pygame.SRCALPHA)
        self.dark = pygame.Surface((W, H), pygame.SRCALPHA)
        self.hole_cache = {}
        self._build_background()
        self._build_domes()

        self.audio = Audio()
        self.fx = FX()
        self.recognizer = Recognizer()

        data = self._load()
        self.settings = dict(cam=True, voice=True, ar=False, trigger='point', diff=1)
        self.settings.update(data.get('settings', {}))
        if not camera:
            self.settings['cam'] = False
        if not voice:
            self.settings['voice'] = False
        self.customs = data.get('custom', [])
        self.archon_memory = data.get('archon')
        for c in self.customs:
            self.recognizer.add_custom(c['id'], [[tuple(p) for p in s] for s in c['samples']])

        self.cam_index = cam_index
        self.tracker = None
        self.hand = dict(present=False)
        self.hand_seq = -1
        self.hand_small = self.hand_full = None
        self.filt_x, self.filt_y = OneEuro(), OneEuro()
        self.hand_on = self.hand_off = 0
        self.hand_lock = 0.0

        self.voice = Voice()
        self.refresh_voice_entries()

        self.mode = 'menu'
        self.t = 0.0
        self.intro_t = 0.0
        self.over = False
        self.player = None
        self.enemy = None
        self.model = None
        self.rocks, self.projs, self.eprojs, self.ghosts, self.timers = [], [], [], [], []
        self.last_cast = None
        self.stroke = None
        self.heard_queue = []
        self.echo = None
        self.stats = None
        self.last_rec = None
        self.over_info = None
        self.over_at = None

        self.wand = dict(x=W / 2, y=H / 2, vx=0.0, vy=0.0, t=0.0, visible=False, src='mouse', trail=[], last_mouse=-10)
        self.mouse = (0, 0)
        self.buttons = []
        self.banner_state = None
        self.ticker_state = None
        self.toast_state = None
        self.book_open = True
        self.book_lit = {}
        self.forge = dict(samples=[], color=BASE_COLORS[0], name='', incant='', base=1, focus='name', msg='')

    # ------------------------------------------------------------ persistence
    def _load(self):
        try:
            return json.loads(SAVE.read_text())
        except (OSError, ValueError):
            return {}

    def save(self):
        try:
            SAVE.parent.mkdir(parents=True, exist_ok=True)
            SAVE.write_text(json.dumps(dict(settings=self.settings, custom=self.customs, archon=self.archon_memory)))
        except OSError:
            pass

    # ------------------------------------------------------------ text & ui helpers
    def font(self, size, serif=False, bold=False):
        key = (size, serif, bold)
        f = self.fonts.get(key)
        if f is None:
            name = 'georgia,palatinolinotype,timesnewroman' if serif else 'segoeui,arial,helvetica'
            f = self.fonts[key] = pygame.font.SysFont(name, size, bold=bold)
        return f

    def render_text(self, s, size, color, serif=False, bold=False, outline=False):
        color = rgb(color) if isinstance(color, str) else tuple(color)
        key = (s, size, color, serif, bold, outline)
        img = self.text_cache.get(key)
        if img is None:
            if len(self.text_cache) > 900:
                self.text_cache.clear()
            f = self.font(size, serif, bold)
            fg = f.render(s, True, color)
            if outline:
                bg = f.render(s, True, (0, 0, 0))
                img = pygame.Surface((fg.get_width() + 4, fg.get_height() + 4), pygame.SRCALPHA)
                for dx, dy in ((0, 2), (4, 2), (2, 0), (2, 4)):
                    img.blit(bg, (dx, dy))
                img.blit(fg, (2, 2))
            else:
                img = fg
            self.text_cache[key] = img
        return img

    def text(self, surf, s, pos, size=16, color=INK, serif=False, bold=False, anchor='topleft', alpha=1.0, outline=False):
        img = self.render_text(str(s), size, color, serif, bold, outline)
        if alpha < 1:
            img = img.copy()
            img.set_alpha(int(255 * clamp(alpha, 0, 1)))
        r = img.get_rect(**{anchor: (int(pos[0]), int(pos[1]))})
        surf.blit(img, r)
        return r

    def rich(self, surf, parts, pos, size=14, serif=False):
        x, y = pos
        for s, color, *opt in parts:
            r = self.text(surf, s, (x, y), size, color, serif, bold=bool(opt and opt[0]))
            x = r.right
        return x

    def wrap(self, s, size, width, serif=False):
        f = self.font(size, serif)
        lines, cur = [], ''
        for word in s.split():
            test = f'{cur} {word}'.strip()
            if f.size(test)[0] > width and cur:
                lines.append(cur)
                cur = word
            else:
                cur = test
        if cur:
            lines.append(cur)
        return lines

    def panel(self, rect, alpha=190, radius=12, fill=(16, 10, 34), border=EDGE):
        rect = pygame.Rect(rect)
        key = (rect.size, alpha, radius, fill, border)
        s = self.panel_cache.get(key)
        if s is None:
            s = pygame.Surface(rect.size, pygame.SRCALPHA)
            pygame.draw.rect(s, (*fill, alpha), s.get_rect(), border_radius=radius)
            if border:
                pygame.draw.rect(s, border, s.get_rect(), 1, border_radius=radius)
            self.panel_cache[key] = s
        self.screen.blit(s, rect)

    def button(self, rect, label, action, primary=False, size=18, serif=True):
        rect = pygame.Rect(rect)
        hover = rect.collidepoint(self.mouse)
        if primary:
            pygame.draw.rect(self.screen, (255, 228, 160) if hover else (240, 196, 110), rect, border_radius=10)
            pygame.draw.rect(self.screen, (255, 240, 200), rect, 1, border_radius=10)
            self.text(self.screen, label, rect.center, size, (26, 15, 5), serif, True, 'center')
        else:
            self.panel(rect, 70 if hover else 35, 10, (255, 255, 255) if hover else (60, 40, 110))
            self.text(self.screen, label, rect.center, size, INK, serif, serif, 'center')
        self.buttons.append((rect, action))
        return rect

    def glyph_surface(self, key, pts, color, size):
        ck = (key, color, size)
        s = self.glyph_cache.get(ck)
        if s is not None:
            return s
        s = pygame.Surface((size, size), pygame.SRCALPHA)
        pygame.draw.rect(s, (0, 0, 0, 90), s.get_rect(), border_radius=8)
        if pts and len(pts) > 1:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            sc = size * 0.68 / max(max(xs) - min(xs), max(ys) - min(ys), 1e-6)
            ox = size / 2 - (min(xs) + max(xs)) / 2 * sc
            oy = size / 2 - (min(ys) + max(ys)) / 2 * sc
            P = [(x * sc + ox, y * sc + oy) for x, y in pts]
            c = rgb(color)
            pygame.draw.lines(s, (*dim(c, 0.4), 255), False, P, max(3, size // 9))
            pygame.draw.lines(s, (*c, 255), False, P, max(2, size // 18))
            pygame.draw.circle(s, (255, 255, 255), P[0], max(2, size // 16))
            a, b = P[-1], P[max(0, len(P) - 5)]
            ang = math.atan2(a[1] - b[1], a[0] - b[0])
            L = size * 0.16
            pygame.draw.polygon(s, c, [(a[0] + math.cos(ang) * L * 0.4, a[1] + math.sin(ang) * L * 0.4),
                                       (a[0] + math.cos(ang + 2.5) * L, a[1] + math.sin(ang + 2.5) * L),
                                       (a[0] + math.cos(ang - 2.5) * L, a[1] + math.sin(ang - 2.5) * L)])
        self.glyph_cache[ck] = s
        return s

    def toast(self, msg, dur=2.8):
        self.toast_state = dict(text=msg, t=0.0, dur=dur)

    def banner(self, title, sub, color, big=False):
        self.banner_state = dict(title=title, sub=sub or '', color=color, big=big, t=0.0, dur=2.2 if big else 1.6)

    def ticker(self, s, color):
        self.ticker_state = dict(text=s, color=color, t=0.0)

    # ------------------------------------------------------------ spells lookup
    def spec_for(self, gesture_or_id):
        sid = GESTURE_TO_SPELL.get(gesture_or_id, gesture_or_id)
        if sid in SPELLS:
            s = SPELLS[sid]
            return dict(id=sid, base=sid, name=s['name'], color=s['color'])
        for c in self.customs:
            if c['id'] == sid:
                return dict(id=c['id'], base=c['base'], name=c['name'], color=c['color'], custom=True)
        return None

    def all_specs(self):
        out = [dict(id=i, base=i, key=n + 1, name=SPELLS[i]['name'], incant=SPELLS[i]['incant'], color=SPELLS[i]['color'],
                    glyph=GLYPHS[SPELLS[i]['gesture']], cost=SPELLS[i]['cost'], desc=SPELLS[i]['desc'])
               for n, i in enumerate(SPELL_ORDER)]
        out += [dict(id=c['id'], base=c['base'], key=None, name=c['name'], incant=c.get('incant', ''), color=c['color'],
                     glyph=c['samples'][0], cost=SPELLS[c['base']]['cost'], desc=f"Forged form - {SPELLS[c['base']]['name']} effect")
                for c in self.customs]
        return out

    def refresh_voice_entries(self):
        self.voice.set_entries([dict(id=i, incant=SPELLS[i]['incant'], aliases=SPELLS[i]['aliases']) for i in SPELL_ORDER]
                               + [dict(id=c['id'], incant=c['incant']) for c in self.customs if c.get('incant')])

    def enemy_geom(self):
        return W * 0.5, H * 0.36, min(H * 0.32, W * 0.34)

    def later(self, t, fn):
        self.timers.append([t, fn])

    # ------------------------------------------------------------ inputs
    def ensure_inputs(self):
        if self.settings['cam'] and self.tracker is None:
            self.tracker = HandTracker(self.cam_index)
            self.tracker.start()
        if self.settings['voice']:
            self.voice.start()
        else:
            self.voice.stop()

    def can_draw(self):
        return self.mode in ('duel', 'practice', 'forge') and not self.over and self.intro_t <= 0

    def update_wand(self, x, y, src, ts):
        w = self.wand
        dt = max(1 / 240, ts - (w['t'] or ts - 0.016))
        w['vx'] = lerp(w['vx'], (x - w['x']) / dt, 0.35)
        w['vy'] = lerp(w['vy'], (y - w['y']) / dt, 0.35)
        w.update(x=x, y=y, t=ts, visible=True, src=src)
        w['trail'].append((x, y, ts))
        while w['trail'] and ts - w['trail'][0][2] > 0.3:
            w['trail'].pop(0)
        s = self.stroke
        if s and s['src'] == src:
            s['pts'].append((x, y, ts))
            if len(s['pts']) % 2 == 0:
                self.fx.spawn(x, y, random.uniform(-30, 30), random.uniform(-30, 30), 0.5, 6, GOLD, 3)

    def begin_stroke(self, src):
        self.stroke = dict(src=src, pts=[(self.wand['x'], self.wand['y'], now())], t0=now())
        self.audio.play('stroke')

    def end_stroke(self):
        s, self.stroke = self.stroke, None
        if not s:
            return
        pts = s['pts']
        if s['src'] == 'hand' and len(pts) > 8:
            pts = pts[1:-3]
        if len(pts) < 5:
            return
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        if max(max(xs) - min(xs), max(ys) - min(ys)) < min(W, H) * 0.07:
            return
        xy = [(p[0], p[1]) for p in pts]
        if self.mode == 'forge':
            self.forge_add_sample(xy)
            self.add_ghost(xy, self.forge['color'])
            return
        res = self.recognizer.recognize(xy)
        self.last_rec = res
        self.handle_gesture(res, xy)

    def process_hand(self):
        if not self.tracker or not self.settings['cam']:
            return
        seq, r, small, full = self.tracker.snapshot()
        self.hand, self.hand_small, self.hand_full = r, small, full
        if seq == self.hand_seq:
            return
        self.hand_seq = seq
        ts = now()
        if not r['present']:
            if self.stroke and self.stroke['src'] == 'hand':
                self.end_stroke()
            self.hand_on = 0
            self.hand_off += 1
            if self.wand['src'] == 'hand':
                self.wand['visible'] = False
            self.filt_x.reset()
            self.filt_y.reset()
            return
        tx, ty = r['tip']
        mx = clamp((tx - 0.12) / 0.76, 0, 1) * W
        my = clamp((ty - 0.1) / 0.72, 0, 1) * H
        self.update_wand(self.filt_x(mx, ts), self.filt_y(my, ts), 'hand', ts)

        drawing = r['pinch'] if self.settings['trigger'] == 'pinch' else r['pose'] == 'point'
        if drawing:
            self.hand_on += 1
            self.hand_off = 0
        else:
            self.hand_off += 1
            self.hand_on = 0
        if self.hand_lock and (self.hand_off >= 2 or ts > self.hand_lock):
            self.hand_lock = 0
        if not self.stroke and self.hand_on >= 2 and not self.hand_lock and self.can_draw():
            self.begin_stroke('hand')
        if self.stroke and self.stroke['src'] == 'hand' and self.hand_off >= 4:
            self.end_stroke()
            self.hand_lock = 0
        if self.held_rock() and r['pose'] == 'open' and self.wand['vy'] < -H * 2.0:
            self.fling_rock()

    def check_dwell(self):
        s = self.stroke
        if not s:
            return
        ts = now()
        if ts - s['t0'] > 4:
            self.end_stroke()
            return
        if s['src'] != 'hand' or len(s['pts']) < 10 or ts - s['t0'] < 0.5:
            return
        recent = [p for p in s['pts'] if ts - p[2] < 0.42]
        if len(recent) < 3:
            return
        lx, ly, _ = recent[-1]
        md = max(math.hypot(p[0] - lx, p[1] - ly) for p in recent)
        if md < min(W, H) * 0.018 and path_length([(p[0], p[1]) for p in s['pts']]) > min(W, H) * 0.15:
            self.end_stroke()
            self.hand_lock = ts + 0.8

    def process_voice(self):
        while not self.voice.events.empty():
            sid, text = self.voice.events.get()
            if self.mode not in ('duel', 'practice') or self.over or self.intro_t > 0:
                continue
            spec = self.spec_for(sid)
            if not spec:
                continue
            self.ticker(f'"{text}"  ->  {spec["name"]}', spec['color'])
            # Network speech has ~1-2s latency, so the echo window is generous.
            if self.echo and self.echo['id'] == sid and self.t - self.echo['t'] < 3.5:
                self.echo_bonus(self.echo)
                self.echo = None
                continue
            if not any(h['id'] == sid for h in self.heard_queue):
                self.heard_queue.append(dict(id=sid, t=self.t))

    # ------------------------------------------------------------ casting
    def add_ghost(self, pts, color):
        self.ghosts.append(dict(pts=pts, color=color, t=0.0, dur=0.7))
        for x, y in pts[::3]:
            self.fx.spawn(x, y, random.uniform(-60, 60), random.uniform(-90, 10), random.uniform(0.4, 0.9),
                          random.uniform(5, 10), color, 2)

    def handle_gesture(self, res, pts):
        end = pts[-1]
        if not res['name']:
            self.add_ghost(pts, '#6d6185')
            guess = res.get('guess')
            self.banner(res.get('reason', 'The form falters'),
                        f"Closest: {GESTURE_LABEL.get(guess, guess)} ({pct(res['score'])} match)" if guess else 'Bolder, cleaner strokes',
                        '#a99bc4')
            self.audio.play('fizzle')
            self.stats['fizzles'] += 1
            return
        if self.held_rock() and res['name'] in ('up', 'flick'):
            self.add_ghost(pts, '#7dffb0')
            self.fling_rock()
            return
        spec = self.spec_for(res['name'])
        if not spec:
            return
        self.add_ghost(pts, spec['color'])
        hi = next((i for i, h in enumerate(self.heard_queue) if h['id'] == spec['id'] and self.t - h['t'] < 2.5), None)
        empowered = hi is not None
        if empowered:
            self.heard_queue.pop(hi)
        ok = self.cast_spell(spec, quality=res['score'], empowered=empowered, origin=end)
        if ok and not empowered:
            self.echo = dict(id=spec['id'], base=spec['base'], t=self.t, origin=end)

    def cast_spell(self, spec, quality=0.9, empowered=False, whisper=False, origin=None):
        if not spec:
            return False
        P, base = self.player, spec['base']
        S = SPELLS[base]
        if P['cds'].get(base, 0) > self.t:
            self.banner(f"{spec['name']} recharging", f"{P['cds'][base] - self.t:.1f}s", '#8899aa')
            self.audio.play('fizzle')
            return False
        cost = round(S['cost'] * (1.4 if whisper else 1))
        if P['mana'] < cost:
            self.banner('Your mana runs dry', f"{spec['name']} needs {cost}", '#6a8cff')
            self.audio.play('fizzle')
            return False
        P['mana'] -= cost
        P['cds'][base] = self.t + S['cd']
        q = clamp((quality - 0.7) / 0.3, 0, 1)
        power = (0.6 if whisper else 1) * (1.5 if empowered else 1) * (0.85 + 0.3 * q)
        st = self.stats
        st['casts'] += 1
        st['quality'].append(quality)
        st['empowered'] += empowered
        st['by_spell'][base] = st['by_spell'].get(base, 0) + 1
        if self.mode == 'duel':
            self.model.record(base)
        self.book_lit[spec['id']] = 0.7

        origin = origin or (W / 2, H * 0.84)
        lc = self.last_cast
        combo = None
        if lc and self.t - lc['t'] < COMBO_WINDOW:
            combo = next((c for c in COMBOS if c['a'] == lc['base'] and c['b'] == base and self.combo_ready(c)), None)
        self.audio.play('cast')
        if empowered:
            self.audio.play('empowered')
            self.fx.ring(*origin, '#ffffff', 10, 160, 0.6, 3)
        if combo:
            self.last_cast = None
            st['combos'] += 1
            self.cast_combo(combo, power, origin)
        else:
            self.last_cast = dict(base=base, t=self.t)
            tags = [f'{pct(quality)} form'] + (['EMPOWERED'] if empowered else []) + (['voice only'] if whisper else [])
            self.banner(spec['name'], '  -  '.join(tags), spec['color'])
            self.cast_base(base, power, origin, spec['color'], empowered)
        return True

    def combo_ready(self, c):
        return bool(self.held_rock()) if c['id'] == 'meteor' else True

    def dark_miss(self, empowered):
        return self.player['dark'] > 0 and not empowered and random.random() < 0.35

    def launch(self, frm, to, dur, color, size=12, kind='orb', on_hit=None, miss=False):
        if miss:
            to = (to[0] + random.choice((-1, 1)) * random.uniform(160, 260), to[1] - random.uniform(60, 140))
            on_hit = lambda: self.fx.text(*to, 'MISSED in darkness', '#8a7bb0', 22)
        self.projs.append(dict(frm=frm, to=to, dur=dur, color=color, size=size, kind=kind, on_hit=on_hit, t=0.0, x=frm[0], y=frm[1]))

    def heal(self, n):
        P = self.player
        h = min(P['max_hp'] - P['hp'], n)
        P['hp'] += h
        if h > 0.5:
            self.fx.text(W * 0.16, H * 0.8, f'+{round(h)}', '#7dffb0', 30)

    def cast_base(self, base, power, origin, color, empowered):
        E, P = self.enemy, self.player
        ex, ey, es = self.enemy_geom()
        fx = self.fx
        if base == 'aegis':
            P['shield'] = 3 * (1.4 if empowered else 1) * (0.9 + 0.1 * power)
            P['shield_max'] = P['shield']
            P['reflect'] = P['reflect'] or empowered
            self.audio.play('shield')
            fx.ring(W / 2, H * 1.05, color, 40, W * 0.5, 0.6, 6)
            fx.burst(*origin, color, 30, 250)
        elif base == 'fulmina':
            miss = self.dark_miss(empowered)
            tx = ex + (random.choice((-1, 1)) * random.uniform(160, 260) if miss else random.uniform(-20, 20))
            ty = ey + (-random.uniform(60, 140) if miss else random.uniform(-30, 30))
            self.audio.play('zap')
            fx.lightning(*origin, tx, ty, color, 0.35, 2.6 * power)
            fx.lightning(*origin, tx, ty, '#ffffff', 0.2, 1.2)
            fx.flash(color, 0.15, 0.15)
            self.later(0.08, lambda: fx.text(tx, ty, 'MISSED in darkness', '#8a7bb0', 22) if miss
                       else self.damage_enemy(12 * power, color=color))
        elif base == 'dissolvo':
            self.audio.play('slash')

            def hit():
                if E.cast:
                    E.interrupt()
                    fx.text(ex, ey - es * 0.6, 'INTERRUPTED!', color, 42)
                    self.audio.play('interrupt')
                    self.stats['interrupts'] += 1
                elif E.ward > 0:
                    self.shatter_ward()
                else:
                    E.disarm(1.2 * power)
                    fx.text(ex, ey - es * 0.6, 'DISARMED', color, 32)
                self.damage_enemy(4 * power, color=color)
            self.launch(origin, (ex, ey), 0.25, color, 16, 'slash', hit)
        elif base == 'ascenda':
            held = self.held_rock()
            if held:
                held['held_t'] = 7
                held['power'] = max(held['power'], power)
                return
            r = self.nearest_rock(origin)
            if not r:
                self.banner('No boulder answers', 'The stones are still settling', color)
                return
            r.update(state='held', held_t=7, power=power, burning=False, vx=0.0, vy=0.0)
            self.audio.play('rock')
            fx.burst(r['x'], r['y'], ['#7dffb0', '#c9b18a'], 40, 260, grav=300)
            fx.text(r['x'], r['y'] - 60, 'LIFTED - swipe up to fling', color, 22)
        elif base == 'solaris':
            was_dark = P['dark'] > 0
            P['dark'] = 0
            self.heal(6 * power)
            self.audio.play('heal')
            fx.burst(*origin, ['#ffe27a', '#fff6c8'], 60, 420, life=1)
            fx.ring(*origin, color, 10, max(W, H) * 0.6, 0.9, 8)
            fx.flash('#fff6c8', 0.5 if was_dark else 0.2, 0.4)
            if was_dark:
                fx.text(W / 2, H * 0.58, 'THE VEIL LIFTS', color, 36)
        elif base == 'glacia':
            self.audio.play('freeze')

            def hit():
                E.burn = 0
                E.frozen = max(E.frozen, 4 * power)
                self.damage_enemy(6 * power, color=color)
                fx.burst(ex, ey, ['#9fd8ff', '#ffffff'], 40, 350)
                fx.text(ex + es * 0.5, ey - 30, 'FROZEN', color, 26)
            self.launch(origin, (ex, ey), 0.35, color, 13, 'ice', hit, self.dark_miss(empowered))
        elif base == 'inferna':
            self.audio.play('fire')

            def hit():
                E.frozen = 0
                E.burn = 4 * power
                self.damage_enemy(6 * power, color=color)
                fx.burst(ex, ey, ['#ff9a3c', '#ff5a1f', '#ffd27a'], 50, 380, grav=-200)
                fx.text(ex - es * 0.5, ey - 30, 'BURNING', color, 26)
            self.launch(origin, (ex, ey), 0.38, color, 16, 'fire', hit, self.dark_miss(empowered))

    def echo_bonus(self, e):
        E, P = self.enemy, self.player
        ex, ey, _ = self.enemy_geom()
        color = SPELLS[e['base']]['color']
        self.stats['empowered'] += 1
        self.audio.play('empowered')
        self.banner('Echo!', 'Voice joined the gesture - bonus power', color)
        self.fx.ring(*e['origin'], '#ffffff', 10, 140, 0.5, 3)
        b = e['base']
        if b == 'aegis':
            P['shield'] += 1.5
            P['shield_max'] = P['shield']
            P['reflect'] = True
        elif b == 'fulmina':
            self.fx.lightning(*e['origin'], ex, ey, color, 0.3, 2)
            self.later(0.08, lambda: self.damage_enemy(7, color=color))
        elif b == 'dissolvo':
            if E.disarmed > 0:
                E.disarmed += 1
            else:
                self.damage_enemy(4, color=color)
        elif b == 'ascenda':
            r = self.held_rock()
            if r:
                r['power'] *= 1.5
                r['held_t'] += 2
        elif b == 'solaris':
            self.heal(6)
        elif b == 'glacia' and E.frozen > 0:
            E.frozen += 2
        elif b == 'inferna' and E.burn > 0:
            E.burn += 2

    def cast_combo(self, c, power, origin):
        E, P, fx = self.enemy, self.player, self.fx
        ex, ey, es = self.enemy_geom()
        self.banner(c['name'], 'SPELL FUSION', c['color'], True)
        self.audio.play('combo')
        fx.flash(c['color'], 0.3, 0.4)
        fx.add_shake(10)
        fx.ring(*origin, c['color'], 10, 260, 0.7, 5)
        cid = c['id']
        if cid == 'shatter':
            frozen = E.frozen > 0 or E.stasis > 0
            for i in range(3):
                fx.lightning(*origin, ex + random.uniform(-40, 40), ey + random.uniform(-50, 50), '#9fd8ff' if i else '#c9a0ff', 0.4, 3)

            def hit():
                E.frozen = E.stasis = 0
                self.damage_enemy((32 if frozen else 18) * power, color='#e0f4ff', big=True)
                fx.burst(ex, ey, ['#e0f4ff', '#9fd8ff', '#c9a0ff'], 140, 750, life=1.1, size=14)
                self.audio.play('shatter')
                if frozen:
                    fx.text(ex, ey - es * 0.75, 'SHATTERED!', '#e0f4ff', 46)
            self.later(0.1, hit)
        elif cid == 'bastion':
            P.update(shield=4.5, shield_max=4.5, reflect=True)
            self.heal(10 * power)
            self.audio.play('shield')
            self.audio.play('heal')
            fx.ring(W / 2, H * 1.05, c['color'], 40, W * 0.55, 0.8, 8)
        elif cid == 'plasma':
            for i in range(4):
                fx.lightning(*origin, ex + random.uniform(-30, 30), ey + random.uniform(-40, 40), '#ff9a3c' if i % 2 else '#ff6bd6', 0.45, 3.5)
            self.audio.play('zap')

            def hit():
                self.damage_enemy(20 * power, color=c['color'], pierce=True, big=True)
                if E.ward > 0:
                    self.shatter_ward()
            self.later(0.1, hit)
        elif cid == 'steam':
            E.blind = True
            if E.cast:
                E.cast['t'] = max(0.0, E.cast['t'] - 0.6)
            for _ in range(60):
                fx.spawn(ex + random.uniform(-es * 0.5, es * 0.5), ey - es * 0.3 + random.uniform(-40, 40),
                         random.uniform(-40, 40), random.uniform(-50, -10), random.uniform(1, 2), random.uniform(20, 40),
                         '#5a6670', 1, 0, 20)
            fx.text(ex, ey - es * 0.7, 'BLINDED', '#d8e3e8', 34)
        elif cid == 'meteor':
            r = self.held_rock()
            r.update(burning=True, power=max(r['power'], power), held_t=max(r['held_t'], 4))
            fx.burst(r['x'], r['y'], ['#ff5a1f', '#ffd27a'], 60, 300, grav=-200)
            fx.text(r['x'], r['y'] - 60, 'IGNITED - FLING IT!', c['color'], 26)
        elif cid == 'stasis':
            E.stasis = 3 * power
            E.cast = None
            self.audio.play('freeze')
            fx.burst(ex, ey, ['#a8f0ff', '#ffffff'], 90, 450)
            fx.text(ex, ey - es * 0.7, 'STASIS', c['color'], 38)

    def shatter_ward(self):
        ex, ey, es = self.enemy_geom()
        self.enemy.ward = 0
        self.audio.play('shatter')
        self.fx.burst(ex, ey, ['#6b5cff', '#b9a2ff', '#ffffff'], 80, 600, size=9)
        self.fx.text(ex, ey - es * 0.8, 'WARD SHATTERED', '#b9a2ff', 30)

    def damage_enemy(self, amount, color=None, pierce=False, dot=False, big=False):
        E = self.enemy
        if not E or E.dead:
            return 0
        ex, ey, es = self.enemy_geom()
        dmg = amount
        if E.ward > 0 and not pierce and not dot:
            a = min(E.ward, dmg)
            E.ward -= a
            dmg -= a
            self.fx.ring(ex, ey, '#6b5cff', es * 0.5, es * 0.75, 0.35, 5)
            self.fx.text(ex - es * 0.4, ey - es * 0.4, f'-{round(a)} ward', '#9d8cff', 20)
            if E.ward <= 0.01:
                self.shatter_ward()
            if dmg <= 0:
                self.audio.play('block')
                return 0
        E.hp -= dmg
        E.since_hit = 0
        self.stats['dmg'] += dmg
        if not dot:
            E.flash = 1
            self.fx.text(ex + random.uniform(-40, 40), ey - es * 0.55, str(round(dmg)), color or '#ffffff', 48 if big else 32)
            self.fx.burst(ex, ey, color or '#ffffff', 25, 300)
            self.fx.add_shake(14 if big else 5)
            self.audio.play('hit')
        if E.dummy:
            E.hp = max(1, E.hp)
            return dmg
        if E.hp <= 0:
            E.hp = 0
            E.dead = True
            self.victory()
        return dmg

    # ------------------------------------------------------------ enemy hooks
    def on_enemy_cast_start(self, sid, dur):
        self.audio.play('charge')

    def on_enemy_phase(self, ph):
        self.banner('The Archon stirs' if ph == 2 else 'The Archon unravels',
                    'Phase II - it can now mend itself' if ph == 2 else 'Phase III - Hollow Barrage unleashed', '#ff3b8a', True)
        self.fx.flash('#ff2a6a', 0.25, 0.5)
        self.fx.add_shake(12)
        self.audio.play('dark')
        self.enemy.think('You wound me. I will not be careless again.' if ph == 2 else 'ENOUGH. No more games.')

    def enemy_release(self, sid):
        S, E, P = ENEMY_SPELLS[sid], self.enemy, self.player
        ex, ey, es = self.enemy_geom()
        if S['kind'] != 'self' and E.blind:
            E.blind = False
            self.fx.text(ex, ey - es * 0.7, 'BLINDED - MISSED', '#d8e3e8', 30)
            return
        if S['kind'] == 'proj':
            self.fire_at(sid)
        elif S['kind'] == 'multi':
            for i in range(S['count']):
                self.later(i * 0.3, lambda: (not self.over) and self.fire_at(sid))
        elif S['kind'] == 'curse':
            P['dark'] = 9
            self.audio.play('dark')
            self.fx.flash('#000000', 0.7, 0.7)
            self.fx.text(W / 2, H * 0.58, 'UMBRAL VEIL - call the light!', '#b9a2ff', 30)
        elif sid == 'ward':
            E.ward = S['absorb'] * (1 + 0.2 * (E.phase - 1))
            self.audio.play('shield')
            self.fx.ring(ex, ey, S['color'], 20, es * 0.75, 0.6, 5)
        elif sid == 'mend':
            h = min(E.max_hp - E.hp, S['heal'] * (1 + 0.2 * (E.phase - 1)))
            E.hp += h
            self.audio.play('heal')
            self.fx.text(ex, ey - es * 0.6, f'+{round(h)}', S['color'], 34)
            self.fx.burst(ex, ey, S['color'], 50, 300, grav=-150)

    def fire_at(self, sid):
        S, E = ENEMY_SPELLS[sid], self.enemy
        ex, ey, es = self.enemy_geom()
        x0, y0 = ex + es * 0.36, ey - es * 0.5
        self.eprojs.append(dict(id=sid, x0=x0, y0=y0, x1=W / 2 + random.uniform(-W * 0.14, W * 0.14), y1=H * 0.78, t=0.0, dur=0.9,
                                dmg=S['dmg'] * E.d['dmg'] * (1 + 0.1 * (E.phase - 1)), x=x0, y=y0, k=0.0))
        if not S.get('pierce'):
            self.model.faced += 1
        self.audio.play('whoosh')

    def hit_player(self, p):
        S, P = ENEMY_SPELLS[p['id']], self.player
        dmg = p['dmg']
        if P['shield'] > 0:
            if S.get('pierce'):
                P['shield'] = 0
                P['reflect'] = False
                dmg *= 1.25
                self.fx.text(W / 2, H * 0.7, 'WARD SHATTERED', S['color'], 34)
                self.audio.play('shatter')
            else:
                self.model.blocked += 1
                self.stats['blocks'] += 1
                self.audio.play('block')
                self.fx.burst(p['x1'], p['y1'], ['#5ee7ff', '#ffffff'], 40, 420)
                if P['reflect']:
                    P['reflect'] = False
                    self.fx.text(p['x1'], p['y1'] - 40, 'REFLECTED!', '#fff3b0', 36)
                    ex, ey, _ = self.enemy_geom()
                    self.launch((p['x1'], p['y1']), (ex, ey), 0.35, S['color'], 18, 'orb',
                                lambda: self.damage_enemy(p['dmg'] * 1.5, color='#fff3b0'))
                else:
                    self.fx.text(p['x1'], p['y1'] - 40, 'BLOCKED', '#5ee7ff', 28)
                return
        P['hp'] -= dmg
        self.stats['taken'] += dmg
        self.fx.flash('#ff0033', 0.35, 0.35)
        self.fx.add_shake(16)
        self.audio.play('hurt')
        self.fx.burst(p['x1'], p['y1'], S['color'], 50, 500)
        self.fx.text(p['x1'], p['y1'] - 30, f'-{round(dmg)}', '#ff5577', 34)
        if P['hp'] <= 0:
            P['hp'] = 0
            self.defeat()

    # ------------------------------------------------------------ rocks
    @staticmethod
    def rock_shape(n=10):
        return [(i / n * math.tau, random.uniform(0.75, 1.1)) for i in range(n)]

    def make_rocks(self):
        self.rocks = [dict(hx=hx, hy=hy, r=r, x=hx * W, y=hy * H, vx=0.0, vy=0.0, state='rest', held_t=0.0, power=1.0,
                           burning=False, respawn=0.0, a=random.uniform(0, 6), shape=self.rock_shape(), fade=1.0, t=0.0, dur=0.55)
                      for hx, hy, r in ((0.12, 0.86, 0.05), (0.88, 0.84, 0.055), (0.27, 0.95, 0.038), (0.73, 0.95, 0.042))]

    def held_rock(self):
        return next((r for r in self.rocks if r['state'] == 'held'), None)

    def nearest_rock(self, pt):
        rest = [r for r in self.rocks if r['state'] == 'rest']
        return min(rest, key=lambda r: math.hypot(r['x'] - pt[0], r['y'] - pt[1]), default=None)

    def fling_rock(self):
        r = self.held_rock()
        if not r:
            return
        r.update(state='flying', t=0.0, dur=0.55, x0=r['x'], y0=r['y'])
        self.stats['flings'] += 1
        self.audio.play('whoosh')
        self.banner('Meteor!' if r['burning'] else 'Boulder flung', '2.5x damage' if r['burning'] else '',
                    '#ff5a1f' if r['burning'] else '#7dffb0')

    def update_rocks(self, dt):
        ex, ey, _ = self.enemy_geom()
        R = min(W, H)
        w = self.wand
        for r in self.rocks:
            st = r['state']
            if st == 'rest':
                r['x'], r['y'] = r['hx'] * W, r['hy'] * H
                r['fade'] = min(1.0, r['fade'] + dt * 2)
            elif st == 'held':
                r['held_t'] -= dt
                tx = w['x'] if w['visible'] else r['hx'] * W
                ty = min(w['y'], H * 0.8) if w['visible'] else r['hy'] * H - 120
                r['vx'] += (tx - r['x']) * 45 * dt
                r['vy'] += (ty - r['y']) * 45 * dt
                k = math.exp(-7 * dt)
                r['vx'] *= k
                r['vy'] *= k
                r['x'] += r['vx'] * dt
                r['y'] += r['vy'] * dt + math.sin(self.t * 4) * 0.3
                r['a'] += dt * 0.8
                if random.random() < 0.6:
                    self.fx.spawn(r['x'] + random.uniform(-1, 1) * r['r'] * R, r['y'] + r['r'] * R * 0.8, 0,
                                  random.uniform(20, 60), 0.6, 7, '#7dffb0', 1)
                if r['burning']:
                    for _ in range(2):
                        self.fx.spawn(r['x'] + random.uniform(-0.6, 0.6) * r['r'] * R, r['y'] + random.uniform(-0.6, 0.6) * r['r'] * R,
                                      random.uniform(-20, 20), random.uniform(-140, -60), 0.5, random.uniform(10, 18),
                                      random.choice(['#ff9a3c', '#ff5a1f', '#ffd27a']), 1)
                if r['held_t'] <= 0:
                    self.fling_rock()
            elif st == 'flying':
                r['t'] += dt
                k = min(1.0, r['t'] / r['dur'])
                cx, cy = (r['x0'] + ex) / 2, min(r['y0'], ey) - H * 0.25
                r['x'] = (1 - k) ** 2 * r['x0'] + 2 * (1 - k) * k * cx + k * k * ex
                r['y'] = (1 - k) ** 2 * r['y0'] + 2 * (1 - k) * k * cy + k * k * ey
                r['a'] += dt * 10
                self.fx.spawn(r['x'], r['y'], 0, 0, 0.4, 22 if r['burning'] else 10, '#ff5a1f' if r['burning'] else '#7dffb0', 1)
                if k >= 1:
                    self.damage_enemy(14 * r['power'] * (2.5 if r['burning'] else 1),
                                      color='#ff5a1f' if r['burning'] else '#c9b18a', big=r['burning'])
                    if r['burning'] and self.enemy:
                        self.enemy.burn = max(self.enemy.burn, 3)
                    self.audio.play('rock')
                    self.fx.burst(r['x'], r['y'], ['#ff5a1f', '#ffd27a', '#5a4632'] if r['burning'] else ['#c9b18a', '#7a6650'],
                                  60, 500, grav=600, size=9)
                    r.update(state='gone', respawn=6.0, burning=False)
            elif st == 'gone':
                r['respawn'] -= dt
                if r['respawn'] <= 0:
                    r.update(state='rest', fade=0.0, shape=self.rock_shape())

    # ------------------------------------------------------------ modes
    def start_mode(self, mode):
        self.ensure_inputs()
        self.mode = mode
        self.t = 0.0
        self.over = False
        self.over_at = None
        self.projs, self.eprojs, self.ghosts, self.timers, self.heard_queue = [], [], [], [], []
        self.last_cast = self.echo = self.stroke = self.last_rec = None
        self.stats = dict(casts=0, quality=[], empowered=0, combos=0, fizzles=0, interrupts=0, blocks=0, dmg=0.0,
                          taken=0.0, flings=0, by_spell={})
        self.player = dict(hp=100.0, max_hp=100, mana=100.0, max_mana=100, shield=0.0, shield_max=1.0, reflect=False, dark=0.0, cds={})
        self.make_rocks()
        self.fx.clear()
        if mode == 'forge':
            self.enemy = None
            return
        self.model = PlayerModel(self.archon_memory if mode == 'duel' else None)
        self.enemy = Enemy(self.settings['diff'], self.model, dummy=(mode == 'practice'))
        self.intro_t = 3.4 if mode == 'duel' else 0.0
        if mode == 'duel':
            self.audio.start_drone()
            fav = self.model.favorite()
            self.enemy.think(f"I remember you. You favored {SPELLS[fav[0]]['name']}. Not this time."
                             if fav and fav[0] in SPELLS else 'Another wand-bearer. Show me your forms.')
        else:
            self.banner('Practice Grounds', 'Cast freely. The effigy never strikes back.', '#7dffb0')

    def to_menu(self):
        self.mode = 'menu'
        self.stroke = None
        self.audio.stop_drone()

    def victory(self):
        if self.over:
            return
        self.over = True
        self.audio.play('win')
        self.audio.stop_drone()
        ex, ey, _ = self.enemy_geom()
        for i in range(5):
            self.later(i * 0.25, lambda: self.fx.burst(ex + random.uniform(-80, 80), ey + random.uniform(-80, 80),
                                                       ['#c070ff', '#ffffff', '#ffe27a'], 80, 700))
        self.banner('Victory', 'The Archon is unmade', '#ffe27a', True)
        self.save_archon_memory()
        self.over_at = (self.t + 2.6, True)

    def defeat(self):
        if self.over:
            return
        self.over = True
        self.audio.play('lose')
        self.audio.stop_drone()
        self.fx.flash('#000000', 0.8, 1.5)
        self.banner('Defeat', 'Your wand falls silent', '#ff5577', True)
        self.enemy.think('Predictable. I will remember this.')
        self.save_archon_memory()
        self.over_at = (self.t + 2.2, False)

    def save_archon_memory(self):
        prev = self.archon_memory or {}
        snap = self.model.freq
        self.archon_memory = {k: prev.get(k, 0) * 0.5 + snap.get(k, 0) for k in set(prev) | set(snap)}
        self.save()

    def show_over(self, win):
        st, m = self.stats, self.model
        avg_q = sum(st['quality']) / len(st['quality']) if st['quality'] else 0
        fav = m.favorite()
        self.over_info = dict(
            win=win,
            sub=f"Morvane falls in {self.t:.0f}s on {DIFF[self.settings['diff']]['name']}." if win
            else f'Morvane endures with {math.ceil(self.enemy.hp)} HP.',
            rows=[('Spells cast', st['casts']), ('Average form', pct(avg_q)), ('Empowered (voice)', st['empowered']),
                  ('Spell fusions', st['combos']), ('Interrupts', st['interrupts']), ('Hexes blocked', st['blocks']),
                  ('Boulders flung', st['flings']), ('Damage dealt', round(st['dmg'])), ('Damage taken', round(st['taken'])),
                  ('Fizzled forms', st['fizzles'])],
            learned=(f"The Archon learned that you favor {SPELLS[fav[0]]['name']} ({pct(fav[1])}), raise your ward against "
                     f"{pct(m.style()['reflex'])} of hexes, and interrupt {pct(m.interrupt_rate())} of its casts. "
                     'It will remember next duel.') if fav and fav[0] in SPELLS
            else 'The Archon learned nothing of you. Cast more next time.')
        self.mode = 'over'

    # ------------------------------------------------------------ forge
    def forge_add_sample(self, pts):
        f = self.forge
        if len(f['samples']) >= 5:
            f['samples'].pop(0)
        f['samples'].append([(round(x), round(y)) for x, y in pts])
        self.audio.play('cast')
        hits = [r for r in (self.recognizer.recognize(s) for s in f['samples']) if r['name'] and not r.get('custom') and r['score'] > 0.85]
        f['msg'] = (f"Careful: this resembles {GESTURE_LABEL[hits[0]['name']]} ({SPELLS[GESTURE_TO_SPELL[hits[0]['name']]]['name']}). "
                    'Your form still wins when it matches closely.') if len(hits) >= math.ceil(len(f['samples']) / 2) else ''

    def forge_save(self):
        f = self.forge
        if not f['name'].strip():
            f['msg'] = 'Name your spell first.'
            return
        if len(f['samples']) < 3:
            f['msg'] = 'Draw the form at least 3 times.'
            return
        c = dict(id=f'c_{int(time.time() * 1000):x}', name=f['name'].strip(), incant=f['incant'].strip(),
                 base=SPELL_ORDER[f['base']], color=f['color'], samples=f['samples'])
        self.customs.append(c)
        self.recognizer.add_custom(c['id'], c['samples'])
        self.save()
        self.refresh_voice_entries()
        f.update(samples=[], name='', incant='', msg='')
        self.audio.play('combo')
        self.toast(f"{c['name']} is bound to your grimoire.")

    def forge_delete(self, cid):
        self.customs = [c for c in self.customs if c['id'] != cid]
        self.recognizer.remove_custom(cid)
        self.save()
        self.refresh_voice_entries()

    # ------------------------------------------------------------ events
    def handle_event(self, e):
        if e.type == pygame.QUIT:
            self.running = False
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            for rect, action in reversed(self.buttons):
                if rect.collidepoint(e.pos):
                    self.audio.play('tick')
                    action()
                    return
            self.wand['last_mouse'] = now()
            self.update_wand(*e.pos, 'mouse', now())
            if self.mode == 'forge' and e.pos[0] > W - 410:
                return
            if self.can_draw():
                if self.stroke:
                    self.end_stroke()
                self.begin_stroke('mouse')
        elif e.type == pygame.MOUSEMOTION:
            self.mouse = e.pos
            if self.hand.get('present') and self.settings['cam'] and not (self.stroke and self.stroke['src'] == 'mouse'):
                return
            self.wand['last_mouse'] = now()
            self.update_wand(*e.pos, 'mouse', now())
        elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
            if self.stroke and self.stroke['src'] == 'mouse':
                self.end_stroke()
        elif e.type == pygame.TEXTINPUT and self.mode == 'forge':
            f = self.forge
            if len(f[f['focus']]) < 24:
                f[f['focus']] += e.text
        elif e.type == pygame.KEYDOWN:
            self.on_key(e)

    def on_key(self, e):
        if self.mode == 'forge':
            f = self.forge
            if e.key == pygame.K_BACKSPACE:
                f[f['focus']] = f[f['focus']][:-1]
            elif e.key == pygame.K_TAB:
                f['focus'] = 'incant' if f['focus'] == 'name' else 'name'
            elif e.key == pygame.K_RETURN:
                self.forge_save()
            elif e.key == pygame.K_ESCAPE:
                self.to_menu()
            return
        if e.key == pygame.K_F11:
            pygame.display.toggle_fullscreen()
        elif e.key == pygame.K_ESCAPE:
            if self.mode == 'menu':
                self.running = False
            else:
                self.to_menu()
        elif e.key == pygame.K_b:
            self.book_open = not self.book_open
        elif self.mode in ('duel', 'practice') and not self.over and self.intro_t <= 0 and pygame.K_1 <= e.key <= pygame.K_7:
            sid = SPELL_ORDER[e.key - pygame.K_1]
            if self.held_rock() and sid == 'solaris':
                self.fling_rock()
                return
            w = self.wand
            origin = (w['x'], w['y']) if w['visible'] else (W / 2, H * 0.8)
            self.cast_spell(self.spec_for(sid), quality=0.9, empowered=bool(e.mod & pygame.KMOD_SHIFT), origin=origin)

    # ------------------------------------------------------------ update
    def update(self, dt):
        self.t += dt
        self.process_hand()
        self.process_voice()
        self.check_dwell()
        for tm in self.timers:
            tm[0] -= dt
        due = [tm for tm in self.timers if tm[0] <= 0]
        self.timers = [tm for tm in self.timers if tm[0] > 0]
        for tm in due:
            tm[1]()

        if self.mode in ('duel', 'practice'):
            self.update_game(dt)
        elif random.random() < 0.3:
            self.fx.spawn(random.uniform(0, W), H + 10, random.uniform(-10, 10), random.uniform(-60, -20), random.uniform(3, 6),
                          random.uniform(2, 5), random.choice(['#c9a0ff', '#5ee7ff', '#ffe27a']), 0)
        if self.wand['visible'] and not self.stroke and random.random() < 0.5:
            self.fx.spawn(self.wand['x'], self.wand['y'], random.uniform(-20, 20), random.uniform(-10, 30), 0.5, 4, '#bfe8ff', 1)
        for g in self.ghosts:
            g['t'] += dt
        self.ghosts = [g for g in self.ghosts if g['t'] < g['dur']]
        for s in (self.banner_state, self.ticker_state, self.toast_state):
            if s:
                s['t'] += dt
        for k in list(self.book_lit):
            self.book_lit[k] -= dt
            if self.book_lit[k] <= 0:
                del self.book_lit[k]
        self.fx.update(dt)
        if self.over_at and self.t >= self.over_at[0] and self.over:
            win = self.over_at[1]
            self.over_at = None
            self.show_over(win)

    def update_game(self, dt):
        P, E = self.player, self.enemy
        ex, ey, es = self.enemy_geom()
        if self.intro_t > 0:
            before = math.ceil(self.intro_t - 0.4)
            self.intro_t -= dt
            after = math.ceil(self.intro_t - 0.4)
            if self.intro_t > 0.4 and after != before:
                self.audio.play('tick')
            elif 0 < self.intro_t <= 0.4 and before > 0:
                self.audio.play('combo')
        if not self.over:
            P['mana'] = min(P['max_mana'], P['mana'] + 11 * dt)
            P['shield'] = max(0.0, P['shield'] - dt)
            if P['shield'] <= 0:
                P['reflect'] = False
            P['dark'] = max(0.0, P['dark'] - dt)
            for h in list(self.heard_queue):
                if self.t - h['t'] > 2.5:
                    self.heard_queue.remove(h)
                    self.cast_spell(self.spec_for(h['id']), quality=0.85, whisper=True, origin=(W / 2, H * 0.82))
        if not self.over or E.dead:
            E.update(dt, self)

        if not E.dead:
            if E.burn > 0:
                for _ in range(2):
                    self.fx.spawn(ex + random.uniform(-0.3, 0.3) * es, ey + random.uniform(-0.3, 0.5) * es, random.uniform(-20, 20),
                                  random.uniform(-160, -60), 0.6, random.uniform(8, 16), random.choice(['#ff9a3c', '#ff5a1f', '#ffd27a']), 1)
            if E.frozen > 0 and random.random() < 0.3:
                self.fx.spawn(ex + random.uniform(-0.4, 0.4) * es, ey + random.uniform(-0.5, 0.5) * es, 0, random.uniform(10, 40), 1, 4, '#e0f4ff', 0)
            if E.cast:
                col = ENEMY_SPELLS[E.cast['id']]['color']
                ox, oy = ex + es * 0.36, ey - es * 0.5
                a, d = random.uniform(0, math.tau), random.uniform(60, 120)
                self.fx.spawn(ox + math.cos(a) * d, oy + math.sin(a) * d, -math.cos(a) * d * 3, -math.sin(a) * d * 3, 0.3, 6, col, 0)
        self.update_rocks(dt)

        for p in self.projs:
            p['t'] += dt
            k = min(1.0, p['t'] / p['dur'])
            (fx0, fy0), (tx, ty) = p['frm'], p['to']
            cx, cy = (fx0 + tx) / 2, (fy0 + ty) / 2 - math.hypot(tx - fx0, ty - fy0) * 0.18
            p['x'] = (1 - k) ** 2 * fx0 + 2 * (1 - k) * k * cx + k * k * tx
            p['y'] = (1 - k) ** 2 * fy0 + 2 * (1 - k) * k * cy + k * k * ty
            self.fx.spawn(p['x'], p['y'], random.uniform(-30, 30), random.uniform(-30, 30), 0.35, p['size'] * 0.8, p['color'], 2)
            if k >= 1:
                p['done'] = True
                if p['on_hit']:
                    p['on_hit']()
        self.projs = [p for p in self.projs if not p.get('done')]

        for p in self.eprojs:
            p['t'] += dt
            k = min(1.0, p['t'] / p['dur'])
            e = k ** 1.7
            p['x'] = lerp(p['x0'], p['x1'], e)
            p['y'] = lerp(p['y0'], p['y1'], e) - math.sin(k * math.pi) * 40
            p['k'] = k
            self.fx.spawn(p['x'], p['y'], random.uniform(-20, 20), random.uniform(-20, 20), 0.3, 6 + 20 * e, ENEMY_SPELLS[p['id']]['color'], 2)
            if k >= 1:
                p['done'] = True
                if not self.over:
                    self.hit_player(p)
        self.eprojs = [p for p in self.eprojs if not p.get('done')]

    # ------------------------------------------------------------ prebuilt art
    def _build_background(self):
        stops = [(0, (4, 2, 11)), (0.35, (18, 10, 40)), (0.5, (42, 20, 70)), (0.52, (13, 7, 24)), (1, (5, 3, 9))]
        ys = np.linspace(0, 1, H)
        col = np.zeros((H, 3))
        for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
            m = (ys >= p0) & (ys <= p1)
            t = ((ys[m] - p0) / max(p1 - p0, 1e-6))[:, None]
            col[m] = np.array(c0) * (1 - t) + np.array(c1) * t
        arr = np.repeat(col[None, :, :], W, axis=0).astype(np.uint8)
        bg = pygame.surfarray.make_surface(arr)
        mx, my, mr = W * 0.82, H * 0.14, min(W, H) * 0.05
        blit_glow(bg, '#5a4a80', mx, my, mr * 5, 5)
        pygame.draw.circle(bg, (239, 230, 255), (mx, my), mr)
        pygame.draw.circle(bg, (18, 10, 40), (mx + mr * 0.35, my - mr * 0.15), mr * 0.9)
        for layer, color in ((0, (26, 15, 51)), (1, (14, 8, 32))):
            seed = layer * 13.7
            pts = [(0, H * 0.53)]
            for x in range(0, W + 21, 20):
                n = math.sin(x * 0.004 + seed) * 0.5 + math.sin(x * 0.011 + seed * 2) * 0.3 + math.sin(x * 0.027 + seed) * 0.15
                pts.append((x, H * (0.5 - 0.07 * (1 - layer * 0.5)) - n * H * (0.06 - layer * 0.02)))
            pts.append((W, H * 0.53))
            pygame.draw.polygon(bg, color, pts)
        self.bg = bg
        self.stars = [(random.uniform(0, W), random.uniform(0, H * 0.5), random.choice((1, 1, 2)), random.uniform(0, 6)) for _ in range(200)]

    def _build_domes(self):
        cx, cy, rx, ry = W / 2, H * 1.1, W * 0.5, H * 0.48
        top = int(cy - ry)
        self.dome_top = top
        self.domes = {}
        for name, color in (('ward', '#5ee7ff'), ('mirror', '#fff3b0')):
            c = rgb(color)
            s = pygame.Surface((W, H - top), pygame.SRCALPHA)
            mask = pygame.Surface((W, H - top), pygame.SRCALPHA)
            rect = pygame.Rect(cx - rx, cy - ry - top, rx * 2, ry * 2)
            for i in range(12):
                k = i / 12
                pygame.draw.ellipse(s, (*c, int(4 + 40 * (1 - k) ** 3)), rect.inflate(-rx * 2 * k * 0.5, -ry * 2 * k * 0.5))
            hr = 34
            y = 0
            row = 0
            while y < H - top:
                x = (hr * 0.87 if row % 2 else 0)
                while x < W:
                    hexpts = [(x + hr * math.cos(j * math.pi / 3), y + hr * math.sin(j * math.pi / 3)) for j in range(6)]
                    pygame.draw.polygon(mask, (*c, 40), hexpts, 1)
                    x += hr * math.sqrt(3)
                y += hr * 1.5
                row += 1
            clip = pygame.Surface((W, H - top), pygame.SRCALPHA)
            pygame.draw.ellipse(clip, (255, 255, 255, 255), rect)
            mask.blit(clip, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
            s.blit(mask, (0, 0))
            pygame.draw.ellipse(s, (*c, 220), rect, 4)
            self.domes[name] = s

    def hole(self, r):
        r = int(r)
        s = self.hole_cache.get(r)
        if s is None:
            d = 2 * r
            y, x = np.mgrid[0:d, 0:d]
            dist = np.clip(np.sqrt((x - r) ** 2 + (y - r) ** 2) / r, 0, 1)
            s = pygame.Surface((d, d), pygame.SRCALPHA)
            s.fill((2, 0, 8, 255))
            alpha = pygame.surfarray.pixels_alpha(s)
            alpha[:] = (dist * 237).astype(np.uint8).T
            del alpha
            self.hole_cache[r] = s
        return s

    # ------------------------------------------------------------ render: world
    def draw_background(self, surf):
        full = self.hand_full
        if self.settings['ar'] and full is not None:
            img = pygame.image.frombuffer(full.tobytes(), (full.shape[1], full.shape[0]), 'RGB')
            h = int(W * full.shape[0] / full.shape[1])
            surf.blit(pygame.transform.scale(img, (W, h)), (0, (H - h) // 2))
            self.overlay.fill((12, 6, 30, 140))
            surf.blit(self.overlay, (0, 0))
            return
        surf.blit(self.bg, (0, 0))
        for x, y, r, tw in self.stars:
            b = int(110 + 140 * abs(math.sin(self.t * 0.8 + tw)))
            surf.fill((b, b - 10, min(255, b + 20)), (x, y, r, r))
        hy, vx = H * 0.52, W / 2
        col = (34, 24, 64)
        for i in range(-12, 13):
            pygame.draw.line(surf, col, (vx + i * 8, hy), (vx + i * W * 0.18, H))
        for i in range(1, 12):
            y = hy + (H - hy) * ((i / 12) ** 2 + ((self.t * 0.05) % (1 / 12)) * (i / 6))
            pygame.draw.line(surf, col, (0, y), (W, y))

    def draw_floor_glyph(self, layer, ex, ey, es, color):
        cy = ey + es * 0.7
        R = es * 0.75
        rot = self.t * 0.3

        def pt(a, rr):
            return (ex + math.cos(a + rot) * rr, cy + math.sin(a + rot) * rr * 0.22)

        blit_glow(layer, color, ex, cy, R * 0.6, 2)
        for rr in (R * 0.9, R * 0.7):
            pygame.draw.lines(layer, dim(color, 0.55), True, [pt(i / 48 * math.tau, rr) for i in range(48)], 2)
        star = [pt(i * 2 / 5 * math.tau, R * 0.7) for i in range(5)]
        pygame.draw.lines(layer, dim(color, 0.55), True, star, 2)

    def draw_ward(self, layer, E, ex, ey, es):
        if E.ward <= 0:
            return
        a = min(1.0, E.ward / 20)
        rect = pygame.Rect(0, 0, es * 1.24, es * 1.6)
        rect.center = (ex, ey + es * 0.05)
        self.overlay.fill((0, 0, 0, 0))
        pygame.draw.ellipse(self.overlay, (107, 92, 255, int(25 + 25 * a)), rect)
        self.screen.blit(self.overlay, (0, 0))
        pygame.draw.ellipse(layer, dim('#8c7dff', 0.5 + 0.3 * a), rect, 3)
        inner = rect.inflate(-es * 0.12, -es * 0.12)
        for i in range(16):
            ang = i / 16 * math.tau + self.t * 1.2
            p = (inner.centerx + math.cos(ang) * inner.w / 2, inner.centery + math.sin(ang) * inner.h / 2)
            q = (inner.centerx + math.cos(ang + 0.2) * inner.w / 2, inner.centery + math.sin(ang + 0.2) * inner.h / 2)
            pygame.draw.line(layer, dim('#8c7dff', 0.6), p, q, 2)

    def draw_rock(self, surf, layer, r):
        R = r['r'] * min(W, H) * (lerp(1, 0.5, r['t'] / r['dur']) if r['state'] == 'flying' else 1)
        x, y = r['x'], r['y']
        if r['state'] == 'held':
            blit_glow(surf, '#ff5a1f' if r['burning'] else '#7dffb0', x, y, R * 2, 4)
            for i in range(2):
                ang = self.t * 2 + i * 1.5
                pts = []
                for j in range(25):
                    a = j / 24 * math.tau
                    ux, uy = math.cos(a) * R * 1.4, math.sin(a) * R * 0.35
                    pts.append((x + ux * math.cos(ang) - uy * math.sin(ang), y + ux * math.sin(ang) + uy * math.cos(ang)))
                pygame.draw.lines(layer, dim('#7dffb0', 0.6), False, pts, 2)
        elif r['state'] == 'rest':
            self.overlay.fill((0, 0, 0, 0))
            pygame.draw.ellipse(self.overlay, (0, 0, 0, int(90 * r['fade'])), pygame.Rect(x - R * 1.1, y + R * 0.55, R * 2.2, R * 0.5))
            surf.blit(self.overlay, (0, 0))
        pts = [(x + math.cos(a + r['a']) * k * R, y + math.sin(a + r['a']) * k * R) for a, k in r['shape']]
        base = (255, 154, 60) if r['burning'] else (110, 98, 82)
        col = tuple(int(c * r['fade'] + 5 * (1 - r['fade'])) for c in base)
        pygame.draw.polygon(surf, col, pts)
        shade = [(x + (px - x) * 0.6 + R * 0.15, y + (py - y) * 0.6 + R * 0.2) for px, py in pts]
        pygame.draw.polygon(surf, tuple(int(c * 0.55) for c in col), shade)
        pygame.draw.polygon(surf, (125, 255, 176) if r['state'] == 'held' else (70, 64, 60), pts, 2)

    def draw_projectiles(self, surf, layer):
        for p in self.projs:
            blit_glow(surf, p['color'], p['x'], p['y'], p['size'] * 2.4)
            if p['kind'] == 'slash':
                ang = math.atan2(p['to'][1] - p['frm'][1], p['to'][0] - p['frm'][0])
                rr = p['size'] * 2
                rect = pygame.Rect(p['x'] - rr, p['y'] - rr, rr * 2, rr * 2)
                pygame.draw.arc(layer, (255, 255, 255), rect, -ang - 1.2, -ang + 1.2, 4)
        for p in self.eprojs:
            S = ENEMY_SPELLS[p['id']]
            s = 12 + 60 * p['k'] ** 1.5
            blit_glow(surf, S['color'], p['x'], p['y'], s * 1.6)
            if S.get('pierce'):
                for i in range(6):
                    a = self.t * 6 + i / 6 * math.tau
                    pygame.draw.line(layer, (255, 200, 210), (p['x'] + math.cos(a) * s * 0.5, p['y'] + math.sin(a) * s * 0.5),
                                     (p['x'] + math.cos(a) * s * 1.2, p['y'] + math.sin(a) * s * 1.2), 3)

    def draw_shield(self, surf, P):
        if P['shield'] <= 0:
            return
        flicker = (1 if math.sin(self.t * 40) > 0 else 0.4) if P['shield'] < 0.8 else 1
        dome = self.domes['mirror' if P['reflect'] else 'ward']
        dome.set_alpha(int(255 * min(1.0, P['shield'] / 0.3) * flicker))
        surf.blit(dome, (0, self.dome_top))

    def draw_darkness(self, surf, P, ex, ey, es):
        k = min(1.0, P['dark'] / 0.6)
        self.dark.fill((2, 0, 8, 237))
        holes = [(ex, ey - es * 0.4, 70)]
        if self.wand['visible']:
            holes.append((self.wand['x'], self.wand['y'], 170))
        holes += [(p['x'], p['y'], 90) for p in self.projs]
        holes += [(p['x'], p['y'], 60 + 60 * p['k']) for p in self.eprojs]
        for x, y, r in holes:
            h = self.hole(r)
            self.dark.blit(h, (x - r, y - r), special_flags=pygame.BLEND_RGBA_MIN)
        self.dark.set_alpha(int(255 * k)) if k < 1 else self.dark.set_alpha(None)
        surf.blit(self.dark, (0, 0))

    def draw_strokes(self, layer):
        for g in self.ghosts:
            a = 1 - g['t'] / g['dur']
            if len(g['pts']) > 1:
                pygame.draw.lines(layer, dim(g['color'], 0.25 * a), False, g['pts'], int(14 + 16 * (1 - a)))
                pygame.draw.lines(layer, dim(g['color'], 0.9 * a), False, g['pts'], 5)
        if self.stroke and len(self.stroke['pts']) > 1:
            pts = [(p[0], p[1]) for p in self.stroke['pts']]
            pygame.draw.lines(layer, dim(GOLD, 0.3), False, pts, 14)
            pygame.draw.lines(layer, dim(GOLD, 0.95), False, pts, 5)
            pygame.draw.lines(layer, (255, 255, 255), False, pts, 2)
        w = self.wand
        if w['visible']:
            tr = w['trail']
            for i in range(1, len(tr)):
                a = i / len(tr)
                pygame.draw.line(layer, dim('#bfe8ff', a * 0.5), tr[i - 1][:2], tr[i][:2], max(1, int(a * 6)))
            r = 26 if self.stroke else 18 + math.sin(self.t * 6) * 3
            blit_glow(layer, GOLD if self.stroke else '#bfe8ff', w['x'], w['y'], r)

    # ------------------------------------------------------------ render: hud
    def bar(self, rect, frac, colors, glow_color=None):
        rect = pygame.Rect(rect)
        pygame.draw.rect(self.screen, (8, 5, 16), rect, border_radius=rect.h // 2)
        fw = int(rect.w * clamp(frac, 0, 1))
        if fw > 2:
            fill = pygame.Rect(rect.x, rect.y, fw, rect.h)
            pygame.draw.rect(self.screen, rgb(colors[0]), fill, border_radius=rect.h // 2)
            hi = fill.inflate(0, -rect.h // 2)
            hi.width = max(1, fw - 6)
            hi.x += 3
            pygame.draw.rect(self.screen, rgb(colors[1]), hi, border_radius=rect.h // 4)
        pygame.draw.rect(self.screen, (90, 75, 130), rect, 1, border_radius=rect.h // 2)

    def chips(self, items, center_x, y, center=True):
        imgs = [(self.render_text(s, 12, c), c) for s, c in items]
        total = sum(i.get_width() + 18 for i, _ in imgs) + 6 * max(0, len(imgs) - 1)
        x = center_x - total / 2 if center else center_x
        for img, c in imgs:
            rect = pygame.Rect(x, y, img.get_width() + 18, 22)
            self.panel(rect, 150, 11, (0, 0, 0), (*rgb(c), 200))
            self.screen.blit(img, img.get_rect(center=rect.center))
            x += rect.w + 6

    def draw_hud(self):
        E, P, sc = self.enemy, self.player, self.screen
        # enemy bar
        bw = 560
        bx = W / 2 - bw / 2
        name = 'Training Effigy' if E.dummy else 'Morvane, the Hollow Archon'
        r = self.text(sc, name, (W / 2, 14), 18, INK, True, True, 'midtop', outline=True)
        if not E.dummy:
            self.text(sc, ['', 'I', 'II', 'III'][E.phase], (r.right + 8, 16), 18, '#ff3b8a', True, True)
        self.bar((bx, 44, bw, 12), E.hp_frac, ('#a050f0', '#e090ff'))
        if E.ward > 0:
            ww = int(bw * min(1, E.ward / E.max_hp * 2.5))
            pygame.draw.rect(sc, (120, 105, 240), (bx, 44, ww, 12), border_radius=6)
        st = []
        if E.ward > 0:
            st.append((f'Ward {math.ceil(E.ward)}', '#8c7dff'))
        if E.frozen > 0:
            st.append((f'Frozen {E.frozen:.1f}s', '#9fd8ff'))
        if E.burn > 0:
            st.append((f'Burning {E.burn:.1f}s', '#ff9a3c'))
        if E.stasis > 0:
            st.append((f'Stasis {E.stasis:.1f}s', '#a8f0ff'))
        if E.disarmed > 0:
            st.append((f'Disarmed {E.disarmed:.1f}s', '#ff7a59'))
        if E.blind:
            st.append(('Blinded', '#d8e3e8'))
        self.chips(st, W / 2, 62)
        if E.cast:
            S = ENEMY_SPELLS[E.cast['id']]
            cr = pygame.Rect(0, 0, 450, 24)
            cr.midtop = (W / 2, 90)
            self.panel(cr, 170, 6, (0, 0, 0), (*rgb(S['color']), 230))
            k = E.cast['t'] / E.cast['dur']
            fill = pygame.Rect(cr.x + 1, cr.y + 1, int((cr.w - 2) * k), cr.h - 2)
            self.overlay.fill((0, 0, 0, 0))
            pygame.draw.rect(self.overlay, (*rgb(S['color']), 110), fill, border_radius=5)
            sc.blit(self.overlay, (0, 0))
            hint = dict(hex='Raise Aegis Ward', barrage='Raise Aegis Ward', breaker='Interrupt with Severance!',
                        mend='Interrupt with Severance!', veil='Dawnspark will lift it', ward='Severance shatters wards')[E.cast['id']]
            img1 = self.render_text(S['name'], 13, INK, bold=True)
            img2 = self.render_text(f'  -  {hint}', 13, GOLD)
            x0 = cr.centerx - (img1.get_width() + img2.get_width()) / 2
            sc.blit(img1, (x0, cr.y + 3))
            sc.blit(img2, (x0 + img1.get_width(), cr.y + 3))

        # player panel
        pr = pygame.Rect(14, H - 14 - 118, 270, 118)
        self.panel(pr)
        self.text(sc, 'VITALITY', (pr.x + 14, pr.y + 10), 11, MUTED)
        self.text(sc, str(math.ceil(P['hp'])), (pr.right - 14, pr.y + 8), 14, INK, bold=True, anchor='topright')
        self.bar((pr.x + 14, pr.y + 28, pr.w - 28, 11), P['hp'] / P['max_hp'], ('#ff3b5c', '#ff8a6b'))
        self.text(sc, 'MANA', (pr.x + 14, pr.y + 46), 11, MUTED)
        self.text(sc, str(int(P['mana'])), (pr.right - 14, pr.y + 44), 14, INK, bold=True, anchor='topright')
        self.bar((pr.x + 14, pr.y + 64, pr.w - 28, 11), P['mana'] / P['max_mana'], ('#3a6cff', '#5ee7ff'))
        ps = []
        if P['shield'] > 0:
            ps.append((f"Ward {P['shield']:.1f}s" + (' - mirror' if P['reflect'] else ''), '#fff3b0' if P['reflect'] else '#5ee7ff'))
        if P['dark'] > 0:
            ps.append((f"Veiled {P['dark']:.1f}s", '#b9a2ff'))
        rk = self.held_rock()
        if rk:
            ps.append((f"{'Meteor' if rk['burning'] else 'Boulder'} {rk['held_t']:.1f}s", '#ff5a1f' if rk['burning'] else '#7dffb0'))
        self.chips(ps, pr.x + 14, pr.y + 86, center=False)

        self.draw_book()
        self.draw_mind()

        # fusion hint
        lc = self.last_cast
        if lc and self.t - lc['t'] < COMBO_WINDOW:
            opts = [c for c in COMBOS if c['a'] == lc['base'] and self.combo_ready(c)]
            if opts:
                hr = pygame.Rect(0, 0, 330, 30 + 20 * len(opts))
                hr.midtop = (W / 2, int(H * 0.72))
                self.panel(hr, 140, 10, (0, 0, 0))
                self.text(sc, 'FUSION OPEN', (hr.centerx, hr.y + 6), 11, GOLD, bold=True, anchor='midtop')
                for i, c in enumerate(opts):
                    img1 = self.render_text(f"{SPELLS[c['b']]['name']}  ->  ", 14, SPELLS[c['b']]['color'])
                    img2 = self.render_text(c['name'], 14, c['color'], bold=True)
                    x0 = hr.centerx - (img1.get_width() + img2.get_width()) / 2
                    sc.blit(img1, (x0, hr.y + 24 + i * 20))
                    sc.blit(img2, (x0 + img1.get_width(), hr.y + 24 + i * 20))
                left = 1 - (self.t - lc['t']) / COMBO_WINDOW
                pygame.draw.rect(sc, rgb(GOLD), (hr.x + 4, hr.bottom - 4, (hr.w - 8) * left, 2))

        # voice status
        vs = self.voice.state
        dot = {'listening': (255, 59, 92), 'offline': (255, 180, 60)}.get(vs, (85, 85, 85))
        label = {'listening': 'listening', 'offline': 'voice offline (no internet)', 'no-mic': 'no microphone',
                 'unsupported': 'voice: pip install SpeechRecognition pyaudio', 'off': 'voice off'}.get(vs, vs)
        if vs == 'listening' and self.voice.last_heard:
            label = f'heard: "{self.voice.last_heard}"'
        rr = self.text(sc, label, (W / 2 + 8, H - 26), 13, MUTED, anchor='midleft')
        if vs == 'listening':
            pygame.draw.circle(sc, dot, (rr.x - 10, rr.centery), 5 + int(math.sin(self.t * 4) > 0))
        else:
            pygame.draw.circle(sc, dot, (rr.x - 10, rr.centery), 5)
        tk = self.ticker_state
        if tk and tk['t'] < 2.2:
            self.text(sc, tk['text'], (W / 2, H - 56), 16, tk['color'], anchor='midbottom', alpha=min(1, (2.2 - tk['t']) * 3), outline=True)

        # intro
        if self.intro_t > 0:
            s = str(math.ceil(self.intro_t - 0.4)) if self.intro_t > 0.4 else 'Duel!'
            blit_glow(sc, GOLD, W / 2, H * 0.58, 120, 3)
            self.text(sc, s, (W / 2, H * 0.58), 130, GOLD, True, True, 'center', outline=True)

    def draw_book(self):
        sc = self.screen
        specs = self.all_specs()
        h = 40 + (len(specs) * 46 if self.book_open else 0)
        br = pygame.Rect(14, 60, 236, h)
        self.panel(br)
        self.button((br.x + 6, br.y + 6, br.w - 12, 28), ('Spellbook  [B]' if self.book_open else 'Spellbook  (show)'),
                    lambda: setattr(self, 'book_open', not self.book_open), size=15)
        if not self.book_open:
            return
        P = self.player
        for i, s in enumerate(specs):
            y = br.y + 40 + i * 46
            row = pygame.Rect(br.x + 6, y, br.w - 12, 42)
            lit = self.book_lit.get(s['id'], 0)
            if lit > 0:
                self.panel(row, int(120 * lit / 0.7), 8, rgb(s['color']), None)
            poor = P['mana'] < s['cost']
            sc.blit(self.glyph_surface(s['id'], s['glyph'], s['color'], 38), (row.x + 2, row.y + 2))
            col = s['color'] if not poor else dim(s['color'], 0.5)
            self.text(sc, s['name'], (row.x + 48, row.y + 3), 14, col, bold=True)
            sub = f"\"{s['incant']}\"  {s['cost']} mana" + (f"  [{s['key']}]" if s['key'] else '')
            self.text(sc, sub, (row.x + 48, row.y + 22), 11, MUTED if not poor else dim(MUTED, 0.6))
            left = max(0.0, P['cds'].get(s['base'], 0) - self.t)
            if left > 0:
                pygame.draw.rect(sc, rgb(s['color']), (row.x, row.bottom - 2, row.w * left / SPELLS[s['base']]['cd'], 2))

    def draw_mind(self):
        sc, E = self.screen, self.enemy
        mr = pygame.Rect(W - 14 - 300, 60, 300, 300)
        self.panel(mr)
        x, y = mr.x + 14, mr.y + 12
        if E.dummy:
            self.text(sc, 'Wand Reading', (x, y), 15, '#ff8ad0', True, True)
            res = self.last_rec
            y += 30
            if res:
                name = (self.spec_for(res['name']) or {}).get('name', res['name']) if res['name'] else 'Unrecognized'
                r = self.text(sc, name, (x, y), 20, INK, True, True)
                self.text(sc, pct(res['score']), (r.right + 8, y + 3), 16, GOLD)
                inf = res.get('info')
                if inf:
                    self.text(sc, f"Straightness {inf['straight']:.2f}   Loops {inf['loops']:.2f}", (x, y + 32), 12, MUTED)
                    self.text(sc, f"Radial variance {inf['cv']:.2f}", (x, y + 50), 12, MUTED)
                if res.get('guess'):
                    self.text(sc, f"Closest template: {GESTURE_LABEL.get(res['guess'], res['guess'])}", (x, y + 68), 12, MUTED)
            else:
                self.text(sc, 'Draw a form...', (x, y), 16, MUTED)
            y = mr.y + 150
            for tip in ('Hold your forms large and steady.', 'Say the incantation as you draw to empower.',
                        'Chain spells within 4s to fuse them.'):
                self.text(sc, f'- {tip}', (x, y), 13, (229, 216, 255))
                y += 22
            return
        m = self.model
        self.text(sc, "The Archon's Mind", (x, y), 15, '#ff8ad0', True, True)
        y += 28
        s = m.style()
        for label, v, c in (('Aggression', s['aggression'], '#ff7a59'), ('Defense', s['defense'], '#5ee7ff'),
                            ('Control', s['control'], '#7dffb0'), ('Block reflex', s['reflex'], '#c9a0ff')):
            self.text(sc, label, (x, y), 12, MUTED)
            br = pygame.Rect(x + 86, y + 6, 150, 6)
            pygame.draw.rect(sc, (40, 34, 60), br, border_radius=3)
            pygame.draw.rect(sc, rgb(c), (br.x, br.y, br.w * clamp(v, 0, 1), br.h), border_radius=3)
            self.text(sc, pct(v), (mr.right - 14, y), 12, INK, anchor='topright')
            y += 19
        y += 6
        pred = m.predict()
        if pred and pred['id'] in SPELLS:
            self.rich(sc, [('Expects next: ', INK), (SPELLS[pred['id']]['name'], SPELLS[pred['id']]['color'], True),
                           (f" {pct(pred['p'])} ", INK), ('(pattern)' if pred['markov'] else '(habit)', MUTED)], (x, y), 13)
        else:
            self.text(sc, 'Expects next: studying you...', (x, y), 13, MUTED)
        y += 20
        if E.options:
            line = 'Weighed: ' + '  '.join(f"{ENEMY_SPELLS[o]['name']} {pct(p)}" for o, p in E.options[:3])
            for ln in self.wrap(line, 12, mr.w - 28):
                self.text(sc, ln, (x, y), 12, MUTED)
                y += 16
        y += 4
        pygame.draw.line(sc, (70, 56, 110), (x, y), (mr.right - 14, y))
        y += 8
        for i, th in enumerate(E.thoughts):
            for ln in self.wrap(th, 13, mr.w - 28, serif=True):
                if y > mr.bottom - 18:
                    return
                self.text(sc, ln, (x, y), 13, (229, 216, 255), serif=True, alpha=1 - i * 0.17)
                y += 17
            y += 3

    def draw_banner(self):
        b = self.banner_state
        if not b or b['t'] >= b['dur']:
            return
        k = b['t'] / b['dur']
        a = k / 0.12 if k < 0.12 else 1 if k < 0.75 else (1 - k) / 0.25
        y = H * 0.61 - (k - 0.75) * 60 * (k > 0.75)
        size = 64 if b['big'] else 42
        blit_glow(self.screen, b['color'], W / 2, y - 6, size * 2.2, int(2 * a) + 1)
        self.text(self.screen, b['title'], (W / 2, y), size, b['color'], True, True, 'midbottom', alpha=a, outline=True)
        if b['sub']:
            self.text(self.screen, b['sub'].upper(), (W / 2, y + 4), 14, INK, anchor='midtop', alpha=a, outline=True)

    def draw_pip(self):
        if not self.settings['cam'] or not self.tracker:
            return
        sc = self.screen
        rect = pygame.Rect(W - 14 - 200, H - 14 - 150, 200, 150)
        if self.tracker.error or not self.tracker.ready:
            msg = self.tracker.error or self.tracker.status or 'Starting camera...'
            self.panel(rect, 200, 10, (0, 0, 0))
            for i, ln in enumerate(self.wrap(msg, 12, 180)):
                self.text(sc, ln, (rect.x + 10, rect.y + 10 + i * 16), 12, MUTED)
            return
        small = self.hand_small
        if small is None:
            return
        sc.blit(pygame.image.frombuffer(small.tobytes(), (200, 150), 'RGB'), rect)
        self.overlay.fill((0, 0, 0, 0))
        pygame.draw.rect(self.overlay, (10, 5, 25, 80), rect)
        sc.blit(self.overlay, (0, 0))
        label = 'no hand'
        r = self.hand
        if r.get('present'):
            L = [(rect.x + px * 200, rect.y + py * 150) for px, py in r['lm']]
            col = rgb(GOLD) if self.stroke else (125, 216, 255)
            for a, b in HAND_EDGES:
                pygame.draw.line(sc, col, L[a], L[b], 2)
            pygame.draw.circle(sc, (255, 255, 255), L[8], 4)
            label = 'casting' if self.stroke else ('pinch' if r['pinch'] else r['pose'])
        pygame.draw.rect(sc, (90, 75, 130), rect, 1, border_radius=4)
        self.text(sc, label.upper(), (rect.x + 8, rect.y + 6), 11, GOLD, bold=True, outline=True)

    # ------------------------------------------------------------ render: screens
    def draw_menu(self):
        sc = self.screen
        self.overlay.fill((5, 3, 11, 150))
        sc.blit(self.overlay, (0, 0))
        self.text(sc, 'A  DUEL  OF  GESTURE  &  WORD', (W / 2, 26), 12, MUTED, anchor='midtop')
        blit_glow(sc, '#b07a2a', W / 2, 92, 220, 2)
        self.text(sc, 'WANDER', (W / 2, 92), 92, GOLD, True, True, 'center', outline=True)
        self.text(sc, 'Your hand is the wand. Your voice is the word.', (W / 2, 146), 19, (217, 205, 245), True, anchor='center')
        self.text(sc, 'No camera? Draw with the mouse.  Keys 1-7 cast (Shift = empowered).  F11 fullscreen.  Esc quits.',
                  (W / 2, 172), 13, MUTED, anchor='center')
        x = W / 2 - (250 + 220 + 200 + 24) / 2
        self.button((x, 194, 250, 50), 'Duel the Archon', lambda: self.start_mode('duel'), primary=True, size=20)
        self.button((x + 262, 194, 220, 50), 'Practice Grounds', lambda: self.start_mode('practice'), size=19)
        self.button((x + 494, 194, 200, 50), 'Spell Forge', lambda: self.start_mode('forge'), size=19)

        s = self.settings

        def toggle(key):
            def f():
                s[key] = not s[key]
                if key == 'voice' and not s[key]:
                    self.voice.stop()
                self.save()
            return f

        def cycle_trigger():
            s['trigger'] = 'pinch' if s['trigger'] == 'point' else 'point'
            self.save()

        def cycle_diff():
            s['diff'] = (s['diff'] + 1) % 3
            self.save()

        items = [(f"Webcam wand: {'On' if s['cam'] else 'Off'}", toggle('cam')),
                 (f"Voice: {'On' if s['voice'] else 'Off'}", toggle('voice')),
                 (f"AR backdrop: {'On' if s['ar'] else 'Off'}", toggle('ar')),
                 (f"Draw with: {TRIGGER_LABEL[s['trigger']]}", cycle_trigger),
                 (f"Foe: {DIFF[s['diff']]['name']}", cycle_diff)]
        widths = [200, 140, 180, 230, 160]
        x = W / 2 - (sum(widths) + 8 * 4) / 2
        for (label, fn), w in zip(items, widths):
            self.button((x, 256, w, 32), label, fn, size=14, serif=False)
            x += w + 8

        mem = self.archon_memory
        if mem:
            fav = max(mem, key=mem.get)
            if fav in SPELLS:
                r = self.rich(sc, [('The Archon remembers your last duels. You lean on ', MUTED),
                                   (SPELLS[fav]['name'], SPELLS[fav]['color'], True), ('.', MUTED)], (W / 2 - 260, 300), 13)

                def forget():
                    self.archon_memory = None
                    self.save()
                    self.toast("The Archon's memory of you is wiped.")
                self.button((r + 10, 297, 130, 22), 'Make it forget', forget, size=12, serif=False)

        how = [('1 - Point', 'Raise one finger (or pinch). Its tip is your wand.'),
               ('2 - Draw', 'Trace a form in the air. Relax or hold still to release.'),
               ('3 - Speak', 'Say the incantation while drawing to empower.'),
               ('4 - Fuse', 'Chain two spells within 4s for a fusion.')]
        bw = 296
        x = W / 2 - (bw * 4 + 10 * 3) / 2
        for i, (t, d) in enumerate(how):
            r = pygame.Rect(x + i * (bw + 10), 330, bw, 60)
            self.panel(r, 60, 10, (40, 25, 80))
            self.text(sc, t, (r.x + 12, r.y + 8), 15, GOLD, True, True)
            self.text(sc, d, (r.x + 12, r.y + 34), 12, (207, 196, 234))

        self.text(sc, 'SPELLBOOK', (40, 404), 16, GOLD, True, True)
        specs = self.all_specs()[:8]
        cw, ch = 296, 70
        for i, s2 in enumerate(specs):
            r = pygame.Rect(40 + (i % 4) * (cw + 10), 428 + (i // 4) * (ch + 8), cw, ch)
            self.panel(r, 60, 10, (40, 25, 80), (*rgb(s2['color']), 90))
            sc.blit(self.glyph_surface(s2['id'], s2['glyph'], s2['color'], 54), (r.x + 8, r.y + 8))
            self.text(sc, s2['name'], (r.x + 72, r.y + 8), 15, s2['color'], True, True)
            self.text(sc, f"\"{s2['incant']}!\"" if s2['incant'] else 'silent', (r.x + 72, r.y + 28), 13, INK, True)
            gest = SPELLS[s2['id']]['desc'].split('.')[0] if s2['id'] in SPELLS else 'Forged form'
            self.text(sc, gest, (r.x + 72, r.y + 48), 12, MUTED)

        self.text(sc, 'SPELL FUSIONS', (40, 586), 16, GOLD, True, True)
        cw2 = 396
        for i, c in enumerate(COMBOS):
            r = pygame.Rect(40 + (i % 3) * (cw2 + 10), 610 + (i // 3) * 52, cw2, 46)
            self.panel(r, 50, 8, (40, 25, 80))
            pygame.draw.rect(sc, rgb(c['color']), (r.x, r.y + 4, 3, r.h - 8))
            self.rich(sc, [(SPELLS[c['a']]['name'], SPELLS[c['a']]['color']), ('  ->  ', MUTED),
                           (SPELLS[c['b']]['name'], SPELLS[c['b']]['color']), ('  =  ', MUTED),
                           (c['name'], c['color'], True)], (r.x + 12, r.y + 5), 13)
            self.text(sc, c['desc'], (r.x + 12, r.y + 25), 12, MUTED)

    def draw_forge(self):
        sc, f = self.screen, self.forge
        area = pygame.Rect(40, 110, W - 480, H - 160)
        self.text(sc, 'Draw your new form here', (area.centerx, 70), 22, (200, 190, 230), True, True, 'midtop')
        for i in range(0, area.w, 20):
            pygame.draw.line(sc, (70, 60, 110), (area.x + i, area.y), (area.x + i + 10, area.y))
            pygame.draw.line(sc, (70, 60, 110), (area.x + i, area.bottom), (area.x + i + 10, area.bottom))
        for i in range(0, area.h, 20):
            pygame.draw.line(sc, (70, 60, 110), (area.x, area.y + i), (area.x, area.y + i + 10))
            pygame.draw.line(sc, (70, 60, 110), (area.right, area.y + i), (area.right, area.y + i + 10))

        pr = pygame.Rect(W - 410, 0, 410, H)
        self.panel(pr, 230, 0, (12, 7, 26))
        x, y = pr.x + 22, 20
        self.text(sc, 'Spell Forge', (x, y), 24, GOLD, True, True)
        y += 40
        for ln in self.wrap('Invent a wand form. Draw it 3-5 times on the left, then bind it to an effect and an incantation.', 13, 366):
            self.text(sc, ln, (x, y), 13, (207, 196, 234))
            y += 18
        y += 8
        for key, label, ph in (('name', 'Spell name', 'Moonveil'), ('incant', 'Incantation', 'Nocturna (optional)')):
            self.text(sc, label, (x, y), 13, MUTED)
            box = pygame.Rect(x, y + 18, 366, 32)
            focused = f['focus'] == key
            self.panel(box, 160, 6, (0, 0, 0), (*rgb(GOLD), 200) if focused else EDGE)
            val = f[key]
            caret = '|' if focused and int(self.t * 2) % 2 == 0 else ''
            self.text(sc, (val + caret) if val or focused else ph, (box.x + 10, box.y + 6), 15, INK if val else (110, 100, 140))
            self.buttons.append((box, (lambda k=key: f.update(focus=k))))
            y += 60
        self.text(sc, 'Effect', (x, y), 13, MUTED)
        y += 18

        def shift(d):
            return lambda: f.update(base=(f['base'] + d) % len(SPELL_ORDER))
        self.button((x, y, 36, 32), '<', shift(-1), size=16)
        er = pygame.Rect(x + 42, y, 282, 32)
        self.panel(er, 120, 6, (0, 0, 0))
        base = SPELL_ORDER[f['base']]
        self.text(sc, SPELLS[base]['name'], er.center, 15, SPELLS[base]['color'], True, True, 'center')
        self.button((x + 330, y, 36, 32), '>', shift(1), size=16)
        y += 44
        self.text(sc, 'Color', (x, y), 13, MUTED)
        y += 20
        for i, c in enumerate(BASE_COLORS):
            cr = pygame.Rect(x + i * 34, y, 26, 26)
            pygame.draw.circle(sc, rgb(c), cr.center, 13)
            if c == f['color']:
                pygame.draw.circle(sc, (255, 255, 255), cr.center, 15, 2)
            self.buttons.append((cr, (lambda cc=c: f.update(color=cc))))
        y += 40
        self.text(sc, f"Samples  {len(f['samples'])}/3", (x, y), 13, MUTED)
        y += 20
        for i, s in enumerate(f['samples']):
            sc.blit(self.glyph_surface(('sample', i, len(s), s[0]), s, f['color'], 64), (x + i * 72, y))
        y += 72
        if f['msg']:
            for ln in self.wrap(f['msg'], 12, 366):
                self.text(sc, ln, (x, y), 12, GOLD)
                y += 16
        y = max(y + 6, 520)
        self.button((x, y, 176, 38), 'Clear samples', lambda: f.update(samples=[], msg=''), size=15)
        self.button((x + 190, y, 176, 38), 'Bind spell', self.forge_save, primary=True, size=16)
        y += 52
        self.text(sc, 'Your grimoire', (x, y), 15, GOLD, True, True)
        y += 24
        if not self.customs:
            self.text(sc, 'No forged spells yet.', (x, y), 13, MUTED)
        for c in self.customs[-3:]:
            sc.blit(self.glyph_surface(c['id'], c['samples'][0], c['color'], 30), (x, y))
            self.text(sc, c['name'], (x + 38, y), 14, c['color'], bold=True)
            self.text(sc, (f"\"{c['incant']}\" - " if c.get('incant') else '') + f"{SPELLS[c['base']]['name']} effect", (x + 38, y + 16), 11, MUTED)
            self.button((pr.right - 52, y + 2, 30, 26), 'x', (lambda cid=c['id']: self.forge_delete(cid)), size=13, serif=False)
            y += 36
        self.button((W - 130, H - 44, 110, 32), '< Menu', self.to_menu, size=14)

    def draw_over(self):
        sc, o = self.screen, self.over_info
        self.overlay.fill((5, 3, 11, 205))
        sc.blit(self.overlay, (0, 0))
        color = GOLD if o['win'] else '#ff5577'
        blit_glow(sc, color, W / 2, 110, 200, 2)
        self.text(sc, 'Victory' if o['win'] else 'Defeat', (W / 2, 110), 84, color, True, True, 'center', outline=True)
        self.text(sc, o['sub'], (W / 2, 168), 18, (217, 205, 245), True, anchor='center')
        cw, ch = 300, 36
        x0 = W / 2 - (cw * 2 + 10) / 2
        for i, (k, v) in enumerate(o['rows']):
            r = pygame.Rect(x0 + (i % 2) * (cw + 10), 200 + (i // 2) * (ch + 8), cw, ch)
            self.panel(r, 70, 8, (40, 25, 80))
            self.text(sc, k, (r.x + 12, r.centery), 14, MUTED, anchor='midleft')
            self.text(sc, str(v), (r.right - 12, r.centery), 15, INK, bold=True, anchor='midright')
        y = 200 + 5 * (ch + 8) + 14
        for ln in self.wrap(o['learned'], 15, 640):
            self.text(sc, ln, (W / 2, y), 15, (229, 216, 255), anchor='midtop')
            y += 22
        y += 20
        x = W / 2 - (200 + 170 + 130 + 20) / 2
        self.button((x, y, 200, 46), 'Rematch', lambda: self.start_mode('duel'), primary=True, size=19)
        self.button((x + 210, y, 170, 46), 'Practice', lambda: self.start_mode('practice'), size=18)
        self.button((x + 390, y, 130, 46), 'Menu', self.to_menu, size=18)

    def render(self):
        sc = self.screen
        self.buttons = []
        self.layer.fill((0, 0, 0))
        shake = self.fx.shake
        self.draw_background(sc)
        in_game = self.mode in ('duel', 'practice', 'over') and self.enemy
        ex, ey, es = self.enemy_geom()
        if in_game:
            E = self.enemy
            glyph_col = '#7dffb0' if E.dummy else ['#c070ff', '#c070ff', '#ff3b8a', '#ff2a2a'][E.phase]
            self.draw_floor_glyph(self.layer, ex, ey, es, glyph_col)
            for r in self.rocks:
                if r['state'] == 'rest':
                    self.draw_rock(sc, self.layer, r)
            ox = random.uniform(-shake, shake) if shake > 0.5 else 0
            oy = random.uniform(-shake, shake) if shake > 0.5 else 0
            if E.dummy:
                draw_effigy(sc, E, ex + ox, ey + oy, es)
            else:
                draw_archon(sc, self.layer, E, ex + ox, ey + oy, es)
            self.draw_ward(self.layer, E, ex, ey, es)
            self.draw_projectiles(sc, self.layer)
        self.fx.draw_particles(sc)
        self.fx.draw_lines(self.layer)
        sc.blit(self.layer, (0, 0), special_flags=ADD)
        if in_game:
            self.layer.fill((0, 0, 0))
            for r in self.rocks:
                if r['state'] in ('held', 'flying'):
                    self.draw_rock(sc, self.layer, r)
            sc.blit(self.layer, (0, 0), special_flags=ADD)
            self.draw_shield(sc, self.player)
            if self.player['dark'] > 0:
                self.draw_darkness(sc, self.player, ex, ey, es)
        self.fx.draw_texts(sc, lambda surf, s, pos, **kw: self.text(surf, s, pos, **kw))

        if self.mode in ('duel', 'practice'):
            self.draw_hud()
            self.button((14, 14, 90, 30), 'x  Leave', self.to_menu, size=13, serif=False)
        elif self.mode == 'menu':
            self.draw_menu()
        elif self.mode == 'forge':
            self.draw_forge()
        elif self.mode == 'over':
            self.draw_over()

        self.layer.fill((0, 0, 0))
        self.draw_strokes(self.layer)
        sc.blit(self.layer, (0, 0), special_flags=ADD)
        if self.mode in ('duel', 'practice', 'forge'):
            self.draw_banner()
        self.fx.draw_flashes(sc, self.overlay)
        self.draw_pip()
        ts = self.toast_state
        if ts and ts['t'] < ts['dur']:
            img = self.render_text(ts['text'], 15, INK)
            r = pygame.Rect(0, 0, img.get_width() + 32, 38)
            r.midtop = (W / 2, 14)
            self.panel(r, 235, 10, (20, 12, 40))
            sc.blit(img, img.get_rect(center=r.center))

    # ------------------------------------------------------------ loop
    def step(self, dt, events=None):
        for e in (pygame.event.get() if events is None else events):
            self.handle_event(e)
        self.update(dt)
        self.render()
        pygame.display.flip()

    def run(self):
        while self.running:
            dt = min(0.05, self.clock.tick(60) / 1000)
            self.step(dt)
        self.shutdown()

    def shutdown(self):
        self.voice.stop()
        if self.tracker:
            self.tracker.stop()
        pygame.quit()


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='Wander - a webcam spellcasting duel')
    ap.add_argument('--no-camera', action='store_true', help='mouse-only casting')
    ap.add_argument('--no-voice', action='store_true', help='disable incantations')
    ap.add_argument('--camera', type=int, default=0, help='webcam index (default 0)')
    ap.add_argument('--fullscreen', action='store_true')
    a = ap.parse_args(argv)
    Game(camera=not a.no_camera, voice=not a.no_voice, cam_index=a.camera, fullscreen=a.fullscreen).run()
