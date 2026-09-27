"""ML HTTP contract, independent of the orchestration service."""
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PredictionItem(Contract):
    request_id: str
    features: dict[str, FiniteFloat | str | None]


class PredictRequest(Contract):
    items: list[PredictionItem] = Field(min_length=1, max_length=512)


class Prediction(Contract):
    request_id: str
    delay_sec: float


class ModelInfo(Contract):
    version: str
    training_date: str
    training_date_source: str
    validation_mae_sec: float
    validation_protocol: str
    target: str
    features: list[str]
    probability_available: bool
    individual_error_interval_available: bool
    causes_available: bool


class PredictResponse(Contract):
    model_version: str
    predictions: list[Prediction]
