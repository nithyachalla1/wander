// Continuous incantation listener on the Web Speech API with fuzzy matching,
// since invented words come back from the recognizer as near-miss English.

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

const norm = (s) => s.toLowerCase().replace(/[^a-z ]/g, ' ').replace(/\s+/g, ' ').trim();

function lev(a, b) {
  const m = a.length, n = b.length;
  if (!m) return n;
  if (!n) return m;
  let prev = Array.from({ length: n + 1 }, (_, j) => j);
  for (let i = 1; i <= m; i++) {
    const cur = [i];
    for (let j = 1; j <= n; j++) {
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    }
    prev = cur;
  }
  return prev[n];
}

// Returns the character index of a match in the text, or -1.
function findKey(text, key) {
  const spaced = ` ${text} `;
  const exact = spaced.indexOf(` ${key} `);
  if (exact >= 0) return exact;
  const tc = text.replace(/ /g, ''), kc = key.replace(/ /g, '');
  if (kc.length < 5) return -1;
  const tol = Math.floor(kc.length * 0.28);
  for (let len = kc.length - 1; len <= kc.length + 1; len++) {
    for (let s = 0; s + len <= tc.length; s++) {
      if (lev(tc.substr(s, len), kc) <= tol) return s;
    }
  }
  return -1;
}

export class Voice {
  constructor({ onSpell, onHeard, onState }) {
    Object.assign(this, { onSpell, onHeard, onState });
    this.supported = !!SR;
    this.entries = [];
    this.fired = new Set();
    this.want = false;
    this.session = 0;
    this.rec = null;
  }

  setEntries(list) {
    this.entries = list.map((e) => ({ id: e.id, keys: [e.incant, ...(e.aliases || [])].map(norm).filter(Boolean) }));
  }

  start() {
    if (!this.supported) { this.onState?.('unsupported'); return false; }
    if (this.want) return true;
    this.want = true;
    this.spawn();
    return true;
  }

  stop() {
    this.want = false;
    try { this.rec?.stop(); } catch {}
  }

  spawn() {
    const r = new SR();
    r.continuous = true;
    r.interimResults = true;
    r.lang = 'en-US';
    r.maxAlternatives = 3;
    const session = ++this.session;
    r.onresult = (ev) => {
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const res = ev.results[i];
        const alts = Array.from(res).map((a) => norm(a.transcript));
        this.onHeard?.(alts[0], res.isFinal);
        const hits = [];
        for (const e of this.entries) {
          const key = `${session}:${i}:${e.id}`;
          if (this.fired.has(key)) continue;
          let pos = -1;
          for (const text of alts) {
            for (const k of e.keys) { pos = findKey(text, k); if (pos >= 0) break; }
            if (pos >= 0) break;
          }
          if (pos >= 0) { this.fired.add(key); hits.push({ id: e.id, pos, text: alts[0] }); }
        }
        hits.sort((a, b) => a.pos - b.pos).forEach((h) => this.onSpell(h.id, h.text));
      }
    };
    r.onerror = (e) => {
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') { this.want = false; this.onState?.('denied'); }
    };
    r.onend = () => {
      if (this.want) setTimeout(() => this.want && this.spawn(), 200);
      else this.onState?.('off');
    };
    this.rec = r;
    try { r.start(); this.onState?.('listening'); } catch {}
  }
}
