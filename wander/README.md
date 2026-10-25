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

The game renders at your screen's native resolution (it opts out of Windows display-scaling blur) and
scales its UI to match. Resize the window freely, or press **F11** for crisp fullscreen.

## Hardware wand
An ideaspark ESP32 board (built-in 1.9" 170x320 LCD) with one LED at the tip, running `../wand/wand.ino` (needs
the *Adafruit ST7735 and ST7789* and *Adafruit GFX* libraries; board: *ESP32 Dev Module*). The webcam follows the
LED, and the LCD shows your current spell's shape and name, your health, and reactions ("Good attack!", "OUCH!",
"FUSION!"). It works in duels, practice, the Forge and the Arena. `../wand/wand_test/` is a standalone sketch that
plays a mock match on the wand with no PC, for checking the screen and LED.

**Wiring.** GPIO25 -> resistor -> LED -> GND (values are in the sketch). Our board's own USB-serial chip is dead,
so it talks to the PC through a Nano used as a USB-serial bridge (Nano RST tied to GND; full wiring at the top of
`wand.ino`). Upload through the Nano's COM port: hold BOOT, tap EN, release BOOT, then upload.

1. Plug the wand (the Nano) in by USB. It's found automatically (`--wand-port COM5` if not).
2. In the menu, click **Wand:** to switch from *Hand* to *IR wand*. The game turns the wand's LED and screen on;
   switching back to *Hand*, or quitting, turns them off. The camera preview shows `WAND OFF (COM5, LED on)`.
3. Click **Calibrate wand** (shows `(!)` until you do it once). First cover the wand tip while it learns your
   room's lamps and windows, which are ignored from then on. Then move the tip to each of the four glowing
   targets and press Space (or hold still for a second). Calibration is saved.
4. Press **Space** to arm, draw a spell, hold still or press Space again to release it.

No wand plugged in? Everything above still works with any bright LED or phone flashlight as the tip.

**Troubleshooting**
- *"Wand tip: NOT seen"* - many laptop webcams filter out IR. Point a phone camera at the LED: if you can't see
  it glow, swap in a bright white LED at the tip. The tracker looks for the brightest small dot, so either works.
  The LED must face the camera.
- *Cursor jumps to a lamp* - recalibrate; step 1 learns static lights (they show as small x's in the preview).
- *Jittery or slow* - the tracker lowers the webcam's exposure so the LED stands out; a dimmer room helps more.

## Arena: multiplayer with AI bots

Menu -> **Arena (Multiplayer)**. A free-for-all for up to 4 wizards; bots fill the empty seats.

- **Host:** type your name, click **Host game**. The lobby shows the address friends should use (e.g. `10.0.0.12:8765`).
  Pick how many bots and their skill, then **Start match**.
- **Join:** type the host's address in *Server address*, click **Join**. (Same Wi-Fi; allow Python through
  the Windows firewall the first time.)
- **Dedicated server:** `python server.py` hosts without playing.
- **In the match:** cast exactly like the duel (gestures, voice, keys 1-7). Click a wizard or press **Tab** to
  choose your target. The server is authoritative: damage, wards, fusions and statuses are resolved there.

### AI-powered bots: Gemini vs Claude vs GPT vs Grok vs Meta
Each bot can be driven by a different AI provider, so you can watch (and fight) a Gemini bot, a Claude bot
and a Grok bot at once. Every few seconds each AI reads the battle and plans for *its own* bots: who to
focus, play style, which fusion to set up, and an in-character taunt (speech bubble + Arena Feed, tagged
with the provider, e.g. `Vexa [Claude]: "..."`). Bots' split-second reactions (shielding, cleansing) stay
local. Bots are spread round-robin across the AIs you have keys for; any AI without a key (or with an
error) falls back to built-in tactics, so everything always works.

| Provider | Key file in `%USERPROFILE%\.wander\` | or environment variable | Model |
|---|---|---|---|
| Gemini | `gemini_key.txt` | `GEMINI_API_KEY` | newest `gemini-X.Y-flash-lite` (auto) |
| Claude | `claude_key.txt` | `ANTHROPIC_API_KEY` | `claude-opus-5-5`, low effort (`pip install anthropic`) |
| GPT (OpenAI) | `openai_key.txt` | `OPENAI_API_KEY` | auto-picked from your account's model list |
| Grok (xAI) | `grok_key.txt` | `XAI_API_KEY` | auto-picked (prefers a "fast" Grok) |
| Meta Llama | `meta_key.txt` | `LLAMA_API_KEY` | auto-picked (prefers a small Llama) |

Only the **host's** computer needs keys. Keep them in that `.wander` folder (or environment variables),
never in the project folder or git. Each lobby shows a live line per AI: model, plans made, reply speed, or
the error it hit.

**Advanced - `%USERPROFILE%\.wander\bots.json`** lists brains explicitly instead of auto-detecting: use
several keys from one provider, pin a model, or point at any OpenAI-compatible server (Groq, OpenRouter,
a local Ollama):
```json
{"brains": [
  {"provider": "gemini"},
  {"provider": "claude", "model": "claude-haiku-5-5"},
  {"provider": "grok"},
  {"provider": "gemini", "label": "Gemini #2", "key_file": "gemini_key_2.txt"},
  {"provider": "openai", "label": "Ollama", "base_url": "http://localhost:11434/v1", "model": "llama3.2"}
]}
```
Claude defaults to Claude Opus 5.5; set `"model": "claude-haiku-5-5"` there if you want faster, cheaper
replies. Pin Gemini with `GEMINI_MODEL`, Claude with `CLAUDE_MODEL`.

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
- **IR wand** (`wander/ir.py`, `wand_input.py`, `wand.py`): brightest-small-dot tracking with sub-pixel
  centroids, learned background-light masking, continuity scoring and dropout coasting; a 4-point
  perspective calibration; and a serial link that reads the ARMED button and drives the wand's OLED.
- **Arena** (`wander/arena.py`, `server.py`, `net.py`, `arena_client.py`, `brains.py`, `gemini.py`):
  server-authoritative rules over WebSockets, bot tactics, and per-provider AI strategists.
- **Gesture recognition** (`wander/recognizer.py`): $1-style template matcher in numpy (uniform scaling,
  ±25° rotation search) plus geometric heuristics (straightness, signed turning, radial variance).
- **Spell Forge**: train your own gesture from 3–5 samples, name it, give it an incantation and an effect.
- **Adaptive AI** (`wander/enemy.py`): the Archon tracks your spell frequencies, a Markov chain of your
  spell transitions, and your block and interrupt rates. It scores its options against that model, picks
  with a softmax, and narrates its reasoning in the "Archon's Mind" panel. Three phases. It **remembers
  you between duels** (saved in `~/.wander/save.json`).
- **Effects & audio** (`wander/fx.py`, `wander/audio.py`): additive-glow particles, procedural lightning,
  and every sound effect synthesized with numpy. No asset files.
