"""Per-vehicle bounded causal state."""
from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, field

from .types import Config, Packet


@dataclass(frozen=True)
class Position:
    event: float
    lon: float
    lat: float


@dataclass
class VehicleState:
    """Receipt, measurement sources, quarantine and finite windows are separate."""
    config: Config
    last_received: float | None = None
    watermark: float | None = None
    position: Position | None = None
    speed: tuple[float, float] | None = None
    heading: tuple[float, float] | None = None
    candidate: list[Position] = field(default_factory=list)
    stationary: bool = False
    stationary_since: float | None = None
    stop_anchor: Position | None = None
    pending: list[tuple[float, int, Packet]] = field(default_factory=list)
    seen: OrderedDict = field(default_factory=OrderedDict)
    packet_ids: OrderedDict = field(default_factory=OrderedDict)
    events: OrderedDict = field(default_factory=OrderedDict)
    positions: deque = field(init=False)
    speeds: deque = field(init=False)
    receipts: deque = field(init=False)
    observations: deque = field(init=False)
    stop_samples: deque = field(init=False)

    def __post_init__(self) -> None:
        for name in ['positions', 'speeds', 'receipts', 'observations', 'stop_samples']:
            setattr(self, name, deque(maxlen=self.config.max_history))

    def remember(self, cache: OrderedDict, key: object, value: object) -> None:
        """Bounded FIFO identity cache; expired retransmissions cannot roll state back."""
        cache[key] = value
        while len(cache) > self.config.dedup_capacity:
            cache.popitem(last=False)

    def trim(self, now: float) -> None:
        """Remove samples outside the diagnostic history window, no historical backfill."""
        for name in ['positions', 'speeds', 'receipts', 'observations', 'stop_samples']:
            history = getattr(self, name)
            while history and history[0][0] < now - self.config.window_s:
                history.popleft()
