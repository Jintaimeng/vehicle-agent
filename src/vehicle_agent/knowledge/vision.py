import json
import tempfile
from pathlib import Path
from typing import Literal, Protocol

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
