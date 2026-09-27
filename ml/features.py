"""Causal feature windows over all emitted decisions, without changing Cleaner."""
from __future__ import annotations

from collections import defaultdict, deque
import math

import numpy as np
import pandas as pd

from cleaning.replay import snapshot
from cleaning.telemetry import Cleaner, distance_m
from cleaning.types import Config, Decision, Packet, wall_seconds

QUALITY_FLAGS = (
    'position_invalid', 'position_missing', 'speed_invalid', 'late',
    'clock_inconsistent', 'speed_disagreement', 'gps_jump', 'event_conflict',
    'same_event_position_ignored', 'same_event_speed_ignored', 'heading_low_speed',
    'high_speed', 'exact_duplicate', 'packet_id_conflict',
)


class History:
    """Full 300-second windows; no silent 512-element truncation in ML aggregates."""

    def __init__(self):
        self.decisions = deque()
        self.receipts = deque()
        self.speeds = deque()
        self.positions = deque()

    def trim(self, now):
        for q in (self.decisions, self.receipts, self.speeds, self.positions):
            while q and q[0][0] < now - 300:
                q.popleft()

    def receive(self, p):
        self.trim(p.receive)
        self.receipts.append((p.receive, p.event))

    def decision(self, d: Decision):
        if d.decision_time is None:
            return
        self.trim(d.decision_time)
        self.decisions.append((d.decision_time, d.flags, d.position_accepted, d.speed_accepted))
        if d.speed_accepted:
            self.speeds.append((d.packet.event, d.speed_clean))
        if d.position_accepted:
            self.positions.append((d.packet.event, d.lon_clean, d.lat_clean))


def bearing(lon1, lat1, lon2, lat2):
    a, b, dl = map(math.radians, (lat1, lat2, lon2 - lon1))
    return math.atan2(math.sin(dl) * math.cos(b),
                      math.cos(a) * math.sin(b) - math.sin(a) * math.cos(b) * math.cos(dl))


def describe(engine, tr_id, now, row, hist):
    hist.trim(now)
    f = snapshot(engine, tr_id, now)
    lon, lat, heading = f.pop('lon'), f.pop('lat'), f.pop('heading')
    f.update(cur_dev_s=float(row.cur_dev_s), horizon_s=wall_seconds(row.target_time_begin) - now)
    target_ok = math.isfinite(row.target_lon) and math.isfinite(row.target_lat)
    pos_ok = math.isfinite(lon) and math.isfinite(lat) and target_ok
    f['distance_target_m'] = distance_m(lon, lat, row.target_lon, row.target_lat) if pos_ok else math.nan
    f['heading_sin'] = math.sin(math.radians(heading))
    f['heading_cos'] = math.cos(math.radians(heading))
    f['heading_available'] = float(math.isfinite(heading))
    f['heading_to_target_cos'] = (math.cos(math.radians(heading) - bearing(lon, lat, row.target_lon, row.target_lat))
                                  if pos_ok and f['distance_target_m'] > 1 else math.nan)
    for window in (60, 300):
        values = [(t, v) for t, v in hist.speeds if t >= now - window]
        v = np.array([x[1] for x in values], dtype=float)
        for name, fun in [('mean', np.mean), ('std', np.std), ('median', np.median),
                          ('p90', lambda x: np.quantile(x, .9)), ('max', np.max),
                          ('zero_fraction', lambda x: np.mean(x == 0))]:
            f[f'speed_{name}_{window}s'] = float(fun(v)) if len(v) else math.nan
        f[f'speed_count_{window}s'] = len(v)
        span = values[-1][0] - values[0][0] if values else 0
        f[f'speed_span_{window}s'] = span
        f[f'speed_trend_{window}s'] = (values[-1][1] - values[0][1]) / span if span > 0 else math.nan
    f['position_count_300s'] = len(hist.positions)
    f['position_span_300s'] = hist.positions[-1][0] - hist.positions[0][0] if hist.positions else 0
    f['message_count_300s'] = len(hist.receipts)
    f['decision_count_300s'] = len(hist.decisions)
    for name, i in [('position', 2), ('speed', 3)]:
        f[f'{name}_accept_fraction_300s'] = float(np.mean([x[i] for x in hist.decisions])) if hist.decisions else math.nan
    for flag in QUALITY_FLAGS:
        f[f'rate_{flag}'] = float(np.mean([flag in x[1] for x in hist.decisions])) if hist.decisions else math.nan
    lags = [r - e for r, e in hist.receipts if e is not None]
    f['delivery_lag_median_300s'] = float(np.median(lags)) if lags else math.nan
    f['delivery_lag_p95_300s'] = float(np.quantile(lags, .95)) if lags else math.nan
    for window in (60, 180):
        positions = [p for p in hist.positions if p[0] >= now - window]
        span = positions[-1][0] - positions[0][0] if positions else 0
        # Compare both ends to this query's target, never to different past targets.
        f[f'approach_span_{window}s'] = span
        f[f'approach_mps_{window}s'] = (
            (distance_m(*positions[0][1:], row.target_lon, row.target_lat)
             - distance_m(*positions[-1][1:], row.target_lon, row.target_lat)) / span
            if span > 0 and pos_ok else math.nan)
    return f


def build_features(packets: list[Packet], points: pd.DataFrame, config: Config, feature_hook=None):
    """One monotonically increasing replay. Labels are never inspected here."""
    if points.sample_id.duplicated().any():
        raise ValueError('Duplicate query sample_id')
    engine = Cleaner(config)
    histories = defaultdict(History)
    result = []
    i = 0
    for row in points.sort_values(['T', 'sample_id'], kind='stable').itertuples(index=False):
        now = wall_seconds(row.T)
        if now is None:
            raise ValueError('Invalid query time')
        while i < len(packets) and (packets[i].receive is None or packets[i].receive <= now):
            p = packets[i]
            # Ingest releases earlier pending decisions before the current receipt.
            for d in engine.ingest(p):
                histories[d.packet.tr_id].decision(d)
            if p.receive is not None:
                histories[p.tr_id].receive(p)
            i += 1
        for d in engine.advance(now):
            histories[d.packet.tr_id].decision(d)
        f = describe(engine, str(row.tr_id), now, row, histories[str(row.tr_id)])
        if feature_hook is not None:
            f.update(feature_hook(engine, str(row.tr_id), now, row))
        result.append({'sample_id': row.sample_id, **f})
    return pd.DataFrame(result).set_index('sample_id').reindex(points.sample_id)
