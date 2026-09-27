"""Read plans and allowed inputs. No hidden schedule facts are ever selected."""
from pathlib import Path
import csv
from types import SimpleNamespace
import math
import pandas as pd
from cleaning.data import DataGuard
from cleaning.schedule import prepare_plan
from cleaning.types import wall_seconds


def iso(value):
    return pd.Timestamp(value, unit='s').isoformat() if value is not None else None


def finite(value):
    try:
        return float(value) if math.isfinite(float(value)) else None
    except (TypeError, ValueError):
        return None


class Catalog:
    def __init__(self, root: Path):
        self.guard = DataGuard(root)
        self.plan = prepare_plan(self.guard.plan[~self.guard.plan.tr_id.isin(self.guard.synthetic_ids)])
        self.groups = {str(k): g.sort_values(['planned', 'tt_action_item_id'], kind='stable').reset_index(drop=True)
                       for k, g in self.plan.groupby('tr_id')}
        self.events = {str(r.tt_action_item_id): r for r in self.plan.itertuples()}

    def query(self, values):
        v = dict(values)
        event = self.events.get(str(v['target_stop_id']))
        if event is None or str(event.tr_id) != str(v['tr_id']):
            raise ValueError('Unknown vehicle/target combination')
        if abs(wall_seconds(v['target_time_begin']) - wall_seconds(event.time_begin)) > .001:
            raise ValueError('Target time differs from the immutable plan')
        v.update(target_lon=event.target_lon, target_lat=event.target_lat)
        v['cur_dev_s'] = float(v['cur_dev_s'])
        if not math.isfinite(v['cur_dev_s']):
            raise ValueError('Missing current deviation')
        return SimpleNamespace(**v)


def load_queries(root, catalog, split):
    if split not in ('train', 'test'):
        raise ValueError('Demo evaluation supports allowed train/test only')
    columns = ['sample_id', 'tr_id', 'T', 'target_stop_id', 'target_time_begin', 'cur_dev_s']
    frame = pd.read_csv(root / 'labels' / f'labels_{split}.csv', usecols=columns,
                        dtype={'sample_id': str, 'tr_id': str, 'target_stop_id': str})
    frame = frame[~frame.tr_id.isin(catalog.guard.synthetic_ids) & ~frame.target_stop_id.isin(catalog.guard.blocked)]
    return frame.sort_values(['T', 'sample_id'], kind='stable').to_dict('records')


def load_allowed_outcomes(root, catalog, split):
    # Simulator ground truth is isolated from backend feature generation. Filter raw
    # identifiers BEFORE converting target values. Emit only after actual AND plan time.
    outcomes = []
    with (root / 'labels' / f'labels_{split}.csv').open() as f:
        for row in csv.DictReader(f):
            if row['tr_id'] in catalog.guard.synthetic_ids or row['target_stop_id'] in catalog.guard.blocked:
                continue
            delay = float(row['target_delay_s'])
            planned = wall_seconds(row['target_time_begin'])
            actual = planned + delay
            outcomes.append({'event_id': row['target_stop_id'], 'vehicle_id': row['tr_id'],
                             'delay_sec': delay, 'actual_time': actual,
                             'available_at': max(planned, actual), 'planned_time': planned})
    return outcomes
