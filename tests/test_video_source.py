import pytest

from test_graph import _alarm, _run
from test_vision import _vision_model
from vehicle_agent.domain.models import Detection
from vehicle_agent.knowledge.video import FfmpegVideoTimeline, resolve_video_locator
from vehicle_agent.knowledge.vision import FixtureObservationIndex, store_video

_JT1078 = "jt1078://013800138000/1"
_RTSP = "rtsp://camera/live"
_FRAMES = [(0.0, b"jpeg-0"), (1.0, b"jpeg-1"), (2.0, b"jpeg-2"), (3.0, b"jpeg-3")]


class _ClosedEyeVision:
    def analyze(self, image_id: str, content: bytes | None = None) -> list[Detection]:
        del image_id, content
        return [
            Detection(
                label="closed_eye",
                confidence=0.91,
                bbox=(0.4, 0.2, 0.62, 0.55),
                model_version="fixture-v1",
            )
        ]


def test_jt1078_and_rtsp_share_the_stream_sampler(tmp_path) -> None:
    calls: list[str] = []

    def sample(url: str) -> list[tuple[float, bytes]]:
        calls.append(url)
        return list(_FRAMES)

    def extract(path) -> list[tuple[float, bytes]]:
        del path
        raise AssertionError("file extract should not run")

    timeline = FfmpegVideoTimeline(_ClosedEyeVision(), tmp_path / "vision", extract, sample)
    jt1078 = timeline.analyze(_JT1078, 85)
    rtsp = timeline.analyze(_RTSP, 85)

    assert resolve_video_locator(_JT1078).rtsp_url == _RTSP
    assert calls == [_RTSP, _RTSP]
    assert jt1078.event is not None
    assert jt1078.event.start_s == 0
    assert jt1078.event.end_s == 3
    assert jt1078.frames[0].image_id == "jt1078_013800138000_1__0"
    assert rtsp.frames[0].image_id == "rtsp_camera_live__0"


def test_unknown_stream_locator_does_not_sample(tmp_path) -> None:
    def sample(url: str) -> list[tuple[float, bytes]]:
        del url
        raise AssertionError("sampler should not run")

    timeline = FfmpegVideoTimeline(
        _ClosedEyeVision(),
        tmp_path / "vision",
        lambda path: [],
        sample,
    )
    for video_id in ("jt1078://999/1", "rtsp://"):
        with pytest.raises(ValueError):
            timeline.analyze(video_id, 85)


def test_stored_file_still_uses_extract(tmp_path) -> None:
    store = tmp_path / "vision"
    store_video(store, "clip", b"video-bytes")
    calls: list[bytes] = []

    def extract(path) -> list[tuple[float, bytes]]:
        calls.append(path.read_bytes())
        return [(0.0, b"jpeg-0")]

    def sample(url: str) -> list[tuple[float, bytes]]:
        del url
        raise AssertionError("sampler should not run")

    outcome = FfmpegVideoTimeline(_ClosedEyeVision(), store, extract, sample).analyze("clip", 85)

    assert calls == [b"video-bytes"]
    assert outcome.frames[0].image_id == "clip__0"
    assert (store / "clip__0").read_bytes() == b"jpeg-0"


def test_jt1078_alarm_keeps_the_video_fatigue_score(tmp_path) -> None:
    def sample(url: str) -> list[tuple[float, bytes]]:
        assert url == _RTSP
        return list(_FRAMES)

    timeline = FfmpegVideoTimeline(
        _ClosedEyeVision(),
        tmp_path / "vision",
        lambda path: [],
        sample,
    )
    _, values = _run(
        tmp_path,
        _alarm(
            id="alarm_jt1078",
            vehicle_id="vehicle_001",
            driver_id="driver_001",
            device_id="device_001",
            speed_kmh=85,
            raw_confidence=0.9,
            video_id=_JT1078,
        ),
        video=timeline,
        observation=FixtureObservationIndex({"jt1078_013800138000_1__0": ["closed_eye"]}),
        content=_vision_model("驾驶员持续闭眼"),
    )

    visual = [item for item in values["result"]["evidence"] if item["type"] == "vision"]
    assert values["risk_score"] == 1.0
    assert "时段 0.00s-3.00s" in visual[0]["description"]
    assert "关键帧 0.00s" in visual[0]["description"]
