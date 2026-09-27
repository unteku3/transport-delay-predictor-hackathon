"""Plan-only geometry and already received hint history, with no target labels."""
from collections import defaultdict
import math

import numpy as np

from cleaning.replay import snapshot
from cleaning.schedule import prepare_plan
from cleaning.telemetry import distance_m
from cleaning.types import wall_seconds


class ContextFeatures:
    def __init__(self, plan):
        self.plans = {}
        for vehicle, group in prepare_plan(plan).groupby('tr_id', sort=False):
            group = group.sort_values(['planned', 'tt_action_item_id'], kind='stable')
            self.plans[str(vehicle)] = group.reset_index(drop=True)
        self.hints = defaultdict(list)

    def __call__(self, engine, vehicle, now, row):
        current = float(row.cur_dev_s)
        history = self.hints[vehicle]
        # Same-time queries are independent: only strictly earlier external hints.
        earlier = [p for p in history if p[0] < now]
        f = {'vehicle': vehicle, 'target_lon': row.target_lon, 'target_lat': row.target_lat,
             'hour_sin': math.sin(2*math.pi*(now % 86400)/86400),
             'hour_cos': math.cos(2*math.pi*(now % 86400)/86400)}
        for window in [900, 1800, 3600]:
            h = [(t,d) for t,d in earlier if now-t <= window]
            v = [p[1] for p in h]
            f[f'hint_count_{window}s'] = len(h)
            f[f'hint_mean_{window}s'] = float(np.mean(v)) if v else math.nan
            f[f'hint_median_{window}s'] = float(np.median(v)) if v else math.nan
            f[f'hint_std_{window}s'] = float(np.std(v)) if v else math.nan
            f[f'hint_change_{window}s'] = current-v[0] if v else math.nan
        f['previous_hint'] = earlier[-1][1] if earlier else math.nan
        f['previous_hint_age_s'] = now-earlier[-1][0] if earlier else math.nan
        f['hint_change'] = current-earlier[-1][1] if earlier else math.nan
        unchanged_since = now
        for t,d in reversed(earlier):
            if d != current:
                break
            unchanged_since = t
        f['hint_unchanged_s'] = now-unchanged_since
        # Keep one value per query time; inputs must agree if a vehicle has ties.
        if history and history[-1][0] == now:
            if history[-1][1] != current:
                raise ValueError('Ambiguous hints at equal query time')
        else:
            history.append((now, current))
        self.hints[vehicle] = [p for p in history if now-p[0] <= 3600]

        plan = self.plans[vehicle]
        idx = np.flatnonzero(plan.tt_action_item_id.to_numpy() == row.target_stop_id)
        if len(idx) != 1:
            raise ValueError('Ambiguous target event')
        i = int(idx[0])
        target = plan.iloc[i]
        f['target_order'] = i
        f['target_fraction'] = i / max(1, len(plan)-1)
        f['previous_plan_gap_s'] = (target.planned-plan.iloc[i-1].planned).total_seconds() if i else math.nan
        f['previous_plan_distance_m'] = (distance_m(plan.iloc[i-1].target_lon, plan.iloc[i-1].target_lat,
                                                   row.target_lon, row.target_lat) if i else math.nan)
        s = snapshot(engine, vehicle, now)
        f['current_lon'], f['current_lat'] = s['lon'], s['lat']
        # Candidate scheduled positions near the clock shifted by current delay.
        times = plan.planned.map(wall_seconds).to_numpy()
        candidates = np.flatnonzero((abs(times-(now-current)) <= 1800) & (np.arange(len(plan)) <= i))
        f.update(nearest_plan_distance_m=math.nan, plan_delay_proxy_s=math.nan,
                 remaining_plan_events=math.nan, remaining_plan_chord_m=math.nan)
        if math.isfinite(s['lon']) and math.isfinite(s['lat']) and len(candidates):
            distances = [distance_m(s['lon'], s['lat'], plan.iloc[j].target_lon, plan.iloc[j].target_lat) for j in candidates]
            finite = [(d,int(j)) for d,j in zip(distances,candidates) if math.isfinite(d)]
            if finite:
                d,j = min(finite)
                f['nearest_plan_distance_m'] = d
                f['plan_delay_proxy_s'] = now-times[j]
                f['remaining_plan_events'] = i-j
                f['remaining_plan_chord_m'] = sum(distance_m(plan.iloc[k].target_lon, plan.iloc[k].target_lat,
                     plan.iloc[k+1].target_lon, plan.iloc[k+1].target_lat) for k in range(j,i))
        return f
