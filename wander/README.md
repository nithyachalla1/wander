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
python main.py --input ir      # track the hardware wand's IR beacon instead of your hand
python main.py --wand-port COM5  # if the wand isn't auto-detected
```

### Hardware wand
Flash `../wand/wand.ino` to the Nano (needs the U8g2 library). The webcam follows the IR LED at the wand's tip,
and the button on the wand arms it: strokes only count while the OLED says **ARMED**. The OLED shows your current
spell's shape and name, your health, and reactions ("Good attack!", "OUCH!"). Switch between hand and wand with
the **Wand:** button in the menu. No wand plugged in? Press `Space` to arm/disarm.

Many laptop webcams filter out IR. Point a phone camera at the sensor: if you can't see the LED glow, swap in a
bright white LED at the tip. The tracker just looks for the brightest small dot, so it works the same.

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
- **Hand tracking** (`wander/hands.py`): OpenCV capture + MediaPipe HandLandmarker on a background thread,
  pose classification (point/open/fist/pinch), One Euro filtering for a steady wand tip.
- **Gesture recognition** (`wander/recognizer.py`): $1-style template matcher in numpy (uniform scaling,
  ±25° rotation search) plus geometric heuristics (straightness, signed turning, radial variance).
- **Spell Forge**: train your own gesture from 3–5 samples, name it, give it an incantation and an effect.
- **Adaptive AI** (`wander/enemy.py`): the Archon tracks your spell frequencies, a Markov chain of your
  spell transitions, and your block and interrupt rates. It scores its options against that model, picks
  with a softmax, and narrates its reasoning in the "Archon's Mind" panel. Three phases. It **remembers
  you between duels** (saved in `~/.wander/save.json`).
- **Effects & audio** (`wander/fx.py`, `wander/audio.py`): additive-glow particles, procedural lightning,
  and every sound effect synthesized with numpy. No asset files.
