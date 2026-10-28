from pathlib import Path

_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "check.yml"
_LIVE_SWITCHES = (
    "VEHICLE_AGENT_RUN_COMPOSE",
    "VEHICLE_AGENT_RUN_KAFKA",
    "VEHICLE_AGENT_RUN_NEO4J",
    "VEHICLE_AGENT_RUN_QDRANT",
    "VEHICLE_AGENT_RUN_MODEL",
    "VEHICLE_AGENT_RUN_YOLO",
    "VEHICLE_AGENT_RUN_VLM",
    "VEHICLE_AGENT_RUN_FFMPEG",
    "VEHICLE_AGENT_RUN_MCP",
    "VEHICLE_AGENT_RUN_ONLINE",
)


def test_check_workflow_runs_the_default_commands_only() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")

    assert "uv run ruff check src tests scripts" in text
    assert "uv run pytest" in text
    assert "3.12" in text
    assert "0.9.5" in text
    for name in _LIVE_SWITCHES:
        assert name not in text
    assert "docker build -f deployments/Dockerfile -t vehicle-agent:ci ." in text
    assert (
        "docker run --rm --entrypoint python vehicle-agent:ci -c "
        '"import importlib.util, os, sys; sys.exit(0 if importlib.util.find_spec(\'ultralytics\') '
        "is None and os.environ.get('VEHICLE_AGENT_MODEL_BACKEND') == 'fixture' else 1)\""
    ) in text
    assert "docker/build-push-action" not in text
    assert ".[vision]" not in text
