"""Arena server: WebSocket host for multiplayer matches with bots.

Run standalone (`python server.py`) or in-process when a player clicks
"Host game". Everything runs on plain threads: one per connection, one game
tick loop, and the optional Gemini strategist.

Protocol (JSON text frames):
  client -> server: hello{name} | start{bots, diff} | cast{spell, target, power} | lobby{}
  server -> client: welcome{you, host} | lobby{players, host, gemini, phase} | state{...} | event{kind, ...} | error{text}
"""

import json
import threading
import time

from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve

from .arena import Arena
from .gemini import Strategist
from .net import DEFAULT_PORT, lan_ip


class ArenaServer:
    def __init__(self, host='0.0.0.0', port=DEFAULT_PORT, gemini_interval=6.0, log=print):
        self.host, self.port, self.log = host, port, log
        self.arena = Arena()
        self.lock = threading.RLock()
        self.conns = {}
        self.host_id = None
        self.server = None
        self.running = False
        self.strategist = Strategist(self.arena, self.lock, self._taunt, gemini_interval)

    def _taunt(self, wid, text):
        self.arena.emit('say', src=wid, text=text)

    # ------------------------------------------------------------ lifecycle
    def start(self):
        self.server = serve(self._handler, self.host, self.port)
        self.running = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        threading.Thread(target=self._tick_loop, daemon=True).start()
        self.strategist.start()
        self.log(f'Wander arena listening on ws://{lan_ip()}:{self.port}  (Gemini: {self.strategist.status})')
        return self

    def stop(self):
        self.running = False
        self.strategist.stop()
        if self.server:
            self.server.shutdown()

    # ------------------------------------------------------------ messaging
    @staticmethod
    def _send(ws, msg):
        try:
            ws.send(json.dumps(msg))
        except Exception:
            pass

    def _broadcast(self, msg, conns=None):
        for ws in list((conns or self.conns).values()):
            self._send(ws, msg)

    def lobby_msg(self):
        a = self.arena
        return dict(t='lobby', host=self.host_id, phase=a.phase, gemini=self.strategist.status,
                    players=[dict(id=i, name=a.wizards[i].name, bot=a.wizards[i].bot, color=a.wizards[i].color) for i in a.order])

    def _handler(self, ws):
        wid = None
        try:
            hello = json.loads(ws.recv(timeout=10))
            if hello.get('t') != 'hello':
                return
            with self.lock:
                if self.arena.phase in ('play', 'countdown'):
                    self._send(ws, dict(t='error', text='A match is in progress. Try again when it ends.'))
                    return
                w = self.arena.add(str(hello.get('name', 'Wizard')))
                if not w:
                    self._send(ws, dict(t='error', text='The arena is full (4 wizards).'))
                    return
                wid = w.id
                self.conns[wid] = ws
                if self.host_id not in self.conns:
                    self.host_id = wid
                lobby = self.lobby_msg()
            self.log(f'{w.name} joined ({wid})')
            self._send(ws, dict(t='welcome', you=wid, host=self.host_id == wid))
            self._broadcast(lobby)
            for raw in ws:
                msg = json.loads(raw)
                kind = msg.get('t')
                with self.lock:
                    if kind == 'cast':
                        self.arena.cast(wid, msg.get('spell'), msg.get('target'), msg.get('power', 1.0))
                    elif kind == 'start' and wid == self.host_id and self.arena.phase in ('lobby', 'over'):
                        self.arena.start(int(msg.get('bots', 2)), int(msg.get('diff', 1)))
                    elif kind == 'lobby' and wid == self.host_id:
                        self.arena.to_lobby()
                    lobby = self.lobby_msg() if kind in ('start', 'lobby') else None
                if lobby:
                    self._broadcast(lobby)
        except (ConnectionClosed, TimeoutError, ValueError, OSError):
            pass
        finally:
            if wid:
                with self.lock:
                    self.conns.pop(wid, None)
                    name = self.arena.wizards[wid].name if wid in self.arena.wizards else wid
                    self.arena.remove(wid)
                    if self.host_id == wid:
                        self.host_id = next(iter(self.conns), None)
                    lobby = self.lobby_msg()
                self.log(f'{name} left')
                self._broadcast(lobby)

    def _tick_loop(self):
        last = time.monotonic()
        state_acc = 0.0
        while self.running:
            time.sleep(1 / 30)
            now = time.monotonic()
            dt = min(0.1, now - last)
            last = now
            state_acc += dt
            with self.lock:
                self.arena.tick(dt)
                events, self.arena.events = self.arena.events, []
                conns = dict(self.conns)
                states = {wid: self.arena.state(wid) for wid in conns} if state_acc >= 1 / 15 or events else None
                if states:
                    state_acc = 0.0
                    status = self.strategist.status
                    for st in states.values():
                        st['gemini'] = status
            for e in events:
                if e['kind'] == 'reject':
                    if e['dst'] in conns:
                        self._send(conns[e['dst']], e)
                else:
                    self._broadcast(e, conns)
            if states:
                for wid, st in states.items():
                    self._send(conns[wid], st)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='Wander arena server')
    ap.add_argument('--port', type=int, default=DEFAULT_PORT)
    ap.add_argument('--gemini-interval', type=float, default=6.0, help='seconds between Gemini bot plans')
    a = ap.parse_args(argv)
    srv = ArenaServer(port=a.port, gemini_interval=a.gemini_interval).start()
    print('Players join with:  python main.py   ->  Arena  ->  Join  ->', f'{lan_ip()}:{a.port}')
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.stop()
