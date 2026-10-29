import json
from datetime import UTC, datetime

import httpx
from fastapi.testclient import TestClient

from vehicle_agent.api.main import create_app, handle_alarm_payload
from vehicle_agent.config import Settings
from vehicle_agent.graph.checkpoint import open_checkpointer
from vehicle_agent.harness.model import FIXTURE_REPORT, OpenAICompatibleModel
from vehicle_agent.ingest import MemoryAlarmTopic, fatigue_alarm_event, publish_vehicle_alarm
from vehicle_agent.repositories.memory import build_seed_repository

_SUMMARY = "在线模型建议驾驶员停车休息。"
_FIXTURE_SUMMARY = json.loads(FIXTURE_REPORT)["summary"]


def test_openai_backend_keeps_scores_and_uses_the_completion(tmp_path) -> None:
    calls: list[httpx.Request] = []
    content = json.dumps(
        {"summary": _SUMMARY, "recommendations": ["停车休息"]},
        ensure_ascii=False,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"total_tokens": 12},
            },
        )

    checkpoint = tmp_path / "checkpoints.sqlite"
    settings = Settings(
        model_backend="openai",
        checkpoint_path=str(checkpoint),
        max_retries=1,
    )
    client = httpx.Client(
        base_url="http://model.test/v1/",
        transport=httpx.MockTransport(handler),
    )
    model = OpenAICompatibleModel(settings, client=client)
    sent: list[tuple[str, bytes]] = []
    publish_vehicle_alarm(
        fatigue_alarm_event(),
        lambda topic, payload: sent.append((topic, payload)),
    )
    fatigue = _app(settings, checkpoint, model)
    demo_checkpoint = tmp_path / "demo.sqlite"
    demo_settings = settings.model_copy(update={"checkpoint_path": str(demo_checkpoint)})
    demo_app = _app(demo_settings, demo_checkpoint, model)

    with TestClient(fatigue) as client:
        task_id = handle_alarm_payload(
            sent[0][1],
            fatigue.state.graph,
            MemoryAlarmTopic(),
            {},
        )
        viewed = client.get("/api/v1/judgements/evt_20261009_000001")
    with TestClient(demo_app) as client:
        demo = client.post(
            "/api/v1/judgements",
            json={
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
            },
        )

    body = viewed.json()
    assert task_id == "evt_20261009_000001"
    assert viewed.status_code == 200
    assert body["status"] == "completed"
    assert body["result"]["risk_score"] == 0.95
    assert body["result"]["risk_level"] == "high"
    assert body["result"]["requires_human_review"] is False
    assert body["result"]["summary"] == _SUMMARY
    assert body["result"]["summary"] != _FIXTURE_SUMMARY
    assert demo.status_code == 200
    assert demo.json()["status"] == "completed"
    assert demo.json()["result"]["risk_score"] == 1.0
    assert demo.json()["result"]["summary"] == _SUMMARY
    assert calls
    for request in calls:
        assert request.url.path.endswith("/chat/completions")
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        assert "recommendations 必须是字符串数组" in payload["messages"][0]["content"]


def _app(settings: Settings, checkpoint, model: OpenAICompatibleModel):
    return create_app(
        settings,
        build_seed_repository(),
        model,
        open_checkpointer(checkpoint),
    )
