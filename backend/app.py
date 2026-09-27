import asyncio
from contextlib import asynccontextmanager, suppress
import json
import logging
import math
import os
from pathlib import Path
import time
import httpx
import anyio
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from backend.state import State
from backend.contracts import (VehicleState, Alert, NetworkSummary, RouteInfo, RouteDetail,
    RouteTimeline, ProblemSegments, WhatIfRequest, WhatIfResult, SystemStatus, Metrics,
    EmulatorControl, VehicleTrack)
from ml.service.contracts import PredictResponse
from backend.ndtp import read_frame, decode_navigation, REPLAY_SERVICE
from cleaning.types import wall_seconds

log = logging.getLogger(__name__)


class Runtime:
    def __init__(self, state):
        self.state = state
        self.queue = asyncio.Queue(maxsize=4096)
        self.predictions = asyncio.Queue(maxsize=512)
        self.clients = set()
        self.connections = set()
        self.reader_tasks = set()
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(3), trust_env=False)
        self.ml_url = os.getenv('ML_URL', 'http://ml:8001')
        self.emulator_url = os.getenv('EMULATOR_URL', 'http://emulator:18080')
        self.session = 0

    async def connection(self, reader, writer):
        self.reader_tasks.add(asyncio.current_task())
        self.connections.add(writer)
        metadata = None
        try:
            while True:
                service, kind, peer, request, body = await read_frame(reader)
                self.state.last_stream_wall = time.monotonic()
                self.state.stream_connected = True
                if service == REPLAY_SERVICE:
                    value = json.loads(body)
                    if value.get('type') == 'packet_metadata':
                        if metadata is not None:
                            raise ValueError('Replay metadata without navigation frame')
                        metadata = value
                    else:
                        await self.queue.put(value)
                elif service == 1 and kind == 101:
                    decoded = decode_navigation(body, peer, request, self.state.now)
                    if metadata:
                        row, seq = metadata['row'], metadata['seq']
                        if int(row['unit_id']) != peer or seq & 0xFFFFFFFF != request:
                            raise ValueError('Replay metadata does not match NDTP navigation')
                        metadata = None
                    else:
                        if str(peer) not in self.state.units or self.state.now is None:
                            raise ValueError('Device requires explicit unit/time mapping before standalone NDTP')
                        row, seq = decoded, request
                        row['tr_id'] = self.state.units[str(peer)]
                    await self.queue.put({'type': 'packet', 'row': row, 'seq': seq})
                elif (service, kind) != (0, 100):
                    raise ValueError('Unsupported NDTP service')
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            self.state.errors += 1
            log.warning('Rejected NDTP connection: %s', exc)
        finally:
            self.connections.discard(writer)
            self.reader_tasks.discard(asyncio.current_task())
            self.state.stream_connected = bool(self.connections)
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()

    async def consume(self):
        while True:
            value = await self.queue.get()
            s = self.state
            try:
                kind = value['type']
                if kind == 'hello':
                    s.units = {str(k): str(v) for k, v in value['units'].items()}
                    if value['time_basis'] != 'dataset_wall_seconds':
                        raise ValueError('Unspecified time adapter')
                    s.speed = float(value['speed'])
                    if not math.isfinite(float(value['now'])) or not .1 <= s.speed <= 120:
                        raise ValueError('Invalid replay clock')
                    if s.now is None:
                        s.advance(float(value['now']))
                    s.clock_value, s.clock_wall = max(s.now, float(value['now'])), time.monotonic()
                elif kind == 'packet':
                    if s.units.get(str(value['row']['unit_id'])) != str(value['row']['tr_id']):
                        raise ValueError('Unknown device/vehicle mapping')
                    s.ingest(value['row'], value['seq'])
                elif kind == 'query':
                    item = s.query(value['value'])
                    if item:
                        try:
                            self.predictions.put_nowait(item)
                        except asyncio.QueueFull:
                            s.inference_failures += 1
                            s.record_prediction(item, item['query']['cur_dev_s'], 'queue_full_current_deviation', 'historical')
                elif kind == 'fact':
                    # Fact is input to the evaluator only, after its timestamp.
                    t = float(value['time'])
                    s.advance(max(s.now or t, t))
                    s.accept_fact(value['value'])
                elif kind == 'clock':
                    now = float(value['now'])
                    if s.now is None or now >= s.now:
                        s.advance(now)
                    s.clock_value, s.clock_wall = s.now, time.monotonic()
                    s.speed = float(value['speed'])
                    s.finished = bool(value['finished'])
                else:
                    raise ValueError(f'Unknown replay message: {kind}')
            except (ValueError, KeyError, TypeError) as exc:
                s.errors += 1
                log.warning('Rejected stream event: %s', exc)
            finally:
                self.queue.task_done()

    async def infer(self):
        while True:
            first = await self.predictions.get()
            await asyncio.sleep(float(os.getenv('INFERENCE_INTERVAL', '.5')))
            batch = [first]
            while len(batch) < 128 and not self.predictions.empty():
                batch.append(self.predictions.get_nowait())
            start = time.monotonic()
            try:
                response = await self.http.post(self.ml_url+'/predict', json={'items': [
                    {'request_id': i['request_id'], 'features': i['features']} for i in batch]})
                response.raise_for_status()
                result = PredictResponse.model_validate(response.json())
                outputs = {p.request_id: p.delay_sec for p in result.predictions}
                if set(outputs) != {i['request_id'] for i in batch} or not all(math.isfinite(v) for v in outputs.values()):
                    raise ValueError('Invalid ML response IDs/values')
                self.state.ml_available = True
                for item in batch:
                    source = 'live' if self.state.status().stream_connected else 'historical'
                    self.state.record_prediction(item, outputs[item['request_id']], result.model_version, source)
            except (httpx.HTTPError, ValueError) as exc:
                self.state.ml_available = False
                self.state.inference_failures += 1
                log.warning('ML fallback: %s', exc)
                for item in batch:
                    self.state.record_prediction(item, item['query']['cur_dev_s'], 'current_deviation_fallback', 'historical')
            finally:
                self.state.latency = (time.monotonic()-start)*1000
                for _ in batch:
                    self.predictions.task_done()

    async def monitor(self):
        while True:
            try:
                response = await self.http.get(self.ml_url+'/health')
                self.state.ml_available = response.is_success
            except httpx.HTTPError:
                self.state.ml_available = False
            # On outages use the emulator's authoritative clock when available.
            # Never advance past buffered NDTP while connected.
            if not self.state.status().stream_connected and self.queue.empty():
                try:
                    r = await self.http.get(self.emulator_url+'/status')
                    r.raise_for_status()
                    data = r.json()
                    now = float(data['now'])
                    if (not self.state.status().stream_connected and self.queue.empty()
                            and self.state.now is not None and now >= self.state.now):
                        self.state.advance(now)
                    self.state.speed = float(data['speed'])
                    self.state.finished = bool(data['finished'])
                except (httpx.HTTPError, ValueError, TypeError, KeyError):
                    s = self.state
                    if not s.status().stream_connected and self.queue.empty() and s.clock_value is not None and not s.finished:
                        s.advance(max(s.now, s.clock_value+(time.monotonic()-s.clock_wall)*s.speed))
            await asyncio.sleep(1)

    def snapshot(self):
        s = self.state
        return {'vehicles': {k: v.model_dump() for k, v in s.vehicles.items()},
                'alerts': {k: a.model_dump() for k, a in s.alerts.items()},
                'summary': s.summary().model_dump(), 'system_status': s.status().model_dump()}

    async def broadcast(self):
        previous = {'vehicles': {}, 'alerts': {}}
        while True:
            s = self.state
            s.refresh()
            if not s.status().stream_connected and s.now is not None:
                for item, delay, method in s.fallback_candidates():
                    s.record_prediction(item, delay, method, 'historical')
            current = self.snapshot()
            self.session += 1
            message = {'type': 'update', 'sequence': self.session,
                'vehicles': {'upsert': [v for k, v in current['vehicles'].items() if previous['vehicles'].get(k) != v], 'removed': []},
                'alerts': {'upsert': [v for k, v in current['alerts'].items() if previous['alerts'].get(k) != v],
                           'closed': [k for k, v in current['alerts'].items() if v['status'] != 'open' and previous['alerts'].get(k, {}).get('status') == 'open']},
                'summary': current['summary'], 'system_status': current['system_status']}
            # Per-client queue prevents a slow browser from stalling ingestion.
            for q in tuple(self.clients):
                if q.full():
                    with suppress(asyncio.QueueEmpty):
                        q.get_nowait()
                    q.put_nowait({'type': 'snapshot', **current})
                else:
                    q.put_nowait(message)
            previous = current
            await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app):
    state = await asyncio.to_thread(State, Path(os.getenv('DATA_ROOT', '.')))
    runtime = Runtime(state)
    app.state.runtime = runtime
    server = await asyncio.start_server(runtime.connection, os.getenv('NDTP_BIND', '0.0.0.0'), int(os.getenv('NDTP_PORT', '9201')))
    tasks = [asyncio.create_task(fn(), name=fn.__name__) for fn in (runtime.consume, runtime.infer, runtime.monitor, runtime.broadcast)]
    app.state.tasks = tasks
    try:
        yield
    finally:
        server.close()
        await server.wait_closed()
        for writer in tuple(runtime.connections):
            writer.close()
        readers = list(runtime.reader_tasks)
        for task in tasks+readers:
            task.cancel()
        await asyncio.gather(*tasks, *readers, return_exceptions=True)
        await runtime.http.aclose()


app = FastAPI(title='Predictor Dispatch Backend', version='1.0', lifespan=lifespan)


def runtime():
    return app.state.runtime


@app.get('/api/network/summary', response_model=NetworkSummary)
async def summary():
    return runtime().state.summary()


@app.get('/api/vehicles', response_model=list[VehicleState])
async def vehicles(route_id: str | None = None, risk: str | None = Query(None, pattern='^(ok|risk|high)$'), type: str | None = None):
    return [v for v in runtime().state.vehicles.values() if (not route_id or v.route_id == route_id)
            and (not risk or v.risk_level == risk) and (not type or v.transport_type == type)]


@app.get('/api/map/tracks', response_model=list[VehicleTrack])
async def map_tracks():
    """Accepted GPS available in the stream, last 30 virtual minutes, bounded memory."""
    return runtime().state.map_tracks()


@app.get('/api/alerts', response_model=list[Alert])
async def alerts(route_id: str | None = None):
    return [a for a in runtime().state.alerts.values() if not route_id or a.route_id == route_id]


@app.get('/api/routes', response_model=list[RouteInfo])
async def routes():
    s = runtime().state
    return [s.route_info(v) for v in s.catalog.groups]


@app.get('/api/routes/{id}', response_model=RouteDetail)
async def route(id: str):
    try:
        return runtime().state.route(id)
    except KeyError as exc:
        raise HTTPException(404, 'Unknown plan') from exc


@app.get('/api/routes/{id}/timeline', response_model=RouteTimeline)
async def timeline(id: str, direction: str = Query('plan', pattern='^plan$')):
    try:
        return runtime().state.timeline(id, direction)
    except KeyError as exc:
        raise HTTPException(404, 'Unknown plan') from exc


@app.get('/api/segments/problems', response_model=ProblemSegments)
async def problems():
    return runtime().state.problems()


@app.post('/api/whatif', response_model=WhatIfResult)
async def whatif(request: WhatIfRequest):
    try:
        return runtime().state.whatif(request)
    except KeyError as exc:
        raise HTTPException(404, 'Unknown plan') from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get('/api/health', response_model=SystemStatus)
async def health():
    failed = [t.get_name() for t in app.state.tasks if t.done()]
    if failed:
        raise HTTPException(503, {'failed_tasks': failed})
    return runtime().state.status()


@app.get('/api/metrics', response_model=Metrics)
async def metrics():
    r = runtime()
    return r.state.metrics(r.queue.qsize()+r.predictions.qsize())


@app.post('/api/demo/control')
async def control(command: EmulatorControl):
    try:
        r = await runtime().http.post(runtime().emulator_url+'/control', json=command.model_dump())
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as exc:
        raise HTTPException(503, 'Replay control unavailable') from exc


@app.websocket('/ws/stream')
async def stream(ws: WebSocket):
    await ws.accept()
    r = runtime()
    queue = asyncio.Queue(maxsize=4)
    r.clients.add(queue)
    try:
        await ws.send_json({'type': 'snapshot', **r.snapshot()})
        async with anyio.create_task_group() as group:
            async def send():
                try:
                    while True:
                        await asyncio.wait_for(ws.send_json(await queue.get()), timeout=5)
                except (WebSocketDisconnect, RuntimeError, asyncio.TimeoutError):
                    pass
                finally:
                    group.cancel_scope.cancel()
            group.start_soon(send)
            try:
                while True:
                    await ws.receive_text()
            except (WebSocketDisconnect, RuntimeError):
                pass
            finally:
                group.cancel_scope.cancel()
    finally:
        r.clients.discard(queue)
