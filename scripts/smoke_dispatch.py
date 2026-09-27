"""Exercise an already running stack, including a real TCP outage and recovery."""
import argparse
import asyncio
import json
import time
import httpx
from websockets.asyncio.client import connect


async def main(base):
    async with httpx.AsyncClient(base_url=base, timeout=15, trust_env=False) as client:
        async def get(path):
            response=await client.get(path);response.raise_for_status();return response.json()
        async def post(body):
            response=await client.post('/api/demo/control',json=body);response.raise_for_status();return response.json()
        for _ in range(40):
            before=await get('/api/metrics')
            if before['packets_received']>0 and before['stream']['ml_available']:
                break
            await asyncio.sleep(.5)
        assert before['packets_received']>0, 'No NDTP packets'
        assert (await client.get('/docs')).status_code==200
        schema=await get('/openapi.json')
        assert 'VehicleState' in schema['components']['schemas']
        for path in ['/api/network/summary','/api/vehicles','/api/alerts','/api/segments/problems']:
            await get(path)
        routes=await get('/api/routes')
        for route in routes:
            await get('/api/routes/'+route['route_id'])
            await get('/api/routes/'+route['route_id']+'/timeline?direction=plan')
        async with connect(base.replace('http','ws',1)+'/ws/stream',proxy=None) as ws:
            snapshot=json.loads(await asyncio.wait_for(ws.recv(),5))
            assert snapshot['type']=='snapshot' and snapshot['vehicles']
            assert json.loads(await asyncio.wait_for(ws.recv(),5))['type']=='update'
        try:
            await post({'action':'disconnect'})
            await asyncio.sleep(3)
            degraded=await get('/api/health')
            assert degraded['status']=='degraded' and not degraded['stream_connected']
            assert all(v['is_stale'] for v in await get('/api/vehicles'))
        finally:
            await post({'action':'reconnect'})
        for _ in range(30):
            recovered=await get('/api/metrics')
            if recovered['stream']['stream_connected'] and recovered['packets_received']>before['packets_received']:
                break
            await asyncio.sleep(.5)
        assert recovered['stream']['stream_connected']
        assert recovered['packets_received']>before['packets_received']
        assert recovered['protocol_errors']==before['protocol_errors'], 'New protocol errors during reconnect'
        alerts=await get('/api/alerts')
        for a in alerts:
            assert 10<=a['lead_time_min']<=15
            assert a['delay_probability'] is None and a['predicted_error_min'] is None
        for action in ['reserve','reduce_dwell','hold']:
            r=await client.post('/api/whatif',json={'route_id':routes[0]['route_id'],'action':action})
            r.raise_for_status();assert r.json()['applied'] is False
        print(json.dumps({'result':'passed','packets':recovered['packets_received'],
            'protocol_errors':recovered['protocol_errors'],'alerts':len(alerts),
            'evaluated_predictions':recovered['evaluated_predictions'],
            'mae_min':recovered['forecast_mae_min'], 'websocket':'snapshot + delta',
            'outage':'degraded, all vehicles stale, automatic reconnect'},ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--base',default='http://127.0.0.1:8080')
    asyncio.run(main(parser.parse_args().base))
