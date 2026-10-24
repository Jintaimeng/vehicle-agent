FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /uvx /bin/

WORKDIR /app
ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    VEHICLE_AGENT_MODEL_BACKEND=fixture \
    VEHICLE_AGENT_HOST=0.0.0.0 \
    VEHICLE_AGENT_CHECKPOINT_PATH=/data/checkpoints.sqlite

COPY pyproject.toml README.md ./
COPY src ./src
RUN uv pip install --system --no-cache .

EXPOSE 8000
CMD ["python", "-m", "vehicle_agent.api.main"]
