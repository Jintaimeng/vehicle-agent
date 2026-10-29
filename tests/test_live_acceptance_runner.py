import importlib.util
import io
import json
from pathlib import Path
from urllib.error import URLError

from scripts.run_live_acceptance import (
    load_env_file,
    model_preflight_errors,
    without_live_switches,
)


class _Response:
    status = 200

    def __init__(self, models=("text-model", "vision-model")) -> None:
        self._content = io.BytesIO(
            json.dumps({"data": [{"id": item} for item in models]}).encode()
        )

    def read(self, size=-1):
        return self._content.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


def test_model_preflight_reports_all_missing_inputs(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(
        "scripts.run_live_acceptance.urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(URLError("offline")),
    )

    errors = model_preflight_errors({}, root=tmp_path)

    assert any("YOLO 依赖" in item for item in errors)
    assert any("YOLO 样图不存在" in item for item in errors)
    assert any("VLM 样图不存在" in item for item in errors)
    assert any("文本模型不可用" in item for item in errors)
    assert any("视觉模型不可用" in item for item in errors)


def test_model_preflight_accepts_official_weights_and_live_endpoints(tmp_path, monkeypatch) -> None:
    image = tmp_path / "sample.jpg"
    image.write_bytes(b"jpeg")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(
        "scripts.run_live_acceptance.urllib.request.urlopen",
        lambda *args, **kwargs: _Response(),
    )

    errors = model_preflight_errors(
        {
            "VEHICLE_AGENT_YOLO_IMAGE": str(image),
            "VEHICLE_AGENT_VLM_IMAGE": str(image),
            "VEHICLE_AGENT_MODEL_NAME": "text-model",
            "VEHICLE_AGENT_VLM_MODEL_NAME": "vision-model",
        },
        root=tmp_path,
    )

    assert errors == []


def test_model_preflight_requires_explicit_local_weights(tmp_path, monkeypatch) -> None:
    image = tmp_path / "sample.jpg"
    image.write_bytes(b"jpeg")
    missing = Path("models/custom.pt")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(
        "scripts.run_live_acceptance.urllib.request.urlopen",
        lambda *args, **kwargs: _Response(),
    )

    errors = model_preflight_errors(
        {
            "VEHICLE_AGENT_YOLO_IMAGE": str(image),
            "VEHICLE_AGENT_VLM_IMAGE": str(image),
            "VEHICLE_AGENT_YOLO_WEIGHTS": str(missing),
            "VEHICLE_AGENT_MODEL_NAME": "text-model",
            "VEHICLE_AGENT_VLM_MODEL_NAME": "vision-model",
        },
        root=tmp_path,
    )

    assert errors == [f"YOLO 权重不存在: {tmp_path / missing}"]


def test_infrastructure_environment_clears_live_switches() -> None:
    cleaned = without_live_switches(
        {
            "PATH": "bin",
            "VEHICLE_AGENT_RUN_YOLO": "1",
            "VEHICLE_AGENT_RUN_COMPOSE": "1",
        }
    )

    assert cleaned == {"PATH": "bin"}


def test_load_env_file_keeps_urls_and_secret_values(tmp_path) -> None:
    path = tmp_path / "online.env"
    path.write_text(
        "# deployment\nMODEL_URL=https://model.example/v1\nAPI_KEY=secret=value\n",
        encoding="utf-8",
    )

    assert load_env_file(path) == {
        "MODEL_URL": "https://model.example/v1",
        "API_KEY": "secret=value",
    }


def test_containerized_preflight_does_not_require_host_yolo(tmp_path, monkeypatch) -> None:
    image = tmp_path / "sample.jpg"
    image.write_bytes(b"jpeg")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(
        "scripts.run_live_acceptance.urllib.request.urlopen",
        lambda *args, **kwargs: _Response(),
    )

    errors = model_preflight_errors(
        {
            "VEHICLE_AGENT_YOLO_IMAGE": str(image),
            "VEHICLE_AGENT_VLM_IMAGE": str(image),
            "VEHICLE_AGENT_YOLO_WEIGHTS": "/app/yolov8n.pt",
            "VEHICLE_AGENT_MODEL_API_KEY": "secret",
            "VEHICLE_AGENT_VLM_API_KEY": "secret",
            "VEHICLE_AGENT_MODEL_NAME": "text-model",
            "VEHICLE_AGENT_VLM_MODEL_NAME": "vision-model",
        },
        root=tmp_path,
        containerized_yolo=True,
    )

    assert errors == []


def test_model_preflight_accepts_gemini_models_prefix(tmp_path, monkeypatch) -> None:
    image = tmp_path / "sample.jpg"
    image.write_bytes(b"jpeg")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(
        "scripts.run_live_acceptance.urllib.request.urlopen",
        lambda *args, **kwargs: _Response(("models/gemini-2.5-flash",)),
    )

    errors = model_preflight_errors(
        {
            "VEHICLE_AGENT_YOLO_IMAGE": str(image),
            "VEHICLE_AGENT_VLM_IMAGE": str(image),
            "VEHICLE_AGENT_MODEL_API_KEY": "secret",
            "VEHICLE_AGENT_VLM_API_KEY": "secret",
            "VEHICLE_AGENT_MODEL_NAME": "gemini-2.5-flash",
            "VEHICLE_AGENT_VLM_MODEL_NAME": "gemini-2.5-flash",
        },
        root=tmp_path,
    )

    assert errors == []
