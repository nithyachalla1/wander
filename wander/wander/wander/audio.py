"""Fully synthesized sound effects (no audio files), generated with numpy."""

import numpy as np
import pygame

SR = 22050
rng = np.random.default_rng(7)


def _n(d):
    return max(1, int(SR * d))


def _env(n, attack=0.005):
    a = min(n, max(1, int(SR * attack)))
    e = np.exp(np.linspace(0, -7.6, n))  # ~ -66 dB at the end, like an exponential ramp
    e[:a] *= np.linspace(0, 1, a)
    return e


def _pad(x, delay):
    return np.concatenate([np.zeros(_n(delay)), x]) if delay > 0 else x


def tone(f0, f1=None, dur=0.3, vol=0.2, wave='sine', delay=0.0, attack=0.005):
    f1 = f0 if f1 is None else f1
    n = _n(dur)
    t = np.arange(n) / SR
    freq = f0 * (max(f1, 1) / f0) ** (t / dur)
    ph = 2 * np.pi * np.cumsum(freq) / SR
    if wave == 'sine':
        w = np.sin(ph)
    elif wave == 'square':
        w = np.sign(np.sin(ph))
    else:
        saw = 2 * ((ph / (2 * np.pi)) % 1) - 1
        w = saw if wave == 'saw' else 2 * np.abs(saw) - 1
    return _pad(w * vol * _env(n, attack), delay)


def _lowpass(x, f):
    k = max(1, int(SR / (2.2 * max(f, 20))))
    return np.convolve(x, np.ones(k) / k, mode='same') if k > 1 else x


def noise(dur=0.3, vol=0.2, kind='band', f0=1000, f1=None, delay=0.0, attack=0.005):
    f1 = f0 if f1 is None else f1
    n = _n(dur)
    src = rng.uniform(-1, 1, n)
    segs = np.array_split(np.arange(n), 8)
    out = np.zeros(n)
    for i, idx in enumerate(segs):
        f = f0 * (f1 / f0) ** (i / 7)
        if kind == 'low':
            y = _lowpass(src, f)
        elif kind == 'high':
            y = src - _lowpass(src, f)
        else:
            y = (_lowpass(src, f * 1.5) - _lowpass(src, f / 1.5)) * 2
        out[idx] = y[idx]
    return _pad(out * vol * _env(n, attack), delay)


def mix(*parts):
    n = max(len(p) for p in parts)
    out = np.zeros(n)
    for p in parts:
        out[:len(p)] += p
    return out


def _arp(freqs, step, **kw):
    return mix(*[tone(f, delay=i * step, **kw) for i, f in enumerate(freqs)])


BUILD = {
    'stroke': lambda: tone(900, 1300, 0.06, 0.05),
    'cast': lambda: mix(tone(380, 1150, 0.28, 0.16, 'tri'), noise(0.22, 0.12, 'high', 3000, 8000)),
    'empowered': lambda: _arp([523, 659, 784, 1046], 0.05, dur=0.5, vol=0.1, wave='tri'),
    'zap': lambda: mix(noise(0.35, 0.5, 'band', 4000, 600), tone(220, 40, 0.3, 0.14, 'saw')),
    'shield': lambda: mix(tone(300, 600, 0.6, 0.14, attack=0.05), tone(450, 900, 0.6, 0.1, attack=0.05)),
    'block': lambda: mix(tone(1200, 700, 0.12, 0.07, 'square'), noise(0.15, 0.2, 'band', 2500)),
    'hit': lambda: mix(noise(0.25, 0.4, 'low', 1800, 200), tone(160, 60, 0.2, 0.25)),
    'hurt': lambda: mix(tone(140, 50, 0.4, 0.22, 'saw'), noise(0.4, 0.4, 'low', 900, 100)),
    'freeze': lambda: mix(_arp([1800, 2400, 3100], 0.04, dur=0.25, vol=0.06), noise(0.4, 0.12, 'high', 5000)),
    'fire': lambda: noise(0.5, 0.45, 'low', 600, 2400, attack=0.05),
    'whoosh': lambda: noise(0.35, 0.35, 'band', 300, 1800, attack=0.05),
    'slash': lambda: noise(0.18, 0.4, 'band', 5000, 1500),
    'rock': lambda: mix(tone(90, 40, 0.5, 0.3), noise(0.5, 0.45, 'low', 500, 80)),
    'heal': lambda: _arp([660, 880, 1320], 0.07, dur=0.4, vol=0.08),
    'charge': lambda: tone(70, 220, 1.5, 0.08, 'saw', attack=1.2),
    'shatter': lambda: mix(*[tone(1500 + rng.random() * 2500, 400, 0.15, 0.05, 'square', i * 0.03) for i in range(6)],
                           noise(0.4, 0.3, 'high', 2000)),
    'fizzle': lambda: tone(300, 120, 0.18, 0.07, 'square'),
    'combo': lambda: mix(_arp([392, 523, 659, 784, 1046, 1318], 0.04, dur=0.6, vol=0.08, wave='tri'),
                         noise(0.8, 0.35, 'low', 3000, 200)),
    'dark': lambda: mix(tone(110, 55, 1.4, 0.22, attack=0.3), tone(116, 58, 1.4, 0.18, attack=0.3)),
    'interrupt': lambda: mix(tone(900, 200, 0.3, 0.12, 'square'), noise(0.3, 0.3, 'band', 1500)),
    'win': lambda: _arp([523, 659, 784, 1046, 784, 1046, 1318], 0.13, dur=0.5, vol=0.1, wave='tri'),
    'lose': lambda: _arp([392, 349, 311, 262], 0.25, dur=0.6, vol=0.08, wave='saw'),
    'tick': lambda: tone(880, dur=0.12, vol=0.1, wave='tri'),
}


def _drone():
    n = _n(8.0)
    t = np.arange(n) / SR
    w = sum(np.sin(2 * np.pi * f * t) for f in (55, 55.5, 82.5)) / 3
    return _lowpass(w + 0.3 * np.sign(np.sin(2 * np.pi * 55 * t)), 400) * 0.25 * (0.75 + 0.25 * np.sin(2 * np.pi * t / 8))


class Audio:
    def __init__(self):
        self.ok = pygame.mixer.get_init() is not None
        self.sounds = {}
        self.drone = None
        if not self.ok:
            return
        self.channels = pygame.mixer.get_init()[2]
        pygame.mixer.set_num_channels(24)
        for name, build in BUILD.items():
            self.sounds[name] = self._make(build())
        self.drone_snd = self._make(_drone())

    def _make(self, x):
        x = np.clip(x, -1, 1)
        a = (x * 32000).astype(np.int16)
        if self.channels == 2:
            a = np.ascontiguousarray(np.column_stack([a, a]))
        return pygame.sndarray.make_sound(a)

    def play(self, name):
        if self.ok and name in self.sounds:
            self.sounds[name].play()

    def start_drone(self):
        if self.ok and not self.drone:
            self.drone = self.drone_snd.play(loops=-1, fade_ms=2000)

    def stop_drone(self):
        if self.drone:
            self.drone.fadeout(800)
            self.drone = None
