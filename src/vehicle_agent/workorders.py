import sqlite3
import threading
from pathlib import Path

from pydantic import BaseModel, Field

from vehicle_agent.domain.models import RiskLevel


class CreateWorkOrderInput(BaseModel):
    task_id: str = Field(min_length=1)
    alarm_id: str = Field(min_length=1)
    risk_level: RiskLevel
    summary: str


class GetWorkOrderInput(BaseModel):
    workorder_id: str = Field(min_length=1)


class UpdateWorkOrderInput(BaseModel):
    workorder_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    comment: str = ""


class WorkOrder(BaseModel):
    workorder_id: str
    task_id: str
    alarm_id: str
    risk_level: RiskLevel
    status: str
    summary: str
    comment: str = ""


class MemoryWorkOrderStore:
    """Process-local work orders. One order per task id."""

    def __init__(self) -> None:
        self._items: dict[str, WorkOrder] = {}
        self._by_task: dict[str, str] = {}

    def create(self, task_id: str, alarm_id: str, risk_level: str, summary: str) -> WorkOrder:
        if risk_level != "high":
            raise ValueError(f"workorder requires high risk: {risk_level}")
        existing = self._by_task.get(task_id)
        if existing is not None:
            return self._items[existing]
        workorder_id = f"wo-{task_id}"
        order = WorkOrder(
            workorder_id=workorder_id,
            task_id=task_id,
            alarm_id=alarm_id,
            risk_level="high",
            status="open",
            summary=summary,
        )
        self._items[workorder_id] = order
        self._by_task[task_id] = workorder_id
        return order

    def get(self, workorder_id: str) -> WorkOrder:
        try:
            return self._items[workorder_id]
        except KeyError as exc:
            raise KeyError(f"workorder not found: {workorder_id}") from exc

    def update(self, workorder_id: str, status: str, comment: str) -> WorkOrder:
        current = self.get(workorder_id)
        updated = current.model_copy(update={"status": status, "comment": comment})
        self._items[workorder_id] = updated
        return updated

    def list_workorders(self) -> list[WorkOrder]:
        return list(self._items.values())

    def __len__(self) -> int:
        return len(self._items)


def workorder_sqlite_path(checkpoint_path: str | Path) -> Path:
    """Place work orders beside the checkpoint file, not inside its tables."""

    return Path(checkpoint_path).parent / "workorders.sqlite"


class SqliteWorkOrderStore:
    """Durable work orders. One order per task id, shared by HTTP and ingest."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS workorders (
                workorder_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL UNIQUE,
                alarm_id TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                status TEXT NOT NULL,
                summary TEXT NOT NULL,
                comment TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def create(self, task_id: str, alarm_id: str, risk_level: str, summary: str) -> WorkOrder:
        if risk_level != "high":
            raise ValueError(f"workorder requires high risk: {risk_level}")
        workorder_id = f"wo-{task_id}"
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM workorders WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is not None:
                return _order_from_row(row)
            self._connection.execute(
                """
                INSERT INTO workorders (
                    workorder_id, task_id, alarm_id, risk_level, status, summary, comment
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (workorder_id, task_id, alarm_id, "high", "open", summary, ""),
            )
            self._connection.commit()
        return WorkOrder(
            workorder_id=workorder_id,
            task_id=task_id,
            alarm_id=alarm_id,
            risk_level="high",
            status="open",
            summary=summary,
        )

    def get(self, workorder_id: str) -> WorkOrder:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM workorders WHERE workorder_id = ?",
                (workorder_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"workorder not found: {workorder_id}")
        return _order_from_row(row)

    def update(self, workorder_id: str, status: str, comment: str) -> WorkOrder:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM workorders WHERE workorder_id = ?",
                (workorder_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"workorder not found: {workorder_id}")
            self._connection.execute(
                "UPDATE workorders SET status = ?, comment = ? WHERE workorder_id = ?",
                (status, comment, workorder_id),
            )
            self._connection.commit()
            current = _order_from_row(row)
        return current.model_copy(update={"status": status, "comment": comment})

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def _order_from_row(row: sqlite3.Row) -> WorkOrder:
    return WorkOrder(
        workorder_id=row["workorder_id"],
        task_id=row["task_id"],
        alarm_id=row["alarm_id"],
        risk_level=row["risk_level"],
        status=row["status"],
        summary=row["summary"],
        comment=row["comment"],
    )


def seed_report_workorders() -> MemoryWorkOrderStore:
    """Fixed orders for the operation report. Judgement keeps its own empty store."""

    store = MemoryWorkOrderStore()
    store.create("report-closed", "alarm_011_1", "high", "定样本已关闭")
    store.update("wo-report-closed", "closed", "")
    store.create("report-open", "alarm_hist_001", "high", "定样本未关闭")
    return store
