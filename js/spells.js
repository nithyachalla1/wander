// The spell canon of Wander. Each spell pairs a wand gesture with a spoken
// incantation; aliases catch common speech-to-text mishearings.

export const SPELLS = {
  aegis: {
    name: 'Aegis Ward', incant: 'Aegis', gesture: 'circle', color: '#5ee7ff', cost: 18, cd: 1.2,
    aliases: ['aegis', 'ages', 'egis', 'a jiss', 'ajis', 'egypt'],
    desc: 'Draw a circle. A ward blocks incoming hexes for 3s. Empowered: reflects the next one.',
  },
  fulmina: {
    name: 'Storm Lance', incant: 'Fulmina', gesture: 'bolt', color: '#c9a0ff', cost: 14, cd: 0.6,
    aliases: ['fulmina', 'full mina', 'fulmine', 'fulminate', 'full meena', 'for mina', 'full nina'],
    desc: 'Draw a lightning zig-zag (or a Z). Hurls a bolt of storm — 12 damage.',
  },
  dissolvo: {
    name: 'Severance', incant: 'Dissolvo', gesture: 'flick', color: '#ff7a59', cost: 16, cd: 1.5,
    aliases: ['dissolvo', 'dissolve', 'dissolve oh', 'dissolve o', 'the solvo'],
    desc: 'Flick diagonally. Interrupts a spell mid-cast, shatters wards, disarms the foe.',
  },
  ascenda: {
    name: 'Skyward Bind', incant: 'Ascenda', gesture: 'spiral', color: '#7dffb0', cost: 20, cd: 2,
    aliases: ['ascenda', 'ascend a', 'ascender', 'a sender', 'assender'],
    desc: 'Draw a spiral (2+ loops). Lifts a boulder that follows your wand. Swipe up to fling it.',
  },
  solaris: {
    name: 'Dawnspark', incant: 'Solaris', gesture: 'up', color: '#ffe27a', cost: 12, cd: 1.5,
    aliases: ['solaris', 'solar is', 'so lyrics', 'sol harris', 'solarus'],
    desc: 'Swipe straight up. Kindles light: banishes darkness curses and mends 6 HP.',
  },
  glacia: {
    name: 'Rime Spire', incant: 'Glacia', gesture: 'triangle', color: '#9fd8ff', cost: 16, cd: 1.2,
    aliases: ['glacia', 'glacier', 'glassier', 'glazier', 'glasha', 'glacial'],
    desc: 'Draw a triangle. Freezes the foe — their casting slows to half speed.',
  },
  inferna: {
    name: 'Ember Nova', incant: 'Inferna', gesture: 'vee', color: '#ff9a3c', cost: 16, cd: 1.2,
    aliases: ['inferna', 'inferno', 'in fern a', 'informa', 'infernal'],
    desc: 'Draw a V. Sets the foe ablaze, burning them over time.',
  },
};

export const SPELL_ORDER = ['aegis', 'fulmina', 'dissolvo', 'ascenda', 'solaris', 'glacia', 'inferna'];

export const GESTURE_TO_SPELL = Object.fromEntries(SPELL_ORDER.map((id) => [SPELLS[id].gesture, id]));

// Spell fusion: cast `a` then `b` within the fusion window.
export const COMBO_WINDOW = 4;
export const COMBOS = [
  { a: 'glacia', b: 'fulmina', id: 'shatter', name: 'Shatterstorm', color: '#e0f4ff', desc: 'Lightning shatters the ice — massive damage on a frozen foe.' },
  { a: 'aegis', b: 'solaris', id: 'bastion', name: 'Radiant Bastion', color: '#fff3b0', desc: 'A mirror ward that reflects the next hex and heals you.' },
  { a: 'inferna', b: 'fulmina', id: 'plasma', name: 'Plasma Arc', color: '#ff6bd6', desc: 'Superheated lightning that pierces any ward.' },
  { a: 'glacia', b: 'inferna', id: 'steam', name: 'Steam Veil', color: '#d8e3e8', desc: 'Scalding fog blinds the foe — their next spell misses.' },
  { a: 'ascenda', b: 'inferna', id: 'meteor', name: 'Meteor', color: '#ff5a1f', desc: 'Your levitated boulder ignites. Fling it for 2.5× damage.' },
  { a: 'dissolvo', b: 'glacia', id: 'stasis', name: 'Stasis Lock', color: '#a8f0ff', desc: 'Freeze a disarmed foe solid for 3 seconds.' },
];

export const BASE_COLORS = ['#5ee7ff', '#c9a0ff', '#ff7a59', '#7dffb0', '#ffe27a', '#9fd8ff', '#ff9a3c', '#ff6bd6', '#ffffff'];
