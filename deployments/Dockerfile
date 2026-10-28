FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /uvx /bin/

WORKDIR /app
ARG VEHICLE_AGENT_INSTALL_VISION=0
ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    VEHICLE_AGENT_MODEL_BACKEND=fixture \
    VEHICLE_AGENT_HOST=0.0.0.0 \
    VEHICLE_AGENT_CHECKPOINT_PATH=/data/checkpoints.sqlite

COPY pyproject.toml README.md ./
COPY src ./src
RUN if [ "$VEHICLE_AGENT_INSTALL_VISION" = "1" ]; then \
      apt-get update && \
      apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 && \
      rm -rf /var/lib/apt/lists/* && \
      uv pip install --system --no-cache \
        --index-url https://download.pytorch.org/whl/cpu torch torchvision && \
      uv pip install --system --no-cache ".[vision]" && \
      python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"; \
    else \
      uv pip install --system --no-cache .; \
    fi

EXPOSE 8000
CMD ["python", "-m", "vehicle_agent.api.main"]
