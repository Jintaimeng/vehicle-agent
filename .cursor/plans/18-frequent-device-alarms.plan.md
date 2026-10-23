---
name: frequent device alarms
overview: 阶段 18 用现有报警历史判断一台在线且无遮挡的设备在 1 日内是否频繁报警。离线和遮挡仍优先，疲劳研判的风险分不变。
todos:
  - id: frequent-seed
    content: 增加 device_005 的 1 日内 3 次报警，并给已遮挡的 device_004 补上 3 次短时报警
    status: completed
  - id: frequent-rule
    content: 诊断复用 list_recent_alarms：离线和遮挡优先，在线无遮挡且 1 日内至少 3 次时原因为 frequent_alarms
    status: completed
  - id: tests-docs
    content: 补频繁报警与失败路径测试，更新 README，测试通过后提交中文 commit
    status: completed
isProject: false
---

# 18 设备频繁报警

上一阶段是已完成的固件与维修记录，见 [.cursor/plans/17-device-firmware-repair.plan.md](.cursor/plans/17-device-firmware-repair.plan.md)。本文件是阶段 18，落地到 [.cursor/plans/18-frequent-device-alarms.plan.md](.cursor/plans/18-frequent-device-alarms.plan.md)。下一阶段再写 `.cursor/plans/19-missing-video.plan.md`，本阶段不创建该文件。延续 [docs/vehicle-risk-agent-implementation-plan.md](docs/vehicle-risk-agent-implementation-plan.md) 第 4.3 节里的短时间大量报警。视频缺失、算法漂移、风险画像、根因分析、运营报告、Kafka 和前端仍不纳入。

本阶段只证明一件事：给定设备标识，代码可以数出固定时间窗里的报警次数，并在在线且没有遮挡时把达到阈值的结果当成原因 `frequent_alarms`。统计复用已有只读工具 `list_recent_alarms`，不改它的入参。模型不选择步骤，也不改原因。疲劳驾驶研判图不因此多调工具。`risk_score`、复核条件、是否建单和 `prompt_version`（`report-v6`）不变。`alarm-verification` 仍是原来六个只读工具。工具数仍是十一个。

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

诊断调用 `list_recent_alarms` 时，`as_of` 固定为 `2026-10-09T10:30:00Z`，`within_days=1`，不读系统时间。返回后只保留 `device_id` 与当前设备相同的报警。1 日内达到 3 次为频繁，更早的报警不计入。

原因优先级固定：

- 设备离线：`offline`。次数只写入证据。
- 设备在线且图谱有 `camera_occlusion`：`camera_occlusion`。即使 1 日内已有 3 次报警，原因仍是遮挡。
- 设备在线、没有遮挡、1 日内至少 3 次：`frequent_alarms`。
- 其余：`no_device_fault`。

`device_001` 原有两条报警分别在 2 天前和 5 天前，1 日内次数是 0，原因仍是 `no_device_fault`。

## 定样本

不改 `vehicle_001` 的报警，避免疲劳研判的历史次数变化。

- `device_004` 增加 3 条 1 日内的疲劳报警，挂在 `vehicle_004` 上。它已有遮挡故障，原因仍是 `camera_occlusion`。
- 新增 `vehicle_005` 与 `device_005`。设备在线，心跳仍是样本时间，固件 `1.4.0`，没有维修记录，图谱只有 `installed_with`，没有遮挡故障。报警 4 条：3 条在样本时间前 30 分钟内，1 条在 2 天前。计入的是前 3 条。

这些报警不进入 `vehicle_001` 的查询结果。

## 诊断

[src/vehicle_agent/skills/device_diagnosis.py](src/vehicle_agent/skills/device_diagnosis.py) 的工具列表改为 `get_device_status`、`query_vehicle_graph`、`list_recent_alarms`、`get_device_maintenance`。`list_recent_alarms` 本来就在 `alarm-verification` 里，两份 Skill 共用它。编译继续核对 `get_device_status` 和 `get_device_maintenance` 不在疲劳 Skill 里。

状态和图谱都成功后才统计。证据用现有类型 `history`，来源 `alarm_history`，写明 1 日内的次数。固件和维修仍在原因确定之后读取，规则与阶段 17 相同。

统计抛错时：

- 原因已经是离线或遮挡：诊断保持 `completed`，原因不变，`alarm_status=degraded`，不编造次数，维修读取仍继续。
- 原因还要靠次数决定：整次 `degraded`，`cause` 为空，不调用维修工具。

次数读到时，达到 3 次为 `alarm_status=hit`，不足为 `miss`。状态或图谱失败时仍不调用统计和维修。

## 验收

- `device_005` 原因为 `frequent_alarms`，证据中的次数是 3。
- `device_004` 原因为 `camera_occlusion`，证据里能看到 1 日内 3 次报警。
- `device_003` 原因为 `offline`，1 日内次数是 0。
- `device_001` 原因为 `no_device_fault`，1 日内次数是 0。
- `device_001` 的报警统计抛错时整次 `degraded`，不调用 `get_device_maintenance`。同一失败下 `device_003` 原因仍是 `offline`。
- 阶段 08 至 17 的疲劳研判保持通过，`risk_score` 不变。高速无图研判的 trace 仍是原来四个查询工具，不含 `get_device_status` 和 `get_device_maintenance`。
- 不调用模型。不新增工具。

更新 [README.md](README.md)：`device-diagnosis` 在在线且无遮挡时，把 1 日内至少 3 次报警判为 `frequent_alarms`；离线和遮挡优先。删掉「本阶段不包含频繁报警」。视频缺失、其余 Skill、Kafka 和前端仍排除。阶段 17 已经把下一阶段指向本文件，核对这句话仍在即可。

测试通过后按仓库规则提交一次中文 commit。阶段 18 的计划只保留本文件。
