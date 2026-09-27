"""Load trusted inference artifacts and preserve their saved prediction formulas."""
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from catboost import CatBoostRegressor


class TunedCatBoost:
    """Online wrapper for the saved 69-feature Optuna CatBoost checkpoint."""

    version = 'optuna-features-v2'

    def __init__(self, directory: Path):
        self.contract = json.loads((directory / 'model_contract.json').read_text())
        self.columns = self.contract['expanded_feature_order']
        if self.columns != self.contract['features']:
            raise ValueError('Service supports the selected full expanded feature set only')
        spec = self.contract['spec']
        if spec['family'] != 'catboost' or spec['feature_set'] != 'expanded':
            raise ValueError('Unsupported tuned model contract')
        path = directory / self.contract['model_file']
        if path.parent.resolve() != directory.resolve():
            raise ValueError('Model file must stay inside MODEL_DIR')
        if hashlib.sha256(path.read_bytes()).hexdigest() != self.contract['model_sha256']:
            raise ValueError('Model checksum mismatch')
        self.model = CatBoostRegressor()
        self.model.load_model(str(path))
        metrics = json.loads((directory / 'nested_metrics.json').read_text())
        self.validation_mae_sec = metrics['metrics']['original_candidates']['tuned']['model_mae_s']

    def predict(self, records):
        for row in records:
            missing = set(self.columns) - set(row)
            if missing:
                raise ValueError(f'Missing model features: {sorted(missing)}')
            if row['cur_dev_s'] is None:
                raise ValueError('cur_dev_s is required; missing is not zero')
        features = pd.DataFrame(records)[self.columns]
        for column in self.columns:
            features[column] = pd.to_numeric(features[column], errors='raise').astype(float)
        hint = features.cur_dev_s.to_numpy()
        if not np.isfinite(hint).all():
            raise ValueError('cur_dev_s must be finite')
        raw = self.model.predict(features)
        spec = self.contract['spec']
        direct = raw + hint if spec['mode'] == 'residual' else raw
        result = hint + spec['correction_weight'] * (direct - hint)
        if not np.isfinite(result).all():
            raise ValueError('Nonfinite prediction')
        return result
