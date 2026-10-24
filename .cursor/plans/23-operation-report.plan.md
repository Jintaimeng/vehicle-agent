---
name: operation report
overview: 阶段 23 先补上 Skill 库最后一项运营报告：按日或周汇总全部疲劳报警、与上一窗口比较趋势，并用定样本工单写出处置效果。检测可视化、目标跟踪和 RTSP 留到这之后。疲劳研判的风险分不变，不新增工具。
todos:
  - id: report-seed
    content: 增加按时间窗读报警和列出工单；定样本工单与研判用的空仓储分开
    status: completed
  - id: report-rule
    content: 新增 operation-report：日周窗口比较趋势，未关闭工单优先于已关闭；失败则降级且不猜测处置效果
    status: completed
  - id: tests-docs
    content: 补报告与失败路径测试，更新 README 和阶段 22 的下一阶段指向，测试通过后提交中文 commit
    status: completed
isProject: false
---

# 23 运营报告

上一阶段是已完成的报警根因分析，见 [.cursor/plans/22-alarm-root-cause.plan.md](.cursor/plans/22-alarm-root-cause.plan.md)。本文件是阶段 23，落地到 [.cursor/plans/23-operation-report.plan.md](.cursor/plans/23-operation-report.plan.md)。下一阶段是 [.cursor/plans/24-detection-visualization.plan.md](.cursor/plans/24-detection-visualization.plan.md)。目标跟踪和 RTSP 排在检测可视化之后，本阶段也不创建那两个计划。延续 [docs/vehicle-risk-agent-implementation-plan.md](docs/vehicle-risk-agent-implementation-plan.md) 第 14.2 节的 `operation-report`。驾驶员汇总、企业汇总、路线偏离、独立的超速报警类型、检测可视化、目标跟踪、RTSP、Kafka 和前端仍不纳入。

本阶段只证明一件事：给定 `day` 或 `week`，代码可以数出固定时间窗里的全部疲劳报警，和上一同等窗口比较出一个趋势，再按这些报警关联的工单写出一个处置效果和一句建议。`as_of` 固定为 `2026-10-09T10:30:00Z`。日窗口是 1 日，周窗口是 7 日。上一窗口紧挨在本窗口之前，长度相同。模型不选择步骤，也不改趋势、处置效果或建议。不调用研判图，不调用 `diagnose_device`、`profile_vehicle` 或 `trace_alarm_cause`。`risk_score`、复核条件、是否建单和 `prompt_version`（`report-v6`）不变。不新增工具，也不调用 `create_workorder` 或 `update_workorder`。工具数仍是十一个。`alarm-verification` 仍是六个只读工具。

## 流程

```mermaid
flowchart LR
  caller[直接调用] --> skill[operation-report]
  skill --> alarms[仓库按时间窗读报警]
  alarms --> orders[工单仓储列出定样本]
  orders --> decide[代码写趋势和处置效果]
```

疲劳驾驶研判图仍是 `validate → context → history → retrieve → graph → vision → score → report/review`。不新增节点，不新增 HTTP 接口。报警按时间窗从仓库读取，不把 `list_recent_alarms` 按车调用一遍。工单从现有 [src/vehicle_agent/workorders.py](src/vehicle_agent/workorders.py) 的 `MemoryWorkOrderStore` 列出，不新增工单工具。

## 判定

本窗口是 `alarm_time` 不早于 `as_of` 减去窗口长度，且不晚于 `as_of`。上一窗口是再往前一个同样长度，不含本窗口起点。趋势只比较两个次数：本窗口更多是 `rising`，更少是 `falling`，相等是 `flat`。两次都是 0 也是 `flat`。

处置只统计报警落在本窗口里的工单。未出现在本窗口报警中的工单不计数。效果优先级固定：

- 至少一张 `open`：`pending`。已关闭张数仍写入证据。
- 否则至少一张 `closed`：`improved`。
- 其余：`no_disposition`。

建议由代码按效果写成一句，模型不生成：

- `pending`：先关闭未完成工单。
- `improved`：保持已关闭工单的处置。
- `no_disposition`：本窗口没有工单。

报警读取失败时整次 `degraded`，趋势、效果和建议为空，不读工单。工单列出失败时同样 `degraded`，不根据报警猜测效果，但保留已经读到的报警次数和趋势。

证据用现有类型。报警证据来源 `alarm_history`，写明本窗口次数、上一窗口次数，以及趋势是上升、下降还是持平。工单成功时再写一条 `context`：已关闭多少张、未关闭多少张。

## 定样本

不改 `vehicle_001` 到 `vehicle_012` 的报警、车速、维修和图谱。现有报警已经能算出：

- 日：本窗口 19 次，上一窗口 4 次，趋势 `rising`。
- 周：本窗口 24 次，上一窗口 0 次，趋势 `rising`。

研判默认使用的空工单仓储不变。报告单独使用 `seed_report_workorders()`，只放两张高风险工单：`alarm_011_1` 已关闭，`alarm_hist_001` 未关闭。`alarm_011_1` 落在日和周里，`alarm_hist_001` 只落在周里。因此日效果是 `improved`，周效果是 `pending`。

下降、持平和无工单不改这批报警。测试另建只含所需报警的仓库，并传入空工单仓储或会抛错的仓储。

## 实现

[src/vehicle_agent/repositories/base.py](src/vehicle_agent/repositories/base.py) 增加 `list_alarms_between`。[src/vehicle_agent/repositories/memory.py](src/vehicle_agent/repositories/memory.py) 返回该时间范围内的报警。`MemoryWorkOrderStore` 增加只读的 `list_workorders`。`seed_report_workorders()` 放在 [src/vehicle_agent/workorders.py](src/vehicle_agent/workorders.py)。

新 Skill 放在 [src/vehicle_agent/skills/operation_report.py](src/vehicle_agent/skills/operation_report.py)，由 [src/vehicle_agent/skills/__init__.py](src/vehicle_agent/skills/__init__.py) 导出。`skill_id` 为 `operation-report`。声明的工具列表为空。直接调用 `build_operation_report`，入参是周期、仓库和工单仓储。一次报告不调用 Harness 工具。编译时在 [src/vehicle_agent/graph/builder.py](src/vehicle_agent/graph/builder.py) 核对注册工具仍是原来十一个：`get_vehicle_context`、`list_recent_alarms`、`get_device_status`、`get_device_maintenance`、`search_regulations`、`query_vehicle_graph`、`analyze_image`、`analyze_video`、`create_workorder`、`get_workorder`、`update_workorder`。并核对 `alarm-verification` 的工具列表不变。

## 验收

- 日报告本窗口 19 次，上一窗口 4 次，趋势 `rising`，效果 `improved`，建议是保持已关闭工单的处置。
- 周报告本窗口 24 次，上一窗口 0 次，趋势 `rising`，效果 `pending`，证据里能看到未关闭工单，建议是先关闭未完成工单。
- 本窗口少于上一窗口时趋势 `falling`。两次相等时趋势 `flat`。没有关联工单时效果 `no_disposition`。
- 报警读取抛错时整次 `degraded`，不调用 `list_workorders`。工单列出抛错时效果为空，报警次数和趋势仍在。
- 阶段 08 至 22 的疲劳研判保持通过，`risk_score` 不变。研判用的工单仓储仍从空仓储开始。高速无图研判的 trace 仍是原来四个查询工具。
- 不调用模型。不新增工具。不创建、不更新工单。

更新 [README.md](README.md)：`operation-report` 按日或周汇总全部疲劳报警，和上一窗口比较趋势，并用关联工单写处置效果。日是 19 对 4、趋势上升、已关闭工单。周是 24 对 0、趋势上升、有未关闭工单。删掉排除项里的「运营报告」。检测可视化、目标跟踪、RTSP、驾驶员汇总、企业汇总、路线偏离、Kafka 和前端仍排除。把阶段 22 计划里的下一阶段指向本文件。

测试通过后按仓库规则提交一次中文 commit。阶段 23 的计划只保留本文件。Cursor 计划工具若把副本写到用户全局 `~/.cursor/plans/`，生成后立刻以本文件为准，并删除全局副本。
