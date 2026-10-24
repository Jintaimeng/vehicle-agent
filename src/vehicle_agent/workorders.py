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


def seed_report_workorders() -> MemoryWorkOrderStore:
    """Fixed orders for the operation report. Judgement keeps its own empty store."""

    store = MemoryWorkOrderStore()
    store.create("report-closed", "alarm_011_1", "high", "定样本已关闭")
    store.update("wo-report-closed", "closed", "")
    store.create("report-open", "alarm_hist_001", "high", "定样本未关闭")
    return store
