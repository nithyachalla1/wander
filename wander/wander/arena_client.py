"""Arena client: the multiplayer lobby and match screens, mixed into Game.

The server decides every outcome; this side sends casts (with the gesture /
voice power computed locally) and turns the server's events into effects."""

import math
import random

import pygame

from . import game as G  # read G.W / G.H / G.U at call time (they change on resize)
from .arena import FUSION_NAMES
from .enemy import draw_archon
from .fx import blit_glow, dim
from .net import DEFAULT_PORT, NetClient, lan_ip
from .spells import COMBOS, SPELLS, rgb

SELF_SPELLS = {'aegis', 'solaris'}
COMBO_COLOR = {c['id']: c['color'] for c in COMBOS}
TRAVEL = dict(fulmina=0.25, dissolvo=0.3, ascenda=1.0, glacia=0.35, inferna=0.4)
COMBO_TRAVEL = dict(shatter=0.25, plasma=0.25, steam=0.4, meteor=1.0, stasis=0.35)
INSTANT = {'fulmina'}
INSTANT_COMBO = {'shatter', 'plasma'}
SPELL_SFX = dict(fulmina='zap', glacia='freeze', inferna='fire', dissolvo='slash', ascenda='rock')


def s(v):
    return v * G.U


class RemoteWiz:
    """Just enough of an Enemy for draw_archon to render a networked wizard."""

    def __init__(self):
        self.t = random.uniform(0, 6)
        self.phase = 1
        self.dead = False
        self.dead_t = 0.0
        self.blind = False
        self.staff = dict(x=0.36, y=0.0, a=0.0, away=False)
        self.cast = None
        self.frozen = 0.0
        self.stasis = 0.0
        self.flash = 0.0


class ArenaMixin:
    def init_arena(self):
        self.lobby = dict(name=self.settings.get('name') or 'Wizard', addr='localhost', focus='name', bots=2,
                          msg='', roster=[], host_id=None, brains=[], phase='lobby')
        self.net = None
        self.hosted = None
        self.net_me = None
        self.astate = None
        self.remote = {}
        self.arena_target = None
        self.bubbles = {}
        self.feed = []
        self.aprojs = []
        self.arena_over = None
        self.over_show_at = None
        self.cast_origin = None
        self.cast_meta = {}

    # ------------------------------------------------------------ lobby actions
    def open_lobby(self):
        self.ensure_inputs()  # warm up the camera while people gather
        self.mode = 'lobby'

    def host_game(self):
        if not self.hosted:
            try:
                from .server import ArenaServer  # needs websockets; imported only when hosting
                self.hosted = ArenaServer(port=DEFAULT_PORT, log=lambda m: None).start()
            except ImportError:
                self.lobby['msg'] = 'Hosting needs the websockets package:  pip install websockets'
                return
            except OSError as e:
                self.lobby['msg'] = f'Could not host on port {DEFAULT_PORT}: {e.strerror or e}'
                return
        self.join_game('127.0.0.1')

    def join_game(self, addr=None):
        self.leave_net(keep_server=True)
        name = self.lobby['name'].strip() or 'Wizard'
        self.settings['name'] = name
        self.save()
        self.net = NetClient(addr or self.lobby['addr'], name)
        self.lobby['msg'] = f'Connecting to {self.net.url} ...'

    def leave_net(self, keep_server=False):
        if self.net:
            self.net.close()
        self.net = None
        self.net_me = None
        self.astate = None
        self.lobby.update(roster=[], host_id=None, brains=[])
        if not keep_server and self.hosted:
            self.hosted.stop()
            self.hosted = None

    def leave_arena(self):
        self.leave_net()
        self.audio.stop_drone()
        self.mode = 'menu'

    def is_host(self):
        return bool(self.net_me) and self.lobby['host_id'] == self.net_me

    def start_match(self):
        if self.net and self.is_host():
            self.net.send(dict(t='start', bots=self.lobby['bots'], diff=self.settings['diff']))

    # ------------------------------------------------------------ network
    def pump_net(self):
        if not self.net:
            return
        for m in self.net.poll():
            kind = m.get('t')
            if kind == 'welcome':
                self.net_me = m['you']
                self.lobby['msg'] = 'Connected. Waiting for the host to start.' if not m.get('host') else 'You are hosting. Start when ready.'
            elif kind == 'lobby':
                self.lobby.update(roster=m['players'], host_id=m['host'], brains=m.get('brains', []), phase=m['phase'])
                if m['phase'] == 'lobby' and self.mode == 'arena':
                    self.mode = 'lobby'
                    self.audio.stop_drone()
            elif kind == 'state':
                self.astate = m
                if m.get('brains'):
                    self.lobby['brains'] = m['brains']
                if m['phase'] in ('countdown', 'play') and self.mode == 'lobby':
                    self.enter_arena()
            elif kind == 'event':
                if self.mode == 'arena':
                    self.on_arena_event(m)
            elif kind == 'error':
                self.lobby['msg'] = m.get('text', 'Server error')
            elif kind == 'closed':
                err = m.get('error')
                self.lobby['msg'] = 'Disconnected' + (f' - {err}' if err else '')
                self.net = None
                self.net_me = None
                if self.mode == 'arena':
                    self.mode = 'lobby'
                    self.audio.stop_drone()
                return

    def enter_arena(self):
        self.mode = 'arena'
        self.t = 0.0
        self.over = False
        self.intro_t = 0.0
        self.fx.clear()
        self.ghosts, self.timers, self.heard_queue, self.projs, self.eprojs, self.rocks = [], [], [], [], [], []
        self.stroke = self.echo = self.last_rec = self.last_cast = None
        self.aprojs, self.remote, self.bubbles, self.feed = [], {}, {}, []
        self.arena_over = self.over_show_at = self.arena_target = None
        self.stats = dict(casts=0, quality=[], empowered=0, combos=0, fizzles=0, interrupts=0, blocks=0, dmg=0.0,
                          taken=0.0, flings=0, by_spell={})
        self.player = dict(hp=100.0, max_hp=100, mana=100.0, max_mana=100, shield=0.0, shield_max=1.0, reflect=False, dark=0.0, cds={})
        self.audio.start_drone()

    # ------------------------------------------------------------ lookups
    def wizards(self):
        return self.astate['wizards'] if self.astate else []

    def wiz(self, wid):
        return next((w for w in self.wizards() if w['id'] == wid), None)

    def me(self):
        return self.wiz(self.net_me)

    def opponents(self):
        return [w for w in self.wizards() if w['id'] != self.net_me]

    def arena_layout(self):
        opps = self.opponents()
        n = max(1, len(opps))
        size = min(G.H * 0.25, G.W / (n + 1) * 0.62)
        return {w['id']: (G.W * (i + 1) / (n + 1), G.H * 0.38, size) for i, w in enumerate(opps)}

    def wiz_pos(self, wid):
        if wid == self.net_me:
            w = self.wand
            return (w['x'], w['y']) if w['visible'] else (G.W / 2, G.H * 0.84)
        x, y, _ = self.arena_layout().get(wid, (G.W / 2, G.H * 0.36, 1))
        return x, y

    def name_of(self, wid):
        return (self.wiz(wid) or {}).get('name', 'Someone')

    def sync_player(self):
        me = self.me()
        if not me:
            return
        P = self.player
        P.update(hp=me['hp'], mana=me['mana'], shield=me['shield'], shield_max=max(P['shield_max'], me['shield']), reflect=me['reflect'])
        P['cds'] = {k: self.t + v for k, v in me.get('cds', {}).items()}

    def pick_target(self):
        alive = [w for w in self.opponents() if w['alive']]
        if not any(w['id'] == self.arena_target for w in alive):
            self.arena_target = alive[0]['id'] if alive else None

    def cycle_target(self):
        alive = [w['id'] for w in self.opponents() if w['alive']]
        if alive:
            i = alive.index(self.arena_target) if self.arena_target in alive else -1
            self.arena_target = alive[(i + 1) % len(alive)]
            self.audio.play('tick')

    def arena_click(self, pos):
        for wid, (x, y, size) in self.arena_layout().items():
            w = self.wiz(wid)
            if w and w['alive'] and abs(pos[0] - x) < size * 0.5 and y - size * 0.7 < pos[1] < y + size * 0.75:
                self.arena_target = wid
                self.audio.play('tick')
                return True
        return False

    # ------------------------------------------------------------ casting
    def arena_cast(self, spec, quality=0.9, empowered=False, whisper=False, origin=None):
        me = self.me()
        if not spec or not me or not self.net or not self.astate:
            return False
        if not me['alive']:
            self.banner('You have fallen', 'Spectating until the match ends', '#8899aa')
            return False
        if self.astate['phase'] != 'play':
            self.banner('Not yet', 'Wait for the fight to begin', '#8899aa')
            return False
        q = max(0.0, min(1.0, (quality - 0.7) / 0.3))
        power = (0.6 if whisper else 1) * (1.5 if empowered else 1) * (0.85 + 0.3 * q)
        self.pick_target()
        self.net.send(dict(t='cast', spell=spec['base'], target=self.arena_target, power=round(power, 2)))
        self.book_lit[spec['id']] = 0.7
        self.stats['casts'] += 1
        self.stats['quality'].append(quality)
        self.stats['empowered'] += empowered
        self.cast_origin = origin
        self.cast_meta = dict(quality=quality, empowered=empowered, whisper=whisper)
        if empowered:
            self.audio.play('empowered')
        return True

    def add_feed(self, text, color='#e5d8ff'):
        self.feed.insert(0, (text, color))
        del self.feed[9:]

    def on_arena_event(self, e):
        k, fx, me = e['kind'], self.fx, self.net_me
        if k == 'cast':
            src, dst, spell, combo = e['src'], e['dst'], e['spell'], e.get('combo')
            color = COMBO_COLOR.get(combo) or SPELLS[spell]['color']
            a = self.cast_origin if src == me and self.cast_origin else self.wiz_pos(src)
            b = self.wiz_pos(dst)
            if src == me:
                self.audio.play('cast')
                self.cast_origin = None
                if combo:
                    self.stats['combos'] += 1
                    self.banner(FUSION_NAMES[combo], 'SPELL FUSION', color, True)
                    self.audio.play('combo')
                    fx.flash(color, 0.25, 0.35)
                    fx.add_shake(8)
                else:
                    m = self.cast_meta
                    tags = [f"{round(m.get('quality', 0.9) * 100)}% form"] + (['EMPOWERED'] if m.get('empowered') else []) \
                        + (['voice only'] if m.get('whisper') else [])
                    self.banner(SPELLS[spell]['name'], '  -  '.join(tags), color)
            elif combo:
                self.add_feed(f'{self.name_of(src)} fused {FUSION_NAMES[combo]}', color)
            if (spell in SELF_SPELLS and not combo) or combo == 'bastion':
                fx.ring(*a, color, s(10), s(150), 0.6, 4)
                fx.burst(*a, color, 30, 260)
                self.audio.play('shield' if spell == 'aegis' or combo == 'bastion' else 'heal')
                return
            self.audio.play(SPELL_SFX.get(spell, 'whoosh'))
            if (spell in INSTANT and not combo) or combo in INSTANT_COMBO:
                fx.lightning(*a, *b, color, 0.35, 2.6)
                fx.lightning(*a, *b, '#ffffff', 0.2, 1.1)
            else:
                self.aprojs.append(dict(frm=a, to=b, t=0.0, dur=COMBO_TRAVEL.get(combo) or TRAVEL.get(spell, 0.3), color=color,
                                        size=16 if combo else 12, incoming=dst == me, x=a[0], y=a[1]))
        elif k == 'hit':
            dst = e['dst']
            x, y = self.wiz_pos(dst)
            if e.get('blocked'):
                fx.text(x, y - s(50), 'REFLECTED!' if e.get('reflected') else 'BLOCKED', '#fff3b0' if e.get('reflected') else '#5ee7ff', 28)
                fx.burst(x, y, ['#5ee7ff', '#ffffff'], 35, 380)
                self.audio.play('block')
                if dst == me:
                    self.stats['blocks'] += 1
                return
            dmg = e.get('dmg', 0)
            fx.burst(x, y, COMBO_COLOR.get(e.get('combo')) or SPELLS.get(e.get('spell') or '', {}).get('color', '#ffffff'), 30, 380)
            if dst == me:
                self.stats['taken'] += dmg
                fx.text(x, y - s(40), f'-{dmg}', '#ff5577', 34)
                fx.flash('#ff0033', 0.3, 0.3)
                fx.add_shake(14)
                self.audio.play('hurt')
            else:
                if e.get('src') == me:
                    self.stats['dmg'] += dmg
                fx.text(x, y - s(60), str(dmg), '#ffffff', 32)
                if dst in self.remote:
                    self.remote[dst].flash = 1.0
                self.audio.play('hit')
        elif k == 'status':
            x, y = self.wiz_pos(e['dst'])
            fx.text(x, y - s(90), e['text'].upper(), '#ffd98a', 24)
        elif k == 'heal':
            x, y = self.wiz_pos(e['dst'])
            fx.text(x, y - s(70), f"+{e['amount']}", '#7dffb0', 28)
        elif k == 'say':
            w = self.wiz(e['src'])
            self.bubbles[e['src']] = dict(text=e['text'], t=self.t)
            tag = f" [{w['brain']}]" if w and w.get('brain') and w['brain'] != 'tactics' else ''
            self.add_feed(f"{self.name_of(e['src'])}{tag}: \"{e['text']}\"", w['color'] if w else '#e5d8ff')
        elif k == 'ko':
            self.add_feed(e['text'], '#ff8a9a')
            if e['dst'] == me:
                self.banner('You have fallen', 'Spectating until the match ends', '#ff5577', True)
                self.audio.play('lose')
            else:
                x, y = self.wiz_pos(e['dst'])
                fx.burst(x, y, ['#ffffff', '#c070ff'], 90, 600)
                fx.text(x, y - s(110), 'DEFEATED', '#ff8a9a', 36)
        elif k == 'reject':
            self.banner(e['text'], '', '#8899aa')
            self.audio.play('fizzle')
        elif k == 'miss':
            self.add_feed(e['text'], '#d8e3e8')
        elif k == 'start':
            self.banner('The arena seals', 'Last wizard standing wins', G.GOLD, True)
        elif k == 'fight':
            self.banner('Fight!', 'Click a wizard or press Tab to choose your target', G.GOLD, True)
            self.audio.play('combo')
        elif k == 'over':
            self.arena_over = e
            won = e.get('winner') == me
            self.banner('Victory' if won else 'Match over', e['text'], G.GOLD if won else '#ff8a9a', True)
            self.audio.play('win' if won else 'lose')
            self.audio.stop_drone()
            self.over_show_at = self.t + 2.5

    # ------------------------------------------------------------ update
    def update_arena(self, dt):
        self.sync_player()
        self.pick_target()
        layout = self.arena_layout()
        for w in self.opponents():
            r = self.remote.setdefault(w['id'], RemoteWiz())
            r.t += dt
            r.frozen, r.blind = w['frozen'], w['blind']
            r.flash = max(0.0, r.flash - dt * 4)
            if not w['alive']:
                r.dead = True
                r.dead_t += dt
            away = w['disarmed'] > 0
            r.staff.update(away=away, x=0.62 if away else 0.36, y=0.5 if away else 0.0, a=1.3 if away else 0.0)
            if w['burn'] > 0 and w['alive'] and w['id'] in layout:
                x, y, size = layout[w['id']]
                self.fx.spawn(x + random.uniform(-0.3, 0.3) * size, y + random.uniform(-0.3, 0.5) * size, random.uniform(-20, 20),
                              random.uniform(-160, -60), 0.6, random.uniform(8, 14), random.choice(['#ff9a3c', '#ff5a1f']), 1)
        for h in list(self.heard_queue):
            if self.t - h['t'] > 2.5:
                self.heard_queue.remove(h)
                self.arena_cast(self.spec_for(h['id']), quality=0.85, whisper=True)
        for p in self.aprojs:
            p['t'] += dt
            k = min(1.0, p['t'] / p['dur'])
            (x0, y0), (x1, y1) = p['frm'], p['to']
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2 - math.hypot(x1 - x0, y1 - y0) * 0.15
            p['x'] = (1 - k) ** 2 * x0 + 2 * (1 - k) * k * cx + k * k * x1
            p['y'] = (1 - k) ** 2 * y0 + 2 * (1 - k) * k * cy + k * k * y1
            p['k'] = k
            self.fx.spawn(p['x'], p['y'], random.uniform(-25, 25), random.uniform(-25, 25), 0.35,
                          p['size'] * (0.8 + (1.6 * k if p['incoming'] else 0)), p['color'], 2)
        self.aprojs = [p for p in self.aprojs if p['t'] < p['dur']]
        for wid in [w for w, b in self.bubbles.items() if self.t - b['t'] > 5]:
            del self.bubbles[wid]

    # ------------------------------------------------------------ render: world
    def render_arena_world(self, sc, layer):
        layout = self.arena_layout()
        for w in self.opponents():
            if w['id'] not in layout:
                continue
            x, y, size = layout[w['id']]
            self.draw_floor_glyph(layer, x, y, size, w['color'] if w['alive'] else '#444455')
            r = self.remote.get(w['id'])
            if r:
                draw_archon(sc, layer, r, x, y, size, G.U, eye=w['color'])
            if w['shield'] > 0 and w['alive']:
                rect = pygame.Rect(0, 0, size * 1.2, size * 1.55)
                rect.center = (x, y + size * 0.05)
                pygame.draw.ellipse(layer, dim('#fff3b0' if w['reflect'] else '#5ee7ff', 0.7), rect, max(2, round(3 * G.U)))
            if w['id'] == self.arena_target and w['alive']:
                pts = [(x + math.cos(a / 40 * math.tau + self.t * 2) * size * 0.65,
                        y + size * 0.72 + math.sin(a / 40 * math.tau + self.t * 2) * size * 0.14) for a in range(41)]
                pygame.draw.lines(layer, rgb(G.GOLD), False, pts, max(2, round(3 * G.U)))
        for p in self.aprojs:
            size = s(p['size'] * 2.4) * (1 + (2.2 * p['k'] if p['incoming'] else 0))
            blit_glow(sc, p['color'], p['x'], p['y'], size)

    def draw_nameplates(self):
        sc = self.screen
        for w in self.opponents():
            layout = self.arena_layout()
            if w['id'] not in layout:
                continue
            x, y, size = layout[w['id']]
            top = y - size * 0.82
            bw = min(size * 1.1, s(240))
            col = w['color'] if w['alive'] else '#777788'
            label = w['name'] + (f"  ({w.get('brain') or 'bot'})" if w['bot'] else '')
            r = self.text(sc, label, (x, top - s(36)), 15, col, True, True, 'midbottom', outline=True)
            if w['id'] == self.arena_target and w['alive']:
                self.text(sc, 'TARGET', (r.centerx, r.top - s(2)), 11, G.GOLD, bold=True, anchor='midbottom', outline=True)
            self.bar((x - bw / 2, top - s(32), bw, s(10)), w['hp'] / 100, ('#a050f0', '#e090ff') if w['alive'] else ('#444', '#555'))
            chips = []
            if w['shield'] > 0:
                chips.append(('Ward' + (' (mirror)' if w['reflect'] else ''), '#5ee7ff'))
            if w['frozen'] > 0:
                chips.append(('Frozen', '#9fd8ff'))
            if w['burn'] > 0:
                chips.append(('Burning', '#ff9a3c'))
            if w['disarmed'] > 0:
                chips.append(('Disarmed', '#ff7a59'))
            if w['blind']:
                chips.append(('Blinded', '#d8e3e8'))
            if not w['alive']:
                chips = [('Defeated', '#ff8a9a')]
            self.chips(chips, x, top - s(18))
            b = self.bubbles.get(w['id'])
            if b and w['alive']:
                lines = self.wrap(f'"{b["text"]}"', 14, s(280), serif=True)
                lh = s(19)
                box = pygame.Rect(0, 0, s(300), s(14) + lh * len(lines))
                box.midbottom = (x, r.top - s(18))
                box.clamp_ip(sc.get_rect())
                alpha = 230 if self.t - b['t'] < 4.3 else int(230 * (5 - (self.t - b['t'])) / 0.7)
                self.panel(box, max(0, alpha), 12, (250, 244, 255), (*rgb(w['color']), 255))
                for i, ln in enumerate(lines):
                    self.text(sc, ln, (box.centerx, box.y + s(7) + i * lh), 14, (30, 20, 50), True, anchor='midtop')

    # ------------------------------------------------------------ render: hud
    def draw_feed(self, rect):
        sc = self.screen
        self.panel(rect)
        x, y = rect.x + s(14), rect.y + s(12)
        self.text(sc, 'Arena Feed', (x, y), 15, '#ff8ad0', True, True)
        y += s(24)
        for line in self.lobby.get('brains') or ['AI brains: unknown']:
            self.text(sc, line, (x, y), 11, '#7dffb0' if ': on' in line else G.MUTED)
            y += s(15)
        y += s(7)
        for text, color in self.feed:
            for ln in self.wrap(text, 13, rect.w - s(28)):
                if y > rect.bottom - s(18):
                    return
                self.text(sc, ln, (x, y), 13, color)
                y += s(17)
            y += s(4)

    def draw_arena_hud(self):
        sc = self.screen
        self.draw_nameplates()
        self.draw_player_panel()
        book_h = s(40 + (len(self.all_specs()) * 46 if self.book_open else 0))
        book = pygame.Rect(s(14), s(60), s(236), book_h)
        self.cached('book', book, self.draw_book)
        self.hotspot((book.x + s(6), book.y + s(6), book.w - s(12), s(28)), lambda: setattr(self, 'book_open', not self.book_open), 'book')
        # Below the opponents' floor glyphs, above the webcam preview.
        top = G.H * 0.6
        bottom = G.H - s(14 + 150 + 12) if self.settings['cam'] and self.tracker else G.H - s(14)
        self.cached('feed', pygame.Rect(G.W - s(14 + 330), top, s(330), max(s(120), bottom - top)), self.draw_feed, every=0.25)
        self.draw_voice_status()
        st = self.astate
        if st and st['phase'] == 'countdown' and st['countdown'] > 0:
            n = str(math.ceil(st['countdown']))
            blit_glow(sc, G.GOLD, G.W / 2, G.H * 0.6, s(120), 3)
            self.text(sc, n, (G.W / 2, G.H * 0.6), 130, G.GOLD, True, True, 'center', outline=True)
        tgt = self.wiz(self.arena_target)
        if tgt and st and st['phase'] == 'play':
            self.text(sc, f"Target: {tgt['name']}   (click a wizard or press Tab)", (G.W / 2, G.H - s(60)), 14, G.GOLD,
                      anchor='midbottom', outline=True)
        self.button((s(14), s(14), s(90), s(30)), 'x  Leave', self.leave_arena, size=13, serif=False)
        if self.arena_over and self.over_show_at is not None and self.t >= self.over_show_at:
            self.draw_arena_over()

    def draw_arena_over(self):
        sc, e, R, D = self.screen, self.arena_over, self.design_rect, self.design_pt
        sc.blit(self.shade[205], (0, 0))
        won = e.get('winner') == self.net_me
        color = G.GOLD if won else '#ff8a9a'
        self.text(sc, 'Victory' if won else 'Match Over', D(640, 120), 76, color, True, True, 'center', outline=True)
        self.text(sc, e['text'], D(640, 176), 18, (217, 205, 245), True, anchor='center')
        heads = ['Wizard', 'Damage', 'KOs', 'Casts', 'Fusions', 'Blocks']
        xs = [330, 640, 730, 810, 890, 970]
        for h, x in zip(heads, xs):
            self.text(sc, h, D(x, 214), 13, G.MUTED, anchor='midtop' if x != 330 else 'topleft')
        for i, st in enumerate(e.get('standings', [])):
            y = 240 + i * 42
            r = R(316, y, 680, 36)
            mine = st['id'] == self.net_me
            self.panel(r, 90 if mine else 55, 8, (60, 40, 110) if mine else (40, 25, 80))
            name = ('1. ' if i == 0 else f'{i + 1}. ') + st['name'] + (' (bot)' if st['bot'] else '') + ('  - you' if mine else '')
            self.text(sc, name, (r.x + s(14), r.centery), 15, G.GOLD if i == 0 else G.INK, True, True, 'midleft')
            for x, key in zip(xs[1:], ['damage', 'kos', 'casts', 'combos', 'blocks']):
                self.text(sc, str(st[key]), (D(x, 0)[0], r.centery), 15, G.INK, anchor='center')
        y = 240 + max(1, len(e.get('standings', []))) * 42 + 30
        if self.is_host():
            self.button(R(440, y, 220, 46), 'Back to lobby', lambda: self.net and self.net.send(dict(t='lobby')), primary=True, size=18)
        else:
            self.text(sc, 'Waiting for the host to return to the lobby...', D(550, y + 23), 14, G.MUTED, anchor='center')
        self.button(R(680, y, 160, 46), 'Leave', self.leave_arena, size=18)

    # ------------------------------------------------------------ lobby screen
    def draw_lobby(self):
        sc, R, D, L = self.screen, self.design_rect, self.design_pt, self.lobby
        sc.blit(self.shade[150], (0, 0))
        self.text(sc, 'ARENA', D(640, 70), 64, G.GOLD, True, True, 'center', outline=True)
        self.text(sc, 'Free-for-all for up to 4 wizards. Friends join over your network; bots fill the empty seats.',
                  D(640, 118), 15, (217, 205, 245), anchor='center')

        left = R(150, 160, 460, 430)
        self.panel(left)
        x, y = left.x + s(24), left.y + s(20)
        for key, label in (('name', 'Your wizard name'), ('addr', "Server address (to join a friend's game)")):
            self.text(sc, label, (x, y), 13, G.MUTED)
            box = pygame.Rect(x, y + s(20), left.w - s(48), s(36))
            focused = L['focus'] == key
            self.panel(box, 160, 6, (0, 0, 0), (*rgb(G.GOLD), 200) if focused else G.EDGE)
            caret = '|' if focused and int(self.t * 2) % 2 == 0 else ''
            self.text(sc, L[key] + caret, (box.x + s(10), box.centery), 16, G.INK, anchor='midleft')
            self.hotspot(box, lambda k=key: L.update(focus=k), f'lobby:{key}')
            y += s(78)
        connected = self.net is not None
        bw = (left.w - s(48) - s(12)) / 2
        if not connected:
            self.button((x, y, bw, s(46)), 'Host game', self.host_game, primary=True, size=17)
            self.button((x + bw + s(12), y, bw, s(46)), 'Join', self.join_game, size=17)
        else:
            self.button((x, y, left.w - s(48), s(46)), 'Disconnect', lambda: self.leave_net(), size=16)
        y += s(62)
        for ln in self.wrap(L['msg'] or 'Host a game, or type a friend\'s address and join.', 13, left.w - s(48)):
            self.text(sc, ln, (x, y), 13, G.GOLD if L['msg'] else G.MUTED)
            y += s(18)
        if self.hosted:
            y += s(8)
            self.text(sc, 'Friends join at:', (x, y), 13, G.MUTED)
            self.text(sc, f'{lan_ip()}:{DEFAULT_PORT}', (x, y + s(18)), 22, '#7dffb0', True, True)
            self.text(sc, '(same Wi-Fi; allow Python through the firewall if asked)', (x, y + s(48)), 11, G.MUTED)

        right = R(670, 160, 460, 430)
        self.panel(right)
        x, y = right.x + s(24), right.y + s(20)
        self.text(sc, 'Wizards in the arena', (x, y), 16, G.GOLD, True, True)
        y += s(32)
        roster = L['roster']
        if not roster:
            self.text(sc, 'Not connected yet.', (x, y), 14, G.MUTED)
        for p in roster:
            pygame.draw.circle(sc, rgb(p['color']), (x + s(8), y + s(10)), s(7))
            tag = '  (host)' if p['id'] == L['host_id'] else ''
            tag += '  - you' if p['id'] == self.net_me else ''
            self.text(sc, p['name'] + tag, (x + s(24), y), 16, G.INK)
            y += s(30)
        if connected and self.is_host():
            free = max(0, 4 - len([p for p in roster if not p['bot']]))
            L['bots'] = min(L['bots'], free)
            y = right.y + s(250)
            self.text(sc, 'Bots to add', (x, y), 13, G.MUTED)
            self.button((x + s(120), y - s(6), s(32), s(30)), '-', lambda: L.update(bots=max(0, L['bots'] - 1)), size=16)
            self.text(sc, str(L['bots']), (x + s(176), y + s(9)), 18, G.INK, True, True, 'center')
            self.button((x + s(200), y - s(6), s(32), s(30)), '+', lambda: L.update(bots=min(free, L['bots'] + 1)), size=16)

            def cycle():
                self.settings['diff'] = (self.settings['diff'] + 1) % 3
                self.save()
            self.button((x + s(250), y - s(6), s(160), s(30)), f"Bots: {G.DIFF[self.settings['diff']]['name']}", cycle, size=13, serif=False)
            self.button((x, right.bottom - s(70), right.w - s(48), s(48)), 'Start match', self.start_match, primary=True, size=18)
        elif connected:
            self.text(sc, 'Waiting for the host to start the match...', (x, right.bottom - s(56)), 14, G.MUTED)
        lines = L['brains'] or (['(connect to see which AI brains are online)'] if not connected else [])
        by = right.y + s(178)
        if lines:
            self.text(sc, 'Bot brains', (x, by), 13, G.MUTED)
        for i, line in enumerate(lines[:5]):
            self.text(sc, line, (x, by + s(20 + i * 15)), 11, '#7dffb0' if ': on' in line else G.MUTED)

        self.button(R(560, 620, 160, 44), '< Menu', self.leave_arena, size=16)
