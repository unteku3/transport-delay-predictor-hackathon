"""Leak-safe input boundary. Facts are masked before parsing or analysis."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pandas as pd


def read_plan(path: Path) -> pd.DataFrame:
    """Read only planned information: the CSV parser never selects the fact column."""
    return pd.read_csv(path, usecols=lambda c: c != 'time_fact_begin',
                       dtype={'tt_action_item_id': str, 'tr_id': str})


def synthetic_mapping(plan: pd.DataFrame) -> pd.DataFrame:
    """Verify candidate copies by full row-order geometry and constant plan shift.

    The 9-million ID block is only a candidate detector for this distribution.
    Unverified candidates cause an error rather than being treated as real data.
    No facts, hints or labels are inspected to identify provenance.
    """
    groups = dict(tuple(plan.groupby('tr_id', sort=False)))
    real = {k: v for k, v in groups.items() if int(k) < 9_000_000}
    rows = []
    for key, g in groups.items():
        if int(key) < 9_000_000:
            continue
        matches = []
        for orig, r in real.items():
            if len(g) != len(r) or g.geom.tolist() != r.geom.tolist():
                continue
            dt = (pd.to_datetime(g.time_begin, format='mixed').reset_index(drop=True)
                  - pd.to_datetime(r.time_begin, format='mixed').reset_index(drop=True))
            if dt.nunique() == 1:
                matches.append((orig, r, dt.iloc[0].total_seconds()))
        if len(matches) != 1:
            raise ValueError(f'Unresolved synthetic provenance: {key}')
        orig, r, shift = matches[0]
        for copy_id, source_id in zip(g.tt_action_item_id, r.tt_action_item_id):
            rows.append(dict(copy_tr_id=key, source_tr_id=orig, copy_event_id=copy_id,
                             source_event_id=source_id, plan_shift_s=shift))
    return pd.DataFrame(rows)


class DataGuard:
    """Deny hidden events and all synthetic answers before numeric conversion."""

    def __init__(self, root: Path):
        self.root = root
        # Schema/ID access only; no validate distributions or hints used here.
        self.hidden = set(pd.read_csv(root / 'validate/points.csv',
                                     usecols=['target_stop_id'], dtype=str).target_stop_id)
        self.plan = read_plan(root / 'train/schedule.csv')
        self.mapping = synthetic_mapping(self.plan)
        self.synthetic_ids = set(self.mapping.copy_tr_id)
        hidden_copies = self.mapping.loc[self.mapping.source_event_id.isin(self.hidden),
                                         'copy_event_id']
        self.blocked = self.hidden | set(hidden_copies)

    def schedule(self, path: Path) -> pd.DataFrame:
        """Mask raw strings first, including all synthetic facts conservatively."""
        rows = []
        with path.open(newline='') as f:
            for row in csv.DictReader(f):
                blocked = (row['tt_action_item_id'] in self.blocked
                           or row['tr_id'] in self.synthetic_ids)
                if blocked and 'time_fact_begin' in row:
                    row['time_fact_begin'] = ''
                row['fact_masked'] = blocked
                rows.append(row)
        return pd.DataFrame(rows)


def sha256(path: Path) -> str:
    """Hash source bytes without parsing values."""
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, data: object) -> None:
    """Stable, readable report serialization."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + '\n')
