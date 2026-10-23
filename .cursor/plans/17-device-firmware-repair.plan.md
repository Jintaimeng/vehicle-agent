---
name: device firmware repair
overview: 阶段 17 为定样本设备补上固件版本和最近一次维修记录，设备诊断把它们写入证据。原因、疲劳研判风险分和建单规则不变。
todos:
  - id: maintenance-seed
    content: 增加 RepairRecord 与定样本：device_003 有一条更晚的未关闭遮挡维修，其余设备只有固件版本
    status: completed
  - id: maintenance-tool
    content: 注册只读 get_device_maintenance，诊断写入证据但不改 offline、camera_occlusion 或 no_device_fault
    status: completed
  - id: tests-docs
    content: 补维修与 MCP 契约测试，更新 README，测试通过后提交中文 commit
    status: completed
isProject: false
---

# 17 设备固件与维修记录

上一阶段是已完成的设备诊断，见 [.cursor/plans/16-device-diagnosis-skill.plan.md](.cursor/plans/16-device-diagnosis-skill.plan.md)。本文件是阶段 17，落地到 [.cursor/plans/17-device-firmware-repair.plan.md](.cursor/plans/17-device-firmware-repair.plan.md)。下一阶段再写 `.cursor/plans/18-frequent-device-alarms.plan.md`，本阶段不创建该文件。延续 [docs/vehicle-risk-agent-implementation-plan.md](docs/vehicle-risk-agent-implementation-plan.md) 第 4.3 节和第 11.1 节里尚未落地的固件版本与维修记录。频繁报警、视频缺失、算法漂移、风险画像、根因分析、运营报告、Kafka 和前端仍不纳入。

本阶段只证明一件事：给定设备标识，可以读出固件版本和最近一次维修记录。诊断把这两项写入证据，原因仍只由在线状态和遮挡故障决定。模型不选择步骤，也不改原因。疲劳驾驶研判图不调用这次读取。`risk_score`、复核条件、是否建单和 `prompt_version`（`report-v6`）不变。`alarm-verification` 仍是原来六个只读工具。`Device` 仍只有标识、在线状态和心跳，`get_device_status` 的出参不变。

## 流程

```mermaid
flowchart LR
  caller[直接调用] --> skill[device-diagnosis]
  skill --> status[get_device_status]
  skill --> graph[query_vehicle_graph]
  skill --> maint[get_device_maintenance]
  status --> rule[代码判定原因]
  graph --> rule
  maint --> evidence[写入证据]
```

疲劳驾驶研判图仍是 `validate → context → history → retrieve → graph → vision → score → report/review`。不新增节点，不新增 HTTP 接口。

## 定样本

维修记录放在内存仓储，不写入图谱，避免现有车辆查询多出关系。时间基准仍是 `2026-10-09T10:30:00Z`。

- `device_001`、`device_002`：固件 `1.4.0`，没有维修记录。
- `device_003`：固件 `1.2.0`。两条遮挡维修：`repair_003_closed` 已关闭，开启于 10 月 1 日、关闭于 10 月 2 日；`repair_003_open` 未关闭，开启于 10 月 6 日。最近一次是未关闭的这一条。
- `device_004`：固件 `1.3.0`，没有维修记录。

未知设备沿用现有工具错误。另留 `device_001` 可在测试里标成维修读取失败，种子默认不失败。

## 工具与诊断

在 [src/vehicle_agent/domain/models.py](src/vehicle_agent/domain/models.py) 增加 `RepairRecord`：`id`、`device_id`、`fault`（只允许 `camera_occlusion`）、`status`（`open` 或 `closed`）、`opened_at`、`closed_at`。关闭的记录必须有 `closed_at`，未关闭的记录没有。

新增只读工具 `get_device_maintenance`，注册在 [src/vehicle_agent/harness/tools.py](src/vehicle_agent/harness/tools.py)。入参 `device_id`。出参是固件版本和至多一条维修记录。多条时按 `opened_at` 取最晚的一条。没有记录时维修字段为空，不返回空列表。角色仍是 `monitor`，`readonly=True`。MCP 继续从 `build_vehicle_tools` 带上这个工具。

[src/vehicle_agent/skills/device_diagnosis.py](src/vehicle_agent/skills/device_diagnosis.py) 的工具列表改为 `get_device_status`、`query_vehicle_graph`、`get_device_maintenance`。编译核对这三项已注册且只读，并且 `get_device_status` 与 `get_device_maintenance` 都不在 `alarm-verification` 里。

状态和图谱都成功时才读取维修。原因规则不变：离线是 `offline`，在线且有遮挡是 `camera_occlusion`，在线且没有遮挡是 `no_device_fault`。证据在原有心跳和遮挡事实之后追加固件版本；有维修时再追加最近一条的状态和故障，没有则写明无维修记录。证据类型仍用现有 `context`，来源为 `maintenance`，不给研判证据增加新类型。

维修工具抛错时，诊断状态保持 `completed`，原因保持按前两项算出的值，另记 `maintenance_status=degraded`，不编造固件或维修。读取成功且有记录为 `hit`，成功但没有记录为 `miss`。状态或图谱失败时仍是整次 `degraded`、不猜测原因，并且不调用维修工具。

## 验收

- `device_003` 原因仍是 `offline`。证据含固件 `1.2.0` 和未关闭的 `repair_003_open`，不含已关闭的那条。
- `device_004` 原因仍是 `camera_occlusion`，证据含固件 `1.3.0` 和无维修记录。
- `device_001` 原因仍是 `no_device_fault`，证据含固件 `1.4.0` 和无维修记录。把这台标成维修读取失败时，原因不变，`maintenance_status=degraded`。
- 未知设备仍是工具错误。`device_error` 仍在状态读取时整次降级，且不调用维修工具。
- 阶段 08 至 16 的疲劳研判保持通过，`risk_score` 不变。高速无图研判的 trace 仍是四个查询工具，不含 `get_device_status` 和 `get_device_maintenance`。
- [tests/test_mcp.py](tests/test_mcp.py) 的 `TOOL_NAMES` 加上 `get_device_maintenance`。该工具为只读、可重试，本地 handler 与 MCP 对 `device_003` 的返回相同。
- 不调用模型。工具数变为十一个。

更新 [README.md](README.md)：十一个工具；设备状态和维修读取都是只读。`device-diagnosis` 会把固件和最近一次维修写入证据，但不因此改原因。删掉「本阶段不包含固件库、维修记录」。频繁报警、视频缺失、其余 Skill、Kafka 和前端仍排除。阶段 16 已经把下一阶段指向本文件，核对这句话仍在即可。

测试通过后按仓库规则提交一次中文 commit。阶段 17 的计划只保留本文件。
