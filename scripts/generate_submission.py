#!/usr/bin/env python3
"""Reproduce the final Optuna CSV from allowed inputs, without training or targets."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import pandas as pd
from cleaning.data import read_plan, sha256
from cleaning.replay import read_packets
from cleaning.types import Config
from ml.queries import INPUT_COLUMNS, prepare_queries
from ml.search_features import expanded_features
from ml.service.model import TunedCatBoost


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, default=ROOT)
    parser.add_argument('--model-dir', type=Path, default=ROOT/'ml/models/optuna_features_v2')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('Choose a new output file')
    hashes = json.loads((ROOT/'data/source_hashes.json').read_text())
    for name in ['validate/points.csv', 'validate/schedule_plan.csv',
                 'validate/traffic.csv', 'sample_submission.csv']:
        if sha256(args.data_root/name) != hashes[name]:
            raise ValueError(f'Source changed: {name}')
    points = pd.read_csv(args.data_root/'validate/points.csv', usecols=INPUT_COLUMNS,
                         dtype={'sample_id':str, 'tr_id':str, 'target_stop_id':str})
    queries = prepare_queries(points, read_plan(args.data_root/'validate/schedule_plan.csv'))
    config = Config(**json.loads((ROOT/'cleaning/config.json').read_text()))
    features = expanded_features(read_packets(args.data_root/'validate/traffic.csv'), queries, config)
    expected = pd.read_csv(args.data_root/'sample_submission.csv', sep=';',
                           usecols=['sample_id'], dtype=str).sample_id
    if not expected.is_unique or not features.index.is_unique or set(expected) != set(features.index):
        raise ValueError('Missing, extra or repeated sample IDs')
    model = TunedCatBoost(args.model_dir)
    if list(features) != model.columns:
        raise ValueError('Final feature order differs from model contract')
    features = features.loc[expected]
    values = model.predict(features.replace({np.nan:None}).to_dict('records'))
    result = pd.DataFrame({'sample_id':expected.to_numpy(), 'prediction':values})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as handle:
        result.to_csv(handle, sep=';', index=False)
    print(f'Created {args.output}: {len(result)} predictions, model={model.version}')


if __name__ == '__main__':
    main()
