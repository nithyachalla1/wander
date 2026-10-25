"""Gemini-powered bot strategist.

One REST call every few seconds plans for *all* bots at once (keeps us well
under free-tier rate limits): who to focus, what style to play, which fusion
to set up, and a short in-character taunt. Bots' real-time tactics follow
the plan. With no API key, or on any error, bots just use built-in tactics.

Key: GEMINI_API_KEY / GOOGLE_API_KEY env var, or ~/.wander/gemini_key.txt.
Optional: GEMINI_MODEL to pin a model."""

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

BASE = 'https://generativelanguage.googleapis.com/v1beta'
API = BASE + '/models/{model}:generateContent'
# Real-time play needs sub-second replies: Flash-Lite answers in ~1s, while full
# Flash with thinking takes 7s+. resolve_model() swaps in the newest Flash-Lite.
DEFAULT_MODEL = 'gemini-flash-lite-latest'
THINKING_LEVELS = ['minimal', 'low']  # tried in order; dropped entirely if a model rejects both

SYSTEM = (
    'You are the battle director for bot wizards in WANDER, a real-time spellcasting arena. '
    'Each bot has a persona. For every bot, choose a target to focus, a play style, a fusion to set up, '
    'and one short in-character taunt (max 70 characters, PG, no emojis). Taunts should react to what is '
    'happening: low health, a rival who keeps shielding, a human who spams one spell, a recent knockout. '
    'Bots should not all focus the same target unless it is clearly winning. Spells: Aegis Ward (shield), '
    'Storm Lance (bolt), Severance (disarm, breaks shields), Skyward Bind (slow boulder), Dawnspark (cleanse+heal), '
    'Rime Spire (freeze), Ember Nova (burn). Fusions: shatter = Rime Spire then Storm Lance; plasma = Ember Nova '
    'then Storm Lance (pierces shields); stasis = Severance then Rime Spire; meteor = Skyward Bind then Ember Nova; '
    'steam = Rime Spire then Ember Nova (blinds).'
)

SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'bots': {
            'type': 'ARRAY',
            'items': {
                'type': 'OBJECT',
                'properties': {
                    'id': {'type': 'STRING'},
                    'focus_target': {'type': 'STRING', 'description': 'id of the wizard to attack'},
                    'style': {'type': 'STRING', 'enum': ['aggressive', 'defensive', 'control']},
                    'combo': {'type': 'STRING', 'enum': ['shatter', 'plasma', 'stasis', 'meteor', 'steam', 'none']},
                    'taunt': {'type': 'STRING'},
                },
                'required': ['id', 'focus_target', 'style', 'combo', 'taunt'],
            },
        },
    },
    'required': ['bots'],
}


def _key_file():
    """Optional: a key saved in ~/.wander/gemini_key.txt (outside the project, so it never reaches git)."""
    try:
        return (Path.home() / '.wander' / 'gemini_key.txt').read_text().strip() or None
    except OSError:
        return None


class Gemini:
    def __init__(self, api_key=None, model=None):
        self.key = api_key or os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY') or _key_file()
        self.pinned = model or os.environ.get('GEMINI_MODEL')
        self.model = self.pinned or DEFAULT_MODEL
        self.last_error = None
        self.calls = 0
        self._resolved = bool(self.pinned)
        self._thinking = list(THINKING_LEVELS)

    @property
    def available(self):
        return bool(self.key)

    def _headers(self):
        return {'Content-Type': 'application/json', 'x-goog-api-key': self.key}

    def resolve_model(self, timeout=10.0):
        """Pick the newest `gemini-X.Y-flash-lite` this key can call (unless GEMINI_MODEL pins one)."""
        self._resolved = True
        try:
            req = urllib.request.Request(BASE + '/models?pageSize=200', headers=self._headers())
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                models = json.loads(resp.read()).get('models', [])
        except (urllib.error.URLError, TimeoutError, ValueError):
            return self.model
        best = None
        for m in models:
            name = m.get('name', '').removeprefix('models/')
            match = re.fullmatch(r'gemini-(\d+(?:\.\d+)?)-flash-lite', name)
            if match and 'generateContent' in m.get('supportedGenerationMethods', []):
                version = tuple(int(x) for x in match.group(1).split('.'))
                if not best or version > best[0]:
                    best = (version, name)
        if best:
            self.model = best[1]
        return self.model

    def _thinking_config(self):
        # This is a real-time game: ask for minimal deliberation to keep latency low.
        if self.model.startswith('gemini-2.5'):
            return dict(thinkingBudget=0)
        return dict(thinkingLevel=self._thinking[0])

    def generate_json(self, system, prompt, schema, timeout=10.0, _retry=True):
        if not self.key:
            return None
        if not self._resolved:
            self.resolve_model()
        cfg = dict(responseMimeType='application/json', responseSchema=schema, temperature=0.9, maxOutputTokens=800)
        if self._thinking:
            cfg['thinkingConfig'] = self._thinking_config()
        body = dict(systemInstruction=dict(parts=[dict(text=system)]),
                    contents=[dict(role='user', parts=[dict(text=prompt)])], generationConfig=cfg)
        req = urllib.request.Request(API.format(model=self.model), data=json.dumps(body).encode(), method='POST',
                                     headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
            self.calls += 1
            text = ''.join(p.get('text', '') for p in data['candidates'][0]['content']['parts'] if not p.get('thought'))
            self.last_error = None
            return json.loads(text)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors='replace')[:300]
            if e.code == 400 and 'thinking' in detail.lower() and self._thinking:
                self._thinking.pop(0)  # step down to the next supported level, or none
                return self.generate_json(system, prompt, schema, timeout, _retry)
            if e.code == 404 and _retry and not self.pinned:
                old = self.model
                if self.resolve_model() != old:
                    return self.generate_json(system, prompt, schema, timeout, _retry=False)
            if e.code in (401, 403):
                self.last_error = ('key rejected (expired token or invalid key) - use a permanent AIza... key '
                                   'from aistudio.google.com/apikey')
            else:
                self.last_error = f'HTTP {e.code}: {detail}'
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError) as e:
            self.last_error = f'{type(e).__name__}: {e}'
        return None


def describe(arena):
    """Compact battle summary for the prompt."""
    lines = []
    for wid in arena.order:
        w = arena.wizards[wid]
        status = [k for k in ('shield', 'frozen', 'burn', 'disarmed') if getattr(w, k) > 0] + (['blind'] if w.blind else [])
        top = ', '.join(f'{arena.wizards[k].name if k in arena.wizards else k}:{round(v)}' for k, v in w.damaged_by.most_common(2))
        who = f"BOT persona='{w.persona['name']}' style={w.persona['style']} bio={w.persona['bio']}" if w.bot else 'HUMAN player'
        lines.append(f"- id={w.id} name={w.name} [{who}] hp={round(w.hp)} mana={round(w.mana)} alive={w.alive} "
                     f"status={status or 'none'} casts={w.stats['casts']} blocks={w.stats['blocks']} took_damage_from=({top or 'nobody'})")
    recent = [e.get('text') for e in arena.plan_log[-6:] if e.get('text')]
    return 'Wizards:\n' + '\n'.join(lines) + '\nRecent events: ' + ('; '.join(recent) or 'the duel just began')
