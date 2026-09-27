"""One causal state machine for stream and offline replay."""
from __future__ import annotations

import heapq
import math
from statistics import median

from .state import Position, VehicleState
from .types import Config, Decision, Packet


def distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle lower bound on travelled distance, in metres."""
    a, b = math.radians(lat1), math.radians(lat2)
    q = math.sin((b-a)/2)**2 + math.cos(a)*math.cos(b)*math.sin(math.radians(lon2-lon1)/2)**2
    return 12_742_000 * math.asin(math.sqrt(min(1.0, max(0.0, q))))


def angular_difference(a: float, b: float) -> float:
    """Unsigned circular distance in degrees."""
    return abs((a - b + 180) % 360 - 180)


def separation(a: Position, b: Position) -> float:
    return distance_m(a.lon, a.lat, b.lon, b.lat)


class Cleaner:
    """Monotone clock. Call ingest in (receive_time, seq) order and advance at T.

    Neither querying the past nor ingesting a receipt behind the clock is allowed.
    Future-dated measurements are held in a bounded per-vehicle heap. The receipt
    itself updates message age immediately. Invalid receive times are audit-only.
    """

    def __init__(self, config: Config = Config()):
        self.config = config
        self.states: dict[str, VehicleState] = {}
        self.now = -math.inf
        self.last_order: tuple[float, int] = (-math.inf, -1)

    def state(self, tr_id: str) -> VehicleState:
        if tr_id not in self.states:
            self.states[tr_id] = VehicleState(self.config)
        return self.states[tr_id]

    def advance(self, now: float) -> list[Decision]:
        """Release available future-dated measurements before returning state at now."""
        if now < self.now:
            raise ValueError('Causal clock cannot go backwards')
        out = []
        for state in self.states.values():
            while state.pending and state.pending[0][0] <= now:
                event, _, packet = heapq.heappop(state.pending)
                out.append(self._apply(state, packet, event, {'clock_inconsistent'}))
        self.now = now
        return sorted(out, key=lambda d: (d.decision_time, d.packet.seq))

    def ingest(self, packet: Packet) -> list[Decision]:
        """Receive one decoded packet; output only final decisions, never future values."""
        if not packet.tr_id or packet.tr_id in ('nan', 'None'):
            return [Decision(packet, packet.receive, frozenset({'invalid_key'}))]
        if packet.receive is None:
            return [Decision(packet, None, frozenset({'invalid_time'}))]
        order = (packet.receive, packet.seq)
        if order < self.last_order or packet.receive < self.now:
            raise ValueError('Packets must arrive in stable (receive_time, seq) order')
        out = self.advance(packet.receive)
        self.last_order = order
        s = self.state(packet.tr_id)
        s.last_received = packet.receive  # also for invalid position, duplicate and late packet
        s.trim(packet.receive)
        fp = packet.fingerprint()
        flags: set[str] = set()
        if packet.event is not None and packet.receive < packet.event:
            flags.add('clock_inconsistent')
        if fp in s.seen:
            return out + [Decision(packet, packet.receive, frozenset(flags | {'exact_duplicate'}))]
        s.remember(s.seen, fp, True)
        key = (packet.unit_id, packet.packet_id)
        if packet.packet_id and key in s.packet_ids and s.packet_ids[key] != fp:
            return out + [Decision(packet, packet.receive, frozenset(flags | {'packet_id_conflict'}))]
        if packet.packet_id:
            s.remember(s.packet_ids, key, fp)
        s.receipts.append((packet.receive, packet.event))
        if packet.event is None:
            return out + [Decision(packet, packet.receive, frozenset(flags | {'invalid_time'}))]
        if packet.event > packet.receive:
            if len(s.pending) >= self.config.max_pending_per_vehicle:
                return out + [Decision(packet, packet.receive, frozenset(flags | {'pending_overflow'}))]
            heapq.heappush(s.pending, (packet.event, packet.seq, packet))
        else:
            out.append(self._apply(s, packet, packet.receive, flags))
        return out

    def _apply(self, s: VehicleState, p: Packet, now: float, flags: set[str]) -> Decision:
        """All accepted measurements and windows change here, at availability only."""
        c = self.config
        assert p.event is not None
        s.trim(now)
        measurement = (p.lon, p.lat, p.speed, p.heading, p.valid)
        if p.event in s.events and s.events[p.event] != measurement:
            flags.add('event_conflict')
        s.remember(s.events, p.event, measurement)
        if s.watermark is not None and p.event < s.watermark:
            flags.add('late')
            s.observations.append((now, frozenset(flags), False, False))
            return Decision(p, now, frozenset(flags))
        s.watermark = p.event
        position_ok = p.valid and p.lon is not None and p.lat is not None
        if not p.valid:
            flags.add('position_invalid')
        if p.lon is None or p.lat is None:
            flags.add('position_missing')
        elif not (-180 <= p.lon <= 180 and -90 <= p.lat <= 90):
            flags.add('coordinate_range'); position_ok = False
        else:
            if p.lon == 0 and p.lat == 0:
                flags.add('zero_coordinates')
            if not (36 <= p.lon <= 39 and 54 <= p.lat <= 57):
                flags.add('outside_moscow')  # diagnostic only
        if p.alt is not None and not (-500 <= p.alt <= 3000):
            flags.add('altitude_suspect')
        speed_ok = p.speed is not None and p.speed >= 0
        if not speed_ok:
            flags.add('speed_invalid')
        if s.position and p.event == s.position.event:
            flags.add('same_event_position_ignored'); position_ok = False
        if s.speed and p.event == s.speed[0]:
            flags.add('same_event_speed_ignored'); speed_ok = False

        new = Position(p.event, p.lon, p.lat) if position_ok else None
        jump = False
        gps_speed = None
        if new and s.position:
            dt = new.event - s.position.event
            dist = separation(new, s.position)
            jump = dt > 0 and dist > c.jump_slack_m + c.jump_speed_kmh * dt / 3.6
            if jump:
                flags.add('gps_jump')
            # Multi-interval evidence; never use suspect endpoints or tiny intervals.
            past = [x[1] for x in s.positions if 0 < new.event - x[1].event <= c.gps_compare_max_s]
            if len(past) >= 2 and not jump:
                anchor = past[0]
                span = new.event - anchor.event
                if span >= 30:
                    # Sum chords, still a lower bound on a winding road.
                    chain = past + [new]
                    gps_speed = sum(separation(a, b) for a, b in zip(chain, chain[1:])) / span * 3.6
        if p.speed is not None and p.speed > c.high_speed_kmh:
            flags.add('high_speed')
        if speed_ok and gps_speed is not None and abs(p.speed - gps_speed) > c.discrepancy_kmh:
            flags.add('speed_disagreement')
        if c.filter_speed and 'high_speed' in flags and 'speed_disagreement' in flags:
            flags.add('speed_excluded'); speed_ok = False

        if new and c.quarantine_gps and s.position:
            gap = new.event - s.position.event
            if gap >= c.recovery_gap_s:
                flags.update({'gap_recovery', 'recovery_uncertain'})
                s.candidate.clear()
                s.positions.clear()
            elif jump:
                if not s.candidate:
                    s.candidate = [new]
                else:
                    last = s.candidate[-1]
                    dt = new.event - last.event
                    coherent = dt > 0 and separation(last, new) <= c.jump_slack_m + c.jump_speed_kmh * dt / 3.6
                    if coherent:
                        s.candidate.append(new)
                        s.candidate = s.candidate[-8:]
                    else:
                        s.candidate = [new]
                seq = s.candidate
                span = seq[-1].event - seq[0].event
                progress = separation(seq[0], seq[-1])
                path_speed = progress / span * 3.6 if span > 0 else 0
                # Repetition alone is never sufficient. Re-anchor is explicitly uncertain.
                consistent = (len(seq) >= 3 and span >= c.candidate_span_s
                              and progress >= c.candidate_progress_m
                              and p.speed is not None and 1 <= p.speed <= c.jump_speed_kmh
                              and abs(p.speed-path_speed) <= c.discrepancy_kmh)
                if consistent:
                    flags.update({'candidate_recovery', 'recovery_uncertain'})
                    s.candidate.clear(); s.positions.clear()
                else:
                    flags.add('gps_quarantined'); position_ok = False
            elif s.candidate:
                flags.add('candidate_return'); s.candidate.clear()
        if position_ok:
            s.position = new
            s.positions.append((p.event, new))
        if speed_ok:
            s.speed = (p.event, p.speed)
            s.speeds.append(s.speed)
        heading = None
        if p.heading is None:
            flags.add('heading_missing')
        else:
            if not (0 <= p.heading < 360):
                flags.add('heading_normalized')
            if not speed_ok or p.speed < c.heading_min_speed:
                flags.add('heading_low_speed')
            else:
                heading = p.heading % 360
                s.heading = (p.event, heading)
        if position_ok or speed_ok:
            # One combined stop sample per event. A later field completion replaces
            # only this event from now on; previous predictions are immutable.
            sample = (p.event, s.position if s.position and s.position.event == p.event else None,
                      s.speed[1] if s.speed and s.speed[0] == p.event else None)
            if s.stop_samples and s.stop_samples[-1][0] == p.event:
                s.stop_samples[-1] = sample
            else:
                s.stop_samples.append(sample)
            self._stop(s, p.event, now, flags)
        s.observations.append((now, frozenset(flags), position_ok, speed_ok))
        return Decision(p, now, frozenset(flags), p.lon if position_ok else None,
                        p.lat if position_ok else None, p.speed if speed_ok else None,
                        heading, gps_speed)

    def _stop(self, s: VehicleState, event: float, now: float, flags: set[str]) -> None:
        """Hysteresis: entry needs dense low-speed history; exit reacts causally."""
        c = self.config
        samples = [x for x in s.stop_samples if event - x[0] <= c.stop_window_s]
        latest = samples[-1]
        was = s.stationary
        move = latest[2] is not None and latest[2] >= c.stop_exit_speed
        if c.detect_stops and latest[1] and s.stop_anchor:
            move |= separation(latest[1], s.stop_anchor) > c.stop_exit_m
        dense = (len(samples) >= 4 and event-samples[0][0] >= c.stop_min_span_s
                 and max(b[0]-a[0] for a, b in zip(samples, samples[1:])) <= c.stop_max_gap_s
                 and now-event <= c.stop_fresh_s)
        speeds = [x[2] for x in samples if x[2] is not None]
        low = (len(speeds) == len(samples) and max(speeds, default=99) <= 3
               and median(speeds or [99]) <= 1)
        compact = True
        if c.detect_stops:
            pos = [x[1] for x in samples if x[1] is not None]
            compact = (len(pos) == len(samples) and len(pos) >= 4
                       and max(separation(pos[0], x) for x in pos) <= c.stop_radius_m
                       and separation(pos[0], pos[-1]) <= c.stop_progress_m)
            if len(pos) >= 4 and event-pos[0].event >= c.stop_min_span_s:
                move |= separation(pos[0], pos[-1]) > 8
        if move or (s.stop_samples and len(samples) < 2):
            s.stationary = False; s.stationary_since = None; s.stop_anchor = None
        elif not s.stationary and dense and low and compact:
            s.stationary = True; s.stationary_since = now; s.stop_anchor = latest[1]
        if s.stationary:
            flags.add('stationary')
        if was and not s.stationary:
            flags.add('movement_start')
