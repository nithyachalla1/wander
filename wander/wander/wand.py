"""Serial link to the Arduino wand (see wand/wand.ino): reads the arm button, and drives the
wand's OLED with the current spell, health and little reactions. Runs on a background thread,
reconnects if the USB cable is pulled, and does nothing if pyserial or the wand is missing."""

import queue
import random
import threading
import time

# Arduino, CH340, FTDI and CP210x USB-serial chips, the usual suspects on Nano boards and clones.
WAND_VIDS = {0x2341, 0x2A03, 0x1A86, 0x0403, 0x10C4}

CHEERS = ['Good attack!', 'Nice cast!', 'Clean form!', 'Keep it up!', 'Brilliant!', 'Again!']
OUCHES = ['OUCH!', 'OOF!', 'ARGH!']
FIZZLES = ['Try again!', 'Steady...', 'Bolder!', 'Almost!']


class WandLink:
    def __init__(self, port=None):
        self.port = port
        self.connected = False
        self.armed = False
        self.status = 'Looking for wand...'
        self.error = None
        self._out = queue.Queue(maxsize=16)
        self._sticky = {}  # last S:/H: lines, re-sent when the wand (re)boots
        self._msg_until = 0.0
        self._stop = False
        self._thread = None

    def start(self):
        if self._thread:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
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
        while not self._stop:
            buf += ser.read(64)
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                line = line.strip().decode('ascii', 'ignore')
                last_rx = time.monotonic()
                if line == 'W':  # wand just booted (opening the port resets a Nano): replay state
                    for v in self._sticky.values():
                        self._send(v)
                elif line in ('B0', 'B1'):
                    self.connected = True
                    self.armed = line == 'B1'
            if time.monotonic() - last_rx > 3:
                self.connected = False
            # The Nano has a 64-byte receive buffer and stalls ~60ms per OLED frame, so pace writes.
            try:
                line = self._out.get_nowait()
            except queue.Empty:
                continue
            ser.write((line + '\n').encode('ascii', 'ignore'))
            time.sleep(0.03)
