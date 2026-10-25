from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the model endpoint, harness budget, and checkpoints."""

    model_config = SettingsConfigDict(env_prefix="VEHICLE_AGENT_", extra="ignore")

    model_backend: Literal["openai", "fixture"] = "openai"
    ingest: Literal["off", "kafka"] = "off"
    kafka_bootstrap: str = "127.0.0.1:9092"
    model_base_url: str = "http://127.0.0.1:8001/v1"
    model_api_key: str = "local"
    model_name: str = "qwen"
    host: str = "127.0.0.1"
    port: int = 8000
    prompt_version: str = "report-v6"
    request_timeout_seconds: float = 30
    max_retries: int = 2
    max_tool_calls: int = 8
    max_model_calls: int = 4
    max_tokens: int = 4000
    max_elapsed_seconds: float = 60
    review_confidence_threshold: float = 0.6
    checkpoint_path: str = Field(default="data/checkpoints.sqlite")
    graph_backend: Literal["memory", "neo4j"] = "memory"
    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "vehicle-agent-local"
    regulation_backend: Literal["markdown", "qdrant"] = "markdown"
    qdrant_url: str = "http://127.0.0.1:6333"
    tool_backend: Literal["local", "mcp"] = "local"
    mcp_transport: Literal["memory", "stdio"] = "memory"
    mcp_role: str = "monitor"
    vision_backend: Literal["fixture", "yolo"] = "fixture"
    video_backend: Literal["fixture", "ffmpeg"] = "fixture"
    yolo_weights: str = "data/yolo.pt"
    review_backend: Literal["fixture", "vlm"] = "fixture"
    vision_store_dir: str = "data/vision"
    vlm_base_url: str = "http://127.0.0.1:8001/v1"
    vlm_api_key: str = "local"
    vlm_model_name: str = "qwen-vl"
