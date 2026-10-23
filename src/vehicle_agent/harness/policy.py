from typing import Protocol

from vehicle_agent.harness.errors import PolicyDeniedError
from vehicle_agent.harness.tools import RegisteredTool


class ToolPolicy(Protocol):
    """Decides whether a tool call may run. Write approval can replace this later."""

    def allow(self, tool: RegisteredTool, arguments: object) -> None: ...


class AllowReadOnlyPolicy:
    def allow(self, tool: RegisteredTool, arguments: object) -> None:
        if not tool.readonly:
            raise PolicyDeniedError(f"write tool {tool.name} requires approval")


class AllowApprovedWritesPolicy:
    """Read tools stay open. Only work-order creation is an approved write."""

    def allow(self, tool: RegisteredTool, arguments: object) -> None:
        if tool.readonly or tool.name == "create_workorder":
            return
        raise PolicyDeniedError(f"write tool {tool.name} requires approval")
