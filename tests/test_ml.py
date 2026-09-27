"""Causality, full-window aggregation and hidden-answer boundary."""
import math

import pandas as pd

from cleaning.types import Config, Packet
from ml.features import build_features


def packet(t, receive=None, speed=10, packet_id=None):
    return Packet.from_row(dict(packet_id=str(t if packet_id is None else packet_id),
        tr_id='1', unit_id='1', event_time=t, receive_time=t if receive is None else receive,
        lon=37.5, lat=55.7, speed=speed, heading=90, location_valid=True), int(t * 1000))


def query(t):
    return pd.DataFrame([dict(sample_id='q', tr_id='1', T=t, target_time_begin=t+600,
        cur_dev_s=120, target_lon=37.6, target_lat=55.7)])


def test_future_receipt_and_future_event_cannot_change_features():
    a = packet(10)
    f = build_features([a], query(20), Config())
    pd.testing.assert_frame_equal(f, build_features([a, packet(15, receive=25, speed=90)], query(20), Config()))
    future = build_features([a, packet(30, receive=15, speed=90)], query(20), Config())
    assert future.loc['q', 'speed'] == 10
    assert future.loc['q', 'speed_count_300s'] == 1
    assert future.loc['q', 'message_count_300s'] == 2
    assert build_features([a, packet(30, receive=15, speed=90)], query(30), Config()).loc['q', 'speed'] == 90


def test_duplicates_count_as_quality_decisions_but_not_speed_samples():
    a = packet(10)
    duplicate = Packet.from_row({**a.raw, 'receive_time': 12}, 2)
    f = build_features([a, duplicate], query(20), Config()).loc['q']
    assert f.speed_count_300s == 1
    assert f.message_count_300s == 2
    assert f.rate_exact_duplicate == .5
    assert f.speed_accept_fraction_300s == .5


def test_windows_are_not_truncated_at_cleaner_history_capacity():
    packets = [packet(i / 10) for i in range(600)]
    f = build_features(packets, query(60), Config(max_history=8)).loc['q']
    assert f.speed_count_300s == 600
    assert f.position_count_300s == 600


def test_stale_values_and_silence_are_not_stops_or_zero_speed():
    f = build_features([packet(0, speed=0)], query(400), Config()).loc['q']
    assert math.isnan(f.speed) and math.isnan(f.stationary)
    assert math.isnan(f.distance_target_m)
    assert f.position_age_s == 400 and f.speed_count_300s == 0


def test_late_does_not_enter_motion_windows():
    f = build_features([packet(10), packet(5, receive=20, speed=99)], query(20), Config()).loc['q']
    assert f.speed == 10 and f.speed_count_300s == 1 and f.rate_late == .5


def test_context_only_uses_earlier_hints_and_plan_not_answers():
    from ml.context import ContextFeatures
    plan = pd.DataFrame({'tt_action_item_id':['e1','e2'],'tr_id':['1','1'],
                         'time_begin':['1970-01-01 00:05:00','1970-01-01 00:10:00'],
                         'geom':['POINT (37.5 55.7)','POINT (37.6 55.7)']})
    points = pd.concat([query(10),query(20),query(30)],ignore_index=True)
    points['sample_id']=['a','b','c'];points['target_stop_id']='e2'
    points['cur_dev_s']=[120,100,999]
    a=build_features([packet(0)],points,Config(),ContextFeatures(plan))
    points.loc[2,'cur_dev_s']=-999
    points['target_delay_s']=123456  # must not enter the feature interface
    b=build_features([packet(0)],points,Config(),ContextFeatures(plan))
    pd.testing.assert_frame_equal(a.loc[['a','b']],b.loc[['a','b']])
    assert math.isnan(a.loc['a','previous_hint'])
    assert a.loc['b','previous_hint']==120 and a.loc['b','hint_change']==-20
