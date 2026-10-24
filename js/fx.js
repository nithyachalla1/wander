// Canvas 2D effects: additive glow particles, procedural lightning, rings,
// floating combat text, screen flashes and shake.

export function hexRgb(h) {
  const n = parseInt(h.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
export const rgba = (h, a) => { const [r, g, b] = hexRgb(h); return `rgba(${r},${g},${b},${a})`; };

function jag(x1, y1, x2, y2, disp, depth, out) {
  if (depth === 0) { out.push([x2, y2]); return; }
  const mx = (x1 + x2) / 2 + (Math.random() - 0.5) * disp;
  const my = (y1 + y2) / 2 + (Math.random() - 0.5) * disp;
  jag(x1, y1, mx, my, disp / 2, depth - 1, out);
  jag(mx, my, x2, y2, disp / 2, depth - 1, out);
}

export class FX {
  constructor() {
    this.parts = [];
    this.rings = [];
    this.bolts = [];
    this.texts = [];
    this.flashes = [];
    this.shake = 0;
    this.cache = new Map();
  }

  sprite(color) {
    let s = this.cache.get(color);
    if (s) return s;
    s = document.createElement('canvas');
    s.width = s.height = 64;
    const g = s.getContext('2d');
    const gr = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    gr.addColorStop(0, 'rgba(255,255,255,1)');
    gr.addColorStop(0.2, rgba(color, 0.95));
    gr.addColorStop(0.5, rgba(color, 0.3));
    gr.addColorStop(1, rgba(color, 0));
    g.fillStyle = gr;
    g.fillRect(0, 0, 64, 64);
    this.cache.set(color, s);
    return s;
  }

  spawn(p) {
    if (this.parts.length > 2600) this.parts.shift();
    const q = Object.assign({ vx: 0, vy: 0, life: 1, size: 10, drag: 2, grav: 0, color: '#ffffff', grow: 0 }, p);
    q.max = q.life;
    this.parts.push(q);
  }

  burst(x, y, color, n = 30, speed = 300, o = {}) {
    for (let i = 0; i < n; i++) {
      const a = o.angle !== undefined ? o.angle + (Math.random() - 0.5) * (o.spread ?? 1) : Math.random() * Math.PI * 2;
      const s = speed * (0.25 + Math.random() * 0.75);
      this.spawn({
        x, y, vx: Math.cos(a) * s, vy: Math.sin(a) * s,
        life: (o.life ?? 0.7) * (0.6 + Math.random() * 0.7),
        size: (o.size ?? 10) * (0.5 + Math.random()),
        color: Array.isArray(color) ? color[(Math.random() * color.length) | 0] : color,
        drag: o.drag ?? 2.5, grav: o.grav ?? 0, grow: o.grow ?? 0,
      });
    }
  }

  ring(x, y, color, r0, r1, dur = 0.5, width = 4) { this.rings.push({ x, y, color, r0, r1, dur, width, t: 0 }); }
  lightning(x1, y1, x2, y2, color, dur = 0.3, width = 3) { this.bolts.push({ x1, y1, x2, y2, color, dur, width, t: 0 }); }
  text(x, y, str, color = '#fff', size = 28, dur = 1.1) { this.texts.push({ x, y, str, color, size, dur, t: 0 }); }
  flash(color, a = 0.4, dur = 0.3) { this.flashes.push({ color, a, dur, t: 0 }); }
  addShake(a) { this.shake = Math.min(30, this.shake + a); }

  update(dt) {
    for (const p of this.parts) {
      const k = Math.exp(-p.drag * dt);
      p.vx *= k; p.vy = p.vy * k + p.grav * dt;
      p.x += p.vx * dt; p.y += p.vy * dt;
      p.size += p.grow * dt;
      p.life -= dt;
    }
    this.parts = this.parts.filter((p) => p.life > 0 && p.size > 0.3);
    for (const arr of [this.rings, this.bolts, this.texts, this.flashes]) for (const o of arr) o.t += dt;
    this.rings = this.rings.filter((o) => o.t < o.dur);
    this.bolts = this.bolts.filter((o) => o.t < o.dur);
    this.texts = this.texts.filter((o) => o.t < o.dur);
    this.flashes = this.flashes.filter((o) => o.t < o.dur);
    this.shake *= Math.exp(-8 * dt);
  }

  draw(ctx) {
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    for (const p of this.parts) {
      const a = Math.max(0, p.life / p.max);
      ctx.globalAlpha = a;
      const s = p.size * (0.6 + 0.4 * a);
      ctx.drawImage(this.sprite(p.color), p.x - s, p.y - s, s * 2, s * 2);
    }
    ctx.globalAlpha = 1;
    for (const r of this.rings) {
      const k = r.t / r.dur, e = 1 - (1 - k) ** 3;
      ctx.strokeStyle = rgba(r.color, 1 - k);
      ctx.lineWidth = r.width * (1 - k * 0.6);
      ctx.beginPath();
      ctx.arc(r.x, r.y, r.r0 + (r.r1 - r.r0) * e, 0, Math.PI * 2);
      ctx.stroke();
    }
    for (const b of this.bolts) {
      const a = 1 - b.t / b.dur;
      const len = Math.hypot(b.x2 - b.x1, b.y2 - b.y1);
      const pts = [[b.x1, b.y1]];
      jag(b.x1, b.y1, b.x2, b.y2, len * 0.22, 6, pts);
      const path = () => { ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]); for (const p of pts) ctx.lineTo(p[0], p[1]); };
      ctx.lineCap = 'round'; ctx.lineJoin = 'round';
      path(); ctx.strokeStyle = rgba(b.color, 0.25 * a); ctx.lineWidth = b.width * 6; ctx.stroke();
      path(); ctx.strokeStyle = rgba(b.color, 0.8 * a); ctx.lineWidth = b.width * 2; ctx.stroke();
      path(); ctx.strokeStyle = `rgba(255,255,255,${a})`; ctx.lineWidth = b.width * 0.7; ctx.stroke();
      for (let i = 0; i < 2; i++) {
        const p = pts[(Math.random() * pts.length) | 0];
        const ang = Math.random() * Math.PI * 2, l = len * (0.08 + Math.random() * 0.15);
        const bp = [[p[0], p[1]]];
        jag(p[0], p[1], p[0] + Math.cos(ang) * l, p[1] + Math.sin(ang) * l, l * 0.4, 3, bp);
        ctx.beginPath(); ctx.moveTo(bp[0][0], bp[0][1]); for (const q of bp) ctx.lineTo(q[0], q[1]);
        ctx.strokeStyle = rgba(b.color, 0.6 * a); ctx.lineWidth = b.width * 0.8; ctx.stroke();
      }
    }
    ctx.restore();

    ctx.save();
    ctx.textAlign = 'center';
    for (const t of this.texts) {
      const k = t.t / t.dur;
      const pop = k < 0.12 ? 0.6 + (k / 0.12) * 0.6 : 1.2 - Math.min(0.2, (k - 0.12) * 0.5);
      ctx.globalAlpha = k > 0.7 ? (1 - k) / 0.3 : 1;
      ctx.font = `700 ${t.size * pop}px Cinzel, serif`;
      const y = t.y - k * 50;
      ctx.lineWidth = 4;
      ctx.strokeStyle = 'rgba(0,0,0,0.7)';
      ctx.strokeText(t.str, t.x, y);
      ctx.fillStyle = t.color;
      ctx.fillText(t.str, t.x, y);
    }
    ctx.restore();
  }

  drawFlashes(ctx, W, H) {
    for (const f of this.flashes) {
      ctx.fillStyle = rgba(f.color, f.a * (1 - f.t / f.dur));
      ctx.fillRect(0, 0, W, H);
    }
  }
}
