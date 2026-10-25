"""Hardware wand support, mixed into Game: input mode (hand vs IR wand), the ARMED gate,
camera->screen calibration, and feedback to the wand's OLED via WandLink (wand.py)."""

import math

import numpy as np
import pygame

from . import game as G  # read G.W / G.H / G.U at call time (they change on resize)
from .fx import blit_glow
from .spells import SPELLS, rgb
from .wand import WandLink

# Where the four calibration targets sit on screen (normalized). Inset from the corners so they are
# comfortable to reach; points beyond them are extrapolated.
TARGETS = [(0.12, 0.14), (0.88, 0.14), (0.88, 0.86), (0.12, 0.86)]
CORNER_NAMES = ['top-left', 'top-right', 'bottom-right', 'bottom-left']
DWELL_SECS = 1.2


def s(v):
    return v * G.U


def homography(src, dst):
    """3x3 perspective transform mapping 4 src points onto 4 dst points."""
    a, b = [], []
    for (x, y), (u, v) in zip(src, dst):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        b += [u, v]
    h = np.linalg.solve(np.array(a, float), np.array(b, float))
    return np.append(h, 1.0).reshape(3, 3)


class WandMixin:
    def init_wand(self, input_mode=None, wand_port=None):
        if input_mode:
            self.settings['input'] = input_mode
        self.settings.setdefault('input', 'hand')
        self.wand_link = WandLink(wand_port)
        self.wand_link.start()
        self.space_armed = False
        self.calib = None
        self.ir_h = None
        self._load_calibration()

    # ------------------------------------------------------------ state
    def ir_mode(self):
        return self.settings.get('input') == 'ir'

    def wand_armed(self):
        return self.wand_link.armed if self.wand_link.connected else self.space_armed

    def toggle_input(self):
        self.settings['input'] = 'hand' if self.ir_mode() else 'ir'
        self.save()
        if self.tracker:  # swap trackers now if the camera is already running
            self.ensure_inputs()
        self.toast('Casting with the IR wand' if self.ir_mode() else 'Casting with your hand')

    def toggle_space_arm(self):
        if self.wand_link.connected:
            self.toast('Use the button on the wand to arm it')
            return
        self.space_armed = not self.space_armed
        self.audio.play('tick')

    def _load_calibration(self):
        pts = self.settings.get('ir_calib')
        try:
            self.ir_h = homography(pts, TARGETS) if pts and len(pts) == 4 else None
        except np.linalg.LinAlgError:
            self.ir_h = None

    def map_tip(self, tx, ty):
        """Camera-normalized wand/hand position -> screen pixels."""
        if self.ir_mode() and self.ir_h is not None and self.mode != 'calibrate':
            u, v, w = self.ir_h @ np.array([tx, ty, 1.0])
            u, v = u / w, v / w
            return min(max(u, -0.02), 1.02) * G.W, min(max(v, -0.02), 1.02) * G.H
        return G.clamp((tx - 0.12) / 0.76, 0, 1) * G.W, G.clamp((ty - 0.1) / 0.72, 0, 1) * G.H

    # ------------------------------------------------------------ wand OLED feedback
    def wand_cast(self, base, name, combo_name=None, custom=False):
        if combo_name:
            self.wand_link.show('star', combo_name)
            self.wand_link.say('FUSION!', urgent=True)
        else:
            self.wand_link.show('star' if custom else SPELLS[base]['gesture'], name)
            self.wand_link.cheer()

    def wand_tick(self):
        if self.mode in ('duel', 'practice') and self.player:
            self.wand_link.hp(self.player['hp'] / self.player['max_hp'])
        elif self.mode == 'arena' and self.me():
            self.wand_link.hp(self.me()['hp'] / 100)
        if self.calib:
            self.update_calibration()

    # ------------------------------------------------------------ calibration
    def start_calibration(self):
        if not self.ir_mode():
            self.settings['input'] = 'ir'
            self.save()
        if not self.settings['cam']:
            self.settings['cam'] = True
            self.save()
        self.ensure_inputs()
        self.mode = 'calibrate'
        self.stroke = None
        self.calib = dict(step=0, t0=G.now(), pts=[], samples=[], armed=self.wand_armed(), last_capture=0.0,
                          learning_started=False)

    def cancel_calibration(self):
        self.calib = None
        self.mode = 'menu'

    def calib_feed(self, r, ts):
        """Called with every tracker result while calibrating."""
        if self.calib and r.get('present') and not r.get('coasting'):
            self.calib['samples'].append((ts, *r['tip']))
            self.calib['samples'] = [p for p in self.calib['samples'] if ts - p[0] < DWELL_SECS + 0.2]

    def capture_corner(self):
        c = self.calib
        if not c or c['step'] < 1:
            return
        now = G.now()
        recent = [p for p in c['samples'] if now - p[0] < 0.3]
        if not recent or now - c['last_capture'] < 0.6:
            if not recent:
                self.toast("Can't see the wand tip - check the LED and that it faces the camera")
            return
        c['pts'].append((sum(p[1] for p in recent) / len(recent), sum(p[2] for p in recent) / len(recent)))
        c['last_capture'] = now
        c['samples'] = []
        self.audio.play('cast')
        if len(c['pts']) == 4:
            self.finish_calibration()
        else:
            c['step'] += 1

    def finish_calibration(self):
        pts = self.calib['pts']
        try:
            self.ir_h = homography(pts, TARGETS)
        except np.linalg.LinAlgError:
            self.toast('Those corners were too close together - try again with bigger movements')
            self.start_calibration()
            return
        self.settings['ir_calib'] = [list(p) for p in pts]
        self.settings['ir_mask'] = [list(p) for p in getattr(self.tracker, 'mask', [])]
        self.save()
        self.calib = None
        self.mode = 'menu'
        self.audio.play('combo')
        self.wand_link.say('Calibrated!', urgent=True)
        self.toast('Wand calibrated. Arm it and draw a spell!')

    def update_calibration(self):
        c = self.calib
        now = G.now()
        if c['step'] == 0:
            # Step 0: wand hidden while the tracker learns the room's static lights.
            if not c['learning_started'] and now - c['t0'] > 1.2 and getattr(self.tracker, 'ready', False):
                self.tracker.learn_background(1.5)
                c['learning_started'] = True
                c['learn_t'] = now
            if c['learning_started'] and now - c['learn_t'] > 1.7 and not self.tracker.learning:
                c['step'] = 1
                c['armed'] = self.wand_armed()
            return
        armed = self.wand_armed()
        if armed != c['armed']:  # any wand-button press captures the corner
            c['armed'] = armed
            self.capture_corner()
            return
        recent = [p for p in c['samples'] if now - p[0] < DWELL_SECS]
        if len(recent) > 8 and now - recent[0][0] > DWELL_SECS * 0.9 and now - c['last_capture'] > 1.5:
            spread = max(math.hypot(p[1] - recent[-1][1], p[2] - recent[-1][2]) for p in recent)
            if spread < 0.012:  # held still: capture automatically
                self.capture_corner()

    def draw_calibrate(self):
        sc, c, W, H = self.screen, self.calib, G.W, G.H
        sc.blit(self.shade[205], (0, 0))
        self.text(sc, 'Wand Calibration', (W / 2, s(40)), 34, G.GOLD, True, True, 'midtop', outline=True)
        hit = self.hand if self.hand.get('present') else None
        if c['step'] == 0:
            msg = 'Hide the wand tip (cover it or turn it away) while the room\'s lights are learned...'
            self.text(sc, msg, (W / 2, H * 0.45), 20, G.INK, anchor='center')
            if c.get('learning_started'):
                self.text(sc, 'Learning background lights...', (W / 2, H * 0.52), 16, G.MUTED, anchor='center')
        else:
            i = c['step'] - 1
            tx, ty = TARGETS[i][0] * W, TARGETS[i][1] * H
            pulse = 1 + 0.15 * math.sin(self.t * 6)
            blit_glow(sc, G.GOLD, tx, ty, s(60) * pulse, 4)
            pygame.draw.circle(sc, rgb(G.GOLD), (tx, ty), s(26) * pulse, max(2, round(3 * G.U)))
            pygame.draw.circle(sc, (255, 255, 255), (tx, ty), s(5))
            for j, (px, py) in enumerate(TARGETS[:i]):
                pygame.draw.circle(sc, (125, 255, 176), (px * W, py * H), s(14), max(2, round(2 * G.U)))
                self.text(sc, 'OK', (px * W, py * H), 12, (125, 255, 176), bold=True, anchor='center')
            self.text(sc, f'Step {c["step"]} of 4: move the wand tip to the {CORNER_NAMES[i]} of your casting space',
                      (W / 2, H * 0.42), 20, G.INK, anchor='center')
            how = 'then press the wand button' if self.wand_link.connected else 'then press Space'
            self.text(sc, f'(where you want the {CORNER_NAMES[i]} target to be) {how}, or hold still for a second',
                      (W / 2, H * 0.42 + s(30)), 15, G.MUTED, anchor='center')
        seen = f"Wand tip: seen  (brightness {hit['ir']['peak']}, {hit['ir']['area']} px)" if hit and hit.get('ir') \
            else 'Wand tip: NOT seen - is the LED on and facing the camera?'
        self.text(sc, seen, (W / 2, H * 0.6), 15, '#7dffb0' if hit else '#ff8a9a', anchor='center')
        self.text(sc, 'Esc to cancel', (W / 2, H - s(40)), 13, G.MUTED, anchor='center')

    def draw_wand_status(self, rect):
        """One line above the webcam preview: wand connection and ARMED state."""
        if not self.ir_mode():
            return
        if self.wand_link.connected:
            label = f"WAND {'ARMED' if self.wand_link.armed else 'OFF'}  ({self.wand_link.status.replace('Wand on ', '')})"
        else:
            label = f"WAND {'ARMED' if self.space_armed else 'OFF'}  (no wand - Space arms)"
        col = '#ffd98a' if self.wand_armed() else G.MUTED
        self.text(self.screen, label, (rect.right, rect.y - s(6)), 12, col, bold=True, anchor='bottomright', outline=True)
