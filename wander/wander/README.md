# wander
Spellcaster Game for HackTX 2026. **Your hand is the wand. Your voice is the word.**

A webcam spellcasting duel written in Python. Draw shapes in the air with your fingertip, speak invented
incantations, fuse spells into combos, and fight **Morvane, the Hollow Archon**, an AI duelist that
models your play style and counters it in real time.

## Setup (conda)

```
conda env create -f environment.yml
conda activate wander
python main.py
```

Or into an existing environment (Python 3.10–3.12): `pip install -r requirements.txt`.

The first time the webcam starts, MediaPipe's hand model (~8 MB) downloads to `~/.wander/`. Voice uses
Google's free speech recognizer, so it needs an internet connection. Everything else runs offline.

### Options
```
python main.py --no-camera     # mouse-only casting
python main.py --no-voice      # skip the microphone
python main.py --camera 1      # pick a different webcam
python main.py --fullscreen    # or press F11 in-game
```

The game renders at your screen's native resolution (it opts out of Windows display-scaling blur) and
scales its UI to match. Resize the window freely, or press **F11** for crisp fullscreen.

## Arena: multiplayer with Gemini bots

Menu -> **Arena (Multiplayer)**. A free-for-all for up to 4 wizards; bots fill the empty seats.

- **Host:** type your name, click **Host game**. The lobby shows the address friends should use (e.g. `10.0.0.12:8765`).
  Pick how many bots and their skill, then **Start match**.
- **Join:** type the host's address in *Server address*, click **Join**. (Same Wi-Fi; allow Python through
  the Windows firewall the first time.)
- **Dedicated server:** `python server.py` hosts without playing.
- **In the match:** cast exactly like the duel (gestures, voice, keys 1-7). Click a wizard or press **Tab** to
  choose your target. The server is authoritative: damage, wards, fusions and statuses are resolved there.

### Gemini-powered bots
With a Gemini API key, a strategist calls Gemini every ~6 seconds and plans for every bot at once: who to
focus, play style, which fusion to set up, and an in-character taunt that reacts to the fight (shown as a
speech bubble and in the Arena Feed). Bots' split-second reactions (shielding, cleansing) stay local.
Without a key, bots use built-in tactics and everything still works.

The game picks the newest `gemini-X.Y-flash-lite` model your key can use (about 1 second per reply; full
Flash models were 7s+, too slow for live play). Pin one with `GEMINI_MODEL` if you like.

Give the **host's** game your key in one of two ways (never put it in the project folder or git):
```
set GEMINI_API_KEY=your-key-here        (Anaconda Prompt, before python main.py / server.py)
```
or save it as the only line of `%USERPROFILE%\.wander\gemini_key.txt`.

## How to cast
| Spell | Form | Incantation | Effect |
|---|---|---|---|
| Aegis Ward | circle | *Aegis* | Blocks hexes for 3s. Empowered: reflects one |
| Storm Lance | lightning zig-zag / Z | *Fulmina* | 12 damage bolt |
| Severance | diagonal flick | *Dissolvo* | Interrupts casts, shatters wards, disarms |
| Skyward Bind | spiral (2+ loops) | *Ascenda* | Levitates a boulder that follows your hand. Swipe up to fling |
| Dawnspark | swipe up | *Solaris* | Lifts the darkness curse, heals |
| Rime Spire | triangle | *Glacia* | Freezes: enemy casts at half speed |
| Ember Nova | V | *Inferna* | Burn over time |

- **Point one finger** (or pinch, toggle in the menu) to draw. Relax your hand or hold still to release.
- **Speak while drawing**: voice + gesture = *Empowered* (1.5x). Voice alone casts a weaker version.
- **Spell fusion**: chain two spells within 4s, e.g. Rime Spire then Storm Lance = *Shatterstorm*.
- No camera? Draw with the mouse. Keys `1-7` cast directly (`Shift` = empowered). `B` toggles the spellbook.

## Under the hood
- **Hand tracking** (`wander/hands.py`): MediaPipe HandLandmarker on 720p MJPG capture (a separate grab
  thread so detection always sees the newest frame), CLAHE contrast boost in dim rooms, two-hand detection
  that locks onto your casting hand, finger poses from 3D joint angles (robust to tilted hands), pinch
  measured in centimeters, majority-vote debouncing, and ~0.2s dropout coasting so a missed frame doesn't
  break your stroke. One Euro filtering keeps the wand tip steady. The preview shows tracking fps.
- **Arena** (`wander/arena.py`, `server.py`, `net.py`, `arena_client.py`, `gemini.py`): server-authoritative
  rules over WebSockets, bot tactics, and the Gemini strategist.
- **Gesture recognition** (`wander/recognizer.py`): $1-style template matcher in numpy (uniform scaling,
  ±25° rotation search) plus geometric heuristics (straightness, signed turning, radial variance).
- **Spell Forge**: train your own gesture from 3–5 samples, name it, give it an incantation and an effect.
- **Adaptive AI** (`wander/enemy.py`): the Archon tracks your spell frequencies, a Markov chain of your
  spell transitions, and your block and interrupt rates. It scores its options against that model, picks
  with a softmax, and narrates its reasoning in the "Archon's Mind" panel. Three phases. It **remembers
  you between duels** (saved in `~/.wander/save.json`).
- **Effects & audio** (`wander/fx.py`, `wander/audio.py`): additive-glow particles, procedural lightning,
  and every sound effect synthesized with numpy. No asset files.
