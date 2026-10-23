---
name: missing video diagnosis
overview: 阶段 19 在现有设备诊断里，用同一次 1 日报警历史判断视频是否持续缺失。离线、遮挡和频繁报警仍然优先，疲劳研判的风险分不变。
todos:
  - id: missing-seed
    content: 增加 device_006 的 1 日内 2 次缺视频报警，以及 device_007 的 1 日内一次有视频、一次缺视频
    status: completed
  - id: missing-rule
    content: 诊断仍用 list_recent_alarms：离线、遮挡、频繁报警优先，其余情况下 1 日内至少 2 次且全部缺少 video_id 时原因为 missing_video
    status: completed
  - id: tests-docs
    content: 补视频缺失与失败路径测试，更新 README 和阶段 18 的下一阶段指向，测试通过后提交中文 commit
    status: completed
isProject: false
---

# 19 设备视频缺失

上一阶段是已完成的频繁报警，见 [.cursor/plans/18-frequent-device-alarms.plan.md](.cursor/plans/18-frequent-device-alarms.plan.md)。本文件是阶段 19，落地到 [.cursor/plans/19-missing-video.plan.md](.cursor/plans/19-missing-video.plan.md)。下一阶段再写 `.cursor/plans/20-algorithm-drift.plan.md`，本阶段不创建该文件。延续 [docs/vehicle-risk-agent-implementation-plan.md](docs/vehicle-risk-agent-implementation-plan.md) 第 4.3 节里的视频持续缺失。算法漂移、风险画像、根因分析、运营报告、Kafka 和前端仍不纳入。

本阶段只证明一件事：给定设备标识，代码可以从已返回的报警里数出 1 日内缺少 `video_id` 的次数，并在离线、遮挡和频繁报警都不成立时，把持续缺失写成原因 `missing_video`。统计仍复用只读工具 `list_recent_alarms`，不改它的入参，不调用 `analyze_video`，也不检查媒体文件是否存在。有 `image_id` 仍算缺少视频。模型不选择步骤，也不改原因。疲劳驾驶研判图不因此多调工具。`risk_score`、复核条件、是否建单和 `prompt_version`（`report-v6`）不变。`alarm-verification` 仍是原来六个只读工具。工具数仍是十一个。

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

诊断调用 `list_recent_alarms` 的方式与阶段 18 相同：`as_of` 固定为 `2026-10-09T10:30:00Z`，`within_days=1`。返回后只保留 `device_id` 与当前设备相同的报警。`video_id` 为空即缺少视频。

持续缺失：1 日内至少 2 次报警，且这几次全部缺少视频。只缺 1 次，或其中任何一次带有 `video_id`，都不是持续缺失。窗口外的报警不计入。

原因优先级固定：

- 设备离线：`offline`。次数和缺视频次数只写入证据。
- 设备在线且图谱有 `camera_occlusion`：`camera_occlusion`。即使 1 日内 3 次报警都没有视频，原因仍是遮挡。
- 设备在线、没有遮挡、1 日内至少 3 次：`frequent_alarms`。即使这 3 次都没有视频，原因仍是频繁报警。
- 设备在线、没有遮挡、不足 3 次，但至少 2 次且全部缺少视频：`missing_video`。
- 其余：`no_device_fault`。

[src/vehicle_agent/skills/device_diagnosis.py](src/vehicle_agent/skills/device_diagnosis.py) 的 `DiagnosisCause` 增加 `missing_video`。同一次历史读取同时得到报警次数和缺视频次数。新增 `video_status`：达到持续缺失为 `hit`，否则为 `miss`；历史读取失败时与 `alarm_status` 一样为 `degraded`。历史失败且原因尚未确定时，整次仍是 `degraded`，不调用维修工具。

证据在现有「1 日内有 N 次报警」之外，再写一条 `history` / `alarm_history`：1 日内有多少次报警缺少视频。

## 定样本

不改 `vehicle_001`、`vehicle_002` 的报警，避免疲劳研判的历史次数和 `risk_score` 变化。`device_003`、`device_004`、`device_005` 的报警继续不填 `video_id`。

- `device_005` 原因仍是 `frequent_alarms`。证据里能看到 1 日内 3 次报警都缺少视频，`video_status=hit`。
- `device_004` 原因仍是 `camera_occlusion`。证据里能看到 1 日内 3 次报警都缺少视频。
- `device_001` 的 1 日内次数仍是 0，原因仍是 `no_device_fault`，`video_status=miss`。
- 新增 `vehicle_006` 与 `device_006`。设备在线，心跳是样本时间，固件 `1.4.0`，没有维修记录。图谱只有 `installed_with`，没有遮挡故障。报警 3 条：2 条在样本时间前 30 分钟内且没有 `video_id`，1 条在 2 天前并带有 `video_id`。计入的是前 2 条，原因是 `missing_video`。
- 新增 `vehicle_007` 与 `device_007`。同样在线、无遮挡、固件 `1.4.0`、无维修。1 日内 2 条报警，只有 1 条缺少 `video_id`。原因是 `no_device_fault`，`video_status=miss`。

这些报警不进入 `vehicle_001` 或 `vehicle_002` 的查询结果。图谱种子加在 [src/vehicle_agent/knowledge/graph.py](src/vehicle_agent/knowledge/graph.py)，报警和设备加在 [src/vehicle_agent/repositories/memory.py](src/vehicle_agent/repositories/memory.py)。

## 验收

- `device_006` 原因为 `missing_video`，1 日内报警次数是 2，缺少视频的次数是 2，`alarm_status=miss`，`video_status=hit`。
- `device_007` 原因为 `no_device_fault`，缺少视频的次数是 1。
- `device_005` 原因为 `frequent_alarms`，缺少视频的次数是 3。
- `device_004` 原因为 `camera_occlusion`，证据里能看到缺少视频。
- `device_001` 原因为 `no_device_fault`，缺少视频的次数是 0。
- `device_006` 的报警历史抛错时整次 `degraded`，不调用 `get_device_maintenance`。同一失败下 `device_003` 原因仍是 `offline`，`video_status=degraded`。
- 阶段 08 至 18 的疲劳研判保持通过，`risk_score` 不变。高速无图研判的 trace 仍是原来四个查询工具，不含 `get_device_status` 和 `get_device_maintenance`。
- 不调用模型。不新增工具。不调用视频分析。

更新 [README.md](README.md)：`device-diagnosis` 在离线、遮挡和频繁报警之后，把 1 日内至少 2 次且全部缺少 `video_id` 判为 `missing_video`。删掉「本阶段不包含视频缺失」。算法漂移、其余 Skill、Kafka 和前端仍排除。把阶段 18 计划里的下一阶段指向本文件。

测试通过后按仓库规则提交一次中文 commit。阶段 19 的计划只保留本文件。Cursor 计划工具若把副本写到用户全局 `~/.cursor/plans/`，生成后立刻以本文件为准，并删除全局副本。
