from vehicle_agent.domain.models import Detection
from vehicle_agent.knowledge.video import VideoFrame, assign_tracks, packaged_video_timeline
from vehicle_agent.knowledge.vision import YoloVisionIndex

_CLOSED_EYE = (0.4, 0.2, 0.62, 0.55)


def test_fixture_fatigue_frames_share_one_track() -> None:
    outcome = packaged_video_timeline().analyze("video_fatigue", 80)
    empty = packaged_video_timeline().analyze("video_empty", 80)

    tracks = assign_tracks(outcome.frames)
    yawn = assign_tracks(
        [
            _frame(0, [_detection("closed_eye", _CLOSED_EYE)]),
            _frame(1, [_detection("yawn", _CLOSED_EYE)]),
        ]
    )
    disjoint = assign_tracks(
        [
            _frame(0, [_detection("closed_eye", _CLOSED_EYE)]),
            _frame(1, [_detection("closed_eye", (0.0, 0.0, 0.1, 0.1))]),
        ]
    )
    touching = assign_tracks(
        [
            _frame(0, [_detection("closed_eye", (0.0, 0.0, 0.3, 0.3))]),
            _frame(1, [_detection("closed_eye", (0.1, 0.0, 0.4, 0.3))]),
        ]
    )

    assert [item.track_id for item in tracks] == [1, 1, 1, 1]
    assert assign_tracks(empty.frames) == []
    assert [item.track_id for item in yawn] == [1, 2]
    assert [item.track_id for item in disjoint] == [1, 2]
    assert [item.track_id for item in touching] == [1, 1]
    assert outcome.event is not None
    assert not hasattr(outcome, "track_id")


def test_fake_yolo_tracks_a_sequence_then_predicts_a_still() -> None:
    model = _FakeModel()
    index = YoloVisionIndex("unused.pt")
    index._model = model

    tracked = index.track_frames([b"frame-a", b"frame-b"])
    still = index.analyze("still", b"frame-c")

    assert model.calls == [
        ("track", True, False),
        ("track", True, False),
        ("predict", False),
    ]
    assert model.predictor.trackers == []
    assert tracked[0][0].track_id == 7
    assert tracked[0][0].detection.label == "driver"
    assert still[0].label == "driver"
    assert not hasattr(still[0], "track_id")


class _Predictor:
    def __init__(self) -> None:
        self.trackers = ["live"]


class _FakeModel:
    def __init__(self) -> None:
        self.names = {0: "person"}
        self.predictor = _Predictor()
        self.calls: list[tuple] = []

    def track(self, path, persist=False, verbose=False):
        self.calls.append(("track", persist, verbose))
        self.predictor.trackers = ["live"]
        return [_result(7)]

    def predict(self, path, verbose=False):
        self.calls.append(("predict", verbose))
        return [_result(None)]


def _result(track_id: int | None):
    box = _Box(track_id)
    return _Result(box)


class _Result:
    def __init__(self, box) -> None:
        self.orig_shape = (10, 10)
        self.boxes = [box]


class _Box:
    def __init__(self, track_id: int | None) -> None:
        self.cls = _Seq(_Item(0))
        self.conf = _Seq(_Item(0.9))
        self.xyxy = _Seq(_Item([1, 1, 5, 5]))
        self.id = None if track_id is None else _Seq(_Item(track_id))


class _Seq:
    def __init__(self, value) -> None:
        self.value = value

    def __getitem__(self, index):
        return self.value


class _Item:
    def __init__(self, value) -> None:
        self.value = value

    def __int__(self) -> int:
        return int(self.value)

    def __float__(self) -> float:
        return float(self.value)

    def tolist(self):
        return list(self.value)


def _frame(timestamp_s: float, detections: list[Detection]) -> VideoFrame:
    return VideoFrame(timestamp_s=timestamp_s, image_id="image", detections=detections)


def _detection(label: str, bbox: tuple[float, float, float, float]) -> Detection:
    return Detection(
        label=label,
        confidence=0.91,
        bbox=bbox,
        model_version="fixture-v1",
    )
