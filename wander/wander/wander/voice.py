"""Background incantation listener (SpeechRecognition + Google's free web
recognizer) with fuzzy matching, since invented words come back as
near-miss English."""

import queue
import re
import threading


def norm(s):
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z ]', ' ', s.lower())).strip()


def lev(a, b):
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def find_key(text, key):
    """Character index of a (fuzzy) match of `key` in `text`, or -1."""
    exact = f' {text} '.find(f' {key} ')
    if exact >= 0:
        return exact
    tc, kc = text.replace(' ', ''), key.replace(' ', '')
    if len(kc) < 5:
        return -1
    tol = int(len(kc) * 0.28)
    for length in range(len(kc) - 1, len(kc) + 2):
        for s in range(0, len(tc) - length + 1):
            if lev(tc[s:s + length], kc) <= tol:
                return s
    return -1


class Voice:
    def __init__(self):
        self.events = queue.Queue()  # (spell_id, heard_text)
        self.state = 'off'
        self.entries = []
        self.last_heard = ''
        self._stopper = None
        self._starting = False

    def set_entries(self, entries):
        self.entries = [(e['id'], [norm(k) for k in [e['incant'], *e.get('aliases', [])] if k]) for e in entries]

    def match(self, alts):
        hits = []
        for sid, keys in self.entries:
            pos = -1
            for text in alts:
                for k in keys:
                    pos = find_key(text, k)
                    if pos >= 0:
                        break
                if pos >= 0:
                    break
            if pos >= 0:
                hits.append((pos, sid))
        return [sid for _, sid in sorted(hits)]

    def start(self):
        if self._stopper or self._starting:
            return
        self._starting = True
        threading.Thread(target=self._start, daemon=True).start()

    def _start(self):
        try:
            import speech_recognition as sr
        except ImportError:
            self.state = 'unsupported'
            self._starting = False
            return
        try:
            self._sr = sr
            rec = sr.Recognizer()
            rec.pause_threshold = 0.4
            rec.non_speaking_duration = 0.3
            rec.dynamic_energy_threshold = True
            mic = sr.Microphone()
            with mic as src:
                rec.adjust_for_ambient_noise(src, duration=0.6)
            self._stopper = rec.listen_in_background(mic, self._heard, phrase_time_limit=2.5)
            self.state = 'listening'
        except Exception:
            self.state = 'no-mic'
        self._starting = False

    def stop(self):
        if self._stopper:
            self._stopper(wait_for_stop=False)
            self._stopper = None
        self.state = 'off'

    def _heard(self, recognizer, audio):
        try:
            res = recognizer.recognize_google(audio, show_all=True)
        except self._sr.RequestError:
            self.state = 'offline'
            return
        except Exception:
            return
        if self.state == 'offline':
            self.state = 'listening'
        alts = [norm(a.get('transcript', '')) for a in res.get('alternative', [])] if isinstance(res, dict) else []
        alts = [a for a in alts if a]
        if not alts:
            return
        self.last_heard = alts[0]
        for sid in self.match(alts):
            self.events.put((sid, alts[0]))
