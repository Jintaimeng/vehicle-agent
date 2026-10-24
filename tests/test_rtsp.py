from pathlib import Path

import pytest
from pydantic import ValidationError

from vehicle_agent.config import Settings
from vehicle_agent.knowledge.video import (
    ffmpeg_extract_frames,
    open_video_timeline,
    sample_rtsp,
)
from vehicle_agent.knowledge.vision import packaged_vision_index

_URL = "rtsp://camera/live"


def test_sample_rtsp_returns_runner_frames() -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], directory: Path) -> str:
        calls.append(argv)
        (directory / "frame_0001.jpg").write_bytes(b"jpeg-a")
        (directory / "frame_0002.jpg").write_bytes(b"jpeg-b")
        return "pts_time:0.0\npts_time:1.0\n"

    frames = sample_rtsp(_URL, run)

    assert frames == [(0.0, b"jpeg-a"), (1.0, b"jpeg-b")]
    assert len(calls) == 1
    argv = calls[0]
    assert argv[:10] == [
        "ffmpeg",
        "-hide_banner",
        "-rtsp_transport",
        "tcp",
        "-t",
        "4",
        "-i",
        _URL,
        "-vf",
        "fps=1,showinfo",
    ]
    assert argv[10].endswith("frame_%04d.jpg")
    assert argv.index("-t") < argv.index("-i")


def test_sample_rtsp_rejects_non_rtsp_urls() -> None:
    def run(argv: list[str], directory: Path) -> str:
        del argv, directory
        raise AssertionError("runner should not be called")

    for url in ("", "   ", "http://camera/live", "data/clip.mp4", "rtsp://"):
        with pytest.raises(ValueError, match="rtsp://"):
            sample_rtsp(url, run)


def test_sample_rtsp_reports_missing_ffmpeg() -> None:
    def run(argv: list[str], directory: Path) -> str:
        del argv, directory
        raise FileNotFoundError("not found")

    with pytest.raises(FileNotFoundError, match="ffmpeg is not installed"):
        sample_rtsp(_URL, run)


def test_sample_rtsp_rejects_empty_or_mismatched_frames() -> None:
    def empty(argv: list[str], directory: Path) -> str:
        del argv, directory
        return "decoder failed"

    def mismatched(argv: list[str], directory: Path) -> str:
        del argv
        (directory / "frame_0001.jpg").write_bytes(b"jpeg-a")
        (directory / "frame_0002.jpg").write_bytes(b"jpeg-b")
        return "pts_time:0.0\n"

    with pytest.raises(RuntimeError, match="decoder failed"):
        sample_rtsp(_URL, empty)
    with pytest.raises(RuntimeError, match="do not match"):
        sample_rtsp(_URL, mismatched)


def test_video_backend_rejects_rtsp(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings(video_backend="rtsp")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unsupported video backend"):
        open_video_timeline("rtsp", packaged_vision_index(), tmp_path)  # type: ignore[arg-type]


def test_file_extract_keeps_local_ffmpeg_args(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, list[str]] = {}

    def fake_run(argv: list[str], **kwargs: object) -> object:
        del kwargs
        seen["argv"] = argv
        folder = Path(argv[-1]).parent
        (folder / "frame_0001.jpg").write_bytes(b"jpeg")

        class _Result:
            stderr = "pts_time:0.5\n"

        return _Result()

    monkeypatch.setattr("vehicle_agent.knowledge.video.subprocess.run", fake_run)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not-a-video")

    frames = ffmpeg_extract_frames(video)

    assert frames == [(0.5, b"jpeg")]
    assert seen["argv"][:6] == [
        "ffmpeg",
        "-hide_banner",
        "-i",
        str(video),
        "-vf",
        "fps=1,showinfo",
    ]
    assert "-rtsp_transport" not in seen["argv"]
    assert "-t" not in seen["argv"]
