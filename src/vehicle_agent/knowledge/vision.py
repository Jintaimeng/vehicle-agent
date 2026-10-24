import base64
import json
import tempfile
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, field_validator

from vehicle_agent.domain.models import Detection, VisionLabel

VisionBackend = Literal["fixture", "yolo"]
VISION_LABELS: frozenset[str] = frozenset(
    {"driver", "mobile_phone", "seat_belt", "closed_eye", "yawn", "smoking", "camera_occlusion"}
)
_LABEL_ALIASES = {
    "person": "driver",
    "cell phone": "mobile_phone",
    "mobile phone": "mobile_phone",
    "phone": "mobile_phone",
    "seatbelt": "seat_belt",
    "seat belt": "seat_belt",
    "closed eyes": "closed_eye",
    "eyes closed": "closed_eye",
    "cigarette": "smoking",
    "occlusion": "camera_occlusion",
    "camera occlusion": "camera_occlusion",
}


class VisionIndex(Protocol):
    def analyze(self, image_id: str, content: bytes | None = None) -> list[Detection]: ...


class FixtureVisionIndex:
    """Labeled samples. Pixel bytes are ignored so tests do not decode images."""

    def __init__(self, samples: dict[str, list[Detection]]) -> None:
        self._samples = samples

    @classmethod
    def from_file(cls, path: Path) -> "FixtureVisionIndex":
        payload = json.loads(path.read_text(encoding="utf-8"))
        samples: dict[str, list[Detection]] = {}
        for image_id, sample in payload.items():
            version = sample["model_version"]
            samples[image_id] = [
                Detection(model_version=version, **item) for item in sample["detections"]
            ]
        return cls(samples)

    def analyze(self, image_id: str, content: bytes | None = None) -> list[Detection]:
        return list(self._samples.get(image_id, []))


def packaged_vision_index() -> FixtureVisionIndex:
    root = Path(__file__).resolve().parent / "vision_fixtures" / "catalog.json"
    return FixtureVisionIndex.from_file(root)


class YoloVisionIndex:
    """Optional detector. ultralytics is imported only when this backend is used."""

    def __init__(self, weights: str) -> None:
        self._weights = weights
        self._model = None

    def analyze(self, image_id: str, content: bytes | None = None) -> list[Detection]:
        if not content:
            raise ValueError("yolo backend requires image bytes")
        model = self._load()
        path = _write_temp_image(content)
        try:
            results = model.predict(path, verbose=False)
        finally:
            Path(path).unlink(missing_ok=True)
        if not results:
            return []
        result = results[0]
        height, width = result.orig_shape
        version = Path(self._weights).name
        detections: list[Detection] = []
        boxes = result.boxes
        if boxes is None:
            return []
        for box in boxes:
            label = map_vision_label(model.names[int(box.cls[0])])
            if label is None:
                continue
            xyxy = box.xyxy[0].tolist()
            detections.append(
                Detection(
                    label=label,
                    confidence=float(box.conf[0]),
                    bbox=_clamp_bbox(xyxy, width, height),
                    model_version=version,
                )
            )
        return detections

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError("ultralytics is not installed") from exc
        weights = Path(self._weights)
        if not weights.is_file():
            raise FileNotFoundError(f"YOLO weights not found: {weights}")
        self._model = YOLO(str(weights))
        return self._model


class ObservationUnavailable(Exception):
    """The observation source could not read this image."""


class ObservationSource(Protocol):
    def observe(self, image_id: str, *, task_id: str) -> list[VisionLabel]: ...


class FixtureObservationIndex:
    """Hand-written labels stored apart from detector samples."""

    def __init__(self, samples: dict[str, list[VisionLabel]]) -> None:
        self._samples = samples

    @classmethod
    def from_file(cls, path: Path) -> "FixtureObservationIndex":
        payload = json.loads(path.read_text(encoding="utf-8"))
        samples: dict[str, list[VisionLabel]] = {}
        for image_id, labels in payload.items():
            samples[image_id] = [_require_label(item) for item in labels]
        return cls(samples)

    def observe(self, image_id: str, *, task_id: str) -> list[VisionLabel]:
        del task_id
        try:
            return list(self._samples[image_id])
        except KeyError as exc:
            raise ObservationUnavailable(f"observation missing: {image_id}") from exc


def packaged_observations() -> FixtureObservationIndex:
    root = Path(__file__).resolve().parent / "vision_fixtures" / "observations.json"
    return FixtureObservationIndex.from_file(root)


class VisionLabelResult(BaseModel):
    labels: list[VisionLabel]

    @field_validator("labels", mode="before")
    @classmethod
    def keep_allowed(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("labels must be a list")
        mapped: list[str] = []
        for item in value:
            label = map_vision_label(str(item))
            if label is None or label in mapped:
                continue
            mapped.append(label)
        if value and not mapped:
            raise ValueError("no allowed vision label")
        return mapped


class VlmObservation:
    """Asks a vision model for the same label list the fixture catalog stores."""

    def __init__(self, harness, store: Path) -> None:
        self._harness = harness
        self._store = store

    def observe(self, image_id: str, *, task_id: str) -> list[VisionLabel]:
        content = read_stored_image(self._store, image_id)
        parsed = self._harness.complete_structured(
            _vlm_messages(image_id, content),
            VisionLabelResult,
            task_id=task_id,
            node="vision",
            model_name=self._harness.settings.vlm_model_name,
            endpoint="vision",
            note_from=lambda item: _review_note(
                image_id, self._harness.settings.prompt_version, item
            ),
        )
        return list(parsed.labels)


def compare_observation(
    detections: list[Detection], labels: list[VisionLabel]
) -> tuple[Literal["match", "conflict"], list[str]]:
    """Check whether every strong detection label is present in the observation."""

    strong: list[VisionLabel] = []
    for item in detections:
        if item.confidence >= 0.6 and item.label not in strong:
            strong.append(item.label)
    observed = set(labels)
    unsupported = [label for label in strong if label not in observed]
    extra = [label for label in labels if label not in strong]
    facts: list[str] = []
    if unsupported:
        facts.append(f"检测类别 {', '.join(strong)}，画面观察不支持 {', '.join(unsupported)}")
        agreement: Literal["match", "conflict"] = "conflict"
    else:
        supported = ", ".join(strong) if strong else "无"
        facts.append(f"画面观察支持 {supported}")
        agreement = "match"
    if extra:
        facts.append(f"画面观察另见 {', '.join(extra)}")
    return agreement, facts


_OVERLAY_SIDE = 100


def render_detection_overlay(
    detections: list[Detection],
    *,
    width: int = _OVERLAY_SIDE,
    height: int = _OVERLAY_SIDE,
) -> bytes:
    """Draw detection borders on a fixed black canvas. Source pixels are not read."""

    if width < 1 or height < 1:
        raise ValueError("overlay side must be at least 1")
    pixels = bytearray(width * height * 3)
    for item in detections:
        _draw_overlay_border(pixels, width, height, item.bbox)
    header = f"P6\n{width} {height}\n255\n".encode("ascii")
    return header + bytes(pixels)


def _draw_overlay_border(
    pixels: bytearray,
    width: int,
    height: int,
    bbox: tuple[float, float, float, float],
) -> None:
    x1, y1, x2, y2 = bbox
    left = int(x1 * width + 1e-6)
    top = int(y1 * height + 1e-6)
    right = int(x2 * width + 1e-6)
    bottom = int(y2 * height + 1e-6)
    left = max(0, min(width, left))
    top = max(0, min(height, top))
    right = max(0, min(width, right))
    bottom = max(0, min(height, bottom))
    if right <= left or bottom <= top:
        return
    for y in range(top, bottom):
        for x in range(left, right):
            if x == left or x == right - 1 or y == top or y == bottom - 1:
                offset = (y * width + x) * 3
                pixels[offset : offset + 3] = b"\xff\xff\xff"


def store_image(directory: Path, image_id: str, content: bytes) -> None:
    path = image_file(directory, image_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def store_video(directory: Path, video_id: str, content: bytes) -> None:
    path = image_file(directory, video_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def read_stored_video(directory: Path, video_id: str) -> Path:
    path = image_file(directory, video_id)
    if not path.is_file():
        raise FileNotFoundError(f"stored video missing: {video_id}")
    return path


def read_stored_image(directory: Path, image_id: str) -> bytes:
    path = image_file(directory, image_id)
    if not path.is_file():
        raise ObservationUnavailable(f"stored image missing: {image_id}")
    return path.read_bytes()


def image_file(directory: Path, image_id: str) -> Path:
    if not image_id or image_id != Path(image_id).name or image_id in {".", ".."}:
        raise ValueError("invalid image_id")
    return directory / image_id


def open_observation(harness, store: Path | None = None):
    if harness.settings.review_backend == "fixture":
        return packaged_observations()
    if harness.settings.review_backend == "vlm":
        return VlmObservation(harness, store or Path(harness.settings.vision_store_dir))
    raise ValueError(f"unsupported review backend: {harness.settings.review_backend}")


def _require_label(value: str) -> VisionLabel:
    label = map_vision_label(value)
    if label is None:
        raise ValueError(f"unsupported observation label: {value}")
    return label


def _review_note(image_id: str, prompt_version: str, parsed: VisionLabelResult) -> str:
    return json.dumps(
        {
            "image_id": image_id,
            "prompt_version": prompt_version,
            "labels": parsed.labels,
        },
        ensure_ascii=False,
    )


def _vlm_messages(image_id: str, content: bytes) -> list[dict]:
    encoded = base64.b64encode(content).decode("ascii")
    return [
        {
            "role": "system",
            "content": (
                "你是画面复核助手。只根据图像列出看得见的类别。"
                "输出 JSON 对象，字段为 labels。"
                "类别只能是 driver、mobile_phone、seat_belt、closed_eye、"
                "yawn、smoking、camera_occlusion。"
                "不要推测身份，不要输出检测框或风险分。"
            ),
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": image_id},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                },
            ],
        },
    ]


def open_vision_index(backend: VisionBackend, *, weights: str) -> VisionIndex:
    if backend == "fixture":
        return packaged_vision_index()
    if backend == "yolo":
        return YoloVisionIndex(weights)
    raise ValueError(f"unsupported vision backend: {backend}")


def map_vision_label(name: str) -> VisionLabel | None:
    raw = name.strip().lower()
    compact = raw.replace("-", " ").replace("_", " ")
    underscored = compact.replace(" ", "_")
    if underscored in VISION_LABELS:
        return underscored  # type: ignore[return-value]
    alias = _LABEL_ALIASES.get(raw) or _LABEL_ALIASES.get(compact)
    if alias in VISION_LABELS:
        return alias  # type: ignore[return-value]
    return None


def _clamp_bbox(xyxy: list[float], width: int, height: int) -> tuple[float, float, float, float]:
    if width <= 0 or height <= 0:
        raise ValueError("image has no size")
    x1, y1, x2, y2 = (xyxy[0] / width, xyxy[1] / height, xyxy[2] / width, xyxy[3] / height)
    ordered = (
        min(x1, x2),
        min(y1, y2),
        max(x1, x2),
        max(y1, y2),
    )
    return tuple(min(1.0, max(0.0, value)) for value in ordered)  # type: ignore[return-value]


def _write_temp_image(content: bytes) -> str:
    handle = tempfile.NamedTemporaryFile(suffix=".img", delete=False)
    try:
        handle.write(content)
    finally:
        handle.close()
    return handle.name
