"""CSV adapter, replay and current cleaned measurement snapshots."""
from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Iterable, Iterator

from .telemetry import Cleaner
from .types import Config, Decision, Packet


def read_packets(path: Path, excluded_tr_ids: set[str] | None = None) -> list[Packet]:
    """CSV read preserves original strings. Offline sort costs O(N log N), O(N) RAM."""
    with path.open(newline='') as f:
        result = [Packet.from_row(row, i) for i, row in enumerate(csv.DictReader(f))
                  if row['tr_id'] not in (excluded_tr_ids or set())]
    return sorted(result, key=lambda p: (p.receive if p.receive is not None else -math.inf, p.seq))


def decisions(packets: Iterable[Packet], config: Config) -> Iterator[Decision]:
    """Replay is exactly ingest + timer advance; no event-time-only shortcut."""
    engine = Cleaner(config)
    for p in packets:
        yield from engine.ingest(p)
    # Final drain is for a packet audit, never for earlier prediction snapshots.
    last = max([engine.now] + [x[0] for s in engine.states.values() for x in s.pending])
    yield from engine.advance(last)


def snapshot(engine: Cleaner, tr_id: str, now: float) -> dict[str, float]:
    """Current cleaned measurements and freshness, without model features.

    Call advance(T) after ingesting all messages received by T. A snapshot never
    fills stale fields with old values or rewrites previously returned snapshots.
    """
    if now != engine.now:
        raise ValueError('Call advance(T) before snapshot at T')
    s = engine.state(tr_id)
    c = engine.config
    s.trim(now)
    nan = math.nan
    pos_age = now - s.position.event if s.position else nan
    speed_age = now - s.speed[0] if s.speed else nan
    heading_age = now - s.heading[0] if s.heading else nan
    pos_ok = bool(s.position and pos_age <= c.position_ttl_s)
    speed_ok = bool(s.speed and speed_age <= c.speed_ttl_s)
    heading_ok = bool(s.heading and heading_age <= c.heading_ttl_s)
    stop_known = (s.watermark is not None and now - s.watermark <= c.stop_fresh_s
                  and len([x for x in s.stop_samples if now - x[0] <= c.stop_window_s]) >= 4
                  and speed_ok)
    return {
        'message_age_s': now - s.last_received if s.last_received is not None else nan,
        'position_age_s': pos_age, 'speed_age_s': speed_age, 'heading_age_s': heading_age,
        'position_available': float(pos_ok), 'speed_available': float(speed_ok),
        'lon': s.position.lon if pos_ok else nan,
        'lat': s.position.lat if pos_ok else nan,
        'speed': s.speed[1] if speed_ok else nan,
        'heading': s.heading[1] if heading_ok else nan,
        'stationary': float(s.stationary) if stop_known else nan,
        'stationary_duration_s': (now - s.stationary_since
                                  if stop_known and s.stationary_since is not None
                                  else (0.0 if stop_known else nan)),
    }
