import os
import shutil
from pathlib import Path

import pytest

from vehicle_agent.knowledge.video import ffmpeg_sample_timestamps

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

    times = ffmpeg_sample_timestamps(video)

    assert len(times) >= 2
    gaps = [later - earlier for earlier, later in zip(times, times[1:], strict=False)]
    assert all(0.8 <= gap <= 1.2 for gap in gaps)
