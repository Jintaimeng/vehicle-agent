import json
import os
import sys
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import mcp.types as types
from anyio.from_thread import BlockingPortal
from mcp.client.stdio import StdioServerParameters, stdio_client
from pydantic import BaseModel

from vehicle_agent.config import Settings
from vehicle_agent.harness.errors import PolicyDeniedError, ToolExecutionError
from vehicle_agent.harness.tools import (
    RegisteredTool,
    ToolRegistry,
    build_vehicle_tools,
)
from vehicle_agent.mcp.server import POLICY_DENIED, memory_streams
from vehicle_agent.repositories.memory import build_seed_repository

Connector = Callable[[], AsyncIterator[object]]


class McpToolSession:
    """Synchronous facade over one async MCP client session."""

    def __init__(self, *, role: str, connector: Connector) -> None:
        self.calls: list[str] = []
        self._role = role
        self._connector = connector
        self._portal: BlockingPortal | None = None
        self._client = None
        self._stop: anyio.Event | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._started = False
        self._closed = False
        self._start_error: Exception | None = None
        self._lock = threading.Lock()

    @classmethod
    def open_memory(cls, registry: ToolRegistry, *, role: str) -> "McpToolSession":
        return cls(role=role, connector=lambda: memory_streams(registry))

    @classmethod
    def open_stdio(cls, settings: Settings) -> "McpToolSession":
        return cls(role=settings.mcp_role, connector=lambda: _stdio_streams(settings))

    def list_tools(self) -> types.ListToolsResult:
        self._ensure_started()
        assert self._portal is not None
        assert self._client is not None
        return self._portal.call(self._client.list_tools)

    def call(self, name: str, arguments: dict) -> dict:
        self.calls.append(name)
        self._ensure_started()
        assert self._portal is not None
        try:
            return self._portal.call(self._invoke, name, arguments)
        except PolicyDeniedError:
            raise
        except ToolExecutionError:
            raise
        except Exception as exc:
            raise ToolExecutionError(str(exc)) from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._started:
            return
        self._ready.wait(timeout=5)
        if self._start_error is None and self._portal is not None and self._stop is not None:
            try:
                self._portal.call(self._stop.set)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)

    async def _invoke(self, name: str, arguments: dict) -> dict:
        assert self._client is not None
        try:
            result = await self._client.call_tool(name, arguments, meta={"role": self._role})
        except Exception as exc:
            raise ToolExecutionError(str(exc)) from exc
        if getattr(result, "is_error", False):
            message = _result_text(result)
            if message == POLICY_DENIED:
                raise PolicyDeniedError("mcp role is not allowed")
            raise ToolExecutionError(message or "mcp tool failed")
        structured = getattr(result, "structured_content", None)
        if isinstance(structured, dict):
            return structured
        body = json.loads(_result_text(result) or "{}")
        if not isinstance(body, dict):
            raise ToolExecutionError("mcp tool result must be a JSON object")
        return body

    def _ensure_started(self) -> None:
        with self._lock:
            if not self._started:
                self._started = True
                self._thread = threading.Thread(
                    target=self._run,
                    name="mcp-tool-session",
                    daemon=True,
                )
                self._thread.start()
        if not self._ready.wait(timeout=10):
            raise ToolExecutionError("mcp session is not ready")
        if self._start_error is not None:
            raise ToolExecutionError(str(self._start_error))

    def _run(self) -> None:
        try:
            anyio.run(self._serve)
        except Exception as exc:
            self._start_error = exc
        finally:
            self._ready.set()

    async def _serve(self) -> None:
        async with BlockingPortal() as portal:
            self._portal = portal
            self._stop = anyio.Event()
            async with self._connector() as client:
                self._client = client
                self._ready.set()
                await self._stop.wait()


def build_mcp_client_tools(
    session: McpToolSession,
    templates: ToolRegistry | None = None,
) -> ToolRegistry:
    source = build_vehicle_tools(build_seed_repository()) if templates is None else templates
    registry = ToolRegistry()
    for tool in source.tools():
        registry.register(
            RegisteredTool(
                name=tool.name,
                description=tool.description,
                input_model=tool.input_model,
                output_model=tool.output_model,
                handler=_client_handler(session, tool.name, tool.output_model),
                readonly=tool.readonly,
                required_role=tool.required_role,
            )
        )
    return registry


def open_registered_tools(
    settings: Settings,
    repository,
    regulations,
    graph,
    vision=None,
    timeline=None,
    workorders=None,
):
    if settings.tool_backend == "local":
        return (
            build_vehicle_tools(
                repository,
                regulations,
                graph,
                vision,
                timeline,
                settings=settings,
                workorders=workorders,
            ),
            None,
        )
    if settings.tool_backend != "mcp":
        raise ValueError(f"unsupported tool backend: {settings.tool_backend}")
    if settings.mcp_transport == "memory":
        server_tools = build_vehicle_tools(
            repository,
            regulations,
            graph,
            vision,
            timeline,
            settings=settings,
            workorders=workorders,
        )
        session = McpToolSession.open_memory(server_tools, role=settings.mcp_role)
        return build_mcp_client_tools(session, server_tools), session
    if settings.mcp_transport == "stdio":
        session = McpToolSession.open_stdio(settings)
        return build_mcp_client_tools(session), session
    raise ValueError(f"unsupported mcp transport: {settings.mcp_transport}")


def _client_handler(session: McpToolSession, name: str, output_model: type[BaseModel]):
    def handle(payload: BaseModel) -> BaseModel:
        body = session.call(name, payload.model_dump(mode="json"))
        return output_model.model_validate(body)

    return handle


def _result_text(result) -> str:
    parts: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


@asynccontextmanager
async def _stdio_streams(settings: Settings):
    from mcp.client.session import ClientSession

    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "vehicle_agent.mcp"],
        env=_child_env(settings),
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session


def _child_env(settings: Settings) -> dict[str, str]:
    import vehicle_agent

    pkg_root = str(Path(vehicle_agent.__file__).resolve().parent.parent)
    current = os.environ.get("PYTHONPATH")
    pythonpath = pkg_root if not current else pkg_root + os.pathsep + current
    return {
        "PYTHONPATH": pythonpath,
        "VEHICLE_AGENT_REGULATION_BACKEND": settings.regulation_backend,
        "VEHICLE_AGENT_QDRANT_URL": settings.qdrant_url,
        "VEHICLE_AGENT_GRAPH_BACKEND": settings.graph_backend,
        "VEHICLE_AGENT_NEO4J_URI": settings.neo4j_uri,
        "VEHICLE_AGENT_NEO4J_USER": settings.neo4j_user,
        "VEHICLE_AGENT_NEO4J_PASSWORD": settings.neo4j_password,
        "VEHICLE_AGENT_VISION_BACKEND": settings.vision_backend,
        "VEHICLE_AGENT_VIDEO_BACKEND": settings.video_backend,
        "VEHICLE_AGENT_VISION_STORE_DIR": settings.vision_store_dir,
        "VEHICLE_AGENT_YOLO_WEIGHTS": settings.yolo_weights,
    }
