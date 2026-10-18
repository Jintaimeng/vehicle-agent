from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

AlarmValidity = Literal["likely_true", "uncertain", "unknown"]
RiskLevel = Literal["low", "medium", "high"]


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


class Evidence(BaseModel):
    type: Literal["history", "telemetry", "context", "document", "graph"]
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
