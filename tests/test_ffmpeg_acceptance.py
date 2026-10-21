import os
import shutil
from pathlib import Path

import pytest

from vehicle_agent.domain.models import Detection
from vehicle_agent.knowledge.video import VideoFrame, aggregate_frames, ffmpeg_extract_frames

pytestmark = pytest.mark.skipif(
    os.environ.get("VEHICLE_AGENT_RUN_FFMPEG") != "1",
    reason="设置 VEHICLE_AGENT_RUN_FFMPEG=1 后用本机 ffmpeg 验收抽帧",
)


def test_ffmpeg_samples_about_one_frame_per_second() -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not on PATH")
    video = Path(os.environ.get("VEHICLE_AGENT_FFMPEG_VIDEO", "data/vision-sample.mp4"))
    if not video.is_file():
        pytest.skip(f"sample video not found: {video}")

    extracted = ffmpeg_extract_frames(video)
    times = [timestamp for timestamp, _ in extracted]

    assert len(extracted) >= 2
    assert all(jpeg.startswith(b"\xff\xd8") for _, jpeg in extracted)
    gaps = [later - earlier for earlier, later in zip(times, times[1:], strict=False)]
    assert all(0.8 <= gap <= 1.2 for gap in gaps)
    outcome = aggregate_frames(
        [
            VideoFrame(
                timestamp_s=timestamp,
                image_id=f"sample__{index}",
                detections=[
                    Detection(
                        label="closed_eye",
                        confidence=0.91,
                        bbox=(0.4, 0.2, 0.62, 0.55),
                        model_version="ffmpeg-v1",
                    )
                ],
            )
            for index, (timestamp, _) in enumerate(extracted)
        ],
        85,
    )
    assert [frame.timestamp_s for frame in outcome.frames] == times
