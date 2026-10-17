from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the model endpoint, harness budget, and checkpoints."""

    model_config = SettingsConfigDict(env_prefix="VEHICLE_AGENT_", extra="ignore")

    model_base_url: str = "http://127.0.0.1:8001/v1"
    model_api_key: str = "local"
    model_name: str = "qwen"
    prompt_version: str = "report-v2"
    request_timeout_seconds: float = 30
    max_retries: int = 2
    max_tool_calls: int = 8
    max_model_calls: int = 4
    max_tokens: int = 4000
    max_elapsed_seconds: float = 60
    review_confidence_threshold: float = 0.6
    checkpoint_path: str = Field(default="data/checkpoints.sqlite")
