"""Server-authoritative rules for the multiplayer Arena (free-for-all, up to 4
wizards, humans and bots). Pure Python; no pygame, no networking.

Every outcome (damage, shields, statuses, fusions) is decided here, and the
server broadcasts the resulting events and state to clients."""

import itertools
import random
from collections import Counter

from .spells import COMBO_WINDOW, SPELLS

MAX_SEATS = 4
MAX_POWER = 1.6  # clients report gesture/voice power; clamp so nobody can cheat past it

BOT_PERSONAS = [
    dict(name='Vexa the Cinder Witch', color='#ff7a3c', style='aggressive',
         bio='Hot-headed pyromancer. Loves Ember Nova and Meteor, mocks anyone who hides behind wards.'),
    dict(name='Old Thorne', color='#7dffb0', style='defensive',
         bio='Ancient, patient druid. Wards early, punishes over-extension, speaks in slow proverbs.'),
    dict(name='Pip Quickwand', color='#5ee7ff', style='control',
         bio='Gleeful trickster. Freezes, disarms and blinds; giggles at stunned foes.'),
    dict(name='Sable Mourne', color='#c9a0ff', style='aggressive',
         bio='Cold storm-caller. Chains Rime Spire into Storm Lance for Shatterstorm. Few words, all cutting.'),
]

DIFF = [
    dict(name='Apprentice', react=(1.6, 2.6), skill=0.4),
    dict(name='Adept', react=(1.0, 1.8), skill=0.7),
    dict(name='Archmage', react=(0.6, 1.2), skill=0.9),
]

OFFENSIVE = ('fulmina', 'dissolvo', 'ascenda', 'glacia', 'inferna')
FUSIONS = {('glacia', 'fulmina'): 'shatter', ('aegis', 'solaris'): 'bastion', ('inferna', 'fulmina'): 'plasma',
           ('glacia', 'inferna'): 'steam', ('ascenda', 'inferna'): 'meteor', ('dissolvo', 'glacia'): 'stasis'}
FUSION_NAMES = {'shatter': 'Shatterstorm', 'bastion': 'Radiant Bastion', 'plasma': 'Plasma Arc',
                'steam': 'Steam Veil', 'meteor': 'Meteor', 'stasis': 'Stasis Lock'}


class Wizard:
    _ids = itertools.count(1)

    def __init__(self, name, bot=False, color='#ffd98a', persona=None):
        self.id = f'w{next(self._ids)}'
        self.name = name[:24] or 'Wizard'
        self.bot = bot
        self.color = color
        self.persona = persona
        self.brain = None       # label of the AI provider planning for this bot
        self.brain_idx = None
        self.reset()

    def reset(self):
        self.hp, self.mana = 100.0, 100.0
        self.shield, self.reflect = 0.0, False
        self.frozen = self.burn = self.disarmed = 0.0
        self.blind = False
        self.cds = {}
        self.last_cast = None
        self.alive = True
        self.damaged_by = Counter()
        self.stats = Counter()

    def view(self, me=False):
        d = dict(id=self.id, name=self.name, bot=self.bot, brain=self.brain, color=self.color, hp=round(self.hp, 1), mana=round(self.mana, 1),
                 shield=round(self.shield, 2), reflect=self.reflect, frozen=round(self.frozen, 2), burn=round(self.burn, 2),
                 disarmed=round(self.disarmed, 2), blind=self.blind, alive=self.alive)
        if me:
            d['cds'] = {k: round(v, 2) for k, v in self.cds.items() if v > 0}
        return d


class Arena:
    def __init__(self):
        self.wizards = {}
        self.order = []
        self.phase = 'lobby'          # lobby | countdown | play | over
        self.t = 0.0
        self.countdown = 0.0
        self.hits = []                # scheduled projectile arrivals
        self.events = []              # drained by the server each tick
        self.brains = {}
        self.diff = 1
        self.winner = None
        self.plan_log = []

    # ------------------------------------------------------------ roster
    def add(self, name, bot=False, persona=None):
        if len(self.order) >= MAX_SEATS:
            return None
        color = persona['color'] if persona else ['#ffd98a', '#ff6bd6', '#9fd8ff', '#ff9a3c'][len(self.order) % 4]
        w = Wizard(name, bot, color, persona)
        self.wizards[w.id] = w
        self.order.append(w.id)
        if bot:
            self.brains[w.id] = BotBrain(w, persona)
        return w

    def remove(self, wid):
        w = self.wizards.pop(wid, None)
        if wid in self.order:
            self.order.remove(wid)
        self.brains.pop(wid, None)
        if w and self.phase in ('play', 'countdown'):
            self.emit('ko', dst=wid, text=f'{w.name} fled the arena')
            self.check_winner()

    def humans(self):
        return [self.wizards[i] for i in self.order if not self.wizards[i].bot]

    def start(self, bots=2, diff=1):
        for wid in [i for i in self.order if self.wizards[i].bot]:
            self.remove(wid)
        self.diff = max(0, min(2, diff))
        free = [p for p in BOT_PERSONAS]
        random.shuffle(free)
        for p in free[:max(0, min(bots, MAX_SEATS - len(self.order)))]:
            self.add(p['name'], bot=True, persona=p)
        labels = getattr(self, 'brain_labels', [])
        for i, wid in enumerate(i for i in self.order if self.wizards[i].bot):
            w = self.wizards[wid]  # spread bots round-robin across the AI brains
            w.brain_idx = i % len(labels) if labels else None
            w.brain = labels[w.brain_idx] if labels else 'tactics'
        for w in self.wizards.values():
            w.reset()
        for b in self.brains.values():
            b.reset(self.diff)
        self.hits.clear()
        self.winner = None
        self.phase = 'countdown'
        self.countdown = 3.0
        self.emit('start', text='The arena seals. Last wizard standing wins.')

    def to_lobby(self):
        for wid in [i for i in self.order if self.wizards[i].bot]:
            self.remove(wid)
        self.phase = 'lobby'
        self.hits.clear()

    # ------------------------------------------------------------ helpers
    def emit(self, kind, **kw):
        self.events.append(dict(t='event', kind=kind, **kw))
        if kind in ('ko', 'status', 'miss') or (kind == 'cast' and kw.get('combo')):
            text = kw.get('text') or f"{self.wizards[kw['src']].name} fused {kw.get('combo_name')}"
            self.plan_log = (self.plan_log + [dict(text=text)])[-20:]

    def opponents(self, w):
        return [self.wizards[i] for i in self.order if i != w.id and self.wizards[i].alive]

    def state(self, for_id=None):
        return dict(t='state', phase=self.phase, countdown=round(self.countdown, 2), winner=self.winner,
                    wizards=[self.wizards[i].view(i == for_id) for i in self.order])

    # ------------------------------------------------------------ casting
    def cast(self, wid, base, target=None, power=1.0):
        w = self.wizards.get(wid)
        if not w or self.phase != 'play' or not w.alive or base not in SPELLS:
            return False
        S = SPELLS[base]
        if w.disarmed > 0:
            self.emit('reject', dst=wid, text='Disarmed! Your wand is out of reach')
            return False
        if w.cds.get(base, 0) > 0:
            self.emit('reject', dst=wid, text=f"{S['name']} recharging")
            return False
        if w.mana < S['cost']:
            self.emit('reject', dst=wid, text='Your mana runs dry')
            return False
        power = max(0.5, min(MAX_POWER, float(power)))
        w.mana -= S['cost']
        w.cds[base] = S['cd']
        w.stats['casts'] += 1
        tgt = self.wizards.get(target)
        if base in OFFENSIVE:
            if not tgt or not tgt.alive or tgt.id == wid:
                opp = self.opponents(w)
                tgt = min(opp, key=lambda o: o.hp) if opp else None
            if not tgt:
                return False
            if w.blind:
                w.blind = False
                self.emit('cast', src=wid, dst=tgt.id, spell=base, missed=True)
                self.emit('miss', src=wid, dst=tgt.id, text=f'{w.name} is blinded - the spell flies wide')
                return True

        combo = None
        if w.last_cast and self.t - w.last_cast[1] < COMBO_WINDOW:
            combo = FUSIONS.get((w.last_cast[0], base))
        w.last_cast = None if combo else (base, self.t)
        if combo:
            w.stats['combos'] += 1
        self.emit('cast', src=wid, dst=tgt.id if tgt else wid, spell=base, combo=combo,
                  combo_name=FUSION_NAMES.get(combo), power=round(power, 2))
        self._apply(w, tgt, base, combo, power)
        return True

    def _hit(self, src, dst, dmg, travel, spell, combo=None, pierce=False, effect=None):
        self.hits.append(dict(src=src.id, dst=dst.id, dmg=dmg, at=self.t + travel, spell=spell, combo=combo,
                              pierce=pierce, effect=effect))

    def _apply(self, w, tgt, base, combo, power):
        if combo == 'shatter':
            self._hit(w, tgt, 0, 0.25, base, combo, effect='shatter')
        elif combo == 'bastion':
            w.shield, w.reflect = 4.5, True
            self._heal(w, 10 * power)
        elif combo == 'plasma':
            self._hit(w, tgt, 20 * power, 0.25, base, combo, pierce=True)
        elif combo == 'steam':
            self._hit(w, tgt, 4 * power, 0.4, base, combo, effect='blind')
        elif combo == 'meteor':
            self._hit(w, tgt, 34 * power, 1.0, base, combo, effect='burn')
        elif combo == 'stasis':
            self._hit(w, tgt, 4 * power, 0.35, base, combo, pierce=True, effect='stasis')
        elif base == 'aegis':
            w.shield = 3.0 * (1.3 if power >= 1.4 else 1.0)
            w.reflect = w.reflect or power >= 1.4
        elif base == 'fulmina':
            self._hit(w, tgt, 12 * power, 0.25, base)
        elif base == 'dissolvo':
            self._hit(w, tgt, 4 * power, 0.3, base, pierce=True, effect='disarm')
        elif base == 'ascenda':
            self._hit(w, tgt, 16 * power, 1.0, base)
        elif base == 'solaris':
            w.burn = w.frozen = 0
            w.blind = False
            self._heal(w, 8 * power)
        elif base == 'glacia':
            self._hit(w, tgt, 6 * power, 0.35, base, effect='freeze')
        elif base == 'inferna':
            self._hit(w, tgt, 6 * power, 0.4, base, effect='burn')

    def _heal(self, w, n):
        h = min(100 - w.hp, n)
        w.hp += h
        if h > 0.5:
            self.emit('heal', dst=w.id, amount=round(h))

    def _resolve(self, h):
        src, dst = self.wizards.get(h['src']), self.wizards.get(h['dst'])
        if not dst or not dst.alive:
            return
        dmg, eff = h['dmg'], h['effect']
        if dst.shield > 0 and not h['pierce']:
            if dst.reflect and src and src.alive:
                dst.reflect = False
                self.emit('hit', src=dst.id, dst=dst.id, dmg=0, blocked=True, reflected=True, spell=h['spell'])
                self._hit(dst, src, max(dmg, 8) * 1.2, 0.3, h['spell'])
            else:
                self.emit('hit', src=h['src'], dst=dst.id, dmg=0, blocked=True, spell=h['spell'])
            dst.stats['blocks'] += 1
            return
        if eff == 'shatter':
            dmg = 30 if dst.frozen > 0 else 18
            dst.frozen = 0
        elif eff == 'disarm':
            if dst.shield > 0:
                dst.shield, dst.reflect = 0, False
                self.emit('status', dst=dst.id, text='Ward shattered')
            else:
                dst.disarmed = max(dst.disarmed, 1.5)
                self.emit('status', dst=dst.id, text='Disarmed')
        elif eff == 'freeze':
            dst.burn = 0
            dst.frozen = max(dst.frozen, 3.0)
        elif eff == 'burn':
            dst.frozen = 0
            dst.burn = max(dst.burn, 4.0)
        elif eff == 'blind':
            dst.blind = True
            self.emit('status', dst=dst.id, text='Blinded')
        elif eff == 'stasis':
            dst.frozen = max(dst.frozen, 2.5)
            dst.disarmed = max(dst.disarmed, 2.5)
            self.emit('status', dst=dst.id, text='Stasis')
        self._damage(dst, dmg, src, h['spell'], h.get('combo'))

    def _damage(self, dst, dmg, src, spell=None, combo=None, dot=False):
        if dmg <= 0:
            return
        dst.hp -= dmg
        if src:
            dst.damaged_by[src.id] += dmg
            src.stats['damage'] += dmg
        if not dot:
            self.emit('hit', src=src.id if src else None, dst=dst.id, dmg=round(dmg), spell=spell, combo=combo)
        if dst.hp <= 0 and dst.alive:
            dst.hp, dst.alive = 0, False
            killer = src.name if src else 'the flames'
            if src:
                src.stats['kos'] += 1
            self.emit('ko', dst=dst.id, src=src.id if src else None, text=f'{dst.name} falls to {killer}')
            self.check_winner()

    def check_winner(self):
        if self.phase not in ('play', 'countdown'):
            return
        alive = [self.wizards[i] for i in self.order if self.wizards[i].alive]
        if len(alive) <= 1 and len(self.order) >= 1:
            self.phase = 'over'
            self.winner = alive[0].id if alive else None
            standings = sorted(self.wizards.values(), key=lambda w: (-w.alive, -w.stats['damage']))
            self.emit('over', winner=self.winner, text=f'{alive[0].name} is the last wizard standing!' if alive else 'No one survives.',
                      standings=[dict(id=w.id, name=w.name, bot=w.bot, damage=round(w.stats['damage']), kos=w.stats['kos'],
                                      casts=w.stats['casts'], combos=w.stats['combos'], blocks=w.stats['blocks']) for w in standings])

    # ------------------------------------------------------------ tick
    def tick(self, dt):
        self.t += dt
        if self.phase == 'countdown':
            self.countdown -= dt
            if self.countdown <= 0:
                self.phase = 'play'
                self.emit('fight', text='Fight!')
            return
        if self.phase != 'play':
            return
        for w in self.wizards.values():
            if not w.alive:
                continue
            slow = 0.5 if w.frozen > 0 else 1.0
            w.mana = min(100.0, w.mana + 11 * dt * slow)
            for k in list(w.cds):
                w.cds[k] = max(0.0, w.cds[k] - dt * slow)
            w.shield = max(0.0, w.shield - dt)
            if w.shield <= 0:
                w.reflect = False
            w.frozen = max(0.0, w.frozen - dt)
            w.disarmed = max(0.0, w.disarmed - dt)
            if w.burn > 0:
                w.burn = max(0.0, w.burn - dt)
                attacker = max(w.damaged_by, key=w.damaged_by.get) if w.damaged_by else None
                self._damage(w, 4 * dt, self.wizards.get(attacker), dot=True)
        due = [h for h in self.hits if h['at'] <= self.t]
        self.hits = [h for h in self.hits if h['at'] > self.t]
        for h in due:
            self._resolve(h)
        for b in list(self.brains.values()):
            if self.phase == 'play':
                b.update(self)


class BotBrain:
    """Fast tactical reflexes. A Gemini plan (if any) sets focus, style and combo goals."""

    def __init__(self, wizard, persona):
        self.w = wizard
        self.persona = persona or {}
        self.plan = dict(focus=None, style=self.persona.get('style', 'aggressive'), combo='none', taunt='')
        self.reset(1)

    def reset(self, diff):
        self.d = DIFF[diff]
        self.next_t = random.uniform(1.0, 2.0)

    def ready(self, base):
        w = self.w
        return w.cds.get(base, 0) <= 0 and w.mana >= SPELLS[base]['cost']

    def update(self, arena):
        w = self.w
        if not w.alive or arena.t < self.next_t or w.disarmed > 0:
            return
        self.next_t = arena.t + random.uniform(*self.d['react'])
        skill = self.d['skill']
        opps = arena.opponents(w)
        if not opps:
            return

        incoming = [h for h in arena.hits if h['dst'] == w.id and not h['pierce'] and h['at'] - arena.t < 0.9]
        if incoming and w.shield <= 0 and random.random() < skill and self.ready('aegis'):
            arena.cast(w.id, 'aegis', power=1.0 + 0.4 * skill)
            return
        if (w.burn > 0 or w.frozen > 0 or w.blind or w.hp < 35) and random.random() < 0.5 + 0.4 * skill and self.ready('solaris'):
            arena.cast(w.id, 'solaris')
            return

        focus = arena.wizards.get(self.plan.get('focus') or '')
        if not focus or not focus.alive or focus.id == w.id:
            style = self.plan.get('style')
            if style == 'defensive' and w.damaged_by:
                focus = arena.wizards.get(max(w.damaged_by, key=w.damaged_by.get))
            if not focus or not focus.alive:
                focus = min(opps, key=lambda o: o.hp) if style == 'aggressive' else random.choice(opps)

        last = w.last_cast[0] if w.last_cast and arena.t - w.last_cast[1] < 3.5 else None
        follow = {'glacia': 'fulmina', 'inferna': 'fulmina', 'dissolvo': 'glacia', 'ascenda': 'inferna'}.get(last)
        if follow and self.ready(follow) and random.random() < 0.4 + 0.5 * skill:
            arena.cast(w.id, follow, focus.id, 1.0 + 0.3 * skill)
            return
        opener = {'shatter': 'glacia', 'plasma': 'inferna', 'stasis': 'dissolvo', 'meteor': 'ascenda', 'steam': 'glacia'}.get(self.plan.get('combo'))
        if focus.shield > 0 and self.ready('dissolvo'):
            choice = 'dissolvo'
        elif focus.frozen > 0 and self.ready('fulmina'):
            choice = 'fulmina'
        elif opener and self.ready(opener) and random.random() < 0.5:
            choice = opener
        else:
            weights = {'aggressive': dict(fulmina=4, inferna=3, ascenda=2, glacia=1, dissolvo=1),
                       'defensive': dict(fulmina=2, glacia=2, inferna=1, dissolvo=1, aegis=2),
                       'control': dict(glacia=3, dissolvo=3, fulmina=2, inferna=1)}[self.plan.get('style', 'aggressive') if self.plan.get('style') in ('aggressive', 'defensive', 'control') else 'aggressive']
            options = [(k, v) for k, v in weights.items() if self.ready(k)]
            if not options:
                return
            choice = random.choices([k for k, _ in options], [v for _, v in options])[0]
        arena.cast(w.id, choice, focus.id, 0.9 + 0.3 * skill)
