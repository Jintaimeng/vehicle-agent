from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

AlarmValidity = Literal["likely_true", "uncertain", "unknown"]
RiskLevel = Literal["low", "medium", "high"]
VisionLabel = Literal[
    "driver",
    "mobile_phone",
    "seat_belt",
    "closed_eye",
    "yawn",
    "smoking",
    "camera_occlusion",
]


class Vehicle(BaseModel):
    id: str
    plate_number: str
    enterprise_id: str
    vehicle_type: str
    device_id: str


class Driver(BaseModel):
    id: str
    name: str
    license_type: str
    enterprise_id: str


class Alarm(BaseModel):
    id: str
    vehicle_id: str
    driver_id: str
    device_id: str
    alarm_type: Literal["driver_fatigue"]
    alarm_time: datetime
    longitude: float
    latitude: float
    speed_kmh: float = Field(ge=0)
    source: str = "device"
    raw_confidence: float = Field(ge=0, le=1)
    status: str = "open"
    image_id: str | None = None
    video_id: str | None = None


class Detection(BaseModel):
    label: VisionLabel
    confidence: float = Field(ge=0, le=1)
    bbox: tuple[float, float, float, float]
    model_version: str

    @field_validator("bbox")
    @classmethod
    def normalized_xyxy(
        cls, value: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        if any(item < 0 or item > 1 for item in value):
            raise ValueError("bbox must be normalized between 0 and 1")
        x1, y1, x2, y2 = value
        if x2 < x1 or y2 < y1:
            raise ValueError("bbox must be xyxy")
        return value


class Evidence(BaseModel):
    type: Literal["history", "telemetry", "context", "document", "graph", "vision"]
    source: str
    description: str


class JudgementResult(BaseModel):
    alarm_validity: AlarmValidity
    risk_level: RiskLevel
    confidence: float = Field(ge=0, le=1)
    risk_score: float = Field(ge=0, le=1)
    summary: str
    evidence: list[Evidence]
    recommendations: list[str]
    requires_human_review: bool


class HumanReview(BaseModel):
    reviewer: str
    decision: Literal["confirm", "reject"]
    comment: str = ""
