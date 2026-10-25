"""AI "brains" for arena bots: each bot can be driven by a different provider
or API key, so Gemini, Claude, GPT, Grok and Meta bots can fight each other.

A brain is a strategist: every few seconds it reads the battle and returns a
plan for *its own* bots (focus target, style, fusion to set up, a taunt).
The bots' split-second reflexes stay local in arena.BotBrain.

Configuration (all optional):
  * Keys are found automatically from environment variables or from files in
    ~/.wander/ (outside the project, so they never reach git):
        gemini_key.txt  claude_key.txt  openai_key.txt  grok_key.txt  meta_key.txt
  * ~/.wander/bots.json lists brains explicitly, e.g. two keys for the same
    provider, a pinned model, or any OpenAI-compatible server:
        {"brains": [
          {"provider": "gemini"},
          {"provider": "claude", "model": "claude-opus-5-5"},
          {"provider": "grok"},
          {"provider": "meta"},
          {"provider": "openai", "model": "...", "key_file": "openai_key.txt"},
          {"provider": "openai", "label": "Ollama", "base_url": "http://localhost:11434/v1", "model": "llama3.2"}
        ]}
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .gemini import SCHEMA as GEMINI_SCHEMA, SYSTEM, Gemini, describe

HOME = Path.home() / '.wander'
CONFIG = HOME / 'bots.json'

# Standard JSON Schema for providers with strict structured output (Claude, OpenAI-compatible).
PLAN_SCHEMA = {
    'type': 'object',
    'properties': {
        'bots': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'id': {'type': 'string'},
                    'focus_target': {'type': 'string'},
                    'style': {'type': 'string', 'enum': ['aggressive', 'defensive', 'control']},
                    'combo': {'type': 'string', 'enum': ['shatter', 'plasma', 'stasis', 'meteor', 'steam', 'none']},
                    'taunt': {'type': 'string'},
                },
                'required': ['id', 'focus_target', 'style', 'combo', 'taunt'],
                'additionalProperties': False,
            },
        },
    },
    'required': ['bots'],
    'additionalProperties': False,
}

# OpenAI-compatible presets. Models are discovered from each provider's /models
# endpoint unless pinned, preferring fast variants (plans must arrive in seconds).
OPENAI_PRESETS = {
    'openai': dict(label='GPT', base_url='https://api.openai.com/v1', env=('OPENAI_API_KEY',), key_file='openai_key.txt',
                   prefer=(r'mini', r'nano'), match=r'^(gpt|o\d)'),
    'grok': dict(label='Grok', base_url='https://api.x.ai/v1', env=('XAI_API_KEY', 'GROK_API_KEY'), key_file='grok_key.txt',
                 prefer=(r'fast', r'mini'), match=r'grok'),
    'meta': dict(label='Meta Llama', base_url='https://api.llama.com/compat/v1', env=('LLAMA_API_KEY', 'META_API_KEY'),
                 key_file='meta_key.txt', prefer=(r'scout', r'8b', r'17b'), match=r'llama'),
}


def read_secret(env_names=(), key_file=None, literal=None):
    if literal:
        return literal
    for name in env_names:
        if os.environ.get(name):
            return os.environ[name]
    if key_file:
        try:
            return (HOME / key_file).read_text().strip() or None
        except OSError:
            pass
    return None


class Brain:
    label = 'AI'

    def __init__(self, label=None):
        self.label = label or self.label
        self.calls = 0
        self.last_error = None
        self.last_latency = None

    @property
    def model(self):
        return '?'

    @property
    def available(self):
        return False

    def plan(self, system, prompt):
        raise NotImplementedError

    def timed_plan(self, system, prompt):
        t0 = time.monotonic()
        result = self.plan(system, prompt)
        if result is not None:
            self.calls += 1
            self.last_latency = time.monotonic() - t0
        return result

    @property
    def status(self):
        if not self.available:
            return f'{self.label}: off (no key)'
        if self.last_error:
            return f'{self.label}: error - {self.last_error[:90]} (bots use built-in tactics)'
        speed = f', {self.last_latency:.1f}s' if self.last_latency else ''
        return f'{self.label}: on ({self.model}, {self.calls} plans{speed})'


class GeminiBrain(Brain):
    label = 'Gemini'

    def __init__(self, key=None, model=None, label=None):
        super().__init__(label)
        self.g = Gemini(api_key=key, model=model)

    @property
    def model(self):
        return self.g.model

    @property
    def available(self):
        return self.g.available

    def plan(self, system, prompt):
        result = self.g.generate_json(system, prompt, GEMINI_SCHEMA)
        self.last_error = self.g.last_error
        return result


class ClaudeBrain(Brain):
    """Claude via the official Anthropic SDK, with schema-enforced JSON output."""
    label = 'Claude'
    DEFAULT_MODEL = 'claude-opus-5-5'
    FALLBACK_MODELS = {'claude-fable-5-1', 'claude-opus-5-5', 'claude-opus-5', 'claude-sonnet-5-5'}

    def __init__(self, key=None, model=None, effort='low', label=None):
        super().__init__(label)
        self._model = model or os.environ.get('CLAUDE_MODEL') or self.DEFAULT_MODEL
        self.effort = effort  # real-time strategy: keep deliberation short
        self.key = key or read_secret(('ANTHROPIC_API_KEY',), 'claude_key.txt')
        self.client = None
        try:
            import anthropic
            self.anthropic = anthropic
            if self.has_credentials():
                self.client = anthropic.Anthropic(api_key=self.key, timeout=30.0, max_retries=1) if self.key \
                    else anthropic.Anthropic(timeout=30.0, max_retries=1)
        except ImportError:
            self.anthropic = None
            self.last_error = 'pip install anthropic'
        # Server-side refusal fallback (beta) on models that support it.
        self.fallbacks = self._model in self.FALLBACK_MODELS

    def has_credentials(self):
        return bool(self.key or os.environ.get('ANTHROPIC_AUTH_TOKEN') or (Path.home() / '.config' / 'anthropic').exists())

    @property
    def model(self):
        return self._model

    @property
    def available(self):
        return self.client is not None

    def plan(self, system, prompt):
        a = self.anthropic
        kwargs = dict(model=self._model, max_tokens=4000, system=system,
                      messages=[{'role': 'user', 'content': prompt}],
                      output_config={'effort': self.effort, 'format': {'type': 'json_schema', 'schema': PLAN_SCHEMA}})
        try:
            if self.fallbacks:
                resp = self.client.beta.messages.create(betas=['server-side-fallback-2026-07-01'], fallbacks='default', **kwargs)
            else:
                resp = self.client.messages.create(**kwargs)
        except a.BadRequestError as e:
            if self.fallbacks and 'fallback' in str(e).lower():
                self.fallbacks = False
                return self.plan(system, prompt)
            self.last_error = f'bad request: {e.message}'
            return None
        except a.AuthenticationError:
            self.last_error = 'invalid Anthropic API key'
            return None
        except a.NotFoundError:
            self.last_error = f'model not found: {self._model}'
            return None
        except a.RateLimitError:
            self.last_error = 'rate limited'
            return None
        except a.APIStatusError as e:
            self.last_error = f'HTTP {e.status_code}'
            return None
        except a.APIConnectionError:
            self.last_error = 'network error'
            return None
        if resp.stop_reason == 'refusal':
            self.last_error = 'declined'
            return None
        if resp.stop_reason == 'max_tokens':
            self.last_error = 'reply cut off (max_tokens)'
            return None
        text = next((b.text for b in resp.content if b.type == 'text'), None)
        try:
            self.last_error = None
            return json.loads(text) if text else None
        except ValueError:
            self.last_error = 'unparseable reply'
            return None


class OpenAICompatBrain(Brain):
    """Any OpenAI-compatible chat API: OpenAI (GPT), xAI (Grok), Meta Llama API, Groq, OpenRouter, Ollama..."""

    def __init__(self, preset='openai', key=None, model=None, base_url=None, label=None, key_file=None, key_env=None):
        p = OPENAI_PRESETS.get(preset, OPENAI_PRESETS['openai'])
        super().__init__(label or p['label'])
        self.preset = p
        self.base_url = (base_url or p['base_url']).rstrip('/')
        self.key = read_secret((key_env,) if key_env else p['env'], key_file or p['key_file'], key)
        self.local = base_url is not None and ('localhost' in base_url or '127.0.0.1' in base_url)
        self._model = model
        self.format_mode = 'json_schema'  # -> json_object -> plain, if the server rejects stricter modes

    @property
    def model(self):
        return self._model or '(auto)'

    @property
    def available(self):
        return bool(self.key) or self.local

    def _request(self, path, body=None, timeout=25.0):
        headers = {'Content-Type': 'application/json'}
        if self.key:
            headers['Authorization'] = f'Bearer {self.key}'
        req = urllib.request.Request(self.base_url + path, data=json.dumps(body).encode() if body else None,
                                     method='POST' if body else 'GET', headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())

    def resolve_model(self):
        """Pick a fast chat model from the provider's own catalog."""
        data = self._request('/models', timeout=10)
        ids = sorted((m.get('id', '') for m in data.get('data', [])), reverse=True)
        ids = [i for i in ids if re.search(self.preset['match'], i, re.I)
               and not re.search(r'image|vision|embed|tts|audio|whisper|realtime|search|guard|moderation', i, re.I)] or ids
        for pattern in self.preset['prefer']:
            hit = next((i for i in ids if re.search(pattern, i, re.I)), None)
            if hit:
                return hit
        return ids[0] if ids else None

    def plan(self, system, prompt):
        try:
            if not self._model:
                self._model = self.resolve_model()
                if not self._model:
                    self.last_error = 'no models listed; set "model" in bots.json'
                    return None
            sys_text = system
            body = dict(model=self._model, temperature=0.9,
                        messages=[{'role': 'system', 'content': sys_text}, {'role': 'user', 'content': prompt}])
            if self.format_mode == 'json_schema':
                body['response_format'] = {'type': 'json_schema',
                                           'json_schema': {'name': 'bot_plans', 'strict': True, 'schema': PLAN_SCHEMA}}
            else:
                body['messages'][0]['content'] = (sys_text + '\nRespond with only a JSON object matching this schema: '
                                                  + json.dumps(PLAN_SCHEMA))
                if self.format_mode == 'json_object':
                    body['response_format'] = {'type': 'json_object'}
            data = self._request('/chat/completions', body)
            text = data['choices'][0]['message']['content'] or ''
            text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())
            self.last_error = None
            return json.loads(text)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors='replace')[:200]
            if e.code in (400, 422) and self.format_mode != 'plain' and ('response_format' in detail or 'schema' in detail.lower()
                                                                          or 'json' in detail.lower()):
                self.format_mode = 'json_object' if self.format_mode == 'json_schema' else 'plain'
                return self.plan(system, prompt)
            self.last_error = f'HTTP {e.code}: {detail}'
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError) as e:
            self.last_error = f'{type(e).__name__}: {e}'
        return None


PROVIDERS = ('gemini', 'claude', 'openai', 'grok', 'meta')


def make_brain(spec):
    provider = spec.get('provider', '').lower()
    key = read_secret((spec['key_env'],) if spec.get('key_env') else (), spec.get('key_file'), spec.get('key'))
    if provider == 'gemini':
        return GeminiBrain(key=key, model=spec.get('model'), label=spec.get('label'))
    if provider == 'claude':
        return ClaudeBrain(key=key, model=spec.get('model'), effort=spec.get('effort', 'low'), label=spec.get('label'))
    if provider in OPENAI_PRESETS:
        return OpenAICompatBrain(provider, key=key, model=spec.get('model'), base_url=spec.get('base_url'),
                                 label=spec.get('label'), key_file=spec.get('key_file'), key_env=spec.get('key_env'))
    return None


def load_brains():
    """Brains from ~/.wander/bots.json if present, else one per provider with a key."""
    try:
        specs = json.loads(CONFIG.read_text()).get('brains', [])
    except (OSError, ValueError):
        specs = [dict(provider=p) for p in PROVIDERS]
        auto = True
    else:
        auto = False
    brains = [b for b in (make_brain(s) for s in specs) if b]
    return [b for b in brains if b.available] if auto else brains


class Strategist:
    """Background planner for the bots assigned to one brain."""

    def __init__(self, brain, index, arena, lock, on_taunt, interval=6.0, delay=0.0):
        self.brain, self.index, self.arena, self.lock = brain, index, arena, lock
        self.on_taunt, self.interval, self.delay = on_taunt, interval, delay
        self._stop = False
        self._thread = None

    def start(self):
        if self.brain.available and not self._thread:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def stop(self):
        self._stop = True

    def my_bots(self):
        return [b for b in self.arena.brains.values() if getattr(b.w, 'brain_idx', None) == self.index]

    def plan_once(self):
        with self.lock:
            mine = self.my_bots()
            if self.arena.phase != 'play' or not mine:
                return False
            ids = ', '.join(b.w.id for b in mine)
            prompt = describe(self.arena) + f'\nYou control ONLY these bots: {ids}. Return a plan for each of them.'
            mine_ids = {b.w.id for b in mine}
            valid_targets = set(self.arena.order)
        result = self.brain.timed_plan(SYSTEM, prompt)
        if not result:
            return False
        with self.lock:
            for p in result.get('bots', []):
                brain = self.arena.brains.get(p.get('id'))
                if not brain or p.get('id') not in mine_ids:
                    continue
                focus = p.get('focus_target') if p.get('focus_target') in valid_targets else None
                brain.plan.update(focus=focus, style=p.get('style', brain.plan['style']), combo=p.get('combo', 'none'),
                                  taunt=str(p.get('taunt', ''))[:90])
                if brain.plan['taunt'] and brain.w.alive:
                    self.on_taunt(brain.w.id, brain.plan['taunt'])
        return True

    def _loop(self):
        time.sleep(self.delay)  # stagger brains so they don't all speak at once
        while not self._stop:
            t0 = time.monotonic()
            try:
                self.plan_once()
            except Exception as e:  # a misbehaving provider must never take the server down
                self.brain.last_error = f'{type(e).__name__}: {e}'
            time.sleep(max(1.0, self.interval - (time.monotonic() - t0)))
