---
name: algorithm drift diagnosis
overview: 阶段 20 在现有设备诊断里，用同一次 1 日报警历史判断带视频的报警是否全部低置信度。离线、遮挡、频繁报警和视频缺失仍然优先，疲劳研判的风险分不变。
todos:
  - id: drift-seed
    content: 增加 device_008 的 2 次带视频低置信度报警、device_009 的 3 次同类报警，以及 device_010 的一次低置信度、一次 0.8
    status: completed
  - id: drift-rule
    content: 诊断仍用 list_recent_alarms：离线、遮挡、频繁报警和视频缺失优先，其余情况下 1 日内至少 2 次且全部为带视频、置信度低于 0.6 时原因为 algorithm_drift
    status: completed
  - id: tests-docs
    content: 补算法漂移与失败路径测试，更新 README 和阶段 19 的下一阶段指向，测试通过后提交中文 commit
    status: completed
isProject: false
---

# 20 设备算法漂移

上一阶段是已完成的视频缺失，见 [.cursor/plans/19-missing-video.plan.md](.cursor/plans/19-missing-video.plan.md)。本文件是阶段 20，落地到 [.cursor/plans/20-algorithm-drift.plan.md](.cursor/plans/20-algorithm-drift.plan.md)。下一阶段是 [.cursor/plans/21-vehicle-risk-profile.plan.md](.cursor/plans/21-vehicle-risk-profile.plan.md)。延续 [docs/vehicle-risk-agent-implementation-plan.md](docs/vehicle-risk-agent-implementation-plan.md) 第 4.3 节里尚未落地的算法漂移。风险画像、根因分析、运营报告、Kafka 和前端仍不纳入。

本阶段只证明一件事：给定设备标识，代码可以从已返回的报警里数出 1 日内带有 `video_id` 且 `raw_confidence` 低于 0.6 的次数，并在离线、遮挡、频繁报警和视频缺失都不成立时，把全部低置信度写成原因 `algorithm_drift`。0.6 与 [src/vehicle_agent/graph/scoring.py](src/vehicle_agent/graph/scoring.py) 里置信度加分的下界相同，等于 0.6 不算低。统计仍复用只读工具 `list_recent_alarms`，不改它的入参。固件和维修仍只写入证据，不参与定因。模型不选择步骤，也不改原因。疲劳驾驶研判图不因此多调工具。`risk_score`、复核条件、是否建单和 `prompt_version`（`report-v6`）不变。`alarm-verification` 仍是原来六个只读工具。工具数仍是十一个。

## 流程

```mermaid
flowchart LR
  caller[直接调用] --> skill[device-diagnosis]
  skill --> status[get_device_status]
  skill --> graph[query_vehicle_graph]
  skill --> alarms[list_recent_alarms]
  status --> rule[代码判定原因]
  graph --> rule
  alarms --> rule
  rule --> maint[get_device_maintenance]
```

疲劳驾驶研判图仍是 `validate → context → history → retrieve → graph → vision → score → report/review`。不新增节点，不新增 HTTP 接口。

## 计数

诊断调用 `list_recent_alarms` 的方式与阶段 19 相同：`as_of` 固定为 `2026-10-09T10:30:00Z`，`within_days=1`。返回后只保留 `device_id` 与当前设备相同的报警。低置信度指 `raw_confidence < 0.6`。没有 `video_id` 的报警不算低置信度，仍归视频缺失。

算法漂移：1 日内至少 2 次报警，且这几次全部带有 `video_id`、全部低于 0.6。只低 1 次，或其中任何一次没有视频、或置信度达到 0.6，都不是漂移。窗口外的报警不计入。

原因优先级固定：

- 设备离线：`offline`。低置信度次数只写入证据。
- 设备在线且图谱有 `camera_occlusion`：`camera_occlusion`。
- 设备在线、没有遮挡、1 日内至少 3 次：`frequent_alarms`。即使这 3 次都是带视频的低置信度报警，原因仍是频繁报警。
- 设备在线、没有遮挡、不足 3 次，但至少 2 次且全部缺少视频：`missing_video`。
- 设备在线、没有遮挡、不足 3 次、不是视频缺失，但至少 2 次且全部是带视频的低置信度报警：`algorithm_drift`。
- 其余：`no_device_fault`。

[src/vehicle_agent/skills/device_diagnosis.py](src/vehicle_agent/skills/device_diagnosis.py) 的 `DiagnosisCause` 增加 `algorithm_drift`。同一次历史读取同时得到报警次数、缺视频次数和低置信度次数。新增 `drift_status`：达到漂移为 `hit`，否则为 `miss`；历史读取失败时与 `alarm_status`、`video_status` 一样为 `degraded`。历史失败且原因尚未确定时，整次仍是 `degraded`，不调用维修工具。

证据在现有两条历史证据之外，再写一条 `history` / `alarm_history`：1 日内有多少次低置信度报警。

## 定样本

不改 `vehicle_001`、`vehicle_002` 的报警，也不改 `device_003` 至 `device_007` 的 `video_id` 和 `raw_confidence`，避免现有原因和疲劳研判的 `risk_score` 变化。这些设备的低置信度次数是 0。

- `device_005` 原因仍是 `frequent_alarms`，`drift_status=miss`。
- `device_006` 原因仍是 `missing_video`，`drift_status=miss`。
- `device_001` 原因仍是 `no_device_fault`，低置信度次数是 0。
- 新增 `vehicle_008` 与 `device_008`。设备在线，心跳是样本时间，固件 `1.4.0`，没有维修记录。图谱只有 `installed_with`。报警 3 条：2 条在样本时间前 30 分钟内，都有 `video_id`，置信度都是 0.4；1 条在 2 天前，置信度 0.9。计入的是前 2 条，原因是 `algorithm_drift`。
- 新增 `vehicle_009` 与 `device_009`。同样在线、无遮挡、固件 `1.4.0`、无维修。1 日内 3 条报警，都有 `video_id`，置信度都是 0.4。原因仍是 `frequent_alarms`，`drift_status=hit`。
- 新增 `vehicle_010` 与 `device_010`。同样在线、无遮挡、固件 `1.4.0`、无维修。1 日内 2 条报警都有 `video_id`，置信度分别是 0.4 和 0.8。原因是 `no_device_fault`，`drift_status=miss`。

这些报警不进入 `vehicle_001` 或 `vehicle_002` 的查询结果。图谱种子加在 [src/vehicle_agent/knowledge/graph.py](src/vehicle_agent/knowledge/graph.py)，报警和设备加在 [src/vehicle_agent/repositories/memory.py](src/vehicle_agent/repositories/memory.py)。

## 验收

- `device_008` 原因为 `algorithm_drift`，1 日内报警次数是 2，低置信度次数是 2，`alarm_status=miss`，`video_status=miss`，`drift_status=hit`。
- `device_009` 原因为 `frequent_alarms`，低置信度次数是 3，`drift_status=hit`。
- `device_010` 原因为 `no_device_fault`，低置信度次数是 1。
- `device_006` 原因为 `missing_video`，低置信度次数是 0。
- `device_005` 原因为 `frequent_alarms`，低置信度次数是 0。
- `device_001` 原因为 `no_device_fault`，低置信度次数是 0。
- `device_008` 的报警历史抛错时整次 `degraded`，不调用 `get_device_maintenance`。同一失败下 `device_003` 原因仍是 `offline`，`drift_status=degraded`。
- 阶段 08 至 19 的疲劳研判保持通过，`risk_score` 不变。高速无图研判的 trace 仍是原来四个查询工具，不含 `get_device_status` 和 `get_device_maintenance`。
- 不调用模型。不新增工具。固件版本不改变原因。

更新 [README.md](README.md)：`device-diagnosis` 在离线、遮挡、频繁报警和视频缺失之后，把 1 日内至少 2 次且全部为带视频的低置信度报警判为 `algorithm_drift`。删掉「本阶段不包含算法漂移」。风险画像、其余 Skill、Kafka 和前端仍排除。把阶段 19 计划里的下一阶段指向本文件。

测试通过后按仓库规则提交一次中文 commit。阶段 20 的计划只保留本文件。Cursor 计划工具若把副本写到用户全局 `~/.cursor/plans/`，生成后立刻以本文件为准，并删除全局副本。
