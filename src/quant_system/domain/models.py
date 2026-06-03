from dataclasses import dataclass, field
from datetime import datetime, timezone


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class MarketSnapshot:
    symbol: str
    price: float
    change_pct: float
    volume: int
    timestamp: datetime = field(default_factory=_utcnow)


@dataclass
class PredictionResult:
    symbol: str
    direction: str
    confidence: float
    target_price: float | None
    horizon: str
    reason: str


@dataclass
class Position:
    symbol: str
    quantity: int
    avg_price: float
    opened_at: datetime = field(default_factory=_utcnow)
