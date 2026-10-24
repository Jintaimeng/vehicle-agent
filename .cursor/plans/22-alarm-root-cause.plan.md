---
name: alarm root cause
overview: 阶段 22 新增按报警追踪的根因 Skill：用现有图谱、法规检索和 7 日同类报警定一个原因和一句建议。疲劳研判的风险分不变，不新增工具。
todos:
  - id: alarm-lookup
    content: 仓库增加 get_alarm；用现有 alarm_004_1、alarm_hist_001、alarm_011_1 作为定样本，不改报警和图谱
    status: completed
  - id: root-cause-rule
    content: 新增 alarm-root-cause-analysis：图谱遮挡优先于 7 日同类历史，再优先于法规命中；失败则降级且不猜测根因
    status: completed
  - id: tests-docs
    content: 补根因与失败路径测试，更新 README 和阶段 21 的下一阶段指向，测试通过后提交中文 commit
    status: completed
isProject: false
---

# 22 报警根因分析

上一阶段是已完成的车辆风险画像，见 [.cursor/plans/21-vehicle-risk-profile.plan.md](.cursor/plans/21-vehicle-risk-profile.plan.md)。本文件是阶段 22，落地到 [.cursor/plans/22-alarm-root-cause.plan.md](.cursor/plans/22-alarm-root-cause.plan.md)。下一阶段是 [.cursor/plans/23-operation-report.plan.md](.cursor/plans/23-operation-report.plan.md)。延续 [docs/vehicle-risk-agent-implementation-plan.md](docs/vehicle-risk-agent-implementation-plan.md) 第 14.2 节的 `alarm-root-cause-analysis`。驾驶员汇总、企业汇总、路线偏离、独立的超速报警类型、运营报告、Kafka 和前端仍不纳入。本阶段不把报警、规则或案例写成新的图谱关系，也不改 [src/vehicle_agent/knowledge/graph.py](src/vehicle_agent/knowledge/graph.py) 的 `query`。`query_vehicle_graph` 多返回事实会改变疲劳研判证据。

本阶段只证明一件事：给定报警标识，代码可以沿该报警的车辆和设备读出图谱故障，再读一条法规摘录和 7 日内其余同类报警，写出一个根因和一句建议。历史案例就是这 7 日内同一辆车、同一报警类型、标识不同的报警，不另建案例库。报警类型仍只有 `driver_fatigue`。模型不选择步骤，也不改根因或建议。不调用 `diagnose_device` 或 `profile_vehicle`。疲劳驾驶研判图不调用这份 Skill。`risk_score`、复核条件、是否建单和 `prompt_version`（`report-v6`）不变。`alarm-verification` 仍是六个只读工具。工具数仍是十一个。

## 流程

```mermaid
flowchart LR
  caller[直接调用] --> skill[alarm-root-cause-analysis]
  skill --> graph[query_vehicle_graph]
  graph --> rules[search_regulations]
  rules --> history[list_recent_alarms]
  history --> decide[代码写根因]
```

疲劳驾驶研判图仍是 `validate → context → history → retrieve → graph → vision → score → report/review`。不新增节点，不新增 HTTP 接口。报警标识从仓库读取，不新增 `get_alarm` 工具。

## 判定

`as_of` 固定为 `2026-10-09T10:30:00Z`，`within_days=7`。仓库找不到报警时抛出 `KeyError`，不调用工具。找到后用报警上的 `vehicle_id` 和 `device_id` 调用 `query_vehicle_graph`，再用 `alarm_type` 调用 `search_regulations`，最后用 `list_recent_alarms` 取 7 日报警。同类历史次数只计标识不同的报警。

根因优先级固定：

- 图谱含 `has_fault` 且目标是 `camera_occlusion`：`camera_occlusion`。历史次数和法规摘录仍写入证据。
- 否则同类历史至少 1 次：`repeated_driver_fatigue`。
- 否则法规摘录至少 1 条：`driver_fatigue`。
- 其余：`unexplained`。

建议由代码按根因写成一句，模型不生成：

- `camera_occlusion`：先核对镜头遮挡，再决定是否按疲劳处置。
- `repeated_driver_fatigue`：对照近期同类报警复核。
- `driver_fatigue`：按疲劳驾驶规则处置。
- `unexplained`：补充法规或图谱证据后再判断。

图谱读取失败时整次 `degraded`，根因和建议为空，不调用法规和历史工具。法规读取失败时同样 `degraded`，不根据图谱猜测根因，但保留已经读到的图谱证据。历史读取失败时同样 `degraded`，不猜测根因，保留图谱和法规证据。

证据用现有类型。图谱证据来源 `vehicle_graph`：有遮挡故障，或无摄像头遮挡故障。法规成功时写一条 `document`，带第一条摘录的标题和章节。历史成功时写一条 `history`，写明 7 日内其余同类报警次数。

## 定样本

不改 `vehicle_001` 到 `vehicle_012` 的报警、车速、维修和图谱，避免疲劳研判、设备诊断和风险画像变化。

- `alarm_004_1`：`vehicle_004` 已有遮挡故障，7 日内另有同类报警。根因 `camera_occlusion`。
- `alarm_hist_001`：`vehicle_001` 无遮挡，7 日内另有 `alarm_hist_002`。根因 `repeated_driver_fatigue`。
- `alarm_011_1`：`vehicle_011` 无遮挡，7 日内没有其余同类报警，法规检索能命中疲劳摘录。根因 `driver_fatigue`。
- `unexplained` 不新增报警。测试给 `alarm_011_1` 换一个返回空摘录的法规索引。

## 实现

仓库协议 [src/vehicle_agent/repositories/base.py](src/vehicle_agent/repositories/base.py) 增加 `get_alarm`。[src/vehicle_agent/repositories/memory.py](src/vehicle_agent/repositories/memory.py) 按标识查找，找不到返回 `None`。

新 Skill 放在 [src/vehicle_agent/skills/alarm_root_cause.py](src/vehicle_agent/skills/alarm_root_cause.py)，由 [src/vehicle_agent/skills/__init__.py](src/vehicle_agent/skills/__init__.py) 导出。`skill_id` 为 `alarm-root-cause-analysis`。声明的工具只有 `query_vehicle_graph`、`search_regulations` 和 `list_recent_alarms`，三步都只读。编译时核对这三步的顺序，并核对没有新增工具进入 `alarm-verification`。直接调用 `trace_alarm_cause`，不进入研判图。一次追踪只调用这三个工具。测试不要在同一个 Harness 上连续追踪超过 2 条报警，默认工具调用上限是 8。

## 验收

- `alarm_004_1` 根因 `camera_occlusion`，证据里能看到遮挡故障，建议是先核对镜头遮挡。
- `alarm_hist_001` 根因 `repeated_driver_fatigue`，其余同类次数是 1。
- `alarm_011_1` 根因 `driver_fatigue`，证据里有法规摘录。
- 空法规索引下 `alarm_011_1` 根因 `unexplained`。
- 图谱抛错时整次 `degraded`，不调用 `search_regulations`。法规抛错或历史抛错时根因为空，已读证据保留。
- 未知报警抛错。
- 阶段 08 至 21 的疲劳研判保持通过，`risk_score` 不变。高速无图研判的 trace 仍是原来四个查询工具。
- 不调用模型。不新增工具。

更新 [README.md](README.md)：`alarm-root-cause-analysis` 按报警标识读取图谱、法规和 7 日同类报警，代码写根因和建议。遮挡优先于重复报警，重复报警优先于单次法规命中。删掉「其余 Skill」这句过宽的排除。驾驶员汇总、企业汇总、路线偏离、运营报告、Kafka 和前端仍排除。把阶段 21 计划里的下一阶段指向本文件。

测试通过后按仓库规则提交一次中文 commit。阶段 22 的计划只保留本文件。Cursor 计划工具若把副本写到用户全局 `~/.cursor/plans/`，生成后立刻以本文件为准，并删除全局副本。
