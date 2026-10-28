from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_online_compose_builds_vision_image_and_uses_one_env_file() -> None:
    compose = (ROOT / "deployments" / "docker-compose.online.yml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "deployments" / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "VEHICLE_AGENT_INSTALL_VISION: \"1\"" in compose
    assert "./online.env" in compose
    assert "uv pip install --system --no-cache \".[vision]\"" in dockerfile
    assert "https://download.pytorch.org/whl/cpu" in dockerfile
    assert "libgl1 libglib2.0-0 libsm6 libxext6 libxrender1" in dockerfile
    assert "YOLO('yolov8n.pt')" in dockerfile
    assert "deployments/online.env" in dockerignore


def test_online_env_template_covers_models_vision_and_dependencies() -> None:
    template = (ROOT / "deployments" / "online.env.example").read_text(encoding="utf-8")
    required = (
        "VEHICLE_AGENT_MODEL_BASE_URL=",
        "VEHICLE_AGENT_API_TOKEN=replace-me",
        "VEHICLE_AGENT_MODEL_API_KEY=",
        "VEHICLE_AGENT_MODEL_NAME=",
        "VEHICLE_AGENT_VLM_BASE_URL=",
        "VEHICLE_AGENT_VLM_API_KEY=",
        "VEHICLE_AGENT_VLM_MODEL_NAME=",
        "VEHICLE_AGENT_VISION_BACKEND=yolo",
        "VEHICLE_AGENT_YOLO_WEIGHTS=/app/yolov8n.pt",
        "VEHICLE_AGENT_GRAPH_BACKEND=neo4j",
        "VEHICLE_AGENT_REGULATION_BACKEND=qdrant",
        "VEHICLE_AGENT_INGEST=kafka",
        "NEO4J_AUTH=",
        "KAFKA_ADVERTISED_HOST=",
    )

    for setting in required:
        assert setting in template
