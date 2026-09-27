"""Validate prediction requests using only declared inputs and planned events."""
import numpy as np
import pandas as pd
from cleaning.schedule import prepare_plan

INPUT_COLUMNS = ['sample_id', 'tr_id', 'T', 'target_stop_id', 'target_time_begin', 'cur_dev_s']


def prepare_queries(points: pd.DataFrame, plan: pd.DataFrame) -> pd.DataFrame:
    """Strict known-input boundary; drop any extra target columns before replay."""
    q = points[INPUT_COLUMNS].copy()
    for col in ['sample_id', 'tr_id', 'target_stop_id']:
        if q[col].isna().any():
            raise ValueError(f'Missing {col}')
        q[col] = q[col].astype(str)
    if q.empty or q.sample_id.duplicated().any():
        raise ValueError('Queries empty or sample IDs repeated')
    for col in ['T', 'target_time_begin']:
        q[col] = pd.to_datetime(q[col], format='mixed', errors='raise')
        if q[col].isna().any() or q[col].dt.tz is not None:
            raise ValueError('Naive, valid query times required')
    q['cur_dev_s'] = pd.to_numeric(q.cur_dev_s, errors='raise')
    if not np.isfinite(q.cur_dev_s.to_numpy()).all():
        raise ValueError('Nonfinite input hint')
    p = prepare_plan(plan)
    if p.tt_action_item_id.duplicated().any():
        raise ValueError('Ambiguous plan event ID')
    out = q.merge(p[['tt_action_item_id', 'tr_id', 'planned', 'target_lon', 'target_lat']],
                  left_on=['target_stop_id', 'tr_id'], right_on=['tt_action_item_id', 'tr_id'],
                  how='left', validate='many_to_one', indicator=True)
    if not out['_merge'].eq('both').all() or not out.planned.eq(out.target_time_begin).all():
        raise ValueError('Target event/vehicle/time does not match plan')
    horizon = (out.target_time_begin - out['T']).dt.total_seconds()
    if not ((horizon > 600) & (horizon <= 900)).all():
        raise ValueError('Target outside (T+600,T+900]')
    return out[INPUT_COLUMNS + ['target_lon', 'target_lat']]
