// Wander — game orchestration: input (hand / mouse / voice / keys), spell
// resolution and fusion, the duel loop, rendering and UI.

import { SPELLS, SPELL_ORDER, GESTURE_TO_SPELL, COMBOS, COMBO_WINDOW, BASE_COLORS } from './spells.js';
import { Recognizer, GLYPHS, pathLength } from './recognizer.js';
import { HandTracker, OneEuro, HAND_EDGES } from './hands.js';
import { Voice } from './voice.js';
import { audioInit, sfx, startDrone, stopDrone } from './audio.js';
import { FX, rgba } from './fx.js';
import { Enemy, PlayerModel, ENEMY_SPELLS, DIFF } from './enemy.js';

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const lerp = (a, b, t) => a + (b - a) * t;
const rand = (a, b) => a + Math.random() * (b - a);
const pick = (arr) => arr[(Math.random() * arr.length) | 0];
const pct = (v) => `${Math.round(v * 100)}%`;
const nowS = () => performance.now() / 1000;

const store = {
  get(k, d) { try { const v = localStorage.getItem(`wander.${k}`); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(`wander.${k}`, JSON.stringify(v)); } catch {} },
};

// ---------------------------------------------------------------- setup
const canvas = $('#stage');
const ctx = canvas.getContext('2d');
const video = $('#cam');
const pip = $('#pip');
const pctx = pip.getContext('2d');
const darkCanvas = document.createElement('canvas');
const dctx = darkCanvas.getContext('2d');
let W = 0, H = 0;

const fx = new FX();
const recognizer = new Recognizer();
const tracker = new HandTracker(video);
const filt = { x: new OneEuro(1.4, 0.015), y: new OneEuro(1.4, 0.015) };

const settings = Object.assign({ cam: true, voice: true, ar: false, trigger: 'point', diff: 1 }, store.get('settings', {}));
let customs = store.get('custom', []);
customs.forEach((c) => recognizer.addCustom(c.id, c.samples));

const voice = new Voice({ onSpell: onVoiceSpell, onHeard, onState: onVoiceState });

const G = {
  mode: 'menu', t: 0, introT: 0, over: false,
  player: null, enemy: null, model: null,
  rocks: [], projs: [], eprojs: [], ghosts: [], timers: [],
  lastCast: null, stroke: null, heardQueue: [], echo: null, stats: null,
  lastRec: null,
};

const wand = { x: 0, y: 0, vx: 0, vy: 0, t: 0, visible: false, src: 'mouse', trail: [], hand: null, lastMouse: -10 };
let handOn = 0, handOff = 0, handLock = 0;

let stars = [], ridges = [];
function resize() {
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  W = window.innerWidth; H = window.innerHeight;
  canvas.width = W * dpr; canvas.height = H * dpr;
  canvas.style.width = `${W}px`; canvas.style.height = `${H}px`;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  darkCanvas.width = W; darkCanvas.height = H;
  stars = Array.from({ length: 200 }, () => ({ x: Math.random() * W, y: Math.random() * H * 0.55, r: Math.random() * 1.4 + 0.3, tw: Math.random() * 6 }));
  ridges = [0, 1].map((layer) => {
    const pts = [];
    const seed = layer * 13.7;
    for (let x = 0; x <= W + 20; x += 20) {
      const n = Math.sin(x * 0.004 + seed) * 0.5 + Math.sin(x * 0.011 + seed * 2) * 0.3 + Math.sin(x * 0.027 + seed) * 0.15;
      pts.push([x, H * (0.5 - 0.07 * (1 - layer * 0.5)) - n * H * (0.06 - layer * 0.02)]);
    }
    return pts;
  });
  for (const r of G.rocks) if (r.state === 'rest') { r.x = r.hx * W; r.y = r.hy * H; }
}
window.addEventListener('resize', resize);
resize();

// ---------------------------------------------------------------- helpers
function enemyGeom() {
  const s = Math.min(H * 0.32, W * 0.34);
  return { x: W * 0.5, y: H * 0.36, s };
}

function later(t, fn) { G.timers.push({ t, fn }); }

function specFor(gestureOrId) {
  const id = GESTURE_TO_SPELL[gestureOrId] || gestureOrId;
  if (SPELLS[id]) return { id, base: id, name: SPELLS[id].name, color: SPELLS[id].color };
  const c = customs.find((x) => x.id === id);
  return c ? { id: c.id, base: c.base, name: c.name, color: c.color, custom: true } : null;
}

const gestureLabel = (g) => ({ circle: 'a circle', bolt: 'a lightning bolt', flick: 'a flick', spiral: 'a spiral', up: 'an upward swipe', triangle: 'a triangle', vee: 'a V' }[g] || g);

function toast(msg, ms = 2600) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toast.h);
  toast.h = setTimeout(() => t.classList.remove('show'), ms);
}

function banner(title, sub, color, big = false) {
  const b = $('#banner');
  b.querySelector('.title').textContent = title;
  b.querySelector('.sub').textContent = sub || '';
  b.style.setProperty('--c', color);
  b.classList.remove('show', 'big');
  void b.offsetWidth;
  b.classList.add('show');
  if (big) b.classList.add('big');
}

function ticker(text, color = '#cfc6ff') {
  const t = $('#voice-ticker');
  t.innerHTML = text;
  t.style.color = color;
  t.classList.add('show');
  clearTimeout(ticker.h);
  ticker.h = setTimeout(() => t.classList.remove('show'), 2200);
}

// ---------------------------------------------------------------- input: wand
function updateWand(x, y, src, ts) {
  const dt = Math.max(1 / 240, ts - (wand.t || ts - 0.016));
  wand.vx = lerp(wand.vx, (x - wand.x) / dt, 0.35);
  wand.vy = lerp(wand.vy, (y - wand.y) / dt, 0.35);
  wand.x = x; wand.y = y; wand.t = ts; wand.visible = true; wand.src = src;
  wand.trail.push({ x, y, t: ts });
  while (wand.trail.length && ts - wand.trail[0].t > 0.3) wand.trail.shift();
  const s = G.stroke;
  if (s && s.src === src) {
    s.pts.push({ x, y, t: ts });
    if (s.pts.length % 2 === 0) fx.spawn({ x, y, vx: rand(-30, 30), vy: rand(-30, 30), life: 0.5, size: 6, color: '#ffd98a', drag: 3 });
  }
}

const canDraw = () => (G.mode === 'duel' || G.mode === 'practice' || G.mode === 'forge') && !G.over && G.introT <= 0;

function beginStroke(src) {
  G.stroke = { src, pts: [{ x: wand.x, y: wand.y, t: nowS() }], t0: nowS() };
  sfx.stroke();
}

function endStroke() {
  const s = G.stroke;
  G.stroke = null;
  if (!s) return;
  let pts = s.pts;
  if (s.src === 'hand' && pts.length > 8) pts = pts.slice(1, -3);
  if (pts.length < 5) return;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const p of pts) { minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x); minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y); }
  if (Math.max(maxX - minX, maxY - minY) < Math.min(W, H) * 0.07) return;
  if (G.mode === 'forge') { forgeAddSample(pts); addGhost(pts, forge.color); return; }
  const res = recognizer.recognize(pts);
  G.lastRec = res;
  handleGesture(res, pts);
}

canvas.addEventListener('pointerdown', (e) => {
  audioInit();
  wand.lastMouse = nowS();
  updateWand(e.clientX, e.clientY, 'mouse', nowS());
  if (!canDraw()) return;
  if (G.stroke) endStroke();
  beginStroke('mouse');
  canvas.setPointerCapture(e.pointerId);
});
canvas.addEventListener('pointermove', (e) => {
  if (wand.hand?.present && settings.cam && G.stroke?.src !== 'mouse') return;
  wand.lastMouse = nowS();
  updateWand(e.clientX, e.clientY, 'mouse', nowS());
});
const pointerEnd = () => { if (G.stroke?.src === 'mouse') endStroke(); };
canvas.addEventListener('pointerup', pointerEnd);
canvas.addEventListener('pointercancel', pointerEnd);

// ---------------------------------------------------------------- input: hand
function processHand(now) {
  if (!tracker.ready || !settings.cam) return;
  const r = tracker.detect(now);
  wand.hand = r;
  if (!r.present) {
    if (G.stroke?.src === 'hand') endStroke();
    handOn = 0; handOff++;
    if (wand.src === 'hand') wand.visible = false;
    filt.x.reset(); filt.y.reset();
    return;
  }
  if (!r.fresh) return;
  r.fresh = false;
  const ts = now / 1000;
  const mx = clamp((r.tip.x - 0.12) / 0.76, 0, 1) * W;
  const my = clamp((r.tip.y - 0.1) / 0.72, 0, 1) * H;
  updateWand(filt.x.filter(mx, ts), filt.y.filter(my, ts), 'hand', ts);

  const drawing = settings.trigger === 'pinch' ? r.pinch : r.pose === 'point';
  if (drawing) { handOn++; handOff = 0; } else { handOff++; handOn = 0; }
  if (handLock && (handOff >= 2 || ts > handLock)) handLock = 0;
  if (!G.stroke && handOn >= 2 && !handLock && canDraw()) beginStroke('hand');
  if (G.stroke?.src === 'hand' && handOff >= 4) { endStroke(); handLock = 0; }

  const rock = heldRock();
  if (rock && r.pose === 'open' && wand.vy < -H * 2.0) flingRock();
}

function checkDwell() {
  const s = G.stroke;
  if (!s) return;
  const ts = nowS();
  if (ts - s.t0 > 4) { endStroke(); return; }
  if (s.src !== 'hand' || s.pts.length < 10 || ts - s.t0 < 0.5) return;
  const recent = s.pts.filter((p) => ts - p.t < 0.42);
  if (recent.length < 3) return;
  const last = recent[recent.length - 1];
  let md = 0;
  for (const p of recent) md = Math.max(md, Math.hypot(p.x - last.x, p.y - last.y));
  if (md < Math.min(W, H) * 0.018 && pathLength(s.pts) > Math.min(W, H) * 0.15) {
    endStroke();
    handLock = ts + 0.8;
  }
}

// ---------------------------------------------------------------- input: voice
function refreshVoiceEntries() {
  voice.setEntries([
    ...SPELL_ORDER.map((id) => ({ id, incant: SPELLS[id].incant, aliases: SPELLS[id].aliases })),
    ...customs.filter((c) => c.incant).map((c) => ({ id: c.id, incant: c.incant })),
  ]);
}
refreshVoiceEntries();

function onHeard(text, final) {
  if (G.mode !== 'duel' && G.mode !== 'practice') return;
  if (!final) $('#voice-live').textContent = text ? `“${text}”` : '';
  else $('#voice-live').textContent = '';
}

function onVoiceState(s) {
  const el = $('#mic');
  el.dataset.state = s;
  el.title = { listening: 'Listening for incantations', denied: 'Microphone blocked', unsupported: 'Voice needs Chrome or Edge', off: 'Voice off' }[s] || s;
  if (s === 'denied') toast('Microphone blocked — gestures still work.');
  if (s === 'unsupported') toast('Voice incantations need Chrome or Edge.');
}

function onVoiceSpell(id, text) {
  if ((G.mode !== 'duel' && G.mode !== 'practice') || G.over || G.introT > 0) return;
  const spec = specFor(id);
  if (!spec) return;
  ticker(`🎙 “${text}” → <b>${spec.name}</b>`, spec.color);
  if (G.echo && G.echo.id === id && G.t - G.echo.t < 1.8) { echoBonus(G.echo); G.echo = null; return; }
  if (G.heardQueue.some((h) => h.id === id)) return;
  G.heardQueue.push({ id, t: G.t });
}

// ---------------------------------------------------------------- input: keys
window.addEventListener('keydown', (e) => {
  if (e.target.matches?.('input, select, textarea')) return;
  if (e.key === 'Escape' && G.mode !== 'menu') { toMenu(); return; }
  if (e.key === 'b' || e.key === 'B') $('#book').classList.toggle('collapsed');
  if ((G.mode === 'duel' || G.mode === 'practice') && !G.over && G.introT <= 0) {
    const m = /^Digit([1-7])$/.exec(e.code);
    if (m) {
      audioInit();
      const id = SPELL_ORDER[+m[1] - 1];
      if (heldRock() && id === 'solaris') { flingRock(); return; }
      const origin = wand.visible ? { x: wand.x, y: wand.y } : { x: W / 2, y: H * 0.8 };
      castSpell(specFor(id), { quality: 0.9, empowered: e.shiftKey, origin });
    }
  }
});

// ---------------------------------------------------------------- casting
function addGhost(pts, color) {
  G.ghosts.push({ pts, color, t: 0, dur: 0.7 });
  for (let i = 0; i < pts.length; i += 3) {
    fx.spawn({ x: pts[i].x, y: pts[i].y, vx: rand(-60, 60), vy: rand(-90, 10), life: rand(0.4, 0.9), size: rand(5, 10), color, drag: 2 });
  }
}

function handleGesture(res, pts) {
  const end = pts[pts.length - 1];
  renderRecDebug(res);
  if (!res.name) {
    addGhost(pts, '#6d6185');
    banner(res.reason || 'The form falters',
      res.guess ? `Closest: ${gestureLabel(res.guess)} (${pct(res.score)} match)` : 'Bolder, cleaner strokes', '#a99bc4');
    sfx.fizzle();
    G.stats.fizzles++;
    return;
  }
  if (heldRock() && (res.name === 'up' || res.name === 'flick')) { addGhost(pts, '#7dffb0'); flingRock(); return; }
  const spec = specFor(res.name);
  if (!spec) return;
  addGhost(pts, spec.color);
  const hi = G.heardQueue.findIndex((h) => h.id === spec.id && G.t - h.t < 1.7);
  const empowered = hi >= 0;
  if (empowered) G.heardQueue.splice(hi, 1);
  const ok = castSpell(spec, { quality: res.score, empowered, origin: end });
  if (ok && !empowered) G.echo = { id: spec.id, base: spec.base, t: G.t, origin: end };
}

function castSpell(spec, o) {
  if (!spec) return false;
  const P = G.player, base = spec.base, S = SPELLS[base];
  if ((P.cds[base] || 0) > G.t) { banner(`${spec.name} recharging`, `${(P.cds[base] - G.t).toFixed(1)}s`, '#8899aa'); sfx.fizzle(); return false; }
  const cost = Math.round(S.cost * (o.whisper ? 1.4 : 1));
  if (P.mana < cost) { banner('Your mana runs dry', `${spec.name} needs ${cost}`, '#6a8cff'); sfx.fizzle(); return false; }
  P.mana -= cost;
  P.cds[base] = G.t + S.cd;
  const q = clamp((o.quality - 0.7) / 0.3, 0, 1);
  const power = (o.whisper ? 0.6 : 1) * (o.empowered ? 1.5 : 1) * (0.85 + 0.3 * q);
  const st = G.stats;
  st.casts++;
  st.quality.push(o.quality);
  if (o.empowered) st.empowered++;
  st.bySpell[base] = (st.bySpell[base] || 0) + 1;
  if (G.mode === 'duel') G.model.record(base);
  flashBook(spec.id);

  const origin = o.origin || { x: W / 2, y: H * 0.84 };
  const lc = G.lastCast;
  const combo = lc && G.t - lc.t < COMBO_WINDOW ? COMBOS.find((c) => c.a === lc.base && c.b === base && comboReady(c)) : null;
  sfx.cast();
  if (o.empowered) { sfx.empowered(); fx.ring(origin.x, origin.y, '#ffffff', 10, 160, 0.6, 3); }
  if (combo) {
    G.lastCast = null;
    st.combos++;
    castCombo(combo, power, origin);
  } else {
    G.lastCast = { base, t: G.t };
    const tags = [`${pct(o.quality)} form`];
    if (o.empowered) tags.push('EMPOWERED');
    if (o.whisper) tags.push('voice only');
    banner(spec.name, tags.join(' · '), spec.color);
    castBase(base, power, origin, spec.color, o);
  }
  return true;
}

const comboReady = (c) => (c.id === 'meteor' ? !!heldRock() : true);
const darkMiss = (o) => G.player.dark > 0 && !o.empowered && Math.random() < 0.35;

function launch(p) {
  if (p.miss) {
    p.to = { x: p.to.x + pick([-1, 1]) * rand(160, 260), y: p.to.y - rand(60, 140) };
    p.onHit = () => fx.text(p.to.x, p.to.y, 'MISSED in darkness', '#8a7bb0', 22);
  }
  G.projs.push({ size: 12, kind: 'orb', ...p, t: 0 });
}

function heal(n) {
  const P = G.player;
  const h = Math.min(P.maxHp - P.hp, n);
  P.hp += h;
  if (h > 0.5) fx.text(W * 0.18, H * 0.82, `+${Math.round(h)}`, '#7dffb0', 30);
}

function castBase(base, power, origin, color, o) {
  const E = G.enemy, P = G.player, eg = enemyGeom();
  const ep = { x: eg.x, y: eg.y };
  switch (base) {
    case 'aegis': {
      P.shield = 3 * (o.empowered ? 1.4 : 1) * (0.9 + 0.1 * power);
      P.shieldMax = P.shield;
      P.reflect = P.reflect || !!o.empowered;
      sfx.shield();
      fx.ring(W / 2, H * 1.05, color, 40, W * 0.5, 0.6, 6);
      fx.burst(origin.x, origin.y, color, 30, 250);
      break;
    }
    case 'fulmina': {
      const miss = darkMiss(o);
      const tx = ep.x + (miss ? pick([-1, 1]) * rand(160, 260) : rand(-20, 20));
      const ty = ep.y + (miss ? -rand(60, 140) : rand(-30, 30));
      sfx.zap();
      fx.lightning(origin.x, origin.y, tx, ty, color, 0.35, 2.6 * power);
      fx.lightning(origin.x, origin.y, tx, ty, '#ffffff', 0.2, 1.2);
      fx.flash(color, 0.15, 0.15);
      later(0.08, () => (miss ? fx.text(tx, ty, 'MISSED in darkness', '#8a7bb0', 22) : damageEnemy(12 * power, { color })));
      break;
    }
    case 'dissolvo': {
      sfx.slash();
      launch({
        from: origin, to: ep, dur: 0.25, color, size: 16, kind: 'slash',
        onHit: () => {
          if (E.cast) {
            E.interrupt();
            fx.text(ep.x, ep.y - eg.s * 0.6, 'INTERRUPTED!', color, 42);
            sfx.interrupt();
            G.stats.interrupts++;
          } else if (E.ward > 0) {
            shatterWard();
          } else {
            E.disarm(1.2 * power);
            fx.text(ep.x, ep.y - eg.s * 0.6, 'DISARMED', color, 32);
          }
          damageEnemy(4 * power, { color });
        },
      });
      break;
    }
    case 'ascenda': {
      if (heldRock()) { heldRock().heldT = 7; heldRock().power = Math.max(heldRock().power, power); break; }
      const r = nearestRock(origin);
      if (!r) { banner('No boulder answers', 'The stones are still settling', color); break; }
      Object.assign(r, { state: 'held', heldT: 7, power, burning: false, vx: 0, vy: 0 });
      sfx.rock();
      fx.burst(r.x, r.y, ['#7dffb0', '#c9b18a'], 40, 260, { grav: 300 });
      fx.text(r.x, r.y - 60, 'LIFTED — swipe up to fling', color, 22);
      break;
    }
    case 'solaris': {
      const wasDark = P.dark > 0;
      P.dark = 0;
      heal(6 * power);
      sfx.heal();
      fx.burst(origin.x, origin.y, ['#ffe27a', '#fff6c8'], 60, 420, { life: 1 });
      fx.ring(origin.x, origin.y, color, 10, Math.max(W, H) * 0.6, 0.9, 8);
      fx.flash('#fff6c8', wasDark ? 0.5 : 0.2, 0.4);
      if (wasDark) fx.text(W / 2, H * 0.58, 'THE VEIL LIFTS', color, 36);
      break;
    }
    case 'glacia': {
      sfx.freeze();
      launch({
        from: origin, to: ep, dur: 0.35, color, size: 13, kind: 'ice', miss: darkMiss(o),
        onHit: () => {
          E.burn = 0;
          E.frozen = Math.max(E.frozen, 4 * power);
          damageEnemy(6 * power, { color });
          fx.burst(ep.x, ep.y, ['#9fd8ff', '#ffffff'], 40, 350);
          fx.text(ep.x + eg.s * 0.5, ep.y - 30, 'FROZEN', color, 26);
        },
      });
      break;
    }
    case 'inferna': {
      sfx.fire();
      launch({
        from: origin, to: ep, dur: 0.38, color, size: 16, kind: 'fire', miss: darkMiss(o),
        onHit: () => {
          E.frozen = 0;
          E.burn = 4 * power;
          damageEnemy(6 * power, { color });
          fx.burst(ep.x, ep.y, ['#ff9a3c', '#ff5a1f', '#ffd27a'], 50, 380, { grav: -200 });
          fx.text(ep.x - eg.s * 0.5, ep.y - 30, 'BURNING', color, 26);
        },
      });
      break;
    }
  }
}

function echoBonus(e) {
  const E = G.enemy, P = G.player, eg = enemyGeom();
  const color = SPELLS[e.base].color;
  G.stats.empowered++;
  sfx.empowered();
  banner('Echo!', 'Voice joined the gesture — bonus power', color);
  fx.ring(e.origin.x, e.origin.y, '#ffffff', 10, 140, 0.5, 3);
  switch (e.base) {
    case 'aegis': P.shield += 1.5; P.shieldMax = P.shield; P.reflect = true; break;
    case 'fulmina': fx.lightning(e.origin.x, e.origin.y, eg.x, eg.y, color, 0.3, 2); later(0.08, () => damageEnemy(7, { color })); break;
    case 'dissolvo': if (E.disarmed > 0) E.disarmed += 1; else damageEnemy(4, { color }); break;
    case 'ascenda': { const r = heldRock(); if (r) { r.power *= 1.5; r.heldT += 2; } break; }
    case 'solaris': heal(6); break;
    case 'glacia': if (E.frozen > 0) E.frozen += 2; break;
    case 'inferna': if (E.burn > 0) E.burn += 2; break;
  }
}

function castCombo(c, power, origin) {
  const E = G.enemy, P = G.player, eg = enemyGeom();
  const ep = { x: eg.x, y: eg.y };
  banner(c.name, 'SPELL FUSION', c.color, true);
  sfx.combo();
  fx.flash(c.color, 0.3, 0.4);
  fx.addShake(10);
  fx.ring(origin.x, origin.y, c.color, 10, 260, 0.7, 5);
  switch (c.id) {
    case 'shatter': {
      const frozen = E.frozen > 0 || E.stasis > 0;
      for (let i = 0; i < 3; i++) fx.lightning(origin.x, origin.y, ep.x + rand(-40, 40), ep.y + rand(-50, 50), i ? '#9fd8ff' : '#c9a0ff', 0.4, 3);
      later(0.1, () => {
        E.frozen = 0; E.stasis = 0;
        damageEnemy((frozen ? 32 : 18) * power, { color: '#e0f4ff', big: true });
        fx.burst(ep.x, ep.y, ['#e0f4ff', '#9fd8ff', '#c9a0ff'], 140, 750, { size: 14, life: 1.1 });
        sfx.shatter();
        if (frozen) fx.text(ep.x, ep.y - eg.s * 0.75, 'SHATTERED!', '#e0f4ff', 46);
      });
      break;
    }
    case 'bastion': {
      P.shield = 4.5; P.shieldMax = 4.5; P.reflect = true;
      heal(10 * power);
      sfx.shield(); sfx.heal();
      fx.ring(W / 2, H * 1.05, c.color, 40, W * 0.55, 0.8, 8);
      break;
    }
    case 'plasma': {
      for (let i = 0; i < 4; i++) fx.lightning(origin.x, origin.y, ep.x + rand(-30, 30), ep.y + rand(-40, 40), i % 2 ? '#ff6bd6' : '#ff9a3c', 0.45, 3.5);
      sfx.zap();
      later(0.1, () => { damageEnemy(20 * power, { color: c.color, pierce: true, big: true }); if (E.ward > 0) shatterWard(); });
      break;
    }
    case 'steam': {
      E.blind = true;
      if (E.cast) E.cast.t = Math.max(0, E.cast.t - 0.6);
      for (let i = 0; i < 60; i++) {
        fx.spawn({ x: ep.x + rand(-eg.s * 0.5, eg.s * 0.5), y: ep.y - eg.s * 0.3 + rand(-40, 40), vx: rand(-40, 40), vy: rand(-50, -10), life: rand(1, 2), size: rand(20, 40), grow: 20, color: '#8a9aa6', drag: 1 });
      }
      fx.text(ep.x, ep.y - eg.s * 0.7, 'BLINDED', '#d8e3e8', 34);
      break;
    }
    case 'meteor': {
      const r = heldRock();
      r.burning = true;
      r.power = Math.max(r.power, power);
      r.heldT = Math.max(r.heldT, 4);
      fx.burst(r.x, r.y, ['#ff5a1f', '#ffd27a'], 60, 300, { grav: -200 });
      fx.text(r.x, r.y - 60, 'IGNITED — FLING IT!', c.color, 26);
      break;
    }
    case 'stasis': {
      E.stasis = 3 * power;
      E.cast = null;
      sfx.freeze();
      fx.burst(ep.x, ep.y, ['#a8f0ff', '#ffffff'], 90, 450);
      fx.text(ep.x, ep.y - eg.s * 0.7, 'STASIS', c.color, 38);
      break;
    }
  }
}

function shatterWard() {
  const E = G.enemy, eg = enemyGeom();
  E.ward = 0;
  sfx.shatter();
  fx.burst(eg.x, eg.y, ['#6b5cff', '#b9a2ff', '#ffffff'], 80, 600, { size: 9 });
  fx.text(eg.x, eg.y - eg.s * 0.8, 'WARD SHATTERED', '#b9a2ff', 30);
}

function damageEnemy(amount, o = {}) {
  const E = G.enemy;
  if (!E || E.dead) return 0;
  const eg = enemyGeom();
  let dmg = amount;
  if (E.ward > 0 && !o.pierce && !o.dot) {
    const a = Math.min(E.ward, dmg);
    E.ward -= a;
    dmg -= a;
    fx.ring(eg.x, eg.y, '#6b5cff', eg.s * 0.5, eg.s * 0.75, 0.35, 5);
    fx.text(eg.x - eg.s * 0.4, eg.y - eg.s * 0.4, `-${Math.round(a)} ward`, '#9d8cff', 20);
    if (E.ward <= 0.01) shatterWard();
    if (dmg <= 0) { sfx.block(); return 0; }
  }
  E.hp -= dmg;
  E.sinceHit = 0;
  G.stats.dmg += dmg;
  if (!o.dot) {
    E.flash = 1;
    fx.text(eg.x + rand(-40, 40), eg.y - eg.s * 0.55, `${Math.round(dmg)}`, o.color || '#fff', o.big ? 48 : 32);
    fx.burst(eg.x, eg.y, o.color || '#ffffff', 25, 300);
    fx.addShake(o.big ? 14 : 5);
    sfx.hit();
  }
  if (E.dummy) { E.hp = Math.max(1, E.hp); return dmg; }
  if (E.hp <= 0) { E.hp = 0; E.dead = true; victory(); }
  return dmg;
}

// ---------------------------------------------------------------- enemy hooks
function onEnemyCastStart(id, dur) {
  sfx.charge(dur);
}

function onEnemyPhase(ph) {
  banner(ph === 2 ? 'The Archon stirs' : 'The Archon unravels', ph === 2 ? 'Phase II — it can now mend itself' : 'Phase III — Hollow Barrage unleashed', '#ff3b8a', true);
  fx.flash('#ff2a6a', 0.25, 0.5);
  fx.addShake(12);
  sfx.dark();
  G.enemy.think(ph === 2 ? 'You wound me. I will not be careless again.' : 'ENOUGH. No more games.');
}

function enemyRelease(id) {
  const S = ENEMY_SPELLS[id], E = G.enemy, P = G.player, eg = enemyGeom();
  const offensive = S.kind !== 'self';
  if (offensive && E.blind) {
    E.blind = false;
    fx.text(eg.x, eg.y - eg.s * 0.7, 'BLINDED — MISSED', '#d8e3e8', 30);
    return;
  }
  switch (S.kind) {
    case 'proj': fireAt(id); break;
    case 'multi': for (let i = 0; i < S.count; i++) later(i * 0.3, () => !G.over && fireAt(id)); break;
    case 'curse':
      P.dark = 9;
      sfx.dark();
      fx.flash('#000000', 0.7, 0.7);
      fx.text(W / 2, H * 0.58, 'UMBRAL VEIL — call the light!', '#b9a2ff', 30);
      break;
    case 'self':
      if (id === 'ward') {
        E.ward = S.absorb * (1 + 0.2 * (E.phase - 1));
        sfx.shield();
        fx.ring(eg.x, eg.y, S.color, 20, eg.s * 0.75, 0.6, 5);
      } else if (id === 'mend') {
        const h = Math.min(E.maxHp - E.hp, S.heal * (1 + 0.2 * (E.phase - 1)));
        E.hp += h;
        sfx.heal();
        fx.text(eg.x, eg.y - eg.s * 0.6, `+${Math.round(h)}`, S.color, 34);
        fx.burst(eg.x, eg.y, S.color, 50, 300, { grav: -150 });
      }
      break;
  }
}

function fireAt(id) {
  const S = ENEMY_SPELLS[id], E = G.enemy, eg = enemyGeom();
  const x0 = eg.x + eg.s * 0.36, y0 = eg.y - eg.s * 0.5;
  G.eprojs.push({ id, x0, y0, x1: W / 2 + rand(-W * 0.14, W * 0.14), y1: H * 0.78, t: 0, dur: 0.9, dmg: S.dmg * E.d.dmg * (1 + 0.1 * (E.phase - 1)), x: x0, y: y0 });
  if (!S.pierce) G.model.faced++;
  sfx.whoosh();
}

function hitPlayer(p) {
  const S = ENEMY_SPELLS[p.id], P = G.player;
  let dmg = p.dmg;
  if (P.shield > 0) {
    if (S.pierce) {
      P.shield = 0; P.reflect = false;
      dmg *= 1.25;
      fx.text(W / 2, H * 0.7, 'WARD SHATTERED', S.color, 34);
      sfx.shatter();
    } else {
      G.model.blocked++;
      G.stats.blocks++;
      sfx.block();
      fx.burst(p.x1, p.y1, ['#5ee7ff', '#ffffff'], 40, 420);
      if (P.reflect) {
        P.reflect = false;
        fx.text(p.x1, p.y1 - 40, 'REFLECTED!', '#fff3b0', 36);
        const eg = enemyGeom();
        launch({ from: { x: p.x1, y: p.y1 }, to: { x: eg.x, y: eg.y }, dur: 0.35, color: S.color, size: 18, kind: 'orb', onHit: () => damageEnemy(p.dmg * 1.5, { color: '#fff3b0' }) });
      } else fx.text(p.x1, p.y1 - 40, 'BLOCKED', '#5ee7ff', 28);
      return;
    }
  }
  P.hp -= dmg;
  G.stats.taken += dmg;
  fx.flash('#ff0033', 0.35, 0.35);
  fx.addShake(16);
  sfx.hurt();
  fx.burst(p.x1, p.y1, S.color, 50, 500);
  fx.text(p.x1, p.y1 - 30, `-${Math.round(dmg)}`, '#ff5577', 34);
  if (P.hp <= 0) { P.hp = 0; defeat(); }
}

Object.assign(G, { damageEnemy, enemyRelease, onEnemyPhase, onEnemyCastStart });

// ---------------------------------------------------------------- rocks
function rockShape(n = 10) {
  return Array.from({ length: n }, (_, i) => ({ a: (i / n) * Math.PI * 2, r: rand(0.75, 1.1) }));
}

function makeRocks() {
  G.rocks = [[0.12, 0.86, 0.05], [0.88, 0.84, 0.055], [0.27, 0.95, 0.038], [0.73, 0.95, 0.042]].map(([hx, hy, r]) => ({
    hx, hy, r, x: hx * W, y: hy * H, vx: 0, vy: 0, state: 'rest', heldT: 0, power: 1, burning: false, respawn: 0, a: rand(0, 6), shape: rockShape(), fade: 1,
  }));
}

const heldRock = () => G.rocks.find((r) => r.state === 'held');

function nearestRock(pt) {
  let best = null, bd = Infinity;
  for (const r of G.rocks) {
    if (r.state !== 'rest') continue;
    const d = Math.hypot(r.x - pt.x, r.y - pt.y);
    if (d < bd) { bd = d; best = r; }
  }
  return best;
}

function flingRock() {
  const r = heldRock();
  if (!r) return;
  Object.assign(r, { state: 'flying', t: 0, dur: 0.55, x0: r.x, y0: r.y });
  G.stats.flings++;
  sfx.whoosh();
  banner(r.burning ? 'Meteor!' : 'Boulder flung', r.burning ? '2.5× damage' : '', r.burning ? '#ff5a1f' : '#7dffb0');
}

function updateRocks(dt) {
  const eg = enemyGeom();
  const R = Math.min(W, H);
  for (const r of G.rocks) {
    if (r.state === 'rest') { r.x = r.hx * W; r.y = r.hy * H; r.fade = Math.min(1, r.fade + dt * 2); }
    else if (r.state === 'held') {
      r.heldT -= dt;
      const tx = wand.visible ? wand.x : r.hx * W;
      const ty = wand.visible ? Math.min(wand.y, H * 0.8) : r.hy * H - 120;
      r.vx += (tx - r.x) * 45 * dt; r.vy += (ty - r.y) * 45 * dt;
      const k = Math.exp(-7 * dt);
      r.vx *= k; r.vy *= k;
      r.x += r.vx * dt; r.y += r.vy * dt + Math.sin(G.t * 4) * 0.3;
      r.a += dt * 0.8;
      if (Math.random() < 0.6) fx.spawn({ x: r.x + rand(-1, 1) * r.r * R, y: r.y + r.r * R * 0.8, vy: rand(20, 60), life: 0.6, size: 7, color: '#7dffb0', drag: 1 });
      if (r.burning) for (let i = 0; i < 2; i++) fx.spawn({ x: r.x + rand(-0.6, 0.6) * r.r * R, y: r.y + rand(-0.6, 0.6) * r.r * R, vx: rand(-20, 20), vy: rand(-140, -60), life: 0.5, size: rand(10, 18), color: pick(['#ff9a3c', '#ff5a1f', '#ffd27a']), drag: 1 });
      if (r.heldT <= 0) flingRock();
    } else if (r.state === 'flying') {
      r.t += dt;
      const k = Math.min(1, r.t / r.dur);
      const cx = (r.x0 + eg.x) / 2, cy = Math.min(r.y0, eg.y) - H * 0.25;
      r.x = (1 - k) ** 2 * r.x0 + 2 * (1 - k) * k * cx + k * k * eg.x;
      r.y = (1 - k) ** 2 * r.y0 + 2 * (1 - k) * k * cy + k * k * eg.y;
      r.a += dt * 10;
      fx.spawn({ x: r.x, y: r.y, life: 0.4, size: r.burning ? 22 : 10, color: r.burning ? '#ff5a1f' : '#7dffb0', drag: 1 });
      if (k >= 1) {
        const dmg = 14 * r.power * (r.burning ? 2.5 : 1);
        damageEnemy(dmg, { color: r.burning ? '#ff5a1f' : '#c9b18a', big: r.burning });
        if (r.burning && G.enemy) G.enemy.burn = Math.max(G.enemy.burn, 3);
        sfx.rock();
        fx.burst(r.x, r.y, r.burning ? ['#ff5a1f', '#ffd27a', '#5a4632'] : ['#c9b18a', '#7a6650'], 60, 500, { grav: 600, size: 9 });
        Object.assign(r, { state: 'gone', respawn: 6, burning: false });
      }
    } else if (r.state === 'gone') {
      r.respawn -= dt;
      if (r.respawn <= 0) { r.state = 'rest'; r.fade = 0; r.shape = rockShape(); }
    }
  }
}

// ---------------------------------------------------------------- modes
function freshStats() {
  return { casts: 0, quality: [], empowered: 0, combos: 0, fizzles: 0, interrupts: 0, blocks: 0, dmg: 0, taken: 0, flings: 0, bySpell: {}, start: 0 };
}

let camStarting = false;
async function ensureInputs() {
  if (settings.cam && !tracker.ready && !camStarting) {
    camStarting = true;
    $('#cam-pip').classList.add('on');
    try {
      await tracker.start((s) => { $('#cam-status').textContent = s; });
      setTimeout(() => { if (tracker.ready) $('#cam-status').textContent = ''; }, 1500);
    } catch (err) {
      console.warn(err);
      $('#cam-status').textContent = 'No camera — draw with mouse';
      toast('Camera unavailable — you can still draw spells with the mouse.');
      camStarting = false;
    }
  }
  if (settings.voice) voice.start(); else voice.stop();
}

function showScreen(id) {
  for (const s of ['#menu', '#over', '#forge']) $(s).classList.toggle('hidden', s !== id);
  $('#hud').classList.toggle('hidden', !(G.mode === 'duel' || G.mode === 'practice'));
  document.body.dataset.mode = G.mode;
}

function startMode(mode) {
  audioInit();
  ensureInputs();
  Object.assign(G, { mode, t: 0, over: false, projs: [], eprojs: [], ghosts: [], timers: [], heardQueue: [], lastCast: null, echo: null, stroke: null, lastRec: null });
  G.stats = freshStats();
  G.player = { hp: 100, maxHp: 100, mana: 100, maxMana: 100, shield: 0, shieldMax: 1, reflect: false, dark: 0, cds: {} };
  makeRocks();
  fx.parts.length = 0;
  if (mode === 'forge') {
    G.enemy = null;
    showScreen('#forge');
    renderForge();
    return;
  }
  const prior = mode === 'duel' ? store.get('archon', null) : null;
  G.model = new PlayerModel(prior);
  G.enemy = new Enemy(settings.diff, G.model, { dummy: mode === 'practice' });
  G.introT = mode === 'duel' ? 3.4 : 0;
  $('#enemy-name').textContent = mode === 'duel' ? 'Morvane, the Hollow Archon' : 'Training Effigy';
  $('#mind-title').textContent = mode === 'duel' ? "The Archon's Mind" : 'Wand Reading';
  showScreen(null);
  renderBook();
  if (mode === 'duel') {
    startDrone();
    const fav = G.model.favorite();
    G.enemy.think(fav ? `I remember you. You favored ${SPELLS[fav.id]?.name}. Not this time.` : 'Another wand-bearer. Show me your forms.');
  } else {
    banner('Practice Grounds', 'Cast freely. The effigy never strikes back.', '#7dffb0');
  }
}

function toMenu() {
  G.mode = 'menu';
  G.stroke = null;
  stopDrone();
  showScreen('#menu');
  renderSpellbook();
}

function victory() {
  if (G.over) return;
  G.over = true;
  sfx.win();
  stopDrone();
  const eg = enemyGeom();
  for (let i = 0; i < 5; i++) later(i * 0.25, () => fx.burst(eg.x + rand(-80, 80), eg.y + rand(-80, 80), ['#c070ff', '#ffffff', '#ffe27a'], 80, 700));
  banner('Victory', 'The Archon is unmade', '#ffe27a', true);
  saveArchonMemory();
  setTimeout(() => G.over && showOver(true), 2600);
}

function defeat() {
  if (G.over) return;
  G.over = true;
  sfx.lose();
  stopDrone();
  fx.flash('#000000', 0.8, 1.5);
  banner('Defeat', 'Your wand falls silent', '#ff5577', true);
  G.enemy.think('Predictable. I will remember this.');
  saveArchonMemory();
  setTimeout(() => G.over && showOver(false), 2200);
}

function saveArchonMemory() {
  const prev = store.get('archon', {}) || {};
  const snap = G.model.snapshot();
  const merged = {};
  for (const k of new Set([...Object.keys(prev), ...Object.keys(snap)])) merged[k] = (prev[k] || 0) * 0.5 + (snap[k] || 0);
  store.set('archon', merged);
}

function showOver(win) {
  G.mode = 'over';
  const st = G.stats, m = G.model;
  const avgQ = st.quality.length ? st.quality.reduce((a, b) => a + b, 0) / st.quality.length : 0;
  $('#over-title').textContent = win ? 'Victory' : 'Defeat';
  $('#over-title').className = win ? 'win' : 'lose';
  $('#over-sub').textContent = win ? `Morvane falls in ${G.t.toFixed(0)}s on ${DIFF[settings.diff].name}.` : `Morvane endures with ${Math.ceil(G.enemy.hp)} HP.`;
  const rows = [
    ['Spells cast', st.casts], ['Average form', pct(avgQ)], ['Empowered (voice)', st.empowered], ['Spell fusions', st.combos],
    ['Interrupts', st.interrupts], ['Hexes blocked', st.blocks], ['Boulders flung', st.flings], ['Damage dealt', Math.round(st.dmg)],
    ['Damage taken', Math.round(st.taken)], ['Fizzled forms', st.fizzles],
  ];
  $('#over-stats').innerHTML = rows.map(([k, v]) => `<div><span>${k}</span><b>${v}</b></div>`).join('');
  const fav = m.favorite();
  const s = m.style();
  $('#over-learned').innerHTML = fav
    ? `The Archon learned that you favor <b style="color:${SPELLS[fav.id].color}">${SPELLS[fav.id].name}</b> (${pct(fav.p)}), raise your ward against ${pct(s.reflex)} of hexes, and interrupt ${pct(m.interruptRate())} of its casts. <i>It will remember next duel.</i>`
    : 'The Archon learned nothing of you. Cast more next time.';
  showScreen('#over');
}

// ---------------------------------------------------------------- forge
const forge = { samples: [], color: BASE_COLORS[0] };

function forgeAddSample(pts) {
  if (forge.samples.length >= 5) forge.samples.shift();
  forge.samples.push(pts.map((p) => ({ x: Math.round(p.x), y: Math.round(p.y) })));
  sfx.cast();
  renderForge();
}

function renderForge() {
  const base = $('#forge-base');
  if (!base.options.length) base.innerHTML = SPELL_ORDER.map((id) => `<option value="${id}">${SPELLS[id].name}</option>`).join('');
  $('#forge-colors').innerHTML = BASE_COLORS.map((c) => `<button class="swatch${c === forge.color ? ' on' : ''}" data-c="${c}" style="background:${c}" aria-label="color ${c}"></button>`).join('');
  $('#forge-count').textContent = `${forge.samples.length}/3`;
  const wrap = $('#forge-samples');
  wrap.innerHTML = '';
  forge.samples.forEach((s) => { const cv = document.createElement('canvas'); cv.width = cv.height = 64; drawGlyph(cv, s, forge.color); wrap.appendChild(cv); });
  let msg = '';
  if (forge.samples.length) {
    const hits = forge.samples.map((s) => recognizer.recognize(s)).filter((r) => r.name && !r.custom && r.score > 0.85);
    if (hits.length >= Math.ceil(forge.samples.length / 2)) msg = `Careful: this resembles ${gestureLabel(hits[0].name)} (${SPELLS[GESTURE_TO_SPELL[hits[0].name]]?.name}). Your form will still win when it matches closely.`;
  }
  $('#forge-msg').textContent = msg;
  $('#forge-list').innerHTML = customs.length
    ? customs.map((c) => `<li><canvas data-glyph="${c.id}" width="40" height="40"></canvas><div><b style="color:${c.color}">${c.name}</b><small>${c.incant ? `“${c.incant}” · ` : ''}${SPELLS[c.base].name} effect</small></div><button data-del="${c.id}" aria-label="Delete ${c.name}">✕</button></li>`).join('')
    : '<li class="empty">No forged spells yet.</li>';
  $$('#forge-list canvas[data-glyph]').forEach((cv) => { const c = customs.find((x) => x.id === cv.dataset.glyph); drawGlyph(cv, c.samples[0], c.color); });
}

$('#forge-colors').addEventListener('click', (e) => { const c = e.target.dataset?.c; if (c) { forge.color = c; renderForge(); } });
$('#forge-clear').addEventListener('click', () => { forge.samples = []; renderForge(); });
$('#forge-back').addEventListener('click', toMenu);
$('#forge-list').addEventListener('click', (e) => {
  const id = e.target.dataset?.del;
  if (!id) return;
  customs = customs.filter((c) => c.id !== id);
  recognizer.removeCustom(id);
  store.set('custom', customs);
  refreshVoiceEntries();
  renderForge();
});
$('#forge-save').addEventListener('click', () => {
  const name = $('#forge-name').value.trim();
  const incant = $('#forge-incant').value.trim();
  if (!name) { $('#forge-msg').textContent = 'Name your spell first.'; return; }
  if (forge.samples.length < 3) { $('#forge-msg').textContent = 'Draw the form at least 3 times.'; return; }
  const c = { id: `c_${Date.now().toString(36)}`, name, incant, base: $('#forge-base').value, color: forge.color, samples: forge.samples };
  customs.push(c);
  recognizer.addCustom(c.id, c.samples);
  store.set('custom', customs);
  refreshVoiceEntries();
  forge.samples = [];
  $('#forge-name').value = '';
  $('#forge-incant').value = '';
  sfx.combo();
  toast(`${name} is bound to your grimoire.`);
  renderForge();
});

// ---------------------------------------------------------------- UI: books & HUD
function drawGlyph(cv, pts, color) {
  const c = cv.getContext('2d');
  const S = cv.width;
  c.clearRect(0, 0, S, S);
  if (!pts || pts.length < 2) return;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const p of pts) { minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x); minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y); }
  const sc = (S * 0.72) / Math.max(maxX - minX, maxY - minY, 1e-6);
  const ox = S / 2 - ((minX + maxX) / 2) * sc, oy = S / 2 - ((minY + maxY) / 2) * sc;
  const P = pts.map((p) => [p.x * sc + ox, p.y * sc + oy]);
  c.strokeStyle = color;
  c.lineWidth = Math.max(1.5, S * 0.05);
  c.lineCap = 'round'; c.lineJoin = 'round';
  c.shadowColor = color; c.shadowBlur = S * 0.12;
  c.beginPath(); c.moveTo(P[0][0], P[0][1]);
  for (const p of P) c.lineTo(p[0], p[1]);
  c.stroke();
  c.shadowBlur = 0;
  c.fillStyle = '#fff';
  c.beginPath(); c.arc(P[0][0], P[0][1], S * 0.06, 0, Math.PI * 2); c.fill();
  const a = P[P.length - 1], b = P[Math.max(0, P.length - 5)];
  const ang = Math.atan2(a[1] - b[1], a[0] - b[0]), L = S * 0.14;
  c.fillStyle = color;
  c.beginPath();
  c.moveTo(a[0] + Math.cos(ang) * L * 0.4, a[1] + Math.sin(ang) * L * 0.4);
  c.lineTo(a[0] + Math.cos(ang + 2.5) * L, a[1] + Math.sin(ang + 2.5) * L);
  c.lineTo(a[0] + Math.cos(ang - 2.5) * L, a[1] + Math.sin(ang - 2.5) * L);
  c.fill();
}

const allSpecs = () => [
  ...SPELL_ORDER.map((id, i) => ({ id, base: id, key: i + 1, name: SPELLS[id].name, incant: SPELLS[id].incant, color: SPELLS[id].color, glyph: GLYPHS[SPELLS[id].gesture], cost: SPELLS[id].cost, desc: SPELLS[id].desc })),
  ...customs.map((c) => ({ id: c.id, base: c.base, name: c.name, incant: c.incant, color: c.color, glyph: c.samples[0], cost: SPELLS[c.base].cost, desc: `Forged form · ${SPELLS[c.base].name} effect`, custom: true })),
];

function renderBook() {
  const list = $('#book-list');
  list.innerHTML = allSpecs().map((s) => `
    <li data-id="${s.id}" data-base="${s.base}" style="--c:${s.color}">
      <canvas width="44" height="44"></canvas>
      <div><b>${s.name}</b><small>${s.incant ? `“${s.incant}”` : ''} · ${s.cost}✦${s.key ? ` · [${s.key}]` : ''}</small></div>
      <i class="cd"></i>
    </li>`).join('');
  const specs = allSpecs();
  $$('#book-list li').forEach((li, i) => drawGlyph(li.querySelector('canvas'), specs[i].glyph, specs[i].color));
}

function renderSpellbook() {
  const specs = allSpecs();
  $('#spellbook-grid').innerHTML = specs.map((s) => `
    <div class="card" style="--c:${s.color}">
      <canvas width="72" height="72"></canvas>
      <div><h4>${s.name}</h4><p class="incant">${s.incant ? `“${s.incant}!”` : '<i>silent</i>'}</p><p>${s.desc}</p></div>
    </div>`).join('');
  $$('#spellbook-grid .card').forEach((el, i) => drawGlyph(el.querySelector('canvas'), specs[i].glyph, specs[i].color));
  $('#combo-grid').innerHTML = COMBOS.map((c) => `
    <div class="combo" style="--c:${c.color}">
      <span style="color:${SPELLS[c.a].color}">${SPELLS[c.a].name}</span> → <span style="color:${SPELLS[c.b].color}">${SPELLS[c.b].name}</span>
      <b>= ${c.name}</b><p>${c.desc}</p>
    </div>`).join('');
  const prior = store.get('archon', null);
  let fav = null, v = 0;
  if (prior) for (const k in prior) if (prior[k] > v) { v = prior[k]; fav = k; }
  $('#memory-note').innerHTML = fav && SPELLS[fav] ? `🜏 The Archon remembers your last duels — you lean on <b style="color:${SPELLS[fav].color}">${SPELLS[fav].name}</b>. <button id="forget">Make it forget</button>` : '';
  $('#forget')?.addEventListener('click', () => { store.set('archon', null); renderSpellbook(); toast('The Archon’s memory of you is wiped.'); });
}

function flashBook(id) {
  const li = $(`#book-list li[data-id="${id}"]`);
  if (!li) return;
  li.classList.remove('lit');
  void li.offsetWidth;
  li.classList.add('lit');
}

const domCache = new Map();
function setHTML(sel, html) {
  if (domCache.get(sel) === html) return;
  domCache.set(sel, html);
  $(sel).innerHTML = html;
}

const chip = (txt, color) => `<span class="chip" style="--c:${color}">${txt}</span>`;
let mindTimer = 0;

function updateHud(dt) {
  const E = G.enemy, P = G.player;
  if (!E || !P) return;
  $('#enemy-hp').style.width = `${E.hpFrac * 100}%`;
  $('#enemy-ward').style.width = `${Math.min(100, (E.ward / E.maxHp) * 100 * 2.5)}%`;
  $('#enemy-phase').textContent = E.dummy ? '' : ['', 'I', 'II', 'III'][E.phase];
  const es = [];
  if (E.ward > 0) es.push(chip(`⬡ Ward ${Math.ceil(E.ward)}`, '#8c7dff'));
  if (E.frozen > 0) es.push(chip(`❄ Frozen ${E.frozen.toFixed(1)}s`, '#9fd8ff'));
  if (E.burn > 0) es.push(chip(`🔥 Burning ${E.burn.toFixed(1)}s`, '#ff9a3c'));
  if (E.stasis > 0) es.push(chip(`◇ Stasis ${E.stasis.toFixed(1)}s`, '#a8f0ff'));
  if (E.disarmed > 0) es.push(chip(`✋ Disarmed ${E.disarmed.toFixed(1)}s`, '#ff7a59'));
  if (E.blind) es.push(chip('☁ Blinded', '#d8e3e8'));
  setHTML('#enemy-status', es.join(''));

  const cb = $('#enemy-cast');
  if (E.cast) {
    const S = ENEMY_SPELLS[E.cast.id];
    cb.classList.remove('hidden');
    cb.style.setProperty('--c', S.color);
    cb.querySelector('.fill').style.width = `${(E.cast.t / E.cast.dur) * 100}%`;
    const hint = { hex: 'Raise Aegis Ward', barrage: 'Raise Aegis Ward', breaker: 'Interrupt with Severance!', mend: 'Interrupt with Severance!', veil: 'Dawnspark will lift it', ward: 'Severance shatters wards' }[E.cast.id];
    setHTML('#enemy-cast .label', `${S.name} <em>— ${hint}</em>`);
  } else cb.classList.add('hidden');

  $('#player-hp').style.width = `${(P.hp / P.maxHp) * 100}%`;
  $('#player-mana').style.width = `${(P.mana / P.maxMana) * 100}%`;
  $('#player-hp-text').textContent = Math.ceil(P.hp);
  $('#player-mana-text').textContent = Math.floor(P.mana);
  const ps = [];
  if (P.shield > 0) ps.push(chip(`◯ Ward ${P.shield.toFixed(1)}s${P.reflect ? ' · mirror' : ''}`, P.reflect ? '#fff3b0' : '#5ee7ff'));
  if (P.dark > 0) ps.push(chip(`☾ Veiled ${P.dark.toFixed(1)}s`, '#b9a2ff'));
  const rk = heldRock();
  if (rk) ps.push(chip(`⛰ ${rk.burning ? 'Meteor' : 'Boulder'} ${rk.heldT.toFixed(1)}s`, rk.burning ? '#ff5a1f' : '#7dffb0'));
  setHTML('#player-status', ps.join(''));

  // Fusion hint
  const lc = G.lastCast;
  if (lc && G.t - lc.t < COMBO_WINDOW) {
    const opts = COMBOS.filter((c) => c.a === lc.base && comboReady(c));
    const left = COMBO_WINDOW - (G.t - lc.t);
    setHTML('#combo-hint', opts.length ? `<div class="k">FUSION OPEN</div>${opts.map((c) => `<span style="color:${SPELLS[c.b].color}">${SPELLS[c.b].name}</span> → <b style="color:${c.color}">${c.name}</b>`).join('<br>')}` : '');
    $('#combo-hint').style.setProperty('--left', `${(left / COMBO_WINDOW) * 100}%`);
    $('#combo-hint').classList.toggle('show', opts.length > 0);
  } else $('#combo-hint').classList.remove('show');

  // Cooldowns in book
  $$('#book-list li').forEach((li) => {
    const b = li.dataset.base;
    const left = Math.max(0, (P.cds[b] || 0) - G.t);
    li.querySelector('.cd').style.width = `${(left / SPELLS[b].cd) * 100}%`;
    li.classList.toggle('poor', P.mana < SPELLS[b].cost);
  });

  mindTimer -= dt;
  if (mindTimer > 0) return;
  mindTimer = 0.2;
  if (E.dummy) { renderRecDebug(G.lastRec); return; }
  const m = G.model, s = m.style();
  const bar = (label, v, c) => `<div class="mbar"><span>${label}</span><i style="--v:${(v * 100).toFixed(0)}%;--c:${c}"></i><em>${pct(v)}</em></div>`;
  setHTML('#mind-style', bar('Aggression', s.aggression, '#ff7a59') + bar('Defense', s.defense, '#5ee7ff') + bar('Control', s.control, '#7dffb0') + bar('Block reflex', s.reflex, '#c9a0ff'));
  const pr = m.predict();
  setHTML('#mind-predict', pr && SPELLS[pr.id] ? `Expects next: <b style="color:${SPELLS[pr.id].color}">${SPELLS[pr.id].name}</b> ${pct(pr.p)} <small>${pr.markov ? '(pattern)' : '(habit)'}</small>` : 'Expects next: <i>studying you…</i>');
  setHTML('#mind-options', E.options.length ? `Weighed: ${E.options.slice(0, 3).map((o) => `${ENEMY_SPELLS[o.id].name} ${pct(o.p)}`).join(' · ')}` : '');
  setHTML('#mind-log', E.thoughts.map((t, i) => `<li style="opacity:${1 - i * 0.17}">${t}</li>`).join(''));
}

function renderRecDebug(res) {
  if (!G.enemy?.dummy) return;
  setHTML('#mind-style', res ? `<div class="rec">${res.name ? `<b>${specFor(res.name)?.name ?? res.name}</b>` : '<b>Unrecognized</b>'} <em>${pct(res.score)}</em></div>` : '<div class="rec"><i>Draw a form…</i></div>');
  const inf = res?.info;
  setHTML('#mind-predict', inf ? `Straightness ${inf.straight.toFixed(2)} · Loops ${inf.loops.toFixed(2)} · Radial var ${inf.cv.toFixed(2)}` : '');
  setHTML('#mind-options', res?.guess ? `Closest template: ${gestureLabel(res.guess)}` : '');
  setHTML('#mind-log', ['Practice tip: hold your forms large and steady.', 'Say the incantation as you draw to empower.', 'Chain spells within 4s to fuse them.'].map((t) => `<li>${t}</li>`).join(''));
}

// ---------------------------------------------------------------- menu wiring
function syncSettingsUI() {
  $('#opt-cam').checked = settings.cam;
  $('#opt-voice').checked = settings.voice;
  $('#opt-ar').checked = settings.ar;
  $('#opt-trigger').value = settings.trigger;
  $('#opt-diff').value = String(settings.diff);
}
syncSettingsUI();
$('#settings').addEventListener('change', () => {
  settings.cam = $('#opt-cam').checked;
  settings.voice = $('#opt-voice').checked;
  settings.ar = $('#opt-ar').checked;
  settings.trigger = $('#opt-trigger').value;
  settings.diff = +$('#opt-diff').value;
  store.set('settings', settings);
  $('#cam-pip').classList.toggle('on', settings.cam && (tracker.ready || camStarting));
  if (!settings.voice) voice.stop();
});
document.addEventListener('click', (e) => {
  const a = e.target.closest('[data-action]')?.dataset.action;
  if (!a) return;
  audioInit();
  if (a === 'duel' || a === 'rematch') startMode('duel');
  else if (a === 'practice') startMode('practice');
  else if (a === 'forge') startMode('forge');
  else if (a === 'menu') toMenu();
});
$('#book-toggle').addEventListener('click', () => $('#book').classList.toggle('collapsed'));

// ---------------------------------------------------------------- update
function update(dt, now) {
  G.t += dt;
  processHand(now);
  checkDwell();
  for (const t of G.timers) t.t -= dt;
  const due = G.timers.filter((t) => t.t <= 0);
  G.timers = G.timers.filter((t) => t.t > 0);
  due.forEach((t) => t.fn());

  if (G.mode === 'duel' || G.mode === 'practice') updateGame(dt);
  else if (Math.random() < 0.3) {
    fx.spawn({ x: rand(0, W), y: H + 10, vx: rand(-10, 10), vy: rand(-60, -20), life: rand(3, 6), size: rand(2, 5), color: pick(['#c9a0ff', '#5ee7ff', '#ffe27a']), drag: 0 });
  }
  if (wand.visible && !G.stroke && Math.random() < 0.5) {
    fx.spawn({ x: wand.x, y: wand.y, vx: rand(-20, 20), vy: rand(-10, 30), life: 0.5, size: 4, color: '#bfe8ff', drag: 1 });
  }
  for (const g of G.ghosts) g.t += dt;
  G.ghosts = G.ghosts.filter((g) => g.t < g.dur);
  fx.update(dt);
}

function updateGame(dt) {
  const P = G.player, E = G.enemy, eg = enemyGeom();
  if (G.introT > 0) {
    const before = Math.ceil(G.introT - 0.4);
    G.introT -= dt;
    const after = Math.ceil(G.introT - 0.4);
    const el = $('#intro');
    if (G.introT > 0.4) { el.textContent = after; el.className = 'show'; if (after !== before) sfx.tick(); }
    else if (G.introT > 0) { if (el.textContent !== 'Duel!') { el.textContent = 'Duel!'; sfx.combo(); } }
    if (G.introT <= 0) el.className = '';
  }
  if (!G.over) {
    P.mana = Math.min(P.maxMana, P.mana + 11 * dt);
    P.shield = Math.max(0, P.shield - dt);
    if (P.shield <= 0) P.reflect = false;
    P.dark = Math.max(0, P.dark - dt);
    for (let i = G.heardQueue.length - 1; i >= 0; i--) {
      const h = G.heardQueue[i];
      if (G.t - h.t > 1.7) { G.heardQueue.splice(i, 1); castSpell(specFor(h.id), { quality: 0.85, whisper: true, origin: { x: W / 2, y: H * 0.82 } }); }
    }
  }
  if (!G.over || E.dead) E.update(dt, G);

  // Enemy status particles
  if (!E.dead) {
    if (E.burn > 0) for (let i = 0; i < 2; i++) fx.spawn({ x: eg.x + rand(-0.3, 0.3) * eg.s, y: eg.y + rand(-0.3, 0.5) * eg.s, vx: rand(-20, 20), vy: rand(-160, -60), life: 0.6, size: rand(8, 16), color: pick(['#ff9a3c', '#ff5a1f', '#ffd27a']), drag: 1 });
    if (E.frozen > 0 && Math.random() < 0.3) fx.spawn({ x: eg.x + rand(-0.4, 0.4) * eg.s, y: eg.y + rand(-0.5, 0.5) * eg.s, vy: rand(10, 40), life: 1, size: 4, color: '#e0f4ff', drag: 0 });
    if (E.cast) {
      const S = ENEMY_SPELLS[E.cast.id];
      const ox = eg.x + eg.s * 0.36, oy = eg.y - eg.s * 0.5;
      const a = rand(0, Math.PI * 2), d = rand(60, 120);
      fx.spawn({ x: ox + Math.cos(a) * d, y: oy + Math.sin(a) * d, vx: -Math.cos(a) * d * 3, vy: -Math.sin(a) * d * 3, life: 0.3, size: 6, color: S.color, drag: 0 });
    }
  }
  updateRocks(dt);

  for (const p of G.projs) {
    p.t += dt;
    const k = Math.min(1, p.t / p.dur);
    const cx = (p.from.x + p.to.x) / 2, cy = (p.from.y + p.to.y) / 2 - Math.hypot(p.to.x - p.from.x, p.to.y - p.from.y) * 0.18;
    p.x = (1 - k) ** 2 * p.from.x + 2 * (1 - k) * k * cx + k * k * p.to.x;
    p.y = (1 - k) ** 2 * p.from.y + 2 * (1 - k) * k * cy + k * k * p.to.y;
    fx.spawn({ x: p.x, y: p.y, vx: rand(-30, 30), vy: rand(-30, 30), life: 0.35, size: p.size * 0.8, color: p.color, drag: 2 });
    if (k >= 1) { p.done = true; p.onHit?.(); }
  }
  G.projs = G.projs.filter((p) => !p.done);

  for (const p of G.eprojs) {
    p.t += dt;
    const k = Math.min(1, p.t / p.dur);
    const e = k ** 1.7;
    p.x = lerp(p.x0, p.x1, e);
    p.y = lerp(p.y0, p.y1, e) - Math.sin(k * Math.PI) * 40;
    p.k = k;
    fx.spawn({ x: p.x, y: p.y, vx: rand(-20, 20), vy: rand(-20, 20), life: 0.3, size: 6 + 20 * e, color: ENEMY_SPELLS[p.id].color, drag: 2 });
    if (k >= 1) { p.done = true; if (!G.over) hitPlayer(p); }
  }
  G.eprojs = G.eprojs.filter((p) => !p.done);

  updateHud(dt);
}

// ---------------------------------------------------------------- render
function drawBackground() {
  const arOn = settings.ar && tracker.ready && video.readyState >= 2;
  if (arOn) {
    const vw = video.videoWidth, vh = video.videoHeight;
    const sc = Math.max(W / vw, H / vh);
    ctx.save();
    ctx.translate(W, 0);
    ctx.scale(-1, 1);
    ctx.drawImage(video, (W - vw * sc) / 2, (H - vh * sc) / 2, vw * sc, vh * sc);
    ctx.restore();
    ctx.fillStyle = 'rgba(12,6,30,0.55)';
    ctx.fillRect(0, 0, W, H);
    return;
  }
  const sky = ctx.createLinearGradient(0, 0, 0, H);
  sky.addColorStop(0, '#04020b');
  sky.addColorStop(0.35, '#120a28');
  sky.addColorStop(0.5, '#2a1446');
  sky.addColorStop(0.52, '#0d0718');
  sky.addColorStop(1, '#050309');
  ctx.fillStyle = sky;
  ctx.fillRect(0, 0, W, H);
  for (const s of stars) {
    ctx.globalAlpha = 0.4 + 0.6 * Math.abs(Math.sin(G.t * 0.8 + s.tw));
    ctx.fillStyle = '#e8e0ff';
    ctx.fillRect(s.x, s.y, s.r, s.r);
  }
  ctx.globalAlpha = 1;
  const mx = W * 0.82, my = H * 0.14, mr = Math.min(W, H) * 0.05;
  const mg = ctx.createRadialGradient(mx, my, mr * 0.5, mx, my, mr * 5);
  mg.addColorStop(0, 'rgba(220,200,255,0.25)');
  mg.addColorStop(1, 'rgba(220,200,255,0)');
  ctx.fillStyle = mg;
  ctx.fillRect(mx - mr * 5, my - mr * 5, mr * 10, mr * 10);
  ctx.fillStyle = '#efe6ff';
  ctx.beginPath(); ctx.arc(mx, my, mr, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = '#120a28';
  ctx.beginPath(); ctx.arc(mx + mr * 0.35, my - mr * 0.15, mr * 0.9, 0, Math.PI * 2); ctx.fill();
  ridges.forEach((pts, i) => {
    ctx.fillStyle = i ? '#0e0820' : '#1a0f33';
    ctx.beginPath();
    ctx.moveTo(0, H * 0.53);
    for (const [x, y] of pts) ctx.lineTo(x, y);
    ctx.lineTo(W, H * 0.53);
    ctx.fill();
  });
  // Perspective floor
  const hy = H * 0.52, vx = W / 2;
  ctx.strokeStyle = 'rgba(150,100,255,0.10)';
  ctx.lineWidth = 1;
  for (let i = -12; i <= 12; i++) { ctx.beginPath(); ctx.moveTo(vx + i * 8, hy); ctx.lineTo(vx + i * W * 0.18, H); ctx.stroke(); }
  for (let i = 1; i < 12; i++) {
    const y = hy + (H - hy) * ((i / 12) ** 2 + ((G.t * 0.05) % (1 / 12)) * (i / 6));
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
  }
}

function drawFloorGlyph(eg, color) {
  ctx.save();
  ctx.translate(eg.x, eg.y + eg.s * 0.7);
  ctx.scale(1, 0.22);
  ctx.globalCompositeOperation = 'lighter';
  const R = eg.s * 0.75;
  const g = ctx.createRadialGradient(0, 0, 0, 0, 0, R);
  g.addColorStop(0, rgba(color, 0.35));
  g.addColorStop(1, rgba(color, 0));
  ctx.fillStyle = g;
  ctx.beginPath(); ctx.arc(0, 0, R, 0, Math.PI * 2); ctx.fill();
  ctx.rotate(G.t * 0.3);
  ctx.strokeStyle = rgba(color, 0.55);
  ctx.lineWidth = 3;
  ctx.beginPath(); ctx.arc(0, 0, R * 0.9, 0, Math.PI * 2); ctx.stroke();
  ctx.beginPath(); ctx.arc(0, 0, R * 0.7, 0, Math.PI * 2); ctx.stroke();
  for (let i = 0; i < 5; i++) {
    const a = (i / 5) * Math.PI * 2, b = ((i + 2) / 5) * Math.PI * 2;
    ctx.beginPath(); ctx.moveTo(Math.cos(a) * R * 0.7, Math.sin(a) * R * 0.7); ctx.lineTo(Math.cos(b) * R * 0.7, Math.sin(b) * R * 0.7); ctx.stroke();
  }
  ctx.restore();
}

function drawEffigy(eg, E) {
  ctx.save();
  ctx.translate(eg.x, eg.y);
  ctx.scale(eg.s, eg.s);
  ctx.fillStyle = '#4a3420';
  ctx.fillRect(-0.03, -0.3, 0.06, 1.0);
  ctx.fillRect(-0.38, -0.12, 0.76, 0.05);
  ctx.fillStyle = '#8a7048';
  ctx.beginPath(); ctx.ellipse(0, 0.1, 0.22, 0.32, 0, 0, Math.PI * 2); ctx.fill();
  ctx.beginPath(); ctx.arc(0, -0.38, 0.13, 0, Math.PI * 2); ctx.fill();
  ctx.strokeStyle = '#5a4428'; ctx.lineWidth = 0.012;
  for (let i = 0; i < 6; i++) { ctx.beginPath(); ctx.moveTo(-0.2, -0.1 + i * 0.07); ctx.lineTo(0.2, -0.08 + i * 0.07); ctx.stroke(); }
  for (const [r, c] of [[0.16, '#c23b3b'], [0.11, '#efe2c4'], [0.06, '#c23b3b']]) { ctx.fillStyle = c; ctx.beginPath(); ctx.arc(0, 0.08, r, 0, Math.PI * 2); ctx.fill(); }
  ctx.fillStyle = '#2a1a0c';
  ctx.fillRect(-0.06, -0.42, 0.03, 0.03); ctx.fillRect(0.03, -0.42, 0.03, 0.03);
  if (E.frozen > 0 || E.stasis > 0) { ctx.fillStyle = 'rgba(160,220,255,0.4)'; ctx.beginPath(); ctx.ellipse(0, 0, 0.32, 0.6, 0, 0, Math.PI * 2); ctx.fill(); }
  if (E.flash > 0) { ctx.fillStyle = `rgba(255,255,255,${E.flash * 0.7})`; ctx.beginPath(); ctx.ellipse(0, 0, 0.3, 0.6, 0, 0, Math.PI * 2); ctx.fill(); }
  ctx.restore();
}

function drawWard(eg, E) {
  if (E.ward <= 0) return;
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.translate(eg.x, eg.y + eg.s * 0.05);
  const a = Math.min(1, E.ward / 20);
  ctx.strokeStyle = rgba('#8c7dff', 0.5 + 0.3 * a);
  ctx.fillStyle = rgba('#6b5cff', 0.08 + 0.08 * a);
  ctx.lineWidth = 3;
  ctx.beginPath(); ctx.ellipse(0, 0, eg.s * 0.62, eg.s * 0.8, 0, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
  ctx.setLineDash([12, 10]);
  ctx.lineDashOffset = -G.t * 40;
  ctx.beginPath(); ctx.ellipse(0, 0, eg.s * 0.56, eg.s * 0.74, 0, 0, Math.PI * 2); ctx.stroke();
  ctx.restore();
}

function drawRock(r) {
  const R = r.r * Math.min(W, H) * (r.state === 'flying' ? lerp(1, 0.5, r.t / r.dur) : 1);
  ctx.save();
  ctx.globalAlpha = r.fade;
  ctx.translate(r.x, r.y);
  if (r.state === 'held') {
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    const g = ctx.createRadialGradient(0, 0, R * 0.5, 0, 0, R * 2);
    g.addColorStop(0, rgba(r.burning ? '#ff5a1f' : '#7dffb0', 0.5));
    g.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(0, 0, R * 2, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = rgba('#7dffb0', 0.6);
    ctx.lineWidth = 2;
    for (let i = 0; i < 2; i++) { ctx.beginPath(); ctx.ellipse(0, 0, R * 1.4, R * 0.35, G.t * 2 + i * 1.5, 0, Math.PI * 2); ctx.stroke(); }
    ctx.restore();
  } else if (r.state === 'rest') {
    ctx.fillStyle = 'rgba(0,0,0,0.35)';
    ctx.beginPath(); ctx.ellipse(0, R * 0.8, R * 1.1, R * 0.25, 0, 0, Math.PI * 2); ctx.fill();
  }
  ctx.rotate(r.a);
  const g = ctx.createLinearGradient(-R, -R, R, R);
  g.addColorStop(0, r.burning ? '#ff9a3c' : '#8a7a66');
  g.addColorStop(1, r.burning ? '#5a1a08' : '#2e261e');
  ctx.fillStyle = g;
  ctx.beginPath();
  r.shape.forEach((p, i) => { const x = Math.cos(p.a) * p.r * R, y = Math.sin(p.a) * p.r * R; i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
  ctx.closePath();
  ctx.fill();
  ctx.strokeStyle = r.state === 'held' ? '#7dffb0' : 'rgba(255,255,255,0.12)';
  ctx.lineWidth = 1.5;
  ctx.stroke();
  ctx.restore();
}

function drawProjectiles() {
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  for (const p of G.projs) {
    const s = p.size * 2.4;
    ctx.drawImage(fx.sprite(p.color), p.x - s, p.y - s, s * 2, s * 2);
    if (p.kind === 'slash') {
      const ang = Math.atan2(p.to.y - p.from.y, p.to.x - p.from.x);
      ctx.strokeStyle = '#fff';
      ctx.lineWidth = 4;
      ctx.beginPath(); ctx.arc(p.x, p.y, s * 0.8, ang - 1.2, ang + 1.2); ctx.stroke();
    }
  }
  for (const p of G.eprojs) {
    const S = ENEMY_SPELLS[p.id];
    const s = 12 + 60 * (p.k || 0) ** 1.5;
    ctx.drawImage(fx.sprite(S.color), p.x - s * 1.6, p.y - s * 1.6, s * 3.2, s * 3.2);
    if (S.pierce) {
      ctx.strokeStyle = 'rgba(255,200,210,0.9)';
      ctx.lineWidth = 3;
      for (let i = 0; i < 6; i++) {
        const a = G.t * 6 + (i / 6) * Math.PI * 2;
        ctx.beginPath(); ctx.moveTo(p.x + Math.cos(a) * s * 0.5, p.y + Math.sin(a) * s * 0.5); ctx.lineTo(p.x + Math.cos(a) * s * 1.2, p.y + Math.sin(a) * s * 1.2); ctx.stroke();
      }
    }
  }
  ctx.restore();
}

function hexPath(cx, cy, r) {
  ctx.moveTo(cx + r, cy);
  for (let i = 1; i <= 6; i++) ctx.lineTo(cx + r * Math.cos((i * Math.PI) / 3), cy + r * Math.sin((i * Math.PI) / 3));
}

function drawPlayerShield(P) {
  if (P.shield <= 0) return;
  const flicker = P.shield < 0.8 ? (Math.sin(G.t * 40) > 0 ? 1 : 0.4) : 1;
  const a = Math.min(1, P.shield / 0.3) * flicker;
  const color = P.reflect ? '#fff3b0' : '#5ee7ff';
  const cx = W / 2, cy = H * 1.1, rx = W * 0.5, ry = H * 0.48;
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.beginPath(); ctx.ellipse(cx, cy, rx, ry, 0, Math.PI, 0); ctx.closePath();
  const g = ctx.createRadialGradient(cx, cy, ry * 0.5, cx, cy, rx);
  g.addColorStop(0, rgba(color, 0));
  g.addColorStop(1, rgba(color, 0.18 * a));
  ctx.fillStyle = g;
  ctx.fill();
  ctx.save();
  ctx.clip();
  ctx.strokeStyle = rgba(color, 0.12 * a);
  ctx.lineWidth = 1.5;
  const hr = 34;
  ctx.beginPath();
  for (let y = cy - ry; y < H; y += hr * 1.5) for (let x = cx - rx, j = 0; x < cx + rx; x += hr * Math.sqrt(3), j++) hexPath(x + (Math.round((y - cy + ry) / (hr * 1.5)) % 2 ? hr * 0.87 : 0), y, hr);
  ctx.stroke();
  ctx.restore();
  ctx.strokeStyle = rgba(color, 0.8 * a);
  ctx.lineWidth = 4;
  ctx.beginPath(); ctx.ellipse(cx, cy, rx, ry, 0, Math.PI, 0); ctx.stroke();
  ctx.restore();
}

function drawDarkness(P, eg) {
  const k = Math.min(1, P.dark / 0.6);
  dctx.globalCompositeOperation = 'source-over';
  dctx.clearRect(0, 0, W, H);
  dctx.fillStyle = 'rgba(2,0,8,0.93)';
  dctx.fillRect(0, 0, W, H);
  dctx.globalCompositeOperation = 'destination-out';
  const hole = (x, y, r) => {
    const g = dctx.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, 'rgba(0,0,0,1)');
    g.addColorStop(1, 'rgba(0,0,0,0)');
    dctx.fillStyle = g;
    dctx.fillRect(x - r, y - r, r * 2, r * 2);
  };
  if (wand.visible) hole(wand.x, wand.y, 170);
  hole(eg.x, eg.y - eg.s * 0.4, 70);
  for (const p of G.projs) hole(p.x, p.y, 90);
  for (const p of G.eprojs) hole(p.x, p.y, 60 + 60 * (p.k || 0));
  ctx.globalAlpha = k;
  ctx.drawImage(darkCanvas, 0, 0, W, H);
  ctx.globalAlpha = 1;
}

function strokePath(pts, color, width, alpha) {
  if (pts.length < 2) return;
  ctx.beginPath();
  ctx.moveTo(pts[0].x, pts[0].y);
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
  ctx.strokeStyle = rgba(color, alpha);
  ctx.lineWidth = width;
  ctx.stroke();
}

function drawStrokes() {
  ctx.save();
  ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  ctx.globalCompositeOperation = 'lighter';
  for (const g of G.ghosts) {
    const a = 1 - g.t / g.dur;
    strokePath(g.pts, g.color, 18 + 20 * (1 - a), 0.18 * a);
    strokePath(g.pts, g.color, 5, 0.9 * a);
  }
  if (G.stroke) {
    strokePath(G.stroke.pts, '#ffd98a', 14, 0.22);
    strokePath(G.stroke.pts, '#ffd98a', 5, 0.95);
    strokePath(G.stroke.pts, '#ffffff', 1.6, 1);
  }
  if (wand.visible) {
    const tr = wand.trail;
    for (let i = 1; i < tr.length; i++) {
      const a = i / tr.length;
      ctx.strokeStyle = rgba('#bfe8ff', a * 0.5);
      ctx.lineWidth = a * 6;
      ctx.beginPath(); ctx.moveTo(tr[i - 1].x, tr[i - 1].y); ctx.lineTo(tr[i].x, tr[i].y); ctx.stroke();
    }
    const c = G.stroke ? '#ffd98a' : '#bfe8ff';
    const s = G.stroke ? 26 : 18 + Math.sin(G.t * 6) * 3;
    ctx.drawImage(fx.sprite(c), wand.x - s, wand.y - s, s * 2, s * 2);
  }
  ctx.restore();
}

function drawPip() {
  if (!tracker.ready || !settings.cam) return;
  const pw = pip.width, ph = pip.height;
  pctx.save();
  pctx.translate(pw, 0);
  pctx.scale(-1, 1);
  if (video.readyState >= 2) pctx.drawImage(video, 0, 0, pw, ph);
  pctx.restore();
  pctx.fillStyle = 'rgba(10,5,25,0.35)';
  pctx.fillRect(0, 0, pw, ph);
  const r = wand.hand;
  let label = 'no hand';
  if (r?.present) {
    const L = r.lm.map((p) => [(1 - p.x) * pw, p.y * ph]);
    const drawing = !!G.stroke;
    pctx.strokeStyle = drawing ? '#ffd98a' : '#7dd8ff';
    pctx.lineWidth = 2;
    pctx.beginPath();
    for (const [a, b] of HAND_EDGES) { pctx.moveTo(L[a][0], L[a][1]); pctx.lineTo(L[b][0], L[b][1]); }
    pctx.stroke();
    pctx.fillStyle = '#fff';
    pctx.beginPath(); pctx.arc(L[8][0], L[8][1], 4, 0, Math.PI * 2); pctx.fill();
    label = drawing ? '✦ casting' : r.pinch ? 'pinch' : r.pose;
  }
  $('#pose-label').textContent = label;
}

function render() {
  ctx.save();
  const sh = fx.shake;
  if (sh > 0.5) ctx.translate(rand(-sh, sh), rand(-sh, sh));
  drawBackground();
  const inGame = G.mode === 'duel' || G.mode === 'practice' || (G.mode === 'over' && G.enemy);
  const eg = enemyGeom();
  if (inGame && G.enemy) {
    const E = G.enemy;
    const glyphColor = E.dummy ? '#7dffb0' : ['#c070ff', '#c070ff', '#ff3b8a', '#ff2a2a'][E.phase];
    drawFloorGlyph(eg, glyphColor);
    for (const r of G.rocks) if (r.state === 'rest') drawRock(r);
    if (E.dummy) drawEffigy(eg, E); else E.draw(ctx, eg.x, eg.y, eg.s);
    drawWard(eg, E);
    drawProjectiles();
  } else if (G.mode === 'forge') {
    ctx.save();
    ctx.textAlign = 'center';
    ctx.fillStyle = 'rgba(230,220,255,0.35)';
    ctx.font = '600 22px Cinzel, serif';
    const cx = Math.max(200, (W - 380) / 2);
    ctx.fillText('Draw your new form here', cx, H * 0.18);
    ctx.strokeStyle = 'rgba(200,180,255,0.15)';
    ctx.setLineDash([10, 10]);
    const bw = Math.min(W - 420, 560), bh = Math.min(H * 0.6, 460);
    ctx.strokeRect(cx - bw / 2, H * 0.22, bw, bh);
    ctx.restore();
  }
  fx.draw(ctx);
  if (inGame) {
    for (const r of G.rocks) if (r.state === 'held' || r.state === 'flying') drawRock(r);
    if (G.player) drawPlayerShield(G.player);
    if (G.player?.dark > 0) drawDarkness(G.player, eg);
  }
  drawStrokes();
  ctx.restore();
  fx.drawFlashes(ctx, W, H);
  drawPip();
}

// ---------------------------------------------------------------- loop
let last = performance.now();
function frame(now) {
  const dt = Math.min(0.05, (now - last) / 1000);
  last = now;
  update(dt, now);
  render();
  requestAnimationFrame(frame);
}

renderSpellbook();
showScreen('#menu');
requestAnimationFrame(frame);

// Exposed for debugging from the console.
window.wander = { G, recognizer, castSpell, specFor, settings };
