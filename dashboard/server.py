"""Local-only development gateway; production uses nginx in its own container."""
import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import httpx
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from websockets.asyncio.client import connect

BACKEND = os.getenv('BACKEND_URL', 'http://127.0.0.1:8000')


@asynccontextmanager
async def lifespan(app):
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        app.state.client = client
        yield


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.api_route('/api/{path:path}', methods=['GET', 'POST'])
@app.get('/docs')
@app.get('/openapi.json')
async def proxy(request: Request, path: str = ''):
    try:
        result = await app.state.client.request(request.method, BACKEND+request.url.path,
            params=request.query_params, content=await request.body(), headers={'Content-Type': 'application/json'})
        return Response(result.content, result.status_code, media_type=result.headers.get('content-type'))
    except httpx.HTTPError:
        return Response('Backend unavailable', status_code=503)


@app.websocket('/ws/stream')
async def socket(ws: WebSocket):
    await ws.accept()
    tasks = []
    try:
        async with connect(BACKEND.replace('http', 'ws', 1)+'/ws/stream', proxy=None) as upstream:
            async def send():
                async for message in upstream:
                    await ws.send_text(message)
            async def receive():
                while True:
                    await ws.receive_text()
            tasks = [asyncio.create_task(send()), asyncio.create_task(receive())]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except Exception:
        try:
            await ws.close()
        except RuntimeError:
            pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


app.mount('/', StaticFiles(directory=Path(__file__).resolve().parent, html=True), name='dashboard')
