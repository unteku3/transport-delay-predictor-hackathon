"""Public API: unknown measurements are null, timestamps are dataset wall time."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')


Risk = Literal['ok', 'risk', 'high']
Source = Literal['live', 'historical']


class VehicleState(Contract):
    vehicle_id: str
    route_id: str
    transport_type: str = 'unknown'
    direction: str = 'plan'
    lat: float | None = None
    lon: float | None = None
    heading: float | None = None
    segment_id: str | None = None
    segment_name: str | None = None
    next_stop: str | None = None
    next_stop_event_id: str | None = None
    next_stop_planned_time: str | None = None
    next_stop_eta: str | None = None
    next_stop_predicted_delay_min: float | None = None
    next_stop_prediction_method: str | None = None
    next_stop_prediction_issued_at: str | None = None
    next_stop_risk_level: Risk = 'ok'
    speed_kmh: float | None = None
    avg_segment_speed_kmh: float | None = None
    dwell_sec: float | None = None
    doors_open: bool | None = None
    deviation_now_min: float | None = None
    forecast_deviation_15_min: float | None = None
    forecast_error_min: float | None = None
    delay_probability: float | None = None
    risk_level: Risk = 'ok'
    last_update: str | None = None
    is_stale: bool = True
    source: Source = 'live'
    prediction_method: str | None = None
    forecast_event_id: str | None = None
    forecast_event_time: str | None = None
    forecast_issued_at: str | None = None
    matched_plan_position: float | None = None
    match_distance_m: float | None = None
    hint_age_sec: float | None = None


class TrackPoint(Contract):
    event_time: str
    lat: FiniteFloat
    lon: FiniteFloat


class VehicleTrack(Contract):
    vehicle_id: str
    route_id: str
    points: list[TrackPoint]
    source: str = 'accepted_live_gps'


class Alert(Contract):
    alert_id: str
    vehicle_id: str
    route_id: str
    target_event_id: str
    segment_id: str | None = None
    segment_name: str | None = None
    delay_probability: float | None = None
    predicted_delay_min: float
    predicted_error_min: float | None = None
    event_time: str
    issued_at: str
    lead_time_min: float
    cause_code: str
    cause_text: str
    evidence: str
    recommendations: list[str]
    source: Source
    prediction_method: str
    risk_level: Risk
    status: Literal['open', 'confirmed', 'false', 'unverified'] = 'open'
    actual_delay_min: float | None = None
    resolved_at: str | None = None


class NetworkSummary(Contract):
    vehicles_total: int
    vehicles_fresh: int
    vehicles_stale: int
    deviations_known: int
    on_schedule_percent: float | None
    late_now: int
    active_alerts: int
    high_risk: int
    risk: int
    forecast_mae_min: float | None
    alert_precision: float | None
    mean_lead_time_min: float | None
    evaluated_predictions: int
    virtual_time: str | None
    evaluation_scope: str = 'Replay on development data; not independent validation'


class SystemStatus(Contract):
    status: Literal['starting', 'online', 'degraded', 'finished']
    stream_connected: bool
    ml_available: bool
    virtual_time: str | None
    speed: float
    last_packet_at: str | None
    stream_age_wall_sec: float | None
    time_basis: str = 'naive dataset wall clock; no timezone assigned'
    replay_finished: bool = False
    detail: str


class PlanStop(Contract):
    event_id: str
    name: str
    planned_time: str
    lat: float | None
    lon: float | None
    order: int


class RouteInfo(Contract):
    route_id: str
    name: str
    transport_type: str = 'unknown'
    geometry_source: str = 'plan_chords_not_road_geometry'
    vehicle_ids: list[str]
    stops_count: int


class RouteDetail(RouteInfo):
    stops: list[PlanStop]
    vehicles: list[VehicleState]
    headways_min: list[float] = Field(default_factory=list)
    bunches: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    limitation: str = 'No route registry: this is one vehicle plan; fleet headways unavailable'


class TimelinePoint(Contract):
    event_id: str
    stop_order: int
    time: str
    issued_at: str | None = None


class RouteTimeline(Contract):
    route_id: str
    direction: str
    planned: list[TimelinePoint]
    actual: list[TimelinePoint]
    forecast: list[TimelinePoint]
    virtual_time: str | None
    limitation: str = 'Only published allowed actual arrivals; forecasts only for queried target events'


class ProblemSegment(Contract):
    segment_id: str
    route_id: str
    segment_name: str
    alerts_count: int
    max_predicted_delay_min: float


class ProblemSegments(Contract):
    items: list[ProblemSegment]
    date: str | None


class WhatIfRequest(Contract):
    route_id: str
    vehicle_id: str | None = None
    action: Literal['reserve', 'reduce_dwell', 'hold']
    minutes: FiniteFloat = Field(default=2, gt=0, le=15)


class ScenarioValues(Contract):
    predicted_delay_min: float | None
    max_headway_min: float | None = None


class WhatIfResult(Contract):
    before: ScenarioValues
    after: ScenarioValues
    action: str
    applied: bool = False
    assumptions: list[str]


class Metrics(Contract):
    packets_received: int
    protocol_errors: int
    discarded_transport_packets: int
    throughput_packets_sec: float
    queue_size: int
    inference_latency_ms: float | None
    inference_failures: int
    evaluated_predictions: int
    forecast_mae_min: float | None
    alert_precision: float | None
    evaluated_alerts: int
    mean_lead_time_min: float | None
    model_mae_min: float | None
    fallback_mae_min: float | None
    stream: SystemStatus
    evaluation_scope: str = 'Development replay, frozen offline fit; not held-out online accuracy'


class EmulatorControl(Contract):
    action: Literal['disconnect', 'reconnect', 'speed']
    speed: FiniteFloat | None = Field(default=None, ge=0.1, le=120)
