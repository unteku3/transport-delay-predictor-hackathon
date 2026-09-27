"""Single-owner causal stream state, immutable forecast ledger and honest metrics."""
from collections import defaultdict, deque
import json
import math
from pathlib import Path
import statistics
import time
import numpy as np
from cleaning.replay import snapshot
from cleaning.telemetry import Cleaner
from cleaning.types import Config, Packet, wall_seconds
from ml.context import ContextFeatures
from ml.features import History, describe
from backend.contracts import (Alert, VehicleState, SystemStatus, NetworkSummary, Metrics,
    RouteInfo, RouteDetail, PlanStop, RouteTimeline, TimelinePoint, ProblemSegment, ProblemSegments,
    WhatIfResult, ScenarioValues, VehicleTrack, TrackPoint)
from backend.data import Catalog, iso, finite

MODEL_METHODS = {'optuna-features-v2'}


def risk(delay):
    return 'high' if delay is not None and delay > 300 else 'risk' if delay is not None and delay > 120 else 'ok'


def stop_name(row):
    value = row.building_address
    if isinstance(value, str) and value.strip() and value.strip().lower() != 'nan':
        return value.strip()
    return f'Событие {row.tt_action_item_id}'


def expanded_online_features(features):
    """The exact target-free derived transform used by optuna_features_v2."""
    def value(name):
        result = features.get(name)
        return float(result) if result is not None and math.isfinite(float(result)) else math.nan

    def difference(left, right):
        a, b = value(left), value(right)
        return a-b if math.isfinite(a) and math.isfinite(b) else math.nan

    distance, horizon = value('distance_target_m'), value('horizon_s')
    speed, mean_speed = value('speed'), value('speed_mean_300s')
    required = 3.6*distance/horizon if math.isfinite(distance) and horizon > 0 else math.nan
    eta_current = 3.6*distance/speed if math.isfinite(distance) and speed > 0 else math.nan
    eta_mean = 3.6*distance/mean_speed if math.isfinite(distance) and mean_speed > 0 else math.nan
    return {
        'speed_recent_change_kmh': difference('speed_mean_60s', 'speed_mean_300s'),
        'required_straight_speed_kmh': required,
        'straight_eta_current_s': eta_current,
        'straight_eta_mean300_s': eta_mean,
        'straight_eta_margin_s': eta_mean-horizon if math.isfinite(eta_mean) and math.isfinite(horizon) else math.nan,
        'speed_minus_required_kmh': speed-required if math.isfinite(speed) and math.isfinite(required) else math.nan,
        'approach_change_mps': difference('approach_mps_60s', 'approach_mps_180s'),
        'speed_window_coverage': value('speed_span_300s')/300,
        'position_window_coverage': value('position_span_300s')/300,
    }


def match_plan(plan, lon, lat, now, deviation=0):
    """Project on nearby-in-time plan chords. This is NOT road-network matching."""
    if lon is None or lat is None:
        return None
    scale_x = 111320 * math.cos(math.radians(lat))
    best = None
    if 'wall_times' not in plan.attrs:
        plan.attrs['wall_times'] = np.array([wall_seconds(t) for t in plan.time_begin])
    times = plan.attrs['wall_times']
    candidates = np.flatnonzero((times[:-1]-1800 <= now-deviation) & (times[1:]+1800 >= now-deviation))
    for i in candidates:
        a, b = plan.iloc[i], plan.iloc[i+1]
        t0, t1 = times[i], times[i+1]
        if t0 is None or t1 is None or not t0-1800 <= now-deviation <= t1+1800:
            continue
        values = [a.target_lon, a.target_lat, b.target_lon, b.target_lat]
        if not all(math.isfinite(v) for v in values):
            continue
        ax, ay = (a.target_lon-lon)*scale_x, (a.target_lat-lat)*111320
        dx, dy = (b.target_lon-a.target_lon)*scale_x, (b.target_lat-a.target_lat)*111320
        den = dx*dx+dy*dy
        f = max(0, min(1, -(ax*dx+ay*dy)/den)) if den else 0
        distance = math.hypot(ax+f*dx, ay+f*dy)
        candidate = (distance, i, f)
        if best is None or candidate < best:
            best = candidate
    # Do not force a position onto a distant chord.
    return best if best is not None and best[0] <= 500 else None


class State:
    def __init__(self, root: Path):
        self.catalog = Catalog(root)
        self.cleaner = Cleaner(Config(**json.loads((root/'cleaning/config.json').read_text())))
        self.context = ContextFeatures(self.catalog.plan)
        self.histories = defaultdict(History)
        self.vehicles = {}
        self.tracks = defaultdict(lambda: deque(maxlen=1800))
        self.hints = {}
        self.forecasts = {}  # append-only request ledger; no retrospective overwrite
        self.alerts = {}
        self.facts = {}
        self.profiles = defaultdict(lambda: deque(maxlen=200))
        self.units = {}
        self.now = None
        self.speed = 10.0
        self.clock_wall = time.monotonic()
        self.clock_value = None
        self.stream_connected = False
        self.last_stream_wall = None
        self.last_packet_time = None
        self.finished = False
        self.ml_available = False
        self.packets = 0
        self.errors = 0
        self.discarded_transport_packets = 0
        self.started_wall = time.monotonic()
        self.inference_failures = 0
        self.latency = None
        self.segment_entered = {}
        self.last_fallback = -math.inf

    def track_decision(self, d):
        if not d.position_accepted or d.packet.event is None:
            return
        points = self.tracks[d.packet.tr_id]
        point = (d.packet.event, float(d.lat_clean), float(d.lon_clean))
        if points and point[0] <= points[-1][0]:
            return
        points.append(point)
        while points and points[0][0] < point[0]-1800:
            points.popleft()

    def map_tracks(self):
        cutoff = (self.now or 0)-1800
        return [VehicleTrack(vehicle_id=vid, route_id=f'plan-{vid}',
            points=[TrackPoint(event_time=iso(t), lat=lat, lon=lon)
                    for t, lat, lon in points if t >= cutoff])
                for vid, points in self.tracks.items()]

    def advance(self, now):
        if not isinstance(now, (int, float)) or not math.isfinite(now):
            raise ValueError('Nonfinite stream time')
        if self.now is not None and now < self.now:
            raise ValueError('Stream time rollback')
        for d in self.cleaner.advance(now):
            self.histories[d.packet.tr_id].decision(d)
            self.track_decision(d)
        self.now = now

    def ingest(self, row, seq):
        p = Packet.from_row(row, seq)
        if p.tr_id not in self.catalog.groups or p.receive is None:
            return
        if self.now is not None and p.receive < self.now:
            self.discarded_transport_packets += 1  # no backfill after a disconnected session
            return
        for d in self.cleaner.ingest(p):
            self.histories[d.packet.tr_id].decision(d)
            self.track_decision(d)
        self.histories[p.tr_id].receive(p)
        self.now = p.receive
        self.packets += 1
        self.last_packet_time = p.receive
        self.vehicles.setdefault(p.tr_id, VehicleState(vehicle_id=p.tr_id, route_id=f'plan-{p.tr_id}'))

    def query(self, data):
        q = self.catalog.query(data)
        t = wall_seconds(q.T)
        if self.now is not None and t < self.now:
            return None
        self.advance(t)
        self.hints[str(q.tr_id)] = (t, q.cur_dev_s)
        f = describe(self.cleaner, str(q.tr_id), t, q, self.histories[str(q.tr_id)])
        f.update(self.context(self.cleaner, str(q.tr_id), t, q))
        f.update(expanded_online_features(f))
        self.vehicles.setdefault(str(q.tr_id), VehicleState(vehicle_id=str(q.tr_id), route_id=f'plan-{q.tr_id}'))
        # JSON has null for missing measurements, never NaN.
        return {'request_id': str(q.sample_id), 'query': vars(q),
                'features': {k: v if isinstance(v, str) else finite(v) for k, v in f.items()}}

    def status(self):
        age = time.monotonic()-self.last_stream_wall if self.last_stream_wall else None
        connected = self.stream_connected and age is not None and age < 4
        state = 'finished' if self.finished else 'starting' if self.now is None else 'online' if connected and self.ml_available else 'degraded'
        return SystemStatus(status=state, stream_connected=connected, ml_available=self.ml_available,
            virtual_time=iso(self.now), speed=self.speed, last_packet_at=iso(self.last_packet_time),
            stream_age_wall_sec=age, replay_finished=self.finished,
            detail='Replay complete' if self.finished else 'NDTP replay + Optuna features v2' if state == 'online'
            else 'Waiting for NDTP' if self.now is None else 'Stream or ML unavailable; fallback is explicitly labelled')

    def refresh(self):
        if self.now is None:
            return
        self.advance(self.now)
        connected = self.status().stream_connected and not self.finished
        for vid, v in self.vehicles.items():
            s = snapshot(self.cleaner, vid, self.now)
            age = finite(s['message_age_s'])
            v.is_stale = not connected or age is None or age > 60
            v.last_update = iso(self.now-age) if age is not None else None
            # Keep last known GPS for the map with explicit stale status; features
            # still use snapshot TTLs and therefore do not reuse expired GPS.
            for name, key in [('lon', 'lon'), ('lat', 'lat'), ('heading', 'heading')]:
                value = finite(s[key])
                if value is not None:
                    setattr(v, name, value)
            v.speed_kmh = finite(s['speed'])
            v.dwell_sec = finite(s['stationary_duration_s'])
            hint = self.hints.get(vid)
            v.deviation_now_min = hint[1]/60 if hint else None
            v.hint_age_sec = self.now-hint[0] if hint else None
            if v.hint_age_sec is not None and v.hint_age_sec > 900:
                v.deviation_now_min = None
            v.source = 'historical' if v.is_stale or (v.prediction_method and v.prediction_method not in MODEL_METHODS) else 'live'
            plan = self.catalog.groups[vid]
            match = match_plan(plan, finite(s['lon']), finite(s['lat']), self.now, hint[1] if hint else 0)
            if match:
                distance, i, fraction = match
                a, b = plan.iloc[i], plan.iloc[i+1]
                segment = f'{a.tt_action_item_id}:{b.tt_action_item_id}'
                if v.segment_id != segment:
                    self.segment_entered[vid] = self.now
                v.segment_id, v.segment_name = segment, f'{stop_name(a)} → {stop_name(b)}'
                v.next_stop = stop_name(b)
                v.next_stop_event_id = str(b.tt_action_item_id)
                next_planned = wall_seconds(b.time_begin)
                v.next_stop_planned_time = iso(next_planned)
                v.matched_plan_position = float(i+fraction)
                v.match_distance_m = distance
                samples = [speed for t, speed in self.histories[vid].speeds if t >= max(self.now-300, self.segment_entered.get(vid, self.now))]
                v.avg_segment_speed_kmh = statistics.mean(samples) if samples else None

                # A model forecast is valid for its explicit target event and
                # must not be relabelled as a next-stop forecast.  Use it only when
                # that target really is the matched next stop.  Otherwise expose a
                # transparent ETA baseline from the latest allowed current-deviation
                # hint.  This keeps the UI honest without changing model weights.
                if (v.forecast_event_id == v.next_stop_event_id
                        and v.forecast_deviation_15_min is not None):
                    next_delay = v.forecast_deviation_15_min
                    next_method = v.prediction_method
                    next_issued = v.forecast_issued_at
                elif v.deviation_now_min is not None and hint is not None:
                    next_delay = v.deviation_now_min
                    next_method = 'current_deviation_next_stop_eta'
                    next_issued = iso(hint[0])
                else:
                    next_delay = next_method = next_issued = None
                next_eta = next_planned + next_delay*60 if next_delay is not None else None
                # A vehicle still matched before the stop cannot have an ETA in
                # the past.  Suppress an expired baseline rather than clamp it to
                # "now" or invent travel time.
                if next_eta is not None and next_eta <= self.now:
                    next_delay = next_method = next_issued = next_eta = None
                v.next_stop_predicted_delay_min = next_delay
                v.next_stop_prediction_method = next_method
                v.next_stop_prediction_issued_at = next_issued
                v.next_stop_eta = iso(next_eta)
                v.next_stop_risk_level = risk(next_delay*60 if next_delay is not None else None)
            else:
                v.matched_plan_position = v.match_distance_m = None
                v.avg_segment_speed_kmh = None
                v.segment_id = v.segment_name = v.next_stop = None
                v.next_stop_event_id = v.next_stop_planned_time = v.next_stop_eta = None
                v.next_stop_predicted_delay_min = v.next_stop_prediction_method = None
                v.next_stop_prediction_issued_at = None
                v.next_stop_risk_level = 'ok'
            if v.forecast_event_time and wall_seconds(v.forecast_event_time) <= self.now:
                v.forecast_deviation_15_min = None
                v.forecast_event_id = v.forecast_event_time = v.forecast_issued_at = None
                v.risk_level = 'ok'

    def record_prediction(self, item, delay, method, source):
        q = item['query']
        key = item['request_id']
        if key in self.forecasts or self.now is None or not math.isfinite(delay):
            return
        target = wall_seconds(q['target_time_begin'])
        issued = self.now  # completion time, NEVER the older query time
        lead = (target-issued)/60
        if not 10 <= lead <= 15:
            return
        vid, event = str(q['tr_id']), str(q['target_stop_id'])
        f = {'request_id': key, 'vehicle_id': vid, 'event_id': event, 'target': target,
             'issued': issued, 'delay': delay, 'lead': lead, 'method': method, 'source': source,
             'actual': None}
        self.forecasts[key] = f
        v = self.vehicles[vid]
        v.forecast_deviation_15_min = delay/60
        v.forecast_event_id = event
        v.forecast_event_time, v.forecast_issued_at = iso(target), iso(issued)
        v.prediction_method = method
        v.source = source
        v.risk_level = risk(delay)
        if delay > 120:
            self.alerts[key] = Alert(alert_id=key, vehicle_id=vid, route_id=v.route_id,
                target_event_id=event, segment_id=v.segment_id, segment_name=v.segment_name,
                predicted_delay_min=delay/60, event_time=iso(target), issued_at=iso(issued),
                lead_time_min=lead, cause_code='forecast_exceeds_threshold',
                cause_text='Прогноз опоздания превышает 2 минуты; причина не установлена',
                evidence=f'Прогноз {delay/60:.1f} мин; доступное текущее отклонение {q["cur_dev_s"]/60:.1f} мин. Метод: {method}.',
                recommendations=['Проверить ситуацию на линии', 'Сравнить сценарии без отправки команд водителю'],
                source=source, prediction_method=method, risk_level=risk(delay))

    def accept_fact(self, fact):
        event = str(fact['event_id'])
        if event in self.catalog.guard.blocked or event not in self.catalog.events:
            raise ValueError('Forbidden or unknown evaluation event')
        row = self.catalog.events[event]
        if str(row.tr_id) != str(fact['vehicle_id']):
            raise ValueError('Fact vehicle mismatch')
        actual, planned, delay = (float(fact[k]) for k in ('actual_time', 'planned_time', 'delay_sec'))
        if not all(math.isfinite(v) for v in (actual, planned, delay)) or abs(actual-planned-delay) > .001:
            raise ValueError('Invalid evaluation fact')
        if abs(planned-wall_seconds(row.time_begin)) > .001:
            raise ValueError('Fact plan mismatch')
        if self.now is None or max(actual, planned, float(fact['available_at'])) > self.now:
            raise ValueError('Future fact')
        if event not in self.facts:
            self.facts[event] = dict(fact)
            self.profiles[str(row.tr_id)].append(delay)
        for key, f in self.forecasts.items():
            if f['event_id'] == event and f['actual'] is None:
                f['actual'] = delay
                if key in self.alerts:
                    a = self.alerts[key]
                    a.actual_delay_min = delay/60
                    a.status = 'confirmed' if delay > 120 else 'false'
                    a.resolved_at = iso(self.now)

    def fallback_candidates(self):
        if self.now is None or self.finished or self.now-self.last_fallback < 30:
            return []
        self.last_fallback = self.now
        seen = {f['event_id'] for f in self.forecasts.values()}
        out = []
        for vid in self.vehicles:
            hint = self.hints.get(vid)
            if not hint:
                continue
            for row in self.catalog.groups[vid].itertuples():
                target = wall_seconds(row.time_begin)
                if row.tt_action_item_id in seen or not 600 <= target-self.now <= 900:
                    continue
                values = self.profiles[vid]
                delay = statistics.median(values) if values else hint[1]
                method = 'past_observed_plan_median' if values else 'last_known_deviation'
                q = {'tr_id': vid, 'target_stop_id': row.tt_action_item_id,
                     'target_time_begin': row.time_begin, 'cur_dev_s': hint[1]}
                out.append(({'request_id': f'fallback-{vid}-{row.tt_action_item_id}', 'query': q}, delay, method))
                break
        return out

    def evaluate(self):
        # No fact is NOT a negative label. Retire unverified alerts after an hour.
        for a in self.alerts.values():
            if a.status == 'open' and self.now is not None and self.now-wall_seconds(a.event_time) > 3600:
                a.status = 'unverified'
        rows = [f for f in self.forecasts.values() if f['actual'] is not None]
        alerts = [a for a in self.alerts.values() if a.status in ('confirmed', 'false')]
        def mae(part):
            return statistics.mean(abs(f['delay']-f['actual'])/60 for f in part) if part else None
        return {'evaluated_predictions': len(rows), 'forecast_mae_min': mae(rows),
                'alert_precision': sum(a.status == 'confirmed' for a in alerts)/len(alerts) if alerts else None,
                'evaluated_alerts': len(alerts),
                'mean_lead_time_min': statistics.mean(a.lead_time_min for a in self.alerts.values()) if self.alerts else None,
                'model_mae_min': mae([f for f in rows if f['method'] in MODEL_METHODS]),
                'fallback_mae_min': mae([f for f in rows if f['method'] not in MODEL_METHODS])}

    def metrics(self, queue_size=0):
        return Metrics(packets_received=self.packets, protocol_errors=self.errors,
            discarded_transport_packets=self.discarded_transport_packets,
            throughput_packets_sec=self.packets/max(.001, time.monotonic()-self.started_wall),
            queue_size=queue_size, inference_latency_ms=self.latency, inference_failures=self.inference_failures,
            stream=self.status(), **self.evaluate())

    def summary(self):
        vs = list(self.vehicles.values())
        known = [v for v in vs if v.deviation_now_min is not None and not v.is_stale]
        alerts = [a for a in self.alerts.values() if a.status == 'open']
        m = self.evaluate()
        return NetworkSummary(vehicles_total=len(vs), vehicles_fresh=sum(not v.is_stale for v in vs),
            vehicles_stale=sum(v.is_stale for v in vs), deviations_known=len(known),
            on_schedule_percent=100*sum(-1 <= v.deviation_now_min <= 2 for v in known)/len(known) if known else None,
            late_now=sum(v.deviation_now_min > 2 for v in known), active_alerts=len(alerts),
            high_risk=sum(a.risk_level == 'high' for a in alerts), risk=sum(a.risk_level == 'risk' for a in alerts),
            forecast_mae_min=m['forecast_mae_min'], alert_precision=m['alert_precision'],
            mean_lead_time_min=m['mean_lead_time_min'], evaluated_predictions=m['evaluated_predictions'], virtual_time=iso(self.now))

    def route_info(self, vid):
        return RouteInfo(route_id=f'plan-{vid}', name=f'План ТС {vid}', vehicle_ids=[vid], stops_count=len(self.catalog.groups[vid]))

    def route(self, route_id):
        vid = route_id.removeprefix('plan-')
        if route_id != f'plan-{vid}' or vid not in self.catalog.groups:
            raise KeyError(route_id)
        return RouteDetail(**self.route_info(vid).model_dump(),
            stops=[PlanStop(event_id=str(r.tt_action_item_id), name=stop_name(r),
                           planned_time=iso(wall_seconds(r.time_begin)), lat=finite(r.target_lat), lon=finite(r.target_lon), order=i)
                   for i, r in enumerate(self.catalog.groups[vid].itertuples())],
            vehicles=[self.vehicles[vid]] if vid in self.vehicles else [])

    def timeline(self, route_id, direction):
        route = self.route(route_id)
        order = {s.event_id: s.order for s in route.stops}
        predicted = {}
        for f in self.forecasts.values():
            if f['event_id'] in order:
                predicted[f['event_id']] = f
        return RouteTimeline(route_id=route_id, direction=direction, virtual_time=iso(self.now),
            planned=[TimelinePoint(event_id=s.event_id, stop_order=s.order, time=s.planned_time) for s in route.stops],
            actual=[TimelinePoint(event_id=e, stop_order=order[e], time=iso(f['actual_time'])) for e, f in self.facts.items() if e in order],
            forecast=[TimelinePoint(event_id=e, stop_order=order[e], time=iso(f['target']+f['delay']), issued_at=iso(f['issued'])) for e, f in predicted.items()])

    def problems(self):
        groups = {}
        for a in self.alerts.values():
            if not a.segment_id or (self.now is not None and a.issued_at[:10] != iso(self.now)[:10]):
                continue
            if a.segment_id not in groups:
                groups[a.segment_id] = ProblemSegment(segment_id=a.segment_id, route_id=a.route_id,
                    segment_name=a.segment_name or a.segment_id, alerts_count=0, max_predicted_delay_min=a.predicted_delay_min)
            g = groups[a.segment_id]
            g.alerts_count += 1
            g.max_predicted_delay_min = max(g.max_predicted_delay_min, a.predicted_delay_min)
        return ProblemSegments(items=sorted(groups.values(), key=lambda g: -g.alerts_count)[:10], date=iso(self.now)[:10] if self.now else None)

    def whatif(self, request):
        route = self.route(request.route_id)
        vs = [v for v in route.vehicles if request.vehicle_id is None or v.vehicle_id == request.vehicle_id]
        if request.vehicle_id and request.vehicle_id not in route.vehicle_ids:
            raise ValueError('Vehicle is not on this plan')
        delays = [v.forecast_deviation_15_min for v in vs if v.forecast_deviation_15_min is not None]
        delay = max(delays) if delays else None
        after = delay
        assumptions = ['Упрощённая арифметика поверх последнего прогноза; модель не оценивает причинный эффект действий.',
                       'Команды транспорту не отправляются. Интервалы неизвестны без реестра маршрутов.']
        if request.action == 'reduce_dwell':
            available = max((v.dwell_sec or 0 for v in vs), default=0)/60
            saved = min(request.minutes, available)
            after = max(0, delay-saved) if delay is not None and delay > 0 else delay
            assumptions.append(f'Условно удаётся сократить будущую стоянку на {saved:.1f} мин (не больше наблюдаемой длительности). Уже прошедшее время не возвращается.')
        elif request.action == 'hold':
            after = delay+request.minutes if delay is not None else None
            assumptions.append(f'Удержание добавляет {request.minutes:.1f} мин к прибытию выбранного ТС.')
        else:
            assumptions.append('Резерв не уменьшает опоздание существующего ТС. Эффект на интервалы неизвестен без положения и времени выезда резерва.')
        return WhatIfResult(before=ScenarioValues(predicted_delay_min=delay), after=ScenarioValues(predicted_delay_min=after), action=request.action, assumptions=assumptions)
