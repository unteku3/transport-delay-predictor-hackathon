"""Plan validation and target coordinates, deliberately no arrival matching."""
from __future__ import annotations
import re
import numpy as np
import pandas as pd

POINT = re.compile(r'^\s*POINT\s*\(\s*([-+\d.eE]+)\s+([-+\d.eE]+)\s*\)\s*$', re.I)


def parse_geom(value: object) -> tuple[float, float] | None:
    """Parse a finite WKT POINT in (longitude, latitude) order."""
    m = POINT.match(str(value))
    if not m:
        return None
    try:
        lon, lat = map(float, m.groups())
    except ValueError:
        return None
    return (lon, lat) if np.isfinite([lon, lat]).all() and -180 <= lon <= 180 and -90 <= lat <= 90 else None


def prepare_plan(plan: pd.DataFrame) -> pd.DataFrame:
    """Add flags; preserve all distinct events, including equal coordinates and times."""
    d = plan.drop(columns=['time_fact_begin'], errors='ignore').copy()
    coords = d.geom.map(parse_geom)
    d['target_lon'] = coords.map(lambda p: p[0] if p else np.nan)
    d['target_lat'] = coords.map(lambda p: p[1] if p else np.nan)
    d['flag_geom_invalid'] = coords.isna()
    d['planned'] = pd.to_datetime(d.time_begin, format='mixed', errors='coerce')
    d['flag_plan_time_invalid'] = d.planned.isna()
    d['flag_event_id_duplicate'] = d.tt_action_item_id.duplicated(keep=False)
    d['flag_plan_time_equal'] = d.duplicated(['tr_id', 'planned'], keep=False)
    return d
