# wander
### Spellcaster Game for HackTX 2026

A game where you defeat your enemies through the power of accurate spellcasting. Time your movements and speech to effectively cause damage before you die. 

**Play in the browser: [wandergame.tech](https://wandergame.tech)** (Chrome or Edge; allow the camera and microphone).
The full desktop version with the hardware wand and multiplayer Arena is in [`wander/`](wander/README.md).

## Website
The browser version (`index.html`, `js/`, `style.css`) is a static site served by GitHub Pages from `main`.
`CNAME` points it at wandergame.tech; `.nojekyll` makes Pages serve the files as they are.
To run it locally: `powershell -ExecutionPolicy Bypass -File serve.ps1`, then open http://localhost:8000.
