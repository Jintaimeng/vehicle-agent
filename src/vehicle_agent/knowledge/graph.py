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


def describe_graph_fact(relation: GraphRelation, source_id: str, target_id: str) -> str:
    if relation == "drives":
        return f"车辆 {source_id} 由驾驶员 {target_id} 驾驶"
    if relation == "installed_with":
        return f"车辆 {source_id} 安装设备 {target_id}"
    if relation == "has_fault" and target_id == "camera_occlusion":
        return f"设备 {source_id} 存在未关闭的摄像头遮挡故障"
    raise ValueError(f"unsupported graph fact: {relation} {source_id}->{target_id}")


def graph_fact(relation: GraphRelation, source_id: str, target_id: str) -> GraphFact:
    return GraphFact(
        relation=relation,
        source_id=source_id,
        target_id=target_id,
        description=describe_graph_fact(relation, source_id, target_id),
    )


def seed_graph_facts() -> list[GraphFact]:
    return [
        graph_fact("drives", "vehicle_001", "driver_001"),
        graph_fact("installed_with", "vehicle_001", "device_001"),
        graph_fact("drives", "vehicle_002", "driver_002"),
        graph_fact("installed_with", "vehicle_002", "device_002"),
        graph_fact("drives", "vehicle_003", "driver_003"),
        graph_fact("installed_with", "vehicle_003", "device_003"),
        graph_fact("has_fault", "device_003", "camera_occlusion"),
    ]


def seed_graph_index() -> MemoryGraphIndex:
    return MemoryGraphIndex(seed_graph_facts())


# Fixed template. Callers only bind vehicle_id and device_id; the model never writes Cypher.
VEHICLE_GRAPH_QUERY = """
MATCH (vehicle:Vehicle {id: $vehicle_id})-[:drives]->(driver:Driver)
RETURN 'drives' AS relation, vehicle.id AS source_id, driver.id AS target_id
UNION ALL
MATCH (vehicle:Vehicle {id: $vehicle_id})-[:installed_with]->(device:Device)
RETURN 'installed_with' AS relation, vehicle.id AS source_id, device.id AS target_id
UNION ALL
MATCH (vehicle:Vehicle {id: $vehicle_id})-[:installed_with]->(device:Device {id: $device_id})
MATCH (device)-[:has_fault]->(fault:Fault)
RETURN 'has_fault' AS relation, device.id AS source_id, fault.id AS target_id
""".strip()

_RELATION_ORDER = {"drives": 0, "installed_with": 1, "has_fault": 2}

CONSTRAINT_STATEMENTS = (
    "CREATE CONSTRAINT vehicle_id_unique IF NOT EXISTS FOR (n:Vehicle) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT driver_id_unique IF NOT EXISTS FOR (n:Driver) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT device_id_unique IF NOT EXISTS FOR (n:Device) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT fault_id_unique IF NOT EXISTS FOR (n:Fault) REQUIRE n.id IS UNIQUE",
)

_SEED_STATEMENTS = {
    "drives": (
        "MERGE (source:Vehicle {id: $source_id}) "
        "MERGE (target:Driver {id: $target_id}) "
        "MERGE (source)-[:drives]->(target)"
    ),
    "installed_with": (
        "MERGE (source:Vehicle {id: $source_id}) "
        "MERGE (target:Device {id: $target_id}) "
        "MERGE (source)-[:installed_with]->(target)"
    ),
    "has_fault": (
        "MERGE (source:Device {id: $source_id}) "
        "MERGE (target:Fault {id: $target_id}) "
        "MERGE (source)-[:has_fault]->(target)"
    ),
}


def seed_statements() -> list[tuple[str, dict[str, str]]]:
    return [
        (
            _SEED_STATEMENTS[fact.relation],
            {"source_id": fact.source_id, "target_id": fact.target_id},
        )
        for fact in seed_graph_facts()
    ]


class Neo4jGraphIndex:
    """Neo4j-backed facts. The read uses one fixed Cypher template."""

    def __init__(self, uri: str, user: str, password: str, *, driver=None) -> None:
        self._uri = uri
        self._user = user
        self._password = password
        self._driver = driver
        self._owns_driver = driver is None
        self._ready = False

    def query(self, vehicle_id: str, device_id: str) -> list[GraphFact]:
        self.ensure_ready()
        parameters = {"vehicle_id": vehicle_id, "device_id": device_id}
        with self._driver.session() as session:
            result = session.run(VEHICLE_GRAPH_QUERY, parameters)
            rows = list(result)
        facts = [_fact_from_row(row) for row in rows]
        return sorted(facts, key=lambda item: (_RELATION_ORDER[item.relation], item.target_id))

    def ensure_ready(self) -> None:
        self._require_driver()
        if self._ready:
            return
        with self._driver.session() as session:
            for statement in CONSTRAINT_STATEMENTS:
                session.run(statement)
            for statement, parameters in seed_statements():
                session.run(statement, parameters)
        self._ready = True

    def close(self) -> None:
        if self._driver is not None and self._owns_driver:
            self._driver.close()
            self._driver = None
            self._ready = False

    def _require_driver(self):
        if self._driver is None:
            from neo4j import GraphDatabase

            self._driver = GraphDatabase.driver(
                self._uri,
                auth=(self._user, self._password),
                connection_timeout=5,
            )
        return self._driver


def open_graph_index(
    backend: Literal["memory", "neo4j"],
    *,
    uri: str,
    user: str,
    password: str,
) -> GraphIndex:
    if backend == "memory":
        return seed_graph_index()
    if backend == "neo4j":
        return Neo4jGraphIndex(uri, user, password)
    raise ValueError(f"unsupported graph backend: {backend}")


def _fact_from_row(row) -> GraphFact:
    relation = row["relation"]
    source_id = row["source_id"]
    target_id = row["target_id"]
    return graph_fact(relation, source_id, target_id)
