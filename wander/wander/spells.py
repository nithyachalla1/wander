"""The spell canon of Wander. Each spell pairs a wand gesture with a spoken
incantation; aliases catch common speech-to-text mishearings."""

from functools import lru_cache


@lru_cache(maxsize=None)
def rgb(hex_color):
    h = hex_color.lstrip('#')
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


SPELLS = {
    'aegis': dict(
        name='Aegis Ward', incant='Aegis', gesture='circle', color='#5ee7ff', cost=18, cd=1.2,
        aliases=['aegis', 'ages', 'egis', 'a jiss', 'ajis', 'egypt'],
        desc='Draw a circle. Blocks incoming hexes for 3s. Empowered: reflects one.'),
    'fulmina': dict(
        name='Storm Lance', incant='Fulmina', gesture='bolt', color='#c9a0ff', cost=14, cd=0.6,
        aliases=['fulmina', 'full mina', 'fulmine', 'fulminate', 'full meena', 'for mina', 'full nina'],
        desc='Draw a lightning zig-zag (or a Z). A bolt of storm, 12 damage.'),
    'dissolvo': dict(
        name='Severance', incant='Dissolvo', gesture='flick', color='#ff7a59', cost=16, cd=1.5,
        aliases=['dissolvo', 'dissolve', 'dissolve oh', 'dissolve o', 'the solvo'],
        desc='Flick diagonally. Interrupts casts, shatters wards, disarms.'),
    'ascenda': dict(
        name='Skyward Bind', incant='Ascenda', gesture='spiral', color='#7dffb0', cost=20, cd=2.0,
        aliases=['ascenda', 'ascend a', 'ascender', 'a sender', 'assender'],
        desc='Draw a spiral (2+ loops). Lifts a boulder; swipe up to fling it.'),
    'solaris': dict(
        name='Dawnspark', incant='Solaris', gesture='up', color='#ffe27a', cost=12, cd=1.5,
        aliases=['solaris', 'solar is', 'so lyrics', 'sol harris', 'solarus'],
        desc='Swipe straight up. Banishes darkness and mends 6 HP.'),
    'glacia': dict(
        name='Rime Spire', incant='Glacia', gesture='triangle', color='#9fd8ff', cost=16, cd=1.2,
        aliases=['glacia', 'glacier', 'glassier', 'glazier', 'glasha', 'glacial'],
        desc='Draw a triangle. Freezes the foe; their casting slows to half.'),
    'inferna': dict(
        name='Ember Nova', incant='Inferna', gesture='vee', color='#ff9a3c', cost=16, cd=1.2,
        aliases=['inferna', 'inferno', 'in fern a', 'informa', 'infernal'],
        desc='Draw a V. Sets the foe ablaze (burn over time).'),
}

SPELL_ORDER = ['aegis', 'fulmina', 'dissolvo', 'ascenda', 'solaris', 'glacia', 'inferna']
GESTURE_TO_SPELL = {SPELLS[i]['gesture']: i for i in SPELL_ORDER}

GESTURE_LABEL = {
    'circle': 'a circle', 'bolt': 'a lightning bolt', 'flick': 'a flick', 'spiral': 'a spiral',
    'up': 'an upward swipe', 'triangle': 'a triangle', 'vee': 'a V',
}

# Spell fusion: cast `a` then `b` within the window.
COMBO_WINDOW = 4.0
COMBOS = [
    dict(a='glacia', b='fulmina', id='shatter', name='Shatterstorm', color='#e0f4ff',
         desc='Lightning shatters the ice: massive damage on a frozen foe.'),
    dict(a='aegis', b='solaris', id='bastion', name='Radiant Bastion', color='#fff3b0',
         desc='A mirror ward that reflects the next hex and heals you.'),
    dict(a='inferna', b='fulmina', id='plasma', name='Plasma Arc', color='#ff6bd6',
         desc='Superheated lightning that pierces any ward.'),
    dict(a='glacia', b='inferna', id='steam', name='Steam Veil', color='#d8e3e8',
         desc='Scalding fog blinds the foe; their next spell misses.'),
    dict(a='ascenda', b='inferna', id='meteor', name='Meteor', color='#ff5a1f',
         desc='Your levitated boulder ignites. Fling it for 2.5x damage.'),
    dict(a='dissolvo', b='glacia', id='stasis', name='Stasis Lock', color='#a8f0ff',
         desc='Freeze a disarmed foe solid for 3 seconds.'),
]

BASE_COLORS = ['#5ee7ff', '#c9a0ff', '#ff7a59', '#7dffb0', '#ffe27a', '#9fd8ff', '#ff9a3c', '#ff6bd6', '#ffffff']
