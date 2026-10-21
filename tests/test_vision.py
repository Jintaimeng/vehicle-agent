import json

import httpx
from fastapi.testclient import TestClient

from test_graph import REPORT, _alarm, _run
from vehicle_agent.api.main import create_app
from vehicle_agent.config import Settings
from vehicle_agent.domain.models import Detection
from vehicle_agent.graph.builder import build_graph
from vehicle_agent.graph.checkpoint import open_checkpointer
from vehicle_agent.graph.scoring import score_fatigue
from vehicle_agent.harness.model import FakeModelAdapter, OpenAICompatibleModel
from vehicle_agent.harness.runtime import Harness
from vehicle_agent.harness.tools import build_vehicle_tools
from vehicle_agent.knowledge.graph import graph_fact
from vehicle_agent.knowledge.index import RegulationSnippet
from vehicle_agent.knowledge.vision import store_image
from vehicle_agent.repositories.memory import build_seed_repository


def _vision_model(fact: str):
    def respond(messages: list[dict[str, str]]) -> str:
        if "visible_facts" in messages[0]["content"]:
            return json.dumps({"visible_facts": [fact]}, ensure_ascii=False)
        return REPORT

    return respond


def _high_alarm(**overrides):
    payload = {
        "vehicle_id": "vehicle_001",
        "driver_id": "driver_001",
        "device_id": "device_001",
        "speed_kmh": 85,
        "raw_confidence": 0.9,
    }
    payload.update(overrides)
    return _alarm(**payload)


class _FailingVision:
    def analyze(self, image_id: str, content: bytes | None = None) -> list[Detection]:
        raise RuntimeError("vision index unavailable")


def test_alarm_without_image_keeps_previous_score(tmp_path) -> None:
    _, values = _run(tmp_path, _high_alarm())

    assert values["status"] == "completed"
    assert values["vision_status"] == "skipped"
    assert values["risk_score"] == 1.0
    assert all(item["type"] != "vision" for item in values["result"]["evidence"])


def test_closed_eye_detection_enters_evidence(tmp_path) -> None:
    _, values = _run(
        tmp_path,
        _high_alarm(image_id="image_closed_eye"),
        content=_vision_model("驾驶员闭眼"),
    )

    visual = [item for item in values["result"]["evidence"] if item["type"] == "vision"]
    assert values["status"] == "completed"
    assert values["vision_status"] == "hit"
    assert values["risk_score"] == 1.0
    assert visual[0]["source"] == "fixture-v1"
    assert "closed_eye" in visual[0]["description"]
    assert "[0.40, 0.20, 0.62, 0.55]" in visual[0]["description"]
    assert "可见事实：画面观察支持 closed_eye" in visual[0]["description"]


def test_visible_facts_do_not_change_score(tmp_path) -> None:
    _, matched = _run(
        tmp_path,
        _high_alarm(id="alarm_closed", image_id="image_closed_eye"),
    )
    _, mismatched = _run(
        tmp_path,
        _high_alarm(id="alarm_mismatch", image_id="image_closed_eye_mismatch"),
    )

    assert matched["risk_score"] == mismatched["risk_score"] == 1.0
    assert matched["status"] == "completed"
    assert mismatched["status"] == "awaiting_review"
    assert mismatched["vision_agreement"] == "conflict"
    assert "画面观察支持 closed_eye" in matched["result"]["evidence"][-1]["description"]
    assert any("不支持 closed_eye" in item["description"] for item in mismatched["evidence"])


def test_visible_facts_are_ignored_by_score_fatigue() -> None:
    shared = dict(
        review_confidence_threshold=0.6,
        regulations=[
            RegulationSnippet(
                document_id="fatigue-rules",
                title="疲劳",
                section="第2节",
                excerpt="停车休息",
            )
        ],
        regulation_status="hit",
        graph_facts=[graph_fact("drives", "vehicle_001", "driver_001")],
        graph_status="hit",
        detections=[
            Detection(
                label="closed_eye",
                confidence=0.91,
                bbox=(0.4, 0.2, 0.62, 0.55),
                model_version="fixture-v1",
            )
        ],
        vision_status="hit",
        vlm_status="hit",
    )
    alarm = _alarm(speed_kmh=70, raw_confidence=0.7)
    vehicle = build_seed_repository().get_vehicle("vehicle_001")
    driver = build_seed_repository().get_driver("driver_001")
    first = score_fatigue(alarm, vehicle, driver, [], visible_facts=["闭眼"], **shared)
    second = score_fatigue(alarm, vehicle, driver, [], visible_facts=["打哈欠"], **shared)

    assert first["risk_score"] == second["risk_score"]
    assert first["risk_level"] == second["risk_level"]
    assert first["alarm_validity"] == second["alarm_validity"]


def test_camera_occlusion_conflicts_without_changing_risk_score(tmp_path) -> None:
    alarm = _alarm(
        vehicle_id="vehicle_002",
        driver_id="driver_002",
        speed_kmh=40,
        raw_confidence=0.8,
    )
    _, plain = _run(tmp_path, alarm)
    _, occluded = _run(
        tmp_path,
        _alarm(
            id="alarm_occluded",
            vehicle_id="vehicle_002",
            driver_id="driver_002",
            device_id="device_002",
            speed_kmh=40,
            raw_confidence=0.8,
            image_id="image_camera_occlusion",
        ),
        content=_vision_model("镜头被挡住"),
    )

    visual = [item for item in occluded["evidence"] if item["type"] == "vision"]
    documents = [item for item in occluded["evidence"] if item["type"] == "document"]
    assert plain["status"] == "completed"
    assert occluded["status"] == "awaiting_review"
    assert occluded["risk_score"] == plain["risk_score"]
    assert documents[0]["source"] == "fatigue-rules"
    assert any("camera_occlusion" in item["description"] for item in visual)
    assert any("证据冲突" in item["description"] for item in visual)
    assert "result" not in occluded


def test_empty_detection_requests_review(tmp_path) -> None:
    _, values = _run(tmp_path, _alarm(image_id="image_empty"))

    assert values["status"] == "awaiting_review"
    assert values["vision_status"] == "miss"
    assert values["errors"] == []
    assert any("图片未检出目标" in item["description"] for item in values["evidence"])
    assert "result" not in values


def test_detector_failure_degrades_to_review(tmp_path) -> None:
    _, values = _run(
        tmp_path,
        _alarm(image_id="image_closed_eye"),
        vision=_FailingVision(),
    )

    assert values["status"] == "awaiting_review"
    assert values["vision_status"] == "degraded"
    assert values["errors"][-1]["code"] == "vision_unavailable"
    assert any("图片检测不可用" in item["description"] for item in values["evidence"])
    assert "result" not in values


class _FailingObservation:
    def observe(self, image_id: str, *, task_id: str) -> list[str]:
        raise RuntimeError("observation unavailable")


def test_observation_failure_keeps_detections_for_review(tmp_path) -> None:
    _, values = _run(
        tmp_path,
        _high_alarm(image_id="image_closed_eye"),
        observation=_FailingObservation(),
    )

    visual = [item for item in values["evidence"] if item["type"] == "vision"]
    assert values["status"] == "awaiting_review"
    assert values["vision_status"] == "hit"
    assert values["vlm_status"] == "degraded"
    assert values["errors"][-1]["code"] == "vision_unavailable"
    assert any("closed_eye" in item["description"] for item in visual)
    assert "result" not in values


def test_image_review_does_not_spend_model_budget(tmp_path) -> None:
    _, values = _run(
        tmp_path,
        _high_alarm(image_id="image_closed_eye"),
        max_model_calls=0,
    )

    assert values["status"] == "failed"
    assert values["vision_status"] == "hit"
    assert values["vlm_status"] == "hit"
    assert values["errors"][-1]["code"] == "budget_exceeded"


def test_upload_returns_fixture_detection(tmp_path) -> None:
    app = create_app(
        Settings(
            max_retries=1,
            checkpoint_path=str(tmp_path / "checkpoints.sqlite"),
            vision_store_dir=str(tmp_path / "vision"),
        ),
        build_seed_repository(),
        FakeModelAdapter(REPORT),
        open_checkpointer(tmp_path / "checkpoints.sqlite"),
    )
    client = TestClient(app)
    response = client.post(
        "/api/v1/vision/images",
        data={"image_id": "image_closed_eye"},
        files={"file": ("frame.jpg", b"not-a-real-image", "image/jpeg")},
    )

    assert response.status_code == 200
    detection = response.json()["detections"][0]
    assert detection["label"] == "closed_eye"
    assert detection["confidence"] == 0.91
    assert detection["bbox"] == [0.4, 0.2, 0.62, 0.55]
    assert (tmp_path / "vision" / "image_closed_eye").read_bytes() == b"not-a-real-image"


def test_unknown_image_is_a_miss(tmp_path) -> None:
    app = create_app(
        Settings(
            max_retries=1,
            checkpoint_path=str(tmp_path / "checkpoints.sqlite"),
            vision_store_dir=str(tmp_path / "vision"),
        ),
        build_seed_repository(),
        FakeModelAdapter(REPORT),
        open_checkpointer(tmp_path / "checkpoints.sqlite"),
    )
    response = TestClient(app).post(
        "/api/v1/vision/images",
        data={"image_id": "missing"},
        files={"file": ("frame.jpg", b"bytes", "image/jpeg")},
    )

    assert response.status_code == 200
    assert response.json() == {"image_id": "missing", "detections": []}


def test_fixture_catalog_covers_required_samples() -> None:
    from vehicle_agent.knowledge.vision import packaged_vision_index

    index = packaged_vision_index()
    assert index.analyze("image_closed_eye")[0].label == "closed_eye"
    assert index.analyze("image_camera_occlusion")[0].label == "camera_occlusion"
    assert index.analyze("image_closed_eye_mismatch")[0].label == "closed_eye"
    assert index.analyze("image_empty") == []
    assert index.analyze("image_closed_eye", b"pixels")[0].label == "closed_eye"


def test_alarm_accepts_optional_image_id() -> None:
    alarm = _alarm(image_id="image_closed_eye")
    assert alarm.image_id == "image_closed_eye"
    assert _alarm().image_id is None


def test_packaged_observations_cover_required_samples() -> None:
    from vehicle_agent.knowledge.vision import packaged_observations

    observations = packaged_observations()
    assert observations.observe("image_closed_eye", task_id="t") == ["closed_eye"]
    assert observations.observe("image_camera_occlusion", task_id="t") == ["camera_occlusion"]
    assert observations.observe("image_closed_eye_mismatch", task_id="t") == ["driver"]


def test_vlm_labels_use_the_same_comparison(tmp_path) -> None:
    store = tmp_path / "vision"
    store_image(store, "image_closed_eye", b"frame-bytes")
    settings = Settings(
        max_retries=0,
        checkpoint_path=str(tmp_path / "checkpoints.sqlite"),
        review_backend="vlm",
        vision_store_dir=str(store),
        vlm_model_name="qwen-vl",
        prompt_version="report-v6",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "qwen-vl"
        parts = body["messages"][1]["content"]
        assert any(part.get("type") == "image_url" for part in parts)
        payload = {"labels": ["driver"]}
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(payload)}}],
                "usage": {"total_tokens": 8},
            },
        )

    model = OpenAICompatibleModel(
        settings,
        vision_client=httpx.Client(
            base_url="http://vlm.test/v1/",
            transport=httpx.MockTransport(handler),
        ),
    )
    harness = Harness(settings, model, build_vehicle_tools(build_seed_repository()))
    graph = build_graph(harness, open_checkpointer(tmp_path / "checkpoints.sqlite"))
    alarm = _high_alarm(id="alarm_vlm", image_id="image_closed_eye")
    graph.invoke(
        {"task_id": alarm.id, "alarm": alarm.model_dump(mode="json"), "errors": []},
        {"configurable": {"thread_id": alarm.id}},
    )
    values = graph.get_state({"configurable": {"thread_id": alarm.id}}).values
    note = json.loads(
        next(
            event["note"]
            for event in values["trace"]
            if event["node"] == "vision" and event["note"]
        )
    )

    assert values["status"] == "awaiting_review"
    assert values["vision_agreement"] == "conflict"
    assert values["vlm_status"] == "hit"
    assert values["risk_score"] == 1.0
    assert note["image_id"] == "image_closed_eye"
    assert note["prompt_version"] == "report-v6"
    assert note["labels"] == ["driver"]
    assert any("不支持 closed_eye" in item["description"] for item in values["evidence"])


class _ByteVision:
    def __init__(self) -> None:
        self.contents: list[bytes | None] = []

    def analyze(self, image_id: str, content: bytes | None = None) -> list[Detection]:
        del image_id
        self.contents.append(content)
        if not content:
            return []
        return [
            Detection(
                label="closed_eye",
                confidence=0.91,
                bbox=(0.4, 0.2, 0.62, 0.55),
                model_version="bytes-v1",
            )
        ]


def test_yolo_backend_passes_stored_image_bytes(tmp_path) -> None:
    store = tmp_path / "vision"
    store_image(store, "image_closed_eye", b"frame-bytes")
    detector = _ByteVision()
    _, values = _run(
        tmp_path,
        _high_alarm(id="alarm_bytes", image_id="image_closed_eye"),
        vision=detector,
        vision_backend="yolo",
        vision_store_dir=str(store),
    )

    visual = [item for item in values["result"]["evidence"] if item["type"] == "vision"]
    assert detector.contents == [b"frame-bytes"]
    assert values["status"] == "completed"
    assert values["vision_status"] == "hit"
    assert values["vision_agreement"] == "match"
    assert values["risk_score"] == 1.0
    assert "画面观察支持 closed_eye" in visual[0]["description"]


def test_missing_or_empty_stored_image_degrades(tmp_path) -> None:
    missing = tmp_path / "missing"
    _, absent = _run(
        tmp_path,
        _high_alarm(id="alarm_absent", image_id="image_closed_eye"),
        vision=_ByteVision(),
        vision_backend="yolo",
        vision_store_dir=str(missing),
    )
    empty = tmp_path / "empty"
    store_image(empty, "image_closed_eye", b"")
    _, blank = _run(
        tmp_path,
        _high_alarm(id="alarm_blank", image_id="image_closed_eye"),
        vision=_ByteVision(),
        vision_backend="yolo",
        vision_store_dir=str(empty),
    )

    assert absent["status"] == "awaiting_review"
    assert absent["vision_status"] == "degraded"
    assert absent["errors"][-1]["code"] == "vision_unavailable"
    assert "result" not in absent
    assert blank["status"] == "awaiting_review"
    assert blank["vision_status"] == "degraded"
    assert "stored image empty" in blank["errors"][-1]["message"]
    assert "result" not in blank


def test_vlm_http_failure_keeps_detections(tmp_path) -> None:
    store = tmp_path / "vision"
    store_image(store, "image_closed_eye", b"frame-bytes")
    settings = Settings(
        max_retries=0,
        checkpoint_path=str(tmp_path / "checkpoints.sqlite"),
        review_backend="vlm",
        vision_store_dir=str(store),
    )
    model = OpenAICompatibleModel(
        settings,
        vision_client=httpx.Client(
            base_url="http://vlm.test/v1/",
            transport=httpx.MockTransport(lambda request: httpx.Response(503)),
        ),
    )
    harness = Harness(settings, model, build_vehicle_tools(build_seed_repository()))
    graph = build_graph(harness, open_checkpointer(tmp_path / "checkpoints.sqlite"))
    alarm = _high_alarm(id="alarm_vlm_down", image_id="image_closed_eye")
    graph.invoke(
        {"task_id": alarm.id, "alarm": alarm.model_dump(mode="json"), "errors": []},
        {"configurable": {"thread_id": alarm.id}},
    )
    values = graph.get_state({"configurable": {"thread_id": alarm.id}}).values
    visual = [item for item in values["evidence"] if item["type"] == "vision"]

    assert values["status"] == "awaiting_review"
    assert values["vision_status"] == "hit"
    assert values["vlm_status"] == "degraded"
    assert values["errors"][-1]["code"] == "model_failed"
    assert any("closed_eye" in item["description"] for item in visual)
    assert "result" not in values
