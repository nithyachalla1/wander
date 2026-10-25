"""Serial link to the ESP32 wand (see wand/wand.ino): turns its tip LED on and off, and drives its
LCD with the current spell, health and little reactions. Runs on a background thread, reconnects if
the USB cable is pulled, and does nothing if pyserial or the wand is missing.

The wand is reached through a Nano used as a USB-serial bridge, so opening the port doesn't reset it.
We re-send our state on every connect and repeat "A:" every second; if the wand stops hearing from us
(game quit or crashed) it turns itself off."""

import queue
import random
import threading
import time

# Arduino, CH340, FTDI and CP210x USB-serial chips, the usual suspects on Nano boards and clones.
WAND_VIDS = {0x2341, 0x2A03, 0x1A86, 0x0403, 0x10C4}
KEEPALIVE_SECS = 1.0  # well inside the wand's 5s LINK_TIMEOUT_MS

CHEERS = ['Good attack!', 'Nice cast!', 'Clean form!', 'Keep it up!', 'Brilliant!', 'Again!']
OUCHES = ['OUCH!', 'OOF!', 'ARGH!']
FIZZLES = ['Try again!', 'Steady...', 'Bolder!', 'Almost!']


class WandLink:
    def __init__(self, port=None):
        self.port = port
        self.connected = False
        self.armed = False  # the wand reports its LED on (B1) / off (B0)
        self.status = 'Looking for wand...'
        self.error = None
        self._active = False
        self._out = queue.Queue(maxsize=16)
        self._sticky = {}  # last S:/H: lines, re-sent when the wand connects or (re)boots
        self._msg_until = 0.0
        self._stop = False
        self._thread = None

    def start(self):
        if self._thread:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Switch the wand off on the way out, rather than leaving it to time out."""
        if self.connected:
            self.set_active(False)
            time.sleep(0.1)
        self._stop = True

    # ------------------------------------------------------------ game -> wand
    def _send(self, line):
        try:
            self._out.put_nowait(line)
        except queue.Full:
            pass

    def _sticky_send(self, key, line):
        if self._sticky.get(key) != line:
            self._sticky[key] = line
            self._send(line)

    def set_active(self, on):
        """Wand option picked / dropped: tip LED and LCD on / off."""
        self._active = bool(on)
        self._send(self._active_line())

    def _active_line(self):
        return 'A:1' if self._active else 'A:0'

    def _replay(self):
        while not self._out.empty():  # drop anything queued while disconnected: stale OUCHes etc.
            self._out.get_nowait()
        self._send(self._active_line())
        for v in self._sticky.values():
            self._send(v)

    def show(self, shape, name):
        self._sticky_send('S', f'S:{shape}:{name[:19]}')

    def hp(self, frac):
        self._sticky_send('H', f'H:{round(max(0.0, min(1.0, frac)) * 100)}')

    def say(self, text, urgent=False):
        """Bottom-band message. Ordinary chatter won't stomp a message that's still on screen."""
        t = time.monotonic()
        if not urgent and t < self._msg_until:
            return
        self._msg_until = t + 0.9
        self._send(f'M:{text[:19]}')

    def ouch(self, text=None):
        self._send(f'O:{(text or random.choice(OUCHES))[:11]}')

    def cheer(self):
        self.say(random.choice(CHEERS))

    def fizzle(self):
        self.say(random.choice(FIZZLES))

    # ------------------------------------------------------------ thread
    def _find_port(self, list_ports):
        ports = list(list_ports.comports())
        for p in ports:
            if p.vid in WAND_VIDS:
                return p.device
        for p in ports:
            if any(k in (p.description or '') for k in ('Arduino', 'CH340', 'USB-SERIAL', 'USB Serial')):
                return p.device
        return None

    def _run(self):
        try:
            import serial
            from serial.tools import list_ports
        except ImportError:
            self.error = 'pip install pyserial for the wand'
            return
        while not self._stop:
            dev = self.port or self._find_port(list_ports)
            if not dev:
                self.status = 'No wand plugged in'
                time.sleep(1.5)
                continue
            try:
                with serial.Serial(dev, 115200, timeout=0.02) as ser:
                    self.status = f'Wand on {dev}'
                    self._talk(ser)
            except (serial.SerialException, OSError) as exc:
                self.status = f'Wand lost ({exc.__class__.__name__})'
            self.connected = False
            self.armed = False
            time.sleep(1.0)

    def _talk(self, ser):
        buf = b''
        last_rx = time.monotonic()
        last_beat = 0.0
        self._replay()  # the wand may have been running all along: bring it up to date
        while not self._stop:
            buf += ser.read(64)
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                line = line.strip().decode('ascii', 'ignore')
                last_rx = time.monotonic()
                if line == 'W':  # wand just booted (EN pressed, power blip): replay state
                    self._replay()
                elif line in ('B0', 'B1'):
                    self.connected = True
                    self.armed = line == 'B1'
            if time.monotonic() - last_rx > 3:
                self.connected = False
            if time.monotonic() - last_beat > KEEPALIVE_SECS:
                last_beat = time.monotonic()
                self._send(self._active_line())
            # Each frame takes the ESP32 ~25ms to push to the LCD, so pace writes.
            try:
                line = self._out.get_nowait()
            except queue.Empty:
                continue
            ser.write((line + '\n').encode('ascii', 'ignore'))
            time.sleep(0.03)
