import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import mcp.types as types
from mcp.server.lowlevel.server import Server
from mcp.server.stdio import stdio_server

from vehicle_agent.config import Settings
from vehicle_agent.harness.tools import RegisteredTool, ToolRegistry, build_vehicle_tools
from vehicle_agent.knowledge.graph import open_graph_index
from vehicle_agent.knowledge.index import open_regulation_index
from vehicle_agent.knowledge.video import open_video_timeline
from vehicle_agent.knowledge.vision import open_vision_index
from vehicle_agent.repositories.memory import build_seed_repository
from vehicle_agent.workorders import MemoryWorkOrderStore

POLICY_DENIED = "policy_denied"


def build_mcp_server(registry: ToolRegistry) -> Server:
    """Expose an existing tool registry over MCP without reimplementing queries."""

    tools = {tool.name: tool for tool in registry.tools()}

    async def on_list_tools(_ctx, _params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[_definition(tool) for tool in tools.values()])

    async def on_call_tool(_ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        tool = tools.get(params.name)
        if tool is None:
            return _error(f"unknown tool: {params.name}")
        if _caller_role(params) != tool.required_role:
            return _error(POLICY_DENIED)
        try:
            payload = tool.input_model.model_validate(params.arguments or {})
            result = tool.handler(payload)
            output = tool.output_model.model_validate(result)
            body = output.model_dump(mode="json")
        except Exception as exc:
            return _error(str(exc))
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(body, ensure_ascii=False))],
            structured_content=body,
            is_error=False,
        )

    return Server(
        "vehicle-agent",
        version="0.1.0",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


def serve_stdio() -> None:
    settings = Settings()
    regulations = open_regulation_index(settings.regulation_backend, url=settings.qdrant_url)
    graph = open_graph_index(
        settings.graph_backend,
        uri=settings.neo4j_uri,
        user=settings.neo4j_user,
        password=settings.neo4j_password,
    )
    _prepare(regulations)
    _prepare(graph)
    vision = open_vision_index(settings.vision_backend, weights=settings.yolo_weights)
    timeline = open_video_timeline(
        settings.video_backend,
        vision,
        Path(settings.vision_store_dir),
    )
    registry = build_vehicle_tools(
        build_seed_repository(),
        regulations,
        graph,
        vision,
        timeline,
        settings=settings,
        workorders=MemoryWorkOrderStore(),
    )
    anyio.run(_serve, build_mcp_server(registry))


def _definition(tool: RegisteredTool) -> types.Tool:
    return types.Tool(
        name=tool.name,
        description=tool.description,
        input_schema=tool.input_model.model_json_schema(),
        annotations=types.ToolAnnotations(
            read_only_hint=tool.readonly,
            idempotent_hint=True,
        ),
    )


def _caller_role(params: types.CallToolRequestParams) -> str | None:
    meta = params.meta or {}
    role = meta.get("role")
    if isinstance(role, str):
        return role
    return None


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=message)],
        is_error=True,
    )


def _prepare(index: object) -> None:
    ensure = getattr(index, "ensure_ready", None)
    if callable(ensure):
        ensure()


@asynccontextmanager
async def memory_streams(registry: ToolRegistry) -> AsyncIterator[object]:
    server = build_mcp_server(registry)
    from mcp.client.session import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams

    async with create_client_server_memory_streams() as (client_streams, server_streams):
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(
                server.run,
                server_streams[0],
                server_streams[1],
                server.create_initialization_options(),
            )
            async with ClientSession(client_streams[0], client_streams[1]) as session:
                await session.initialize()
                try:
                    yield session
                finally:
                    task_group.cancel_scope.cancel()


async def _serve(server: Server) -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
