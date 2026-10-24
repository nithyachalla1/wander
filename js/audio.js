// Fully synthesized sound effects (no audio files) using the Web Audio API.

let ac = null, out = null, noiseBuf = null, drone = null;

export function audioInit() {
  if (ac) { if (ac.state === 'suspended') ac.resume(); return; }
  ac = new (window.AudioContext || window.webkitAudioContext)();
  const comp = ac.createDynamicsCompressor();
  comp.connect(ac.destination);
  out = ac.createGain();
  out.gain.value = 0.55;
  out.connect(comp);
  noiseBuf = ac.createBuffer(1, ac.sampleRate * 2, ac.sampleRate);
  const d = noiseBuf.getChannelData(0);
  for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
}

function env(g, t, vol, attack, dur) {
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(vol, t + attack);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
}

function tone({ type = 'sine', f0 = 440, f1 = f0, dur = 0.3, vol = 0.2, delay = 0, attack = 0.005 }) {
  if (!ac) return;
  const t = ac.currentTime + delay;
  const o = ac.createOscillator(), g = ac.createGain();
  o.type = type;
  o.frequency.setValueAtTime(f0, t);
  o.frequency.exponentialRampToValueAtTime(Math.max(1, f1), t + dur);
  env(g, t, vol, attack, dur);
  o.connect(g).connect(out);
  o.start(t);
  o.stop(t + dur + 0.05);
}

function noise({ dur = 0.3, vol = 0.2, type = 'bandpass', f0 = 1000, f1 = f0, q = 1, delay = 0, attack = 0.005 }) {
  if (!ac) return;
  const t = ac.currentTime + delay;
  const s = ac.createBufferSource(), f = ac.createBiquadFilter(), g = ac.createGain();
  s.buffer = noiseBuf;
  f.type = type;
  f.Q.value = q;
  f.frequency.setValueAtTime(f0, t);
  f.frequency.exponentialRampToValueAtTime(Math.max(20, f1), t + dur);
  env(g, t, vol, attack, dur);
  s.connect(f).connect(g).connect(out);
  s.start(t, Math.random());
  s.stop(t + dur + 0.05);
}

export const sfx = {
  stroke: () => tone({ f0: 900, f1: 1300, dur: 0.06, vol: 0.03 }),
  cast: () => { tone({ type: 'triangle', f0: 380, f1: 1150, dur: 0.28, vol: 0.13 }); noise({ type: 'highpass', f0: 3000, f1: 8000, dur: 0.22, vol: 0.06 }); },
  empowered: () => { [523, 659, 784, 1046].forEach((f, i) => tone({ type: 'triangle', f0: f, dur: 0.5, vol: 0.08, delay: i * 0.05 })); },
  zap: () => { noise({ type: 'bandpass', f0: 4000, f1: 600, q: 0.7, dur: 0.35, vol: 0.35 }); tone({ type: 'sawtooth', f0: 220, f1: 40, dur: 0.3, vol: 0.12 }); },
  shield: () => { tone({ f0: 300, f1: 600, dur: 0.6, vol: 0.12, attack: 0.05 }); tone({ f0: 450, f1: 900, dur: 0.6, vol: 0.08, attack: 0.05 }); },
  block: () => { tone({ type: 'square', f0: 1200, f1: 700, dur: 0.12, vol: 0.06 }); noise({ f0: 2500, dur: 0.15, vol: 0.12, q: 3 }); },
  hit: () => { noise({ type: 'lowpass', f0: 1800, f1: 200, dur: 0.25, vol: 0.3 }); tone({ f0: 160, f1: 60, dur: 0.2, vol: 0.2 }); },
  hurt: () => { tone({ type: 'sawtooth', f0: 140, f1: 50, dur: 0.4, vol: 0.2 }); noise({ type: 'lowpass', f0: 900, f1: 100, dur: 0.4, vol: 0.3 }); },
  freeze: () => { [1800, 2400, 3100].forEach((f, i) => tone({ f0: f, f1: f * 0.7, dur: 0.25, vol: 0.05, delay: i * 0.04 })); noise({ type: 'highpass', f0: 5000, dur: 0.4, vol: 0.08 }); },
  fire: () => { noise({ type: 'lowpass', f0: 600, f1: 2400, dur: 0.5, vol: 0.3, attack: 0.05 }); },
  whoosh: () => noise({ type: 'bandpass', f0: 300, f1: 1800, q: 1.2, dur: 0.35, vol: 0.2, attack: 0.05 }),
  slash: () => { noise({ type: 'bandpass', f0: 5000, f1: 1500, q: 2, dur: 0.18, vol: 0.25 }); },
  rock: () => { tone({ f0: 90, f1: 40, dur: 0.5, vol: 0.25 }); noise({ type: 'lowpass', f0: 500, f1: 80, dur: 0.5, vol: 0.3 }); },
  heal: () => { [660, 880, 1320].forEach((f, i) => tone({ f0: f, dur: 0.4, vol: 0.06, delay: i * 0.07 })); },
  charge: (d = 1) => { tone({ type: 'sawtooth', f0: 70, f1: 220, dur: d, vol: 0.06, attack: d * 0.8 }); },
  shatter: () => { for (let i = 0; i < 6; i++) tone({ type: 'square', f0: 1500 + Math.random() * 2500, f1: 400, dur: 0.15, vol: 0.04, delay: i * 0.03 }); noise({ type: 'highpass', f0: 2000, dur: 0.4, vol: 0.2 }); },
  fizzle: () => { tone({ type: 'square', f0: 300, f1: 120, dur: 0.18, vol: 0.05 }); },
  combo: () => { [392, 523, 659, 784, 1046, 1318].forEach((f, i) => tone({ type: 'triangle', f0: f, dur: 0.6, vol: 0.07, delay: i * 0.04 })); noise({ type: 'lowpass', f0: 3000, f1: 200, dur: 0.8, vol: 0.25 }); },
  dark: () => { tone({ type: 'sine', f0: 110, f1: 55, dur: 1.4, vol: 0.2, attack: 0.3 }); tone({ type: 'sine', f0: 116, f1: 58, dur: 1.4, vol: 0.15, attack: 0.3 }); },
  interrupt: () => { tone({ type: 'square', f0: 900, f1: 200, dur: 0.3, vol: 0.1 }); noise({ f0: 1500, dur: 0.3, vol: 0.2, q: 4 }); },
  win: () => { [523, 659, 784, 1046, 784, 1046, 1318].forEach((f, i) => tone({ type: 'triangle', f0: f, dur: 0.5, vol: 0.09, delay: i * 0.13 })); },
  lose: () => { [392, 349, 311, 262].forEach((f, i) => tone({ type: 'sawtooth', f0: f, f1: f * 0.98, dur: 0.6, vol: 0.06, delay: i * 0.25 })); },
  tick: () => tone({ type: 'triangle', f0: 880, dur: 0.12, vol: 0.08 }),
};

export function startDrone() {
  if (!ac || drone) return;
  const g = ac.createGain();
  g.gain.value = 0;
  g.gain.linearRampToValueAtTime(0.05, ac.currentTime + 2);
  const f = ac.createBiquadFilter();
  f.type = 'lowpass';
  f.frequency.value = 400;
  const lfo = ac.createOscillator(), lfoG = ac.createGain();
  lfo.frequency.value = 0.08;
  lfoG.gain.value = 250;
  lfo.connect(lfoG).connect(f.frequency);
  const oscs = [55, 55.4, 82.4].map((fr) => { const o = ac.createOscillator(); o.type = 'sawtooth'; o.frequency.value = fr; o.connect(f); o.start(); return o; });
  f.connect(g).connect(out);
  lfo.start();
  drone = { g, oscs, lfo };
}

export function stopDrone() {
  if (!drone) return;
  const d = drone;
  drone = null;
  d.g.gain.linearRampToValueAtTime(0, ac.currentTime + 0.8);
  setTimeout(() => { d.oscs.forEach((o) => o.stop()); d.lfo.stop(); }, 900);
}
