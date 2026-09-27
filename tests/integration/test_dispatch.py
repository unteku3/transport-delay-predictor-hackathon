import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from cleaning.types import Packet, wall_seconds
from backend.ndtp import navigation, read_frame, decode_navigation, control
from backend.data import Catalog, load_queries, iso
from backend.state import State, risk, expanded_online_features
from backend.contracts import WhatIfRequest
from ml.service.model import TunedCatBoost
from ml.search_features import DERIVED

ROOT = Path(__file__).resolve().parents[2]


def test_ndtp_fragmentation_crc_and_hemispheres():
    row = dict(tr_id='1', unit_id='42', packet_id='999999999999999999', event_time=1767665100,
               receive_time=1767665102, lon=-37.1234567, lat=-55.9876543,
               speed=17, heading=270, alt=100, location_valid=True)
    data = navigation(Packet.from_row(row, 7))
    async def parse(raw):
        reader = asyncio.StreamReader()
        async def feed():
            for i in range(0,len(raw),3):
                reader.feed_data(raw[i:i+3])
                await asyncio.sleep(0)
            reader.feed_eof()
        task = asyncio.create_task(feed())
        result = await read_frame(reader)
        await task
        return result
    service,kind,peer,request,body = asyncio.run(parse(data))
    decoded = decode_navigation(body,peer,request,row['receive_time'])
    assert (service,kind,peer,request)==(1,101,42,7)
    assert decoded['lat']==pytest.approx(row['lat'])
    assert decoded['lon']==pytest.approx(row['lon'])
    damaged=bytearray(data);damaged[-1]^=1
    with pytest.raises(ValueError,match='CRC'):
        asyncio.run(parse(damaged))


@pytest.fixture(scope='module')
def tuned_model():
    return TunedCatBoost(ROOT/'ml/models/optuna_features_v2')


def test_tuned_service_matches_saved_contract(tuned_model):
    from catboost import CatBoostRegressor
    directory = ROOT/'ml/models/optuna_features_v2'
    contract = json.loads((directory/'model_contract.json').read_text())
    sample = pd.DataFrame([{name: float(i + 1) for name in tuned_model.columns} for i in range(8)])
    raw = CatBoostRegressor().load_model(str(directory/contract['model_file'])).predict(sample)
    hint = sample.cur_dev_s.to_numpy()
    expected = hint + contract['spec']['correction_weight']*(raw-hint)
    records = sample.replace({np.nan: None}).to_dict('records')
    np.testing.assert_allclose(tuned_model.predict(records), expected, atol=1e-10, rtol=0)
    with pytest.raises(ValueError, match='Missing model features'):
        tuned_model.predict([{'cur_dev_s': 0}])


def test_online_derived_features_match_offline_transform(monkeypatch):
    from ml import search_features
    from ml.queries import INPUT_COLUMNS
    FEATURE_NAMES = json.loads((ROOT/'ml/models/optuna_features_v2/model_contract.json').read_text())['features']
    row = {name: 12.0 for name in FEATURE_NAMES}
    row.update(speed_mean_60s=18, speed_mean_300s=9, speed=0,
               distance_target_m=900, horizon_s=600, speed_span_300s=150,
               position_span_300s=240, approach_mps_60s=2, approach_mps_180s=1)
    monkeypatch.setattr(search_features, 'build_features', lambda *args: pd.DataFrame([row]))
    queries = pd.DataFrame(columns=INPUT_COLUMNS + ['target_lon', 'target_lat'])
    expected = search_features.expanded_features([], queries, None).iloc[0]
    actual = expanded_online_features(row)
    for name in DERIVED:
        assert actual[name] == pytest.approx(expected[name], nan_ok=True)


@pytest.fixture
def state():
    return State(ROOT)


def example(state):
    q=load_queries(ROOT,state.catalog,'test')[0]
    t=wall_seconds(q['T'])
    item=state.query(q)
    return q,t,item


def test_alert_is_issued_now_never_backdated(state):
    q,t,item=example(state)
    state.advance(t+2)
    state.record_prediction(item,360,'optuna-features-v2','live')
    alert=state.alerts[item['request_id']]
    assert alert.issued_at==iso(t+2)
    assert 10<=alert.lead_time_min<=15
    assert alert.delay_probability is None
    assert alert.predicted_error_min is None
    state.advance(wall_seconds(q['target_time_begin'])-599)
    item=dict(item,request_id='late-response')
    state.record_prediction(item,600,'optuna-features-v2','live')
    assert 'late-response' not in state.forecasts
    assert alert.issued_at==iso(t+2)


def test_future_hidden_and_missing_facts_cannot_score(state):
    q,t,item=example(state)
    state.record_prediction(item,180,'optuna-features-v2','live')
    target=wall_seconds(q['target_time_begin'])
    fact=dict(event_id=q['target_stop_id'],vehicle_id=q['tr_id'],delay_sec=120,
              actual_time=target+120,planned_time=target,available_at=target+120)
    with pytest.raises(ValueError,match='Future'):state.accept_fact(fact)
    assert state.evaluate()['evaluated_predictions']==0
    state.advance(target+120)
    state.accept_fact(fact)
    assert state.evaluate()['forecast_mae_min']==1
    assert state.evaluate()['alert_precision']==0
    with pytest.raises(ValueError,match='Forbidden'):
        state.accept_fact(dict(fact,event_id=next(iter(state.catalog.guard.blocked))))


def test_unknown_fact_is_not_negative_and_reserve_is_not_magic(state):
    q,t,item=example(state)
    state.record_prediction(item,300,'optuna-features-v2','live')
    state.advance(wall_seconds(q['target_time_begin'])+3700)
    assert state.evaluate()['evaluated_predictions']==0
    assert state.alerts[item['request_id']].status=='unverified'
    result=state.whatif(WhatIfRequest(route_id=f'plan-{q["tr_id"]}',action='reserve'))
    assert result.before==result.after
    assert result.applied is False


def test_reconnected_transport_cannot_roll_back_state(state):
    q,t,item=example(state)
    with pytest.raises(ValueError,match='Nonfinite'):
        state.advance(float('nan'))
    state.ingest(dict(tr_id=q['tr_id'],unit_id='1',packet_id='old',receive_time=t-1,
                      event_time=t-2,lon=37,lat=55,speed=10,heading=0,location_valid=True),0)
    assert state.now==t
    assert state.discarded_transport_packets==1
    assert state.packets==0


def test_clock_poll_cannot_overtake_a_reconnecting_tcp_stream(state):
    import time
    from backend.app import Runtime
    q,t,item=example(state)
    async def check():
        rt=Runtime(state)
        await rt.http.aclose()
        observed=asyncio.Event()
        class FakeClient:
            async def get(self,url):
                if url.endswith('/health'):
                    return SimpleNamespace(is_success=True)
                # The HTTP request started disconnected, but TCP returned while
                # awaiting it. Its clock must not jump ahead of the TCP consumer.
                state.stream_connected=True
                state.last_stream_wall=time.monotonic()
                observed.set()
                return SimpleNamespace(raise_for_status=lambda:None,
                    json=lambda:{'now':t+60,'speed':10,'finished':False})
        rt.http=FakeClient()
        task=asyncio.create_task(rt.monitor())
        await asyncio.wait_for(observed.wait(),1)
        assert state.now==t
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)
    asyncio.run(check())


def test_api_contract_and_error_responses(monkeypatch):
    from backend.app import app
    monkeypatch.setenv('NDTP_PORT','0')
    monkeypatch.setenv('DATA_ROOT',str(ROOT))
    monkeypatch.setenv('ML_URL','http://127.0.0.1:1')
    monkeypatch.setenv('EMULATOR_URL','http://127.0.0.1:1')
    with TestClient(app) as client:
        assert client.get('/docs').status_code==200
        schema=client.get('/openapi.json').json()
        assert 'VehicleState' in schema['components']['schemas']
        for path in ['/api/network/summary','/api/map/tracks','/api/vehicles','/api/alerts','/api/routes','/api/segments/problems','/api/health','/api/metrics']:
            assert client.get(path).status_code==200
        assert client.get('/api/routes/missing').status_code==404
        assert client.get('/api/vehicles?risk=nonsense').status_code==422
        assert client.post('/api/whatif',json={'route_id':'missing','action':'hold'}).status_code==404
        with client.websocket_connect('/ws/stream') as ws:
            assert ws.receive_json()['type']=='snapshot'


def test_dashboard_contains_no_mock_forecasts():
    logic=(ROOT/'dashboard/live.js').read_text()
    html=(ROOT/'dashboard/index.html').read_text()
    assert logic in html
    for token in ['const ALERTS=', 'const V144=', 'const WI=', '612 ТС', '04312', 'ДТП на Варшавском']:
        assert token not in html


def test_map_tracks_only_accepted_ordered_bounded_points():
    state = State(ROOT)
    def point(t, accepted=True):
        return SimpleNamespace(position_accepted=accepted, packet=SimpleNamespace(tr_id='122048', event=t),
                               lat_clean=55.65, lon_clean=37.7)
    state.track_decision(point(100))
    state.track_decision(point(101, False))
    state.track_decision(point(99))
    state.track_decision(point(200))
    state.now = 200
    assert len(state.map_tracks()[0].points) == 2
    state.track_decision(point(2001))
    state.now = 2001
    assert len(state.map_tracks()[0].points) == 1
    state.now = 4000
    assert state.map_tracks()[0].points == []


def test_next_stop_eta_is_not_mislabeled_as_v3_forecast():
    state = State(ROOT)
    selected = None
    for vid, plan in state.catalog.groups.items():
        for i in range(len(plan)-1):
            a, b = plan.iloc[i], plan.iloc[i+1]
            if all(np.isfinite(v) for v in (a.target_lon, a.target_lat, b.target_lon, b.target_lat)):
                selected = vid, a, b
                break
        if selected:
            break
    assert selected is not None
    vid, a, b = selected
    t0, t1 = wall_seconds(a.time_begin), wall_seconds(b.time_begin)
    now = (t0+t1)/2
    state.ingest(dict(tr_id=vid, unit_id='1', packet_id='eta', receive_time=now,
        event_time=now, lon=(a.target_lon+b.target_lon)/2,
        lat=(a.target_lat+b.target_lat)/2, speed=10, heading=0,
        location_valid=True), 1)
    state.hints[vid] = (now, 180)
    state.refresh()
    vehicle = state.vehicles[vid]
    assert vehicle.next_stop_event_id == str(b.tt_action_item_id)
    assert vehicle.next_stop_planned_time == iso(t1)
    assert vehicle.next_stop_eta == iso(t1+180)
    assert vehicle.next_stop_predicted_delay_min == 3
    assert vehicle.next_stop_prediction_method == 'current_deviation_next_stop_eta'
    assert vehicle.next_stop_risk_level == 'risk'
