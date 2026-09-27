"""Final 69-feature transform; no target access."""
import numpy as np
from ml.features import build_features
from ml.queries import INPUT_COLUMNS

DERIVED = {
    'speed_recent_change_kmh': 'Средняя скорость за 60 с минус средняя за 300 с, км/ч.',
    'required_straight_speed_kmh': 'Расстояние по прямой / плановый горизонт ×3.6; не требуемая скорость по дороге.',
    'straight_eta_current_s': 'Расстояние по прямой / положительная свежая скорость; NaN при нуле/пропуске.',
    'straight_eta_mean300_s': 'Расстояние по прямой / положительная средняя скорость за 300 с.',
    'straight_eta_margin_s': 'Оценка прямого времени движения минус плановый горизонт.',
    'speed_minus_required_kmh': 'Свежая скорость минус требуемая по прямой.',
    'approach_change_mps': 'Приближение за 60 с минус приближение за 180 с.',
    'speed_window_coverage': 'Охват скорости за 300 с / 300; не доля отсутствующих пакетов.',
    'position_window_coverage': 'Охват позиции за 300 с / 300.',
}


def expanded_features(packets, queries, config):
    # Strict boundary: ignore all unknown columns, including any targets or facts.
    q = queries[INPUT_COLUMNS + ['target_lon', 'target_lat']].copy()
    x = build_features(packets, q, config)
    x['speed_recent_change_kmh'] = x.speed_mean_60s - x.speed_mean_300s
    x['required_straight_speed_kmh'] = 3.6 * x.distance_target_m / x.horizon_s.where(x.horizon_s > 0)
    x['straight_eta_current_s'] = 3.6 * x.distance_target_m / x.speed.where(x.speed > 0)
    x['straight_eta_mean300_s'] = 3.6 * x.distance_target_m / x.speed_mean_300s.where(x.speed_mean_300s > 0)
    x['straight_eta_margin_s'] = x.straight_eta_mean300_s - x.horizon_s
    x['speed_minus_required_kmh'] = x.speed - x.required_straight_speed_kmh
    x['approach_change_mps'] = x.approach_mps_60s - x.approach_mps_180s
    x['speed_window_coverage'] = x.speed_span_300s / 300
    x['position_window_coverage'] = x.position_span_300s / 300
    if np.isinf(x.to_numpy()).any():
        raise ValueError('Infinite expanded feature')
    return x
