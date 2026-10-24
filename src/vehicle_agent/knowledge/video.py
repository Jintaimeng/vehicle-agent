import json
import re
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from vehicle_agent.domain.models import Detection
from vehicle_agent.knowledge.vision import (
    VisionIndex,
    packaged_vision_index,
    read_stored_video,
    store_image,
)

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


class TrackAssignment(BaseModel):
    timestamp_s: float
    image_id: str
    detection: Detection
    track_id: int


_MIN_IOU = 0.5


def assign_tracks(frames: list[VideoFrame]) -> list[TrackAssignment]:
    """Give each detection a track id from the previous frame only."""

    ordered = sorted(frames, key=lambda item: item.timestamp_s)
    assignments: list[TrackAssignment] = []
    previous: list[tuple[Detection, int]] = []
    next_id = 1
    for frame in ordered:
        current: list[tuple[Detection, int]] = []
        used: set[int] = set()
        for detection in frame.detections:
            match = _best_track(detection, previous, used)
            if match is None:
                track_id = next_id
                next_id += 1
            else:
                used.add(match)
                track_id = previous[match][1]
            current.append((detection, track_id))
            assignments.append(
                TrackAssignment(
                    timestamp_s=frame.timestamp_s,
                    image_id=frame.image_id,
                    detection=detection,
                    track_id=track_id,
                )
            )
        previous = current
    return assignments


def _best_track(
    detection: Detection,
    previous: list[tuple[Detection, int]],
    used: set[int],
) -> int | None:
    best_index: int | None = None
    best_iou = -1.0
    for index, (prior, _track_id) in enumerate(previous):
        if index in used or prior.label != detection.label:
            continue
        score = _iou(prior.bbox, detection.bbox)
        if score + 1e-9 < _MIN_IOU or score <= best_iou:
            continue
        best_iou = score
        best_index = index
    return best_index


def _iou(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    ix1 = max(left[0], right[0])
    iy1 = max(left[1], right[1])
    ix2 = min(left[2], right[2])
    iy2 = min(left[3], right[3])
    intersection = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if intersection == 0:
        return 0.0
    area_left = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    area_right = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = area_left + area_right - intersection
    if union <= 0:
        return 0.0
    return intersection / union


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


VideoBackend = Literal["fixture", "ffmpeg"]
FrameExtractor = Callable[[Path], list[tuple[float, bytes]]]
FfmpegRunner = Callable[[list[str], Path], str]
StreamSampler = Callable[[str], list[tuple[float, bytes]]]
_RTSP_SCHEME = "rtsp://"
_JT1078_LOCATOR = "jt1078://013800138000/1"
_JT1078_RTSP = "rtsp://camera/live"
VideoKind = Literal["file", "rtsp", "jt1078"]


class VideoLocator(BaseModel):
    kind: VideoKind
    video_id: str
    rtsp_url: str | None = None


def resolve_video_locator(video_id: str) -> VideoLocator:
    """Map a stored filename, rtsp URL, or the one fixed JT1078 channel."""

    cleaned = video_id.strip()
    if cleaned.startswith(_RTSP_SCHEME):
        if cleaned == _RTSP_SCHEME:
            raise ValueError("rtsp url must start with rtsp://")
        return VideoLocator(kind="rtsp", video_id=cleaned, rtsp_url=cleaned)
    if cleaned.startswith("jt1078://"):
        if cleaned != _JT1078_LOCATOR:
            raise ValueError("unknown jt1078 channel")
        return VideoLocator(kind="jt1078", video_id=cleaned, rtsp_url=_JT1078_RTSP)
    if not cleaned or cleaned != Path(cleaned).name or cleaned in {".", ".."}:
        raise ValueError("invalid video_id")
    return VideoLocator(kind="file", video_id=cleaned)


def frame_image_id(video_id: str, index: int) -> str:
    """Turn a locator into a single filename, then append the frame index."""

    replaced = "".join(char if char.isascii() and char.isalnum() else "_" for char in video_id)
    collapsed = re.sub(r"_+", "_", replaced).strip("_")
    return f"{collapsed}__{index}"


class FfmpegVideoTimeline:
    """Sample a stored file or a resolved stream, then use the existing aggregator."""

    def __init__(
        self,
        vision: VisionIndex,
        store: Path,
        extract: FrameExtractor | None = None,
        sample: StreamSampler | None = None,
    ) -> None:
        self._vision = vision
        self._store = store
        self._extract = ffmpeg_extract_frames if extract is None else extract
        self._sample = sample_rtsp if sample is None else sample

    def analyze(
        self, video_id: str, speed_kmh: float, content: bytes | None = None
    ) -> VideoOutcome:
        del content
        locator = resolve_video_locator(video_id)
        if locator.kind == "file":
            extracted = self._extract(read_stored_video(self._store, locator.video_id))
        else:
            extracted = self._sample(locator.rtsp_url or "")
        if not extracted:
            raise RuntimeError(f"ffmpeg produced no frames: {video_id}")
        frames: list[VideoFrame] = []
        track_frames = getattr(self._vision, "track_frames", None)
        if track_frames is None:
            detected = [
                self._vision.analyze(frame_image_id(locator.video_id, index), jpeg)
                for index, (_timestamp, jpeg) in enumerate(extracted)
            ]
        else:
            detected = [
                [item.detection for item in frame]
                for frame in track_frames([jpeg for _timestamp, jpeg in extracted])
            ]
        for index, ((timestamp, jpeg), detections) in enumerate(
            zip(extracted, detected, strict=True)
        ):
            image_id = frame_image_id(locator.video_id, index)
            store_image(self._store, image_id, jpeg)
            frames.append(
                VideoFrame(
                    timestamp_s=timestamp,
                    image_id=image_id,
                    detections=detections,
                )
            )
        return aggregate_frames(frames, speed_kmh)


def open_video_timeline(
    backend: VideoBackend, vision: VisionIndex, store: Path
) -> VideoTimeline:
    if backend == "fixture":
        return packaged_video_timeline(vision)
    if backend == "ffmpeg":
        return FfmpegVideoTimeline(vision, store)
    raise ValueError(f"unsupported video backend: {backend}")


def ffmpeg_extract_frames(path: Path) -> list[tuple[float, bytes]]:
    """Sample about one JPEG per second and return pts_time with file bytes."""

    return _collect_sampled_frames(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-vf", "fps=1,showinfo"],
        _run_ffmpeg,
    )


def sample_rtsp(url: str, run: FfmpegRunner | None = None) -> list[tuple[float, bytes]]:
    """Sample one JPEG per second from an rtsp:// URL for at most four seconds."""

    cleaned = url.strip()
    if not cleaned.startswith(_RTSP_SCHEME) or cleaned == _RTSP_SCHEME:
        raise ValueError("rtsp url must start with rtsp://")
    return _collect_sampled_frames(
        [
            "ffmpeg",
            "-hide_banner",
            "-rtsp_transport",
            "tcp",
            "-t",
            "4",
            "-i",
            cleaned,
            "-vf",
            "fps=1,showinfo",
        ],
        _run_ffmpeg if run is None else run,
    )


def _run_ffmpeg(argv: list[str], directory: Path) -> str:
    del directory
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise FileNotFoundError("ffmpeg is not installed") from exc
    return completed.stderr


def _collect_sampled_frames(argv_prefix: list[str], run: FfmpegRunner) -> list[tuple[float, bytes]]:
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        argv = [*argv_prefix, str(folder / "frame_%04d.jpg")]
        try:
            stderr = run(argv, folder)
        except FileNotFoundError as exc:
            raise FileNotFoundError("ffmpeg is not installed") from exc
        return _pair_frame_bytes(stderr, folder)


def _pair_frame_bytes(stderr: str, directory: Path) -> list[tuple[float, bytes]]:
    times = [float(item) for item in _PTS_TIME.findall(stderr)]
    frames = sorted(directory.glob("frame_*.jpg"))
    if not frames:
        message = stderr.strip() or "ffmpeg produced no frames"
        raise RuntimeError(message)
    if len(frames) != len(times):
        raise RuntimeError("ffmpeg frame timestamps do not match images")
    return [
        (timestamp, frame.read_bytes())
        for timestamp, frame in zip(times, frames, strict=True)
    ]


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
