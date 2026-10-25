"""Mock game for testing the wand (wand.ino) without running wander: walks through a scripted match
over serial so you can watch the LED and screen react to every command.

    pip install pyserial
    python wand/mock_game.py               # finds the wand's port on its own
    python wand/mock_game.py --port COM5   # or name it
    python wand/mock_game.py --step        # wait for Enter between steps
    python wand/mock_game.py --loop        # repeat the match until Ctrl+C

Close the Arduino Serial Monitor first: only one program can hold the port.
"""

import argparse
import sys
import threading
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit('pip install pyserial')

# Same USB-serial chips wander/wand.py looks for (CP210x and CH340 are the usual ESP32 ones).
WAND_VIDS = {0x2341, 0x2A03, 0x1A86, 0x0403, 0x10C4}

# (seconds to hold afterwards, line sent to the wand or None, what you should see)
SCRIPT = [
    (2.0, None, 'Idle: WANDER title card, LED off'),
    (2.0, 'A:1', 'Wand option picked: LED ON, "ON" pill, "let\'s go!"'),
    (1.5, 'H:100', 'Full green health bar'),
    (2.0, 'S:circle:Shield', 'Cyan circle, "Shield"'),
    (2.0, 'M:Good attack!', 'Yellow message band at the bottom (~1.4s)'),
    (2.0, 'S:bolt:Lightning', 'Purple bolt, "Lightning"'),
    (1.5, 'O:OUCH!', 'Red full-screen flash (~0.7s)'),
    (1.5, 'H:60', 'Health bar drops to 60, still green'),
    (2.0, 'S:flick:Fireball', 'Orange flick arrow, "Fireball"'),
    (1.5, 'M:Nice cast!', 'Message band'),
    (2.0, 'S:spiral:Vortex', 'Green spiral'),
    (1.5, 'O:OOF!', 'Red flash'),
    (1.5, 'H:40', 'Health bar turns yellow'),
    (2.0, 'S:up:Levitate', 'Yellow up arrow'),
    (2.0, 'S:triangle:Prism', 'Light blue triangle'),
    (2.0, 'S:vee:Claw', 'Orange vee'),
    (1.5, 'O:ARGH!', 'Red flash'),
    (1.5, 'H:15', 'Health bar turns red'),
    (2.0, 'S:star:Supernova Fusion', 'Gold star, long name shrinks to fit'),
    (2.0, 'O:VICTORY', 'Gold full-screen VICTORY flash'),
    (2.0, 'M:Brilliant!', 'Message band'),
    (2.5, 'A:0', 'Wand option deselected: LED OFF, back to the title card'),
]


def find_port():
    ports = list(list_ports.comports())
    for p in ports:
        if p.vid in WAND_VIDS:
            return p.device
    for p in ports:
        if any(k in (p.description or '') for k in ('CP210', 'CH340', 'USB-SERIAL', 'USB Serial', 'Arduino')):
            return p.device
    return None


def reader(ser, booted, stop):
    """Prints what the wand says back; B1/B0 heartbeats only when they change."""
    buf, last = b'', None
    while not stop.is_set():
        try:
            buf += ser.read(64)
        except serial.SerialException:
            print('\n!! wand disconnected')
            stop.set()
            return
        while b'\n' in buf:
            line, buf = buf.split(b'\n', 1)
            line = line.strip().decode('ascii', 'ignore')
            if line == 'W':
                booted.set()
                print('   <- W        (wand booted)')
            elif line in ('B0', 'B1'):
                if line != last:
                    print(f'   <- {line}       (wand reports LED {"ON" if line == "B1" else "OFF"})')
                last = line
            elif line:
                print(f'   <- {line}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--port', help='serial port, e.g. COM5 (default: auto-detect)')
    ap.add_argument('--step', action='store_true', help='wait for Enter between steps')
    ap.add_argument('--loop', action='store_true', help='repeat the match until Ctrl+C')
    ap.add_argument('--speed', type=float, default=1.0, help='pause multiplier (0.5 = twice as fast)')
    args = ap.parse_args()

    dev = args.port or find_port()
    if not dev:
        print('No wand found. Ports seen:')
        for p in list_ports.comports():
            print(f'  {p.device}  {p.description}')
        sys.exit('Pass --port COMx')

    ser = serial.Serial(dev, 115200, timeout=0.05)
    print(f'Opened {dev}. Waiting for the wand to boot (opening the port resets it)...')
    booted, stop = threading.Event(), threading.Event()
    threading.Thread(target=reader, args=(ser, booted, stop), daemon=True).start()
    if not booted.wait(4):
        print('   (no "W" boot line seen; carrying on anyway)')
    time.sleep(0.3)

    try:
        while True:
            for i, (hold, line, expect) in enumerate(SCRIPT, 1):
                if stop.is_set():
                    sys.exit(1)
                print(f'\n[{i:2}/{len(SCRIPT)}] {expect}')
                if line:
                    print(f'   -> {line}')
                    ser.write((line + '\n').encode('ascii'))
                if args.step:
                    input('   (Enter for next) ')
                else:
                    time.sleep(hold * args.speed)
            if not args.loop:
                break
            print('\n--- again ---')
    except KeyboardInterrupt:
        print()
    finally:
        # Leave the wand dark so it doesn't keep lighting up after the test.
        try:
            ser.write(b'A:0\n')
            ser.flush()
        except serial.SerialException:
            pass
        stop.set()
        ser.close()
        print('Done (sent A:0, LED off).')


if __name__ == '__main__':
    main()
