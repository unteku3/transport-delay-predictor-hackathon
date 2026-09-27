"""Public packet and configuration contracts. Time is naive-wall-clock seconds."""
from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Any, Mapping

import pandas as pd


def number(value: Any) -> float | None:
    """Finite scalar only; do not turn missing values into zero."""
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (TypeError, ValueError):
        return None


def wall_seconds(value: Any) -> float | None:
    """Encode naive datetime as seconds without assigning a timezone."""
    if isinstance(value, (int, float)):
        return number(value)
    if value is None or str(value).strip() in ('', 'NaT', 'nan'):
        return None
    try:
        t = pd.Timestamp(value)
        if t.tzinfo is not None:
            raise ValueError('Timezone-aware input requires an explicit adapter')
        return t.value / 1e9 if not pd.isna(t) else None
    except (TypeError, OverflowError):
        return None
    except ValueError as e:
        if 'Timezone-aware' in str(e):
            raise
        return None


@dataclass(frozen=True)
class Packet:
    """Decoded navigation record with stable ingestion sequence and raw payload."""
    seq: int
    tr_id: str
    unit_id: str
    packet_id: str
    event: float | None
    receive: float | None
    lon: float | None
    lat: float | None
    speed: float | None
    heading: float | None
    alt: float | None
    valid: bool
    raw: Mapping[str, Any] = field(compare=False, repr=False)

    @classmethod
    def from_row(cls, row: Mapping[str, Any], seq: int) -> 'Packet':
        """Preserve source strings; packet_id is never converted through float."""
        raw = dict(row)
        return cls(seq, str(row.get('tr_id', '')), str(row.get('unit_id', '')),
                   str(row.get('packet_id', '')), wall_seconds(row.get('event_time')),
                   wall_seconds(row.get('receive_time')), number(row.get('lon')),
                   number(row.get('lat')), number(row.get('speed')),
                   number(row.get('heading')), number(row.get('alt')),
                   str(row.get('location_valid', '')).lower() in ('true', '1'), raw)

    def fingerprint(self) -> tuple:
        """Retransmission identity ignores delivery time, includes all other raw fields."""
        return tuple((k, str(v)) for k, v in sorted(self.raw.items()) if k != 'receive_time')


@dataclass(frozen=True)
class Config:
    """Bounded engineering defaults; calibrated values loaded from config.json."""
    filter_speed: bool = False
    quarantine_gps: bool = False
    detect_stops: bool = False
    high_speed_kmh: float = 115.0
    discrepancy_kmh: float = 60.0
    jump_speed_kmh: float = 130.0
    jump_slack_m: float = 100.0
    gps_compare_max_s: float = 120.0
    recovery_gap_s: float = 300.0
    candidate_span_s: float = 30.0
    candidate_progress_m: float = 30.0
    window_s: float = 300.0
    position_ttl_s: float = 300.0
    speed_ttl_s: float = 120.0
    heading_ttl_s: float = 60.0
    heading_min_speed: float = 5.0
    stop_window_s: float = 60.0
    stop_min_span_s: float = 45.0
    stop_max_gap_s: float = 30.0
    stop_radius_m: float = 15.0
    stop_progress_m: float = 6.0
    stop_exit_m: float = 20.0
    stop_exit_speed: float = 5.0
    stop_fresh_s: float = 45.0
    max_history: int = 512
    dedup_capacity: int = 1024
    max_pending_per_vehicle: int = 128


FLAGS = (
    'invalid_time', 'invalid_key', 'clock_inconsistent', 'pending_overflow',
    'exact_duplicate', 'packet_id_conflict', 'event_conflict', 'late',
    'position_invalid', 'position_missing', 'coordinate_range', 'zero_coordinates',
    'outside_moscow', 'altitude_suspect', 'speed_invalid', 'high_speed',
    'speed_disagreement', 'speed_excluded', 'gps_jump', 'gps_quarantined',
    'candidate_return', 'candidate_recovery', 'gap_recovery', 'recovery_uncertain',
    'same_event_position_ignored', 'same_event_speed_ignored',
    'heading_missing', 'heading_normalized', 'heading_low_speed',
    'stationary', 'movement_start',
)


@dataclass(frozen=True)
class Decision:
    """Immutable decision emitted at availability time, never rewritten later."""
    packet: Packet
    decision_time: float | None
    flags: frozenset[str]
    lon_clean: float | None = None
    lat_clean: float | None = None
    speed_clean: float | None = None
    heading_clean: float | None = None
    gps_speed_kmh: float | None = None

    @property
    def position_accepted(self) -> bool:
        return self.lon_clean is not None

    @property
    def speed_accepted(self) -> bool:
        return self.speed_clean is not None

    def as_row(self) -> dict:
        """Original CSV fields plus explicit clean fields and machine-readable reasons."""
        return {**self.packet.raw, 'source_seq': self.packet.seq,
                'decision_time_s': self.decision_time, 'lon_clean': self.lon_clean,
                'lat_clean': self.lat_clean, 'speed_clean': self.speed_clean,
                'heading_clean': self.heading_clean, 'gps_speed_kmh': self.gps_speed_kmh,
                'position_accepted': self.position_accepted, 'speed_accepted': self.speed_accepted,
                'reasons': '|'.join(sorted(self.flags)),
                **{'flag_' + f: f in self.flags for f in FLAGS}}
