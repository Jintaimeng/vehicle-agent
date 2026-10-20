from fastapi.testclient import TestClient

from test_graph import REPORT, _alarm, _run
from test_vision import _vision_model
from vehicle_agent.api.main import create_app
from vehicle_agent.config import Settings
from vehicle_agent.domain.models import Detection
from vehicle_agent.graph.checkpoint import open_checkpointer
from vehicle_agent.harness.model import FakeModelAdapter
from vehicle_agent.knowledge.video import VideoFrame, aggregate_frames, packaged_video_timeline
from vehicle_agent.repositories.memory import build_seed_repository


class _FailingTimeline:
    def analyze(self, video_id: str, speed_kmh: float, content: bytes | None = None):
        raise RuntimeError("video timeline unavailable")


def _detection(label: str, confidence: float = 0.91) -> Detection:
    return Detection(
        label=label,
        confidence=confidence,
        bbox=(0.4, 0.2, 0.62, 0.55),
        model_version="fixture-v1",
    )


def _frame(timestamp: float, label: str | None = None, quality: float = 1) -> VideoFrame:
    detections = [] if label is None else [_detection(label)]
    return VideoFrame(
        timestamp_s=timestamp,
        image_id="frame",
        detections=detections,
        face_quality=quality,
    )


def _steady(**overrides):
    payload = {
        "vehicle_id": "vehicle_002",
        "driver_id": "driver_002",
        "device_id": "device_002",
        "speed_kmh": 70,
        "raw_confidence": 0.8,
    }
    payload.update(overrides)
    return _alarm(**payload)


def test_continuous_closed_eye_records_keyframe(tmp_path) -> None:
    _, values = _run(
        tmp_path,
        _alarm(
            vehicle_id="vehicle_001",
            driver_id="driver_001",
            device_id="device_001",
            speed_kmh=85,
            raw_confidence=0.9,
            video_id="video_fatigue",
        ),
        content=_vision_model("驾驶员持续闭眼"),
    )

    visual = [item for item in values["result"]["evidence"] if item["type"] == "vision"]
    assert values["status"] == "completed"
    assert values["vision_status"] == "hit"
    assert values["risk_score"] == 1.0
    assert "时段 0.00s-3.00s" in visual[0]["description"]
    assert "关键帧 0.00s" in visual[0]["description"]
    assert "可见事实：驾驶员持续闭眼" in visual[0]["description"]


def test_single_blink_does_not_form_event(tmp_path) -> None:
    plain_alarm = _steady()
    blink_alarm = _steady(id="alarm_blink", video_id="video_blink")
    _, plain = _run(tmp_path, plain_alarm)
    _, blinked = _run(tmp_path, blink_alarm)

    assert plain["status"] == "completed"
    assert blinked["status"] == "awaiting_review"
    assert blinked["vision_status"] == "miss"
    assert blinked["risk_score"] == plain["risk_score"]
    assert all("关键帧" not in item["description"] for item in blinked["evidence"])
    assert "result" not in blinked


def test_occlusion_video_conflicts_without_changing_risk_score(tmp_path) -> None:
    _, plain = _run(tmp_path, _steady())
    _, occluded = _run(
        tmp_path,
        _steady(id="alarm_video_occluded", video_id="video_occlusion"),
        content=_vision_model("镜头被挡住"),
    )

    visual = [item for item in occluded["evidence"] if item["type"] == "vision"]
    documents = [item for item in occluded["evidence"] if item["type"] == "document"]
    assert occluded["status"] == "awaiting_review"
    assert occluded["risk_score"] == plain["risk_score"]
    assert documents[0]["source"] == "fatigue-rules"
    assert any("camera_occlusion" in item["description"] for item in visual)
    assert any("证据冲突" in item["description"] for item in visual)
    assert any("关键帧" in item["description"] for item in visual)
    assert "result" not in occluded


def test_timeline_failure_degrades_to_review(tmp_path) -> None:
    _, values = _run(
        tmp_path,
        _steady(video_id="video_fatigue"),
        video=_FailingTimeline(),
    )

    assert values["status"] == "awaiting_review"
    assert values["vision_status"] == "degraded"
    assert values["errors"][-1]["code"] == "vision_unavailable"
    assert "result" not in values


def test_upload_returns_keyframe_timestamp(tmp_path) -> None:
    app = create_app(
        Settings(max_retries=1, checkpoint_path=str(tmp_path / "checkpoints.sqlite")),
        build_seed_repository(),
        FakeModelAdapter(REPORT),
        open_checkpointer(tmp_path / "checkpoints.sqlite"),
    )
    response = TestClient(app).post(
        "/api/v1/vision/videos",
        data={"video_id": "video_fatigue"},
        files={"file": ("clip.mp4", b"not-a-real-video", "video/mp4")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["frame_timestamps"] == [0, 1, 2, 3]
    assert body["event_formed"] is True
    assert body["keyframe_timestamp_s"] == 0


def test_low_speed_and_poor_face_do_not_form_event() -> None:
    frames = [_frame(float(second), "closed_eye") for second in range(4)]
    assert aggregate_frames(frames, 40).event is None

    poor = [
        _frame(0, "closed_eye"),
        _frame(1, "closed_eye", quality=0.2),
        _frame(2, "closed_eye", quality=0.2),
        _frame(3, "closed_eye"),
    ]
    assert aggregate_frames(poor, 80).event is None


def test_packaged_timeline_reuses_image_detections() -> None:
    outcome = packaged_video_timeline().analyze("video_blink", 85)
    assert outcome.event is None
    assert outcome.frames[0].detections[0].label == "closed_eye"
    assert packaged_video_timeline().analyze("missing", 85).frames == []


def test_alarm_accepts_optional_video_id() -> None:
    assert _alarm(video_id="video_fatigue").video_id == "video_fatigue"
    assert _alarm().video_id is None
