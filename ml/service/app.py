import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
from fastapi import FastAPI, HTTPException
from ml.service.contracts import PredictRequest, PredictResponse, Prediction, ModelInfo
from ml.service.model import TunedCatBoost


@asynccontextmanager
async def lifespan(app):
    app.state.model = await asyncio.to_thread(
        TunedCatBoost, Path(os.getenv('MODEL_DIR', 'ml/models/optuna_features_v2')))
    app.state.lock = asyncio.Lock()
    yield


app = FastAPI(title='Predictor ML · Optuna features v2', lifespan=lifespan)


@app.get('/health')
def health():
    return {'status': 'ok', 'model_version': app.state.model.version}


@app.get('/model/info', response_model=ModelInfo)
def info():
    return {'version': app.state.model.version, 'training_date': '2026-09-26',
            'training_date_source': 'experiment documentation, day precision',
            'validation_mae_sec': app.state.model.validation_mae_sec,
            'validation_protocol': 'nested grouped development validation on one studied day; not independent online validation',
            'target': 'arrival delay at supplied planned event, seconds',
            'features': app.state.model.columns, 'probability_available': False,
            'individual_error_interval_available': False, 'causes_available': False}


@app.post('/predict', response_model=PredictResponse)
async def predict(request: PredictRequest):
    try:
        async with app.state.lock:
            values = await asyncio.to_thread(app.state.model.predict, [i.features for i in request.items])
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return PredictResponse(model_version=app.state.model.version,
        predictions=[Prediction(request_id=i.request_id, delay_sec=float(p))
                     for i, p in zip(request.items, values)])


@app.post('/train', status_code=501)
def train():
    return {'detail': 'Disabled: frozen weights. Offline training is a separate explicitly authorized experiment.'}
