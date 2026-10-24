"""Run one high-risk fatigue alarm through the local API without a live model."""

import json
import sys
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from vehicle_agent.api.main import create_app
from vehicle_agent.config import Settings
from vehicle_agent.graph.checkpoint import open_checkpointer
from vehicle_agent.harness.model import FIXTURE_REPORT, FakeModelAdapter
from vehicle_agent.repositories.memory import build_seed_repository


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    settings = Settings(checkpoint_path="data/demo-checkpoints.sqlite")
    app = create_app(
        settings,
        build_seed_repository(),
        FakeModelAdapter(FIXTURE_REPORT),
        open_checkpointer(settings.checkpoint_path),
    )
    alarm = {
        "id": "alarm_demo",
        "vehicle_id": "vehicle_001",
        "driver_id": "driver_001",
        "device_id": "device_001",
        "alarm_type": "driver_fatigue",
        "alarm_time": datetime(2026, 10, 9, 10, 30, tzinfo=UTC).isoformat(),
        "longitude": 113.2644,
        "latitude": 23.1291,
        "speed_kmh": 85,
        "raw_confidence": 0.9,
    }
    response = TestClient(app).post("/api/v1/judgements", json=alarm)
    response.raise_for_status()
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
