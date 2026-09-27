"""Behavioural invariants and adversarial causal sequences, not visual smoothness."""
from dataclasses import replace
from pathlib import Path
import copy
import math

import pandas as pd
import pytest

from cleaning.data import DataGuard
from cleaning.replay import decisions, snapshot
from cleaning.schedule import prepare_plan
from cleaning.telemetry import Cleaner, angular_difference
from cleaning.types import Config, Packet


def packet(t=0, receive=None, lon=37.5, lat=55.7, speed=10, **kw):
    row = dict(packet_id=str(kw.pop('id', t)), tr_id='bus', unit_id='device',
               event_time=t, receive_time=t if receive is None else receive,
               lon=lon, lat=lat, speed=speed, heading=359, alt=150, location_valid=True)
    row.update(kw)
    return Packet.from_row(row, kw.pop('seq', int(t*10)))


def feed(ps, c=Config(quarantine_gps=True, filter_speed=True, detect_stops=True)):
    e = Cleaner(c); ds=[]
    for p in ps: ds.extend(e.ingest(p))
    return e, ds


def test_invalid_fields_are_missing_and_raw_is_preserved():
    p = packet(lon=float('inf'), lat=100, speed=-2, heading=None)
    raw = dict(p.raw); e, ds = feed([p])
    d = ds[-1]
    assert not d.position_accepted and not d.speed_accepted
    assert d.packet.raw == raw and d.as_row()['speed'] == -2
    assert e.state('bus').last_received == 0
    assert math.isnan(snapshot(e, 'bus', 0)['speed'])
    assert not feed([packet(lat=91)])[1][-1].position_accepted
    assert not feed([packet(location_valid=False)])[1][-1].position_accepted


def test_zero_coordinates_are_not_globally_impossible():
    _, ds = feed([packet(lon=0, lat=0)])
    assert ds[0].position_accepted and 'zero_coordinates' in ds[0].flags


def test_exact_duplicate_event_conflict_and_packet_id_conflict():
    a = packet(0, speed=10)
    repeat = Packet.from_row({**a.raw, 'receive_time': 1}, 1)
    b = packet(0, receive=2, speed=99, id='different')
    bad_id = packet(5, receive=5, id='0')
    e, ds = feed([a, repeat, b, bad_id])
    assert 'exact_duplicate' in ds[1].flags
    assert 'event_conflict' in ds[2].flags
    assert 'packet_id_conflict' in ds[3].flags
    assert e.state('bus').speed == (0,10)
    assert len(e.state('bus').speeds) == 1


def test_conflict_can_fill_only_missing_field_after_receipt():
    e = Cleaner()
    e.ingest(packet(0, speed=None, location_valid=False))
    old = copy.deepcopy(snapshot(e,'bus',0))
    d = e.ingest(packet(0,receive=5,speed=12,id='fill'))[-1]
    assert d.position_accepted and d.speed_accepted
    assert math.isnan(old['speed']) and snapshot(e,'bus',5)['speed'] == 12


def test_late_does_not_backfill_windows_or_replace_state():
    e, ds = feed([packet(10), packet(5,receive=20,lon=38,speed=30)])
    assert 'late' in ds[-1].flags
    assert e.state('bus').position.event == 10
    assert len(e.state('bus').positions) == 1
    assert e.state('bus').last_received == 20


def test_future_event_waits_for_event_time():
    e = Cleaner()
    assert e.ingest(packet(20,receive=5)) == []
    assert e.state('bus').last_received == 5 and e.state('bus').position is None
    e.advance(19); assert e.state('bus').position is None
    d=e.advance(20)[0]
    assert d.position_accepted and 'clock_inconsistent' in d.flags


def test_pending_capacity_and_invalid_time():
    e=Cleaner(Config(max_pending_per_vehicle=1))
    e.ingest(packet(100,receive=0))
    assert 'pending_overflow' in e.ingest(packet(101,receive=1))[-1].flags
    assert 'invalid_time' in e.ingest(packet(2,receive='bad'))[-1].flags
    with pytest.raises(ValueError): e.ingest(packet(0,receive=-1))


def test_teleport_and_return_quarantine_has_no_backfill():
    e, ds = feed([packet(0),packet(15,lon=39),packet(30,lon=37.5001)])
    assert 'gps_quarantined' in ds[1].flags
    assert not ds[1].position_accepted and ds[1].speed_accepted
    assert 'candidate_return' in ds[2].flags
    assert ds[2].position_accepted and not e.state('bus').candidate
    assert [x[0] for x in e.state('bus').positions] == [0,30]


def test_repetition_not_proof_and_recovery_after_gap():
    ps=[packet(0)]+[packet(t,lon=39,speed=0) for t in [15,30,45,60]]
    e,ds=feed(ps)
    assert all(not d.position_accepted for d in ds[1:])
    d=e.ingest(packet(301,lon=39,speed=0))[-1]
    assert d.position_accepted and 'recovery_uncertain' in d.flags


def test_wrong_anchor_can_recover_from_moving_candidate():
    ps=[packet(0,lon=39)]+[packet(t,lon=37.5+(t-15)*0.00005,speed=11) for t in [15,30,45]]
    e,ds=feed(ps)
    assert 'candidate_recovery' in ds[-1].flags
    assert e.state('bus').position.lon < 38


def test_jitter_long_wait_silence_and_movement():
    ps=[packet(t,lon=37.5+(0.00001 if t%30==0 else -0.00001),speed=0) for t in range(0,601,15)]
    e,ds=feed(ps)
    assert e.state('bus').stationary
    assert snapshot(e,'bus',600)['stationary_duration_s'] >= 500
    e.advance(700)
    assert math.isnan(snapshot(e,'bus',700)['stationary'])
    e.ingest(packet(710,lon=37.501,speed=10))
    assert not e.state('bus').stationary


def test_slow_real_movement_not_stop_and_turn_is_allowed():
    ps=[packet(t,lon=37.5+t*0.000005,speed=1) for t in range(0,121,15)]
    e,ds=feed(ps)
    assert not e.state('bus').stationary
    assert all(d.position_accepted for d in ds)
    d=e.ingest(packet(135,lon=ps[-2].lon,speed=4,heading=181))[-1]
    assert d.position_accepted


def test_speed_exclusion_is_separate_from_position():
    e,ds=feed([packet(0,speed=0),packet(15,speed=0),packet(30,speed=190)])
    assert 'speed_excluded' in ds[-1].flags
    assert ds[-1].position_accepted and not ds[-1].speed_accepted
    _, flagonly=feed([packet(0,speed=0),packet(15,speed=0),packet(30,speed=190)],Config())
    assert flagonly[-1].speed_accepted


def test_heading_wrap_and_expiration():
    assert angular_difference(359,1)==2
    e,ds=feed([packet(0,heading=361),packet(15,speed=0,heading=99)])
    assert ds[0].heading_clean==1 and ds[1].heading_clean is None
    e.advance(61); assert math.isnan(snapshot(e,'bus',61)['heading'])


def test_offline_is_identical_to_stream_and_future_cannot_change_past():
    ps=[packet(0),packet(20,receive=5),packet(15,receive=25),packet(40,lon=39)]
    expected=list(decisions(ps,Config(quarantine_gps=True)))
    e=Cleaner(Config(quarantine_gps=True));got=[]
    for p in ps: got.extend(e.ingest(p))
    got.extend(e.advance(40))
    assert expected==got
    prefix=Cleaner(); prefix.ingest(ps[0]);prefix.ingest(ps[1]);prefix.advance(10)
    saved=snapshot(prefix,'bus',10); frozen=copy.deepcopy(saved)
    prefix.ingest(ps[2]);prefix.ingest(ps[3])
    assert saved==frozen
    with pytest.raises(ValueError): prefix.advance(10)


def test_windows_and_caches_are_bounded():
    c=Config(max_history=8,dedup_capacity=10)
    e,_=feed([packet(t) for t in range(100)],c)
    s=e.state('bus')
    assert len(s.positions)<=8 and len(s.seen)<=10 and len(s.events)<=10




def test_schedule_preserves_equal_events_and_manual_fill():
    d=pd.DataFrame(dict(tt_action_item_id=['a','b'],tr_id=['x','x'],geom=['POINT (37 55)']*2,
                        time_begin=['2026-01-06 12:00']*2,manual_fill=[True,False]))
    p=prepare_plan(d)
    assert len(p)==2 and p.flag_plan_time_equal.all() and not p.flag_geom_invalid.any()






def test_invalid_position_does_not_remove_valid_speed():
    _,ds=feed([packet(location_valid=False,speed=42)])
    assert ds[0].speed_accepted and not ds[0].position_accepted


def test_identifiers_preserve_integer_precision_and_timezone_is_explicit():
    p=packet(id='-5517330596515664264')
    assert p.packet_id=='-5517330596515664264'
    with pytest.raises(ValueError,match='Timezone-aware'):
        Packet.from_row({**p.raw,'event_time':'2026-01-06T12:00:00+03:00'},0)


def test_real_data_guard_masks_hidden_and_synthetic_facts():
    guard = DataGuard(Path(__file__).resolve().parents[1])
    assert 'time_fact_begin' not in guard.plan
    schedule = guard.schedule(guard.root / 'train/schedule.csv')
    assert schedule.loc[schedule.fact_masked, 'time_fact_begin'].eq('').all()
    assert schedule.loc[schedule.tr_id.isin(guard.synthetic_ids), 'fact_masked'].all()
    assert schedule.loc[schedule.tt_action_item_id.isin(guard.blocked), 'fact_masked'].all()


def test_snapshot_expires_each_measurement_independently():
    engine, _ = feed([packet(0)])
    engine.advance(121)
    current = snapshot(engine, 'bus', 121)
    assert current['position_available'] == 1
    assert current['speed_available'] == 0
    assert math.isnan(current['speed']) and math.isnan(current['heading'])
    engine.advance(301)
    current = snapshot(engine, 'bus', 301)
    assert current['position_available'] == 0 and math.isnan(current['lon'])
    with pytest.raises(ValueError):
        snapshot(engine, 'bus', 300)


@pytest.mark.parametrize('extra_args,expected_speed', [([], '190.0'), (['--filter-speed'], '')])
def test_csv_export_without_model_artifacts_and_no_overwrite(tmp_path, extra_args, expected_speed):
    import csv
    import gzip
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    source = tmp_path / 'traffic.csv'
    output = tmp_path / 'clean.csv.gz'
    rows = [packet(t, speed=speed).raw for t, speed in [(0, 0), (15, 0), (30, 190)]]
    for row in rows:
        row['tr_id'] = '9000001'  # Large IDs must survive the export unchanged.
        for field in ['event_time', 'receive_time']:
            row[field] = str(pd.Timestamp('2026-01-06') + pd.Timedelta(seconds=row[field]))
    with source.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    before = source.read_bytes()
    command = [sys.executable, str(root / 'scripts/clean_csv.py'),
               '--input', str(source), '--output', str(output), *extra_args]
    subprocess.run(command, check=True, capture_output=True, text=True)
    with gzip.open(output, 'rt', newline='') as handle:
        cleaned = list(csv.DictReader(handle))
    assert len(cleaned) == 3
    assert {row['tr_id'] for row in cleaned} == {'9000001'}
    assert cleaned[-1]['speed'] == '190'
    assert cleaned[-1]['speed_clean'] == expected_speed
    assert cleaned[-1]['flag_high_speed'] == 'True'
    assert cleaned[-1]['position_accepted'] == 'True'
    assert source.read_bytes() == before
    saved = output.read_bytes()
    assert subprocess.run(command, capture_output=True).returncode != 0
    assert output.read_bytes() == saved
    command[command.index('--output') + 1] = str(source)
    assert subprocess.run(command, capture_output=True).returncode != 0
    assert source.read_bytes() == before
