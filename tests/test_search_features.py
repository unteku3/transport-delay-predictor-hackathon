import math
import numpy as np
import pandas as pd
from cleaning.types import Config, Packet
from ml.search_features import expanded_features
from ml.features import build_features


def query():
 return pd.DataFrame([dict(sample_id='q',tr_id='1',T=100,target_time_begin=820,
                          target_stop_id='e',cur_dev_s=30,target_lon=37.6,target_lat=55.7)])


def packet(t,receive=None,speed=10):
 return Packet.from_row(dict(tr_id='1',unit_id='1',packet_id=str(t),event_time=t,
   receive_time=t if receive is None else receive,lon=37.5,lat=55.7,speed=speed,
   heading=90,location_valid=True),int(t))


def test_extended_features_keep_base_and_are_target_and_future_independent():
 q=query();a=expanded_features([packet(80)],q,Config())
 q['target_delay_s']=-99999;q['time_fact_begin']='future'
 b=expanded_features([packet(80),packet(90,receive=110,speed=200)],q,Config())
 pd.testing.assert_frame_equal(a,b)
 base=build_features([packet(80)],q,Config())
 pd.testing.assert_frame_equal(a[base.columns],base)
 assert a.loc['q','required_straight_speed_kmh']>0


def test_eta_missing_for_zero_speed_and_stale_coordinates():
 a=expanded_features([packet(80,speed=0)],query(),Config()).loc['q']
 assert math.isnan(a.straight_eta_current_s) and math.isnan(a.straight_eta_mean300_s)
 assert not np.isinf(a.to_numpy()).any()
 q=query();q['T']=1000;q['target_time_begin']=1720
 a=expanded_features([packet(80)],q,Config()).loc['q']
 assert math.isnan(a.required_straight_speed_kmh)
 assert math.isnan(a.straight_eta_current_s)
