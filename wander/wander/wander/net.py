"""Client side of the arena protocol: a background thread that keeps the
WebSocket open and hands incoming messages to the game loop via a queue."""

import json
import queue
import socket
import threading

DEFAULT_PORT = 8765


def lan_ip():
    """This machine's LAN address (no packets are sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        return s.getsockname()[0]
    except OSError:
        return '127.0.0.1'
    finally:
        s.close()


def normalize_url(addr):
    addr = (addr or 'localhost').strip()
    # The server listens on IPv4; Windows resolves "localhost" to IPv6 first.
    addr = addr.replace('localhost', '127.0.0.1')
    if '://' not in addr:
        addr = f'ws://{addr}'
    host_part = addr.split('://', 1)[1]
    if ':' not in host_part.split('/')[0]:
        addr = f'{addr.rstrip("/")}:{DEFAULT_PORT}'
    return addr


class NetClient:
    def __init__(self, addr, name):
        self.url = normalize_url(addr)
        self.name = name
        self.inbox = queue.Queue()
        self.state = 'connecting'
        self.error = None
        self.ws = None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            from websockets.sync.client import connect
        except ImportError:
            self.error = 'pip install websockets'
            self.state = 'closed'
            self.inbox.put(dict(t='closed', error=self.error))
            return
        try:
            with connect(self.url, open_timeout=5, max_size=2 ** 20) as ws:
                self.ws = ws
                ws.send(json.dumps(dict(t='hello', name=self.name, v=1)))
                self.state = 'connected'
                for raw in ws:
                    self.inbox.put(json.loads(raw))
        except Exception as e:  # refused, timeout, closed mid-game...
            self.error = f'{type(e).__name__}: {e}'
        finally:
            self.state = 'closed'
            self.inbox.put(dict(t='closed', error=self.error))

    def send(self, msg):
        if self.ws and self.state == 'connected':
            try:
                self.ws.send(json.dumps(msg))
            except Exception:
                pass

    def poll(self):
        out = []
        while not self.inbox.empty():
            out.append(self.inbox.get())
        return out

    def close(self):
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
