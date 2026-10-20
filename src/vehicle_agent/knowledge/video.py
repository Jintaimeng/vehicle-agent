import json
import re
import subprocess
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from vehicle_agent.domain.models import Detection
from vehicle_agent.knowledge.vision import VisionIndex, packaged_vision_index

MIN_DURATION_S = 2.0
MAX_GAP_S = 1.5
MIN_MEAN_CONFIDENCE = 0.6
MIN_SPEED_KMH = 60.0
MIN_FACE_QUALITY = 0.5
_FATIGUE_LABELS = frozenset({"closed_eye", "yawn"})
_OCCLUSION = "camera_occlusion"
_PTS_TIME = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")


class VideoFrame(BaseModel):
    timestamp_s: float = Field(ge=0)
    image_id: str
    detections: list[Detection]
    face_quality: float = Field(default=1, ge=0, le=1)


class VideoEvent(BaseModel):
    start_s: float
    end_s: float
    keyframe_timestamp_s: float
    keyframe_image_id: str
    detection: Detection


class VideoOutcome(BaseModel):
    frames: list[VideoFrame]
    event: VideoEvent | None = None
    occlusion: Detection | None = None


class VideoTimeline(Protocol):
    def analyze(
        self, video_id: str, speed_kmh: float, content: bytes | None = None
    ) -> VideoOutcome: ...


def aggregate_frames(frames: list[VideoFrame], speed_kmh: float) -> VideoOutcome:
    """Collapse timestamped frames into at most one fatigue event."""

    ordered = sorted(frames, key=lambda item: item.timestamp_s)
    event = _fatigue_event(ordered, speed_kmh)
    occlusion = None if event is not None else _strongest_occlusion(ordered)
    return VideoOutcome(frames=ordered, event=event, occlusion=occlusion)


class FixtureVideoTimeline:
    """Timestamped image ids. File bytes are ignored so tests do not decode video."""

    def __init__(
        self, samples: dict[str, list[tuple[float, str, float]]], vision: VisionIndex
    ) -> None:
        self._samples = samples
        self._vision = vision

    @classmethod
    def from_file(cls, path: Path, vision: VisionIndex) -> "FixtureVideoTimeline":
        payload = json.loads(path.read_text(encoding="utf-8"))
        samples: dict[str, list[tuple[float, str, float]]] = {}
        for video_id, sample in payload.items():
            samples[video_id] = [
                (
                    float(frame["timestamp_s"]),
                    frame["image_id"],
                    float(frame.get("face_quality", 1)),
                )
                for frame in sample["frames"]
            ]
        return cls(samples, vision)

    def analyze(
        self, video_id: str, speed_kmh: float, content: bytes | None = None
    ) -> VideoOutcome:
        rows = self._samples.get(video_id)
        if rows is None:
            return VideoOutcome(frames=[])
        frames = [
            VideoFrame(
                timestamp_s=timestamp,
                image_id=image_id,
                detections=self._vision.analyze(image_id),
                face_quality=quality,
            )
            for timestamp, image_id, quality in rows
        ]
        return aggregate_frames(frames, speed_kmh)


def packaged_video_timeline(vision: VisionIndex | None = None) -> FixtureVideoTimeline:
    root = Path(__file__).resolve().parent / "video_fixtures" / "catalog.json"
    return FixtureVideoTimeline.from_file(root, vision or packaged_vision_index())


def ffmpeg_sample_timestamps(path: Path) -> list[float]:
    """Sample about one frame per second and return pts_time values from ffmpeg."""

    try:
        completed = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-i",
                str(path),
                "-vf",
                "fps=1,showinfo",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise FileNotFoundError("ffmpeg is not installed") from exc
    times = [float(item) for item in _PTS_TIME.findall(completed.stderr)]
    if completed.returncode != 0 and not times:
        message = completed.stderr.strip() or "ffmpeg failed"
        raise RuntimeError(message)
    return times


def _fatigue_event(frames: list[VideoFrame], speed_kmh: float) -> VideoEvent | None:
    if speed_kmh < MIN_SPEED_KMH:
        return None
    hits: list[tuple[VideoFrame, Detection]] = []
    for frame in frames:
        detection = _fatigue_detection(frame)
        if detection is not None:
            hits.append((frame, detection))
    run: list[tuple[VideoFrame, Detection]] = []
    for frame, detection in hits:
        if run and frame.timestamp_s - run[-1][0].timestamp_s > MAX_GAP_S:
            event = _event_from_run(run, frames)
            if event is not None:
                return event
            run = []
        run.append((frame, detection))
    return _event_from_run(run, frames)


def _event_from_run(
    run: list[tuple[VideoFrame, Detection]], frames: list[VideoFrame]
) -> VideoEvent | None:
    if not run:
        return None
    start = run[0][0].timestamp_s
    end = run[-1][0].timestamp_s
    if _window_has_occlusion(frames, start, end):
        return None
    if end - start < MIN_DURATION_S:
        return None
    mean = sum(detection.confidence for _, detection in run) / len(run)
    if mean < MIN_MEAN_CONFIDENCE:
        return None
    frame, detection = max(run, key=lambda item: (item[1].confidence, -item[0].timestamp_s))
    return VideoEvent(
        start_s=start,
        end_s=end,
        keyframe_timestamp_s=frame.timestamp_s,
        keyframe_image_id=frame.image_id,
        detection=detection,
    )


def _fatigue_detection(frame: VideoFrame) -> Detection | None:
    if frame.face_quality < MIN_FACE_QUALITY:
        return None
    matched = [
        item
        for item in frame.detections
        if item.label in _FATIGUE_LABELS and item.confidence >= MIN_MEAN_CONFIDENCE
    ]
    if not matched:
        return None
    return max(matched, key=lambda item: item.confidence)


def _window_has_occlusion(frames: list[VideoFrame], start: float, end: float) -> bool:
    return any(
        start <= frame.timestamp_s <= end and _frame_occlusion(frame) is not None
        for frame in frames
    )


def _frame_occlusion(frame: VideoFrame) -> Detection | None:
    matched = [
        item
        for item in frame.detections
        if item.label == _OCCLUSION and item.confidence >= MIN_MEAN_CONFIDENCE
    ]
    if not matched:
        return None
    return max(matched, key=lambda item: item.confidence)


def _strongest_occlusion(frames: list[VideoFrame]) -> Detection | None:
    matched = [
        item
        for frame in frames
        for item in frame.detections
        if item.label == _OCCLUSION and item.confidence >= MIN_MEAN_CONFIDENCE
    ]
    if not matched:
        return None
    return max(matched, key=lambda item: item.confidence)
