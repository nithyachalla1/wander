// The Hollow Archon: an adaptive duelist. It keeps a running model of the
// player (spell frequencies, a Markov chain of spell transitions, block and
// interrupt reflexes) and scores its options against that model each turn.

import { SPELLS } from './spells.js';
import { rgba } from './fx.js';

export const ENEMY_SPELLS = {
  hex: { name: 'Hex Bolt', color: '#b04cff', charge: 1.1, dmg: 9, kind: 'proj' },
  breaker: { name: 'Wardbreaker', color: '#ff3355', charge: 1.7, dmg: 14, kind: 'proj', pierce: true },
  veil: { name: 'Umbral Veil', color: '#5a3aa6', charge: 1.4, kind: 'curse' },
  ward: { name: 'Obsidian Ward', color: '#6b5cff', charge: 0.9, kind: 'self', absorb: 24 },
  mend: { name: 'Hollow Mend', color: '#4dffa6', charge: 2.0, kind: 'self', heal: 24 },
  barrage: { name: 'Hollow Barrage', color: '#d14cff', charge: 1.6, dmg: 7, kind: 'multi', count: 3 },
};

export const DIFF = [
  { name: 'Apprentice', hp: 120, idle: [2.6, 3.8], charge: 1.3, temp: 1.1, dmg: 0.7 },
  { name: 'Adept', hp: 170, idle: [1.7, 2.7], charge: 1.0, temp: 0.55, dmg: 1 },
  { name: 'Archmage', hp: 230, idle: [1.0, 1.8], charge: 0.8, temp: 0.28, dmg: 1.3 },
];

const rand = (a, b) => a + Math.random() * (b - a);
const pct = (v) => `${Math.round(v * 100)}%`;

export class PlayerModel {
  constructor(prior) {
    this.freq = {};
    this.trans = {};
    this.last = null;
    this.faced = 0;
    this.blocked = 0;
    this.casts = 0;
    this.interrupts = 0;
    if (prior) for (const k in prior) this.freq[k] = prior[k] * 0.5;
  }
  record(id) {
    for (const k in this.freq) this.freq[k] *= 0.88;
    this.freq[id] = (this.freq[id] || 0) + 1;
    if (this.last) {
      const row = this.trans[this.last] || (this.trans[this.last] = {});
      row[id] = (row[id] || 0) + 1;
    }
    this.last = id;
  }
  sum(o = this.freq) { let s = 0; for (const k in o) s += o[k]; return s; }
  share(id) { const s = this.sum(); return s ? (this.freq[id] || 0) / s : 0; }
  favorite() {
    let best = null, v = 0;
    for (const k in this.freq) if (this.freq[k] > v) { v = this.freq[k]; best = k; }
    return best ? { id: best, p: this.share(best) } : null;
  }
  predict() {
    const row = this.last && this.trans[this.last];
    const src = row && this.sum(row) >= 2 ? row : this.freq;
    const s = this.sum(src);
    if (!s) return null;
    let best = null, v = 0;
    for (const k in src) if (src[k] > v) { v = src[k]; best = k; }
    return { id: best, p: v / s, markov: src === row };
  }
  blockRate() { return (this.blocked + 1) / (this.faced + 3); }
  interruptRate() { return (this.interrupts + 0.3) / (this.casts + 2); }
  style() {
    const s = (id) => this.share(id);
    return {
      aggression: Math.min(1, s('fulmina') + s('inferna') + 0.5 * s('glacia') + 0.5 * s('ascenda')),
      defense: Math.min(1, s('aegis') + 0.5 * s('solaris')),
      control: Math.min(1, s('dissolvo') + 0.5 * s('glacia') + 0.5 * s('ascenda')),
      reflex: this.blockRate(),
    };
  }
  snapshot() { return { ...this.freq }; }
}

export class Enemy {
  constructor(diff, model, { dummy = false } = {}) {
    this.d = DIFF[diff];
    this.model = model;
    this.dummy = dummy;
    this.maxHp = dummy ? 300 : this.d.hp;
    this.hp = this.maxHp;
    this.ward = 0;
    this.frozen = 0;
    this.burn = 0;
    this.disarmed = 0;
    this.stasis = 0;
    this.blind = false;
    this.cast = null;
    this.cool = 2.2;
    this.phase = 1;
    this.last = null;
    this.mendCd = 0;
    this.thoughts = [];
    this.flash = 0;
    this.t = 0;
    this.sinceHit = 0;
    this.dead = false;
    this.deadT = 0;
    this.staff = { x: 0, y: 0, a: 0, vx: 0, vy: 0, va: 0, away: false };
    this.options = [];
  }

  get hpFrac() { return Math.max(0, this.hp / this.maxHp); }

  think(str) {
    this.thoughts.unshift(str);
    if (this.thoughts.length > 5) this.thoughts.length = 5;
  }

  disarm(t) {
    this.cast = null;
    this.disarmed = Math.max(this.disarmed, t);
    const dir = Math.random() < 0.5 ? -1 : 1;
    Object.assign(this.staff, { x: 0.36, y: 0, a: 0, vx: dir * rand(1.2, 1.8), vy: -2.2, va: dir * 14, away: true });
  }

  interrupt() {
    if (!this.cast) return false;
    this.model.interrupts++;
    this.disarm(2.6);
    return true;
  }

  update(dt, g) {
    this.t += dt;
    this.flash = Math.max(0, this.flash - dt * 4);
    this.sinceHit += dt;
    if (this.dead) { this.deadT += dt; return; }

    if (this.burn > 0) { this.burn -= dt; g.damageEnemy(4 * dt, { dot: true }); }
    this.frozen = Math.max(0, this.frozen - dt);
    this.mendCd -= dt;

    // Flung staff physics (unit space relative to the enemy).
    const st = this.staff;
    if (st.away) {
      if (this.disarmed > 0) {
        st.vy += 6 * dt; st.x += st.vx * dt; st.y += st.vy * dt; st.a += st.va * dt;
        if (st.y > 0.55) { st.y = 0.55; st.vy *= -0.3; st.vx *= 0.6; st.va *= 0.5; }
      } else {
        st.x += (0.36 - st.x) * Math.min(1, dt * 8); st.y += (0 - st.y) * Math.min(1, dt * 8); st.a *= Math.exp(-dt * 10);
        if (Math.abs(st.x - 0.36) < 0.01 && Math.abs(st.y) < 0.01) st.away = false;
      }
    }

    if (!this.dummy) {
      const ph = this.hpFrac > 0.66 ? 1 : this.hpFrac > 0.33 ? 2 : 3;
      if (ph > this.phase) { this.phase = ph; g.onEnemyPhase(ph); }
    }

    if (this.stasis > 0) { this.stasis -= dt; return; }
    if (this.disarmed > 0) { this.disarmed -= dt; return; }

    if (this.dummy) {
      if (this.sinceHit > 3) this.hp = Math.min(this.maxHp, this.hp + 60 * dt);
      return;
    }

    if (g.introT > 0) return;
    const speed = (this.frozen > 0 ? 0.5 : 1) * (1 + (this.phase - 1) * 0.12);
    if (this.cast) {
      this.cast.t += dt * speed;
      if (this.cast.t >= this.cast.dur) {
        const id = this.cast.id;
        this.cast = null;
        this.last = id;
        g.enemyRelease(id);
        this.cool = rand(...this.d.idle) / (1 + (this.phase - 1) * 0.18);
      }
      return;
    }
    this.cool -= dt * speed;
    if (this.cool <= 0) this.decide(g);
  }

  decide(g) {
    const m = this.model, st = m.style(), br = m.blockRate(), ir = m.interruptRate(), pred = m.predict();
    const p = g.player;
    const fav = m.favorite();
    const opts = [];
    const add = (id, u, why) => opts.push({ id, u: Math.max(0.02, u), why });
    const predName = pred ? SPELLS[pred.id]?.name : null;

    add('hex', 1.0 - 0.7 * br + (p.shield <= 0 ? 0.25 : 0),
      br > 0.55 ? `Your ward rises ${pct(br)} of the time… a feint, then.` : 'Testing your guard.');
    add('breaker', 0.3 + 1.5 * br + (p.shield > 0 ? 1.3 : 0) + 0.6 * st.defense,
      p.shield > 0 ? 'A ward? It will shatter.' : `You block ${pct(br)} of my hexes. Wardbreaker.`);
    if (this.ward <= 0) {
      const predAtk = pred && (pred.id === 'fulmina' || pred.id === 'inferna') ? 0.9 * pred.p : 0;
      add('ward', 0.25 + 1.6 * st.aggression + predAtk - 0.9 * m.share('dissolvo'),
        predAtk > 0.3 ? `I foresee ${predName} (${pct(pred.p)}). Obsidian Ward.`
          : `You lean on offense (${pct(st.aggression)}). Raising my ward.`);
    }
    if (p.dark <= 0) {
      add('veil', 0.35 + (1 - m.share('solaris')) * 0.55,
        m.share('solaris') < 0.08 ? 'You rarely call the light. Darkness.' : 'Let the Veil fall.');
    }
    if (this.phase >= 2 && this.hpFrac < 0.6 && this.mendCd <= 0) {
      add('mend', 0.5 + (1 - this.hpFrac) * 1.6 - ir * 1.4,
        ir < 0.25 ? `You seldom interrupt (${pct(ir)}). I will mend.` : 'Wounded… I must mend.');
    }
    if (this.phase >= 3) add('barrage', 0.9 + 0.6 * (1 - br), 'Enough. Hollow Barrage!');

    for (const o of opts) if (o.id === this.last) o.u *= 0.55;
    const T = this.d.temp;
    const mx = Math.max(...opts.map((o) => o.u));
    const ws = opts.map((o) => Math.exp((o.u - mx) / T));
    const total = ws.reduce((a, b) => a + b, 0);
    let r = Math.random() * total, pick = opts[0];
    for (let i = 0; i < opts.length; i++) { r -= ws[i]; if (r <= 0) { pick = opts[i]; break; } }
    this.options = opts.map((o, i) => ({ id: o.id, p: ws[i] / total })).sort((a, b) => b.p - a.p);

    if (pick.id === 'mend') this.mendCd = 12;
    this.cast = { id: pick.id, t: 0, dur: ENEMY_SPELLS[pick.id].charge * this.d.charge };
    m.casts++;
    this.think(pick.why);
    if (fav && m.sum() > 4 && Math.random() < 0.15) this.think(`(${SPELLS[fav.id]?.name ?? fav.id}: ${pct(fav.p)} of your casts.)`);
    g.onEnemyCastStart(pick.id, this.cast.dur);
  }

  // Drawn in unit space: figure is ~1.3 units tall, centered on the chest.
  draw(ctx, x, y, s) {
    const t = this.t;
    const bob = Math.sin(t * 1.6) * 0.03;
    ctx.save();
    ctx.translate(x, y + bob * s);
    ctx.scale(s, s);
    if (this.dead) {
      const k = Math.min(1, this.deadT / 1.5);
      ctx.globalAlpha = 1 - k;
      ctx.translate(0, k * 0.3);
      ctx.scale(1 + k * 0.3, 1 - k * 0.5);
    }

    const eye = this.phase === 1 ? '#c070ff' : this.phase === 2 ? '#ff3b8a' : '#ff2a2a';

    // Aura
    const aura = ctx.createRadialGradient(0, 0, 0.05, 0, 0, 0.9);
    aura.addColorStop(0, rgba(eye, 0.22 + 0.08 * Math.sin(t * 3)));
    aura.addColorStop(1, rgba(eye, 0));
    ctx.fillStyle = aura;
    ctx.beginPath(); ctx.arc(0, 0, 0.9, 0, Math.PI * 2); ctx.fill();

    // Robe
    const robe = () => {
      ctx.beginPath();
      ctx.moveTo(0, -0.64);
      ctx.bezierCurveTo(0.22, -0.62, 0.27, -0.36, 0.2, -0.22);
      ctx.bezierCurveTo(0.36, -0.1, 0.42, 0.3, 0.46, 0.62);
      for (let i = 0; i <= 8; i++) {
        const hx = 0.46 - i * 0.115;
        const hy = 0.62 + (i % 2 ? 0.07 : 0) + Math.sin(t * 3 + i) * 0.02;
        ctx.lineTo(hx, hy);
      }
      ctx.bezierCurveTo(-0.42, 0.3, -0.36, -0.1, -0.2, -0.22);
      ctx.bezierCurveTo(-0.27, -0.36, -0.22, -0.62, 0, -0.64);
      ctx.closePath();
    };
    const g = ctx.createLinearGradient(0, -0.64, 0, 0.7);
    g.addColorStop(0, '#2c1c4a');
    g.addColorStop(0.6, '#140b26');
    g.addColorStop(1, '#05030b');
    ctx.fillStyle = g;
    robe(); ctx.fill();
    ctx.strokeStyle = rgba(eye, 0.35);
    ctx.lineWidth = 0.012;
    ctx.stroke();

    // Rune trim
    ctx.strokeStyle = rgba(eye, 0.5);
    ctx.lineWidth = 0.008;
    ctx.beginPath(); ctx.moveTo(0, -0.18); ctx.lineTo(0, 0.6); ctx.stroke();
    for (let i = 0; i < 4; i++) {
      const yy = -0.05 + i * 0.16;
      ctx.beginPath(); ctx.moveTo(-0.03, yy); ctx.lineTo(0, yy + 0.04); ctx.lineTo(0.03, yy); ctx.stroke();
    }

    // Face void + eyes
    ctx.fillStyle = '#000';
    ctx.beginPath(); ctx.ellipse(0, -0.38, 0.115, 0.14, 0, 0, Math.PI * 2); ctx.fill();
    if (!this.blind) {
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      const blink = Math.sin(t * 0.7) > 0.97 ? 0.2 : 1;
      for (const ex of [-0.045, 0.045]) {
        const eg = ctx.createRadialGradient(ex, -0.4, 0, ex, -0.4, 0.06);
        eg.addColorStop(0, '#fff');
        eg.addColorStop(0.3, eye);
        eg.addColorStop(1, rgba(eye, 0));
        ctx.fillStyle = eg;
        ctx.beginPath(); ctx.ellipse(ex, -0.4, 0.06, 0.06 * blink, 0, 0, Math.PI * 2); ctx.fill();
      }
      ctx.restore();
    }

    // Sleeves / hands
    ctx.fillStyle = '#1c1233';
    ctx.beginPath(); ctx.ellipse(0.3, 0.02, 0.07, 0.05, -0.4, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.ellipse(-0.3, 0.02, 0.07, 0.05, 0.4, 0, Math.PI * 2); ctx.fill();

    // Staff
    const st = this.staff;
    ctx.save();
    ctx.translate(st.away ? st.x : 0.36, st.away ? st.y : 0);
    ctx.rotate(st.away ? st.a : 0);
    ctx.strokeStyle = '#3b2a1a';
    ctx.lineWidth = 0.03;
    ctx.lineCap = 'round';
    ctx.beginPath(); ctx.moveTo(0, -0.42); ctx.lineTo(0, 0.6); ctx.stroke();
    ctx.strokeStyle = '#6a4a2a';
    ctx.lineWidth = 0.012;
    ctx.beginPath(); ctx.moveTo(-0.06, -0.48); ctx.quadraticCurveTo(0, -0.4, 0.06, -0.48); ctx.stroke();
    ctx.globalCompositeOperation = 'lighter';
    const orbC = this.cast ? ENEMY_SPELLS[this.cast.id].color : eye;
    const og = ctx.createRadialGradient(0, -0.5, 0, 0, -0.5, 0.12);
    og.addColorStop(0, '#fff');
    og.addColorStop(0.3, orbC);
    og.addColorStop(1, rgba(orbC, 0));
    ctx.fillStyle = og;
    ctx.beginPath(); ctx.arc(0, -0.5, 0.12, 0, Math.PI * 2); ctx.fill();
    ctx.restore();

    // Casting rune circle
    if (this.cast) {
      const k = this.cast.t / this.cast.dur;
      const sp = ENEMY_SPELLS[this.cast.id];
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      ctx.translate(0, 0.08);
      ctx.scale(1, 0.45);
      ctx.rotate(t * 2);
      const R = 0.25 + 0.3 * k;
      ctx.strokeStyle = rgba(sp.color, 0.4 + 0.6 * k);
      ctx.lineWidth = 0.015;
      ctx.beginPath(); ctx.arc(0, 0, R, 0, Math.PI * 2); ctx.stroke();
      ctx.beginPath(); ctx.arc(0, 0, R * 0.75, 0, Math.PI * 2 * k); ctx.stroke();
      for (let i = 0; i < 6; i++) {
        const a = (i / 6) * Math.PI * 2;
        ctx.beginPath();
        ctx.moveTo(Math.cos(a) * R, Math.sin(a) * R);
        ctx.lineTo(Math.cos(a + 2.1) * R, Math.sin(a + 2.1) * R);
        ctx.stroke();
      }
      ctx.restore();
    }

    // Status overlays
    if (this.frozen > 0 || this.stasis > 0) {
      ctx.save();
      ctx.globalAlpha = this.stasis > 0 ? 0.55 : 0.35;
      ctx.fillStyle = '#9fd8ff';
      robe(); ctx.fill();
      ctx.globalAlpha = 0.8;
      ctx.strokeStyle = '#e6f7ff';
      ctx.lineWidth = 0.01;
      const n = this.stasis > 0 ? 12 : 6;
      for (let i = 0; i < n; i++) {
        const a = (i / n) * Math.PI * 2;
        const cx = Math.cos(a) * 0.42, cy = 0.05 + Math.sin(a) * 0.55;
        ctx.beginPath(); ctx.moveTo(cx, cy - 0.08); ctx.lineTo(cx + 0.035, cy); ctx.lineTo(cx, cy + 0.08); ctx.lineTo(cx - 0.035, cy); ctx.closePath(); ctx.stroke();
      }
      if (this.stasis > 0) {
        ctx.strokeStyle = 'rgba(168,240,255,0.7)';
        ctx.lineWidth = 0.015;
        ctx.strokeRect(-0.55, -0.75, 1.1, 1.5);
      }
      ctx.restore();
    }
    if (this.flash > 0) {
      ctx.save();
      ctx.globalAlpha = this.flash;
      ctx.fillStyle = '#fff';
      robe(); ctx.fill();
      ctx.restore();
    }
    ctx.restore();
  }
}
