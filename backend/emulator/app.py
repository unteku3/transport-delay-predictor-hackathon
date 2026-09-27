import asyncio
from contextlib import asynccontextmanager, suppress
import logging
import os
from pathlib import Path
import time
from collections import Counter
from fastapi import FastAPI, HTTPException
from cleaning.replay import read_packets
from cleaning.types import wall_seconds
from backend.contracts import EmulatorControl
from backend.data import Catalog, load_queries, load_allowed_outcomes, iso
from backend.ndtp import control, handshake, navigation

log = logging.getLogger(__name__)


class Replay:
    def __init__(self):
        self.speed = float(os.getenv('SPEED', '10'))
        if not .1 <= self.speed <= 120:
            raise ValueError('SPEED must be between 0.1 and 120')
        self.enabled = True
        self.connected = False
        self.finished = False
        self.now = None
        self.writer = None
        self.sent = 0
        self.dropped = 0
        self.sequence = 0
        self.retry_in = 0

    def load(self):
        root = Path(os.getenv('DATA_ROOT', '.'))
        split = os.getenv('REPLAY_SPLIT', 'test')
        catalog = Catalog(root)
        queries = load_queries(root, catalog, split)
        # Pick a busy window using only query TIMES, never outcome values.
        blocks = Counter(int(wall_seconds(q['T'])//1800)*1800 for q in queries)
        busiest = min(blocks, key=lambda t: (-blocks[t], t))
        self.now = wall_seconds(os.getenv('START_TIME')) or busiest - 300
        events = []
        packets = read_packets(root / split / 'traffic.csv', catalog.guard.synthetic_ids)
        self.units = {}
        for p in packets:
            if str(p.unit_id) in self.units and self.units[str(p.unit_id)] != p.tr_id:
                raise ValueError('Ambiguous unit mapping requires an explicit fleet registry')
            self.units[str(p.unit_id)] = p.tr_id
        for p in packets:
            if p.receive is not None and p.receive >= self.now:
                events.append((p.receive, 0, p.seq, 'packet', p))
        for i, q in enumerate(queries):
            t = wall_seconds(q['T'])
            if t >= self.now:
                events.append((t, 1, i, 'query', q))
        for i, f in enumerate(load_allowed_outcomes(root, catalog, split)):
            if f['available_at'] >= self.now:
                events.append((f['available_at'], 2, i, 'fact', f))
        self.events = sorted(events, key=lambda e: e[:3])
        self.index = 0
        self.end = self.events[-1][0] if self.events else self.now

    async def send(self, value):
        self.sequence += 1
        self.writer.write(control(value, self.sequence))
        await self.writer.drain()

    async def disconnect(self):
        self.connected = False
        if self.writer:
            self.writer.close()
            with suppress(Exception):
                await self.writer.wait_closed()
        self.writer = None

    async def run(self):
        host, port = os.getenv('NDTP_HOST', 'backend'), int(os.getenv('NDTP_PORT', '9201'))
        last, retry_at, backoff = time.monotonic(), 0, .5
        started = False
        try:
            while True:
                wall = time.monotonic()
                # Freeze until first successful connection, then outages lose telemetry.
                if started and not self.finished:
                    self.now = min(self.end, self.now + (wall-last)*self.speed)
                last = wall
                self.retry_in = max(0, retry_at-wall)
                if started and not self.connected:
                    while self.index < len(self.events) and self.events[self.index][0] <= self.now:
                        self.index += 1
                        self.dropped += 1
                if self.enabled and not self.writer and wall >= retry_at and not self.finished:
                    try:
                        _, self.writer = await asyncio.wait_for(asyncio.open_connection(host, port), 2)
                        self.writer.write(handshake(0))
                        await self.send({'type': 'hello', 'now': self.now, 'speed': self.speed,
                                         'units': self.units, 'time_basis': 'dataset_wall_seconds'})
                        self.connected = True
                        started, backoff = True, .5
                    except (OSError, asyncio.TimeoutError):
                        await self.disconnect()
                        retry_at = wall + backoff
                        backoff = min(15, backoff*2)
                while started and self.index < len(self.events) and self.events[self.index][0] <= self.now:
                    t, _, _, kind, value = self.events[self.index]
                    # Nothing is replayed from the gap on reconnection.
                    self.index += 1
                    if not self.connected:
                        self.dropped += 1
                        continue
                    try:
                        if kind == 'packet':
                            await self.send({'type': 'packet_metadata', 'seq': value.seq, 'row': value.raw})
                            self.writer.write(navigation(value))
                            await self.writer.drain()
                            self.sent += 1
                        else:
                            await self.send({'type': kind, 'time': t, 'value': value})
                    except (OSError, ConnectionError):
                        await self.disconnect()
                if self.connected:
                    try:
                        await self.send({'type': 'clock', 'now': self.now, 'speed': self.speed,
                                         'finished': self.index >= len(self.events)})
                    except (OSError, ConnectionError):
                        await self.disconnect()
                self.finished = started and self.index >= len(self.events)
                await asyncio.sleep(.2)
        finally:
            await self.disconnect()


@asynccontextmanager
async def lifespan(app):
    replay = Replay()
    await asyncio.to_thread(replay.load)
    app.state.replay = replay
    task = asyncio.create_task(replay.run())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(title='NDTP historical replay', lifespan=lifespan)


@app.get('/health')
@app.get('/status')
def status():
    r = app.state.replay
    return {'ready': True, 'enabled': r.enabled, 'connected': r.connected, 'finished': r.finished,
            'now': r.now, 'virtual_time': iso(r.now), 'speed': r.speed,
            'sent': r.sent, 'dropped_events': r.dropped, 'retry_in_sec': r.retry_in}


@app.post('/control')
async def change(command: EmulatorControl):
    r = app.state.replay
    if command.action == 'disconnect':
        r.enabled = False
        await r.disconnect()
    elif command.action == 'reconnect':
        r.enabled = True
    elif command.speed is None:
        raise HTTPException(422, 'speed required')
    else:
        r.speed = command.speed
    return status()
