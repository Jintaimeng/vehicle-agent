from typing import Literal, Protocol

from pydantic import BaseModel

GraphRelation = Literal["drives", "installed_with", "has_fault"]


class GraphFact(BaseModel):
    relation: GraphRelation
    source_id: str
    target_id: str
    description: str


class GraphIndex(Protocol):
    def query(self, vehicle_id: str, device_id: str) -> list[GraphFact]: ...


class MemoryGraphIndex:
    """Fixed in-memory relations. Queries walk at most two hops from the vehicle."""

    def __init__(self, facts: list[GraphFact]) -> None:
        self._facts = facts

    def query(self, vehicle_id: str, device_id: str) -> list[GraphFact]:
        direct = [
            fact
            for fact in self._facts
            if fact.source_id == vehicle_id and fact.relation in {"drives", "installed_with"}
        ]
        installed = {
            fact.target_id
            for fact in direct
            if fact.relation == "installed_with" and fact.target_id == device_id
        }
        faults = [
            fact
            for fact in self._facts
            if fact.relation == "has_fault" and fact.source_id in installed
        ]
        return [*direct, *faults]


def seed_graph_index() -> MemoryGraphIndex:
    return MemoryGraphIndex(
        [
            GraphFact(
                relation="drives",
                source_id="vehicle_001",
                target_id="driver_001",
                description="车辆 vehicle_001 由驾驶员 driver_001 驾驶",
            ),
            GraphFact(
                relation="installed_with",
                source_id="vehicle_001",
                target_id="device_001",
                description="车辆 vehicle_001 安装设备 device_001",
            ),
            GraphFact(
                relation="drives",
                source_id="vehicle_002",
                target_id="driver_002",
                description="车辆 vehicle_002 由驾驶员 driver_002 驾驶",
            ),
            GraphFact(
                relation="installed_with",
                source_id="vehicle_002",
                target_id="device_002",
                description="车辆 vehicle_002 安装设备 device_002",
            ),
            GraphFact(
                relation="drives",
                source_id="vehicle_003",
                target_id="driver_003",
                description="车辆 vehicle_003 由驾驶员 driver_003 驾驶",
            ),
            GraphFact(
                relation="installed_with",
                source_id="vehicle_003",
                target_id="device_003",
                description="车辆 vehicle_003 安装设备 device_003",
            ),
            GraphFact(
                relation="has_fault",
                source_id="device_003",
                target_id="camera_occlusion",
                description="设备 device_003 存在未关闭的摄像头遮挡故障",
            ),
        ]
    )
