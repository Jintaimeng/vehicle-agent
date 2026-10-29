"""Run the opt-in acceptance layers without silently skipping live dependencies."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", str(ROOT / "deployments" / "docker-compose.yml")]
ONLINE_ENV = ROOT / "deployments" / "online.env"
ONLINE_COMPOSE = [
    "docker",
    "compose",
    "-p",
    "vehicle-agent-online",
    "--env-file",
    str(ONLINE_ENV),
    "-f",
    str(ROOT / "deployments" / "docker-compose.yml"),
    "-f",
    str(ROOT / "deployments" / "docker-compose.online.yml"),
]
LIVE_SWITCHES = {
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
}


def model_preflight_errors(
    env: dict[str, str],
    *,
    root: Path = ROOT,
    containerized_yolo: bool = False,
) -> list[str]:
    """Return every missing prerequisite for a real YOLO + VLM run."""

    errors: list[str] = []
    if not containerized_yolo and importlib.util.find_spec("ultralytics") is None:
        errors.append('未安装 YOLO 依赖；请先执行 uv pip install -e ".[dev,vision]"')

    image = _path_from_env(env, "VEHICLE_AGENT_YOLO_IMAGE", "data/vision-sample.jpg", root)
    vlm_image = _path_from_env(env, "VEHICLE_AGENT_VLM_IMAGE", str(image), root)
    for label, path in (("YOLO 样图", image), ("VLM 样图", vlm_image)):
        if not path.is_file():
            errors.append(f"{label}不存在: {path}")

    configured_weights = env.get("VEHICLE_AGENT_YOLO_WEIGHTS")
    if configured_weights and not containerized_yolo:
        weights = _resolve_path(configured_weights, root)
        if not weights.is_file():
            errors.append(f"YOLO 权重不存在: {weights}")

    for label, variable, model_variable, default in (
        (
            "文本模型",
            "VEHICLE_AGENT_MODEL_BASE_URL",
            "VEHICLE_AGENT_MODEL_NAME",
            "http://127.0.0.1:8001/v1",
        ),
        (
            "视觉模型",
            "VEHICLE_AGENT_VLM_BASE_URL",
            "VEHICLE_AGENT_VLM_MODEL_NAME",
            "http://127.0.0.1:8001/v1",
        ),
    ):
        endpoint = env.get(variable, default).rstrip("/") + "/models"
        api_key = env.get(
            "VEHICLE_AGENT_MODEL_API_KEY" if label == "文本模型" else "VEHICLE_AGENT_VLM_API_KEY",
            "local",
        )
        if not api_key or api_key == "replace-me":
            errors.append(f"{label} API key 未配置: {variable.replace('BASE_URL', 'API_KEY')}")
            continue
        request = urllib.request.Request(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                if response.status != 200:
                    errors.append(f"{label} /models 返回 HTTP {response.status}: {endpoint}")
                    continue
                payload = json.load(response)
                available = {
                    item.get("id", "").removeprefix("models/")
                    for item in payload.get("data", [])
                }
                selected = env.get(model_variable, "")
                if not selected:
                    errors.append(f"{label}模型名未配置: {model_variable}")
                elif selected not in available:
                    errors.append(f"{label}模型不存在: {selected}")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            errors.append(f"{label}不可用: {endpoint} ({exc})")
    return errors


def run_infrastructure(env: dict[str, str]) -> None:
    env = without_live_switches(env)
    _pytest([], env)
    _pytest(
        ["tests/test_compose_acceptance.py"],
        env | {"VEHICLE_AGENT_RUN_COMPOSE": "1"},
    )
    _run([*COMPOSE, "up", "-d", "kafka", "neo4j", "qdrant"], env)
    _pytest(
        [
            "tests/test_kafka_acceptance.py",
            "tests/test_neo4j_acceptance.py",
            "tests/test_qdrant_acceptance.py",
            "tests/test_mcp_acceptance.py",
        ],
        env
        | {
            "VEHICLE_AGENT_RUN_KAFKA": "1",
            "VEHICLE_AGENT_RUN_NEO4J": "1",
            "VEHICLE_AGENT_RUN_QDRANT": "1",
            "VEHICLE_AGENT_RUN_MCP": "1",
        },
    )


def run_models(env: dict[str, str]) -> None:
    configured = env | load_env_file(ONLINE_ENV)
    errors = model_preflight_errors(configured, containerized_yolo=True)
    api_token = configured.get("VEHICLE_AGENT_API_TOKEN", "")
    if not api_token or api_token == "replace-me":
        errors.append("线上 API token 未配置: VEHICLE_AGENT_API_TOKEN")
    if errors:
        details = "\n".join(f"- {item}" for item in errors)
        raise SystemExit(f"真实模型验收前置检查失败：\n{details}")
    _run([*ONLINE_COMPOSE, "up", "-d", "--build"], configured)
    live = configured | {"VEHICLE_AGENT_RUN_ONLINE": "1"}
    _pytest(
        ["tests/test_online_deployment_acceptance.py"],
        live,
    )


def without_live_switches(env: dict[str, str]) -> dict[str, str]:
    return {name: value for name, value in env.items() if name not in LIVE_SWITCHES}


def load_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise SystemExit(f"配置文件不存在: {path}")
    values: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise SystemExit(f"配置文件第 {number} 行缺少 '=': {path}")
        name, value = stripped.split("=", 1)
        values[name.strip()] = value.strip()
    return values


def _pytest(paths: list[str], env: dict[str, str]) -> None:
    temp = ROOT / ".test-tmp"
    temp.mkdir(parents=True, exist_ok=True)
    base_temp = Path(".test-tmp") / f"live-acceptance-{uuid4().hex}"
    (ROOT / base_temp).mkdir()
    command = [
        sys.executable,
        "-m",
        "pytest",
        *paths,
        "--basetemp",
        str(base_temp),
    ]
    _run(command, env | {"TEMP": str(temp), "TMP": str(temp)})


def _run(command: list[str], env: dict[str, str]) -> None:
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def _path_from_env(env: dict[str, str], name: str, default: str, root: Path) -> Path:
    return _resolve_path(env.get(name, default), root)


def _resolve_path(value: str, root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def main() -> None:
    parser = argparse.ArgumentParser(description="执行真实服务与模型验收")
    parser.add_argument("profile", choices=("infrastructure", "models", "all"))
    args = parser.parse_args()
    env = dict(os.environ)
    if args.profile in {"infrastructure", "all"}:
        run_infrastructure(env)
    if args.profile in {"models", "all"}:
        run_models(env)


if __name__ == "__main__":
    main()
