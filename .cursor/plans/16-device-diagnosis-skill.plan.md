---
name: device diagnosis skill
overview: 阶段 16 用现有在线状态和图谱遮挡故障做一次确定性设备诊断，区分离线和摄像头遮挡。疲劳驾驶研判的风险分、工具和建单规则不变。
todos:
  - id: diagnosis-seed
    content: 增加 vehicle_004 / device_004：在线且有摄像头遮挡；图谱事实与 Neo4j 种子同源
    status: completed
  - id: diagnosis-skill
    content: 实现 device-diagnosis：只读工具加代码判定 offline、camera_occlusion 或 no_device_fault
    status: completed
  - id: tests-docs
    content: 补诊断与疲劳研判不变的测试，更新 README，测试通过后提交中文 commit
    status: completed
isProject: false
---

# 16 设备诊断 Skill

上一阶段是已完成的设备在线状态，见 [.cursor/plans/15-device-online-status.plan.md](.cursor/plans/15-device-online-status.plan.md)。本文件是阶段 16，落地到 [.cursor/plans/16-device-diagnosis-skill.plan.md](.cursor/plans/16-device-diagnosis-skill.plan.md)。下一阶段再写 `.cursor/plans/17-device-firmware-repair.plan.md`，本阶段不创建该文件。延续 [docs/vehicle-risk-agent-implementation-plan.md](docs/vehicle-risk-agent-implementation-plan.md) 第 4.3 节和第 14.2 节的 `device-diagnosis`：用设备状态和历史故障区分摄像头遮挡与设备离线。固件库、维修记录、频繁报警、视频缺失、算法漂移、风险画像、根因分析、运营报告、Kafka 和前端仍不纳入。

本阶段只证明一件事：给定设备标识，代码可以给出一个原因：`offline`、`camera_occlusion` 或 `no_device_fault`。读取只走已有的只读工具 `get_device_status` 和 `query_vehicle_graph`。模型不选择步骤，也不改原因。疲劳驾驶研判图不调用这次诊断。`risk_score`、复核条件、是否建单和 `prompt_version`（`report-v6`）不变。`alarm-verification` 仍是原来六个只读工具。

## 流程

```mermaid
flowchart LR
  caller[直接调用] --> skill[device-diagnosis]
  skill --> status[get_device_status]
  skill --> graph[query_vehicle_graph]
  status --> rule[代码判定原因]
  graph --> rule
```

疲劳驾驶研判图仍是 `validate → context → history → retrieve → graph → vision → score → report/review`。不新增节点，不注册新工具，不新增 HTTP 接口。

## 定样本

在现有三台车之外增加一对只给诊断用的样本，避免现有车辆查询多出故障：

- `vehicle_004` 安装 `device_004`。不增加驾驶员，不增加疲劳报警。
- `device_004`：`online`，心跳仍是 `2026-10-09T10:30:00Z`。
- 图谱增加 `installed_with vehicle_004 -> device_004` 和 `has_fault device_004 -> camera_occlusion`。`device_001`、`device_002` 仍无故障。`device_003` 仍是离线，且保留已有遮挡故障。

`vehicle_001` 配 `device_003` 的查询结果不变，仍然看不到这台设备的故障。Neo4j 种子从同一份 `seed_graph_facts()` 写出，不手写第二套事实。

## 判定

新增 [src/vehicle_agent/skills/device_diagnosis.py](src/vehicle_agent/skills/device_diagnosis.py)。定义字段与 `alarm-verification` 同一类：标识、输入、步骤、工具、输出、失败处理和评测案例。标识是 `device-diagnosis`。工具只有 `get_device_status` 和 `query_vehicle_graph`。编译疲劳图时继续只核对 `alarm-verification`；另加一次核对，确认这两项已注册且只读。`get_device_status` 不进入 `alarm-verification`。`query_vehicle_graph` 仍是疲劳研判原有工具，两份 Skill 共用它。

诊断函数入参只有 `device_id`。先按设备标识找到安装它的车辆，再调用上述两个工具。原因由代码决定，优先级固定：

- 设备离线：原因是 `offline`。`device_003` 同时有遮挡故障，证据里保留这条图谱事实，原因仍是离线。
- 设备在线且图谱有 `camera_occlusion`：原因是 `camera_occlusion`。`device_004` 走这条。
- 设备在线且没有遮挡故障：原因是 `no_device_fault`。`device_001` 走这条。

未知设备沿用现有工具错误。`device_error` 或图谱查询抛错时，诊断状态为 `degraded`，不猜测原因，也不把疲劳研判标为失败。

## 验收

- `device_003` 原因为 `offline`，证据同时有离线心跳和遮挡故障。
- `device_004` 原因为 `camera_occlusion`。
- `device_001` 原因为 `no_device_fault`。
- `device_error` 为 `degraded`。未知设备是工具错误。
- 阶段 08 至 15 的疲劳研判保持通过，`risk_score` 不变。高速无图研判的 trace 仍是四个查询工具，不含 `get_device_status`。`alarm-verification` 的工具列表仍是原来六个。
- 不调用模型。工具数仍是十个。

更新 [README.md](README.md)：`device-diagnosis` 只区分离线、遮挡和无设备故障；疲劳研判不调用它。固件、维修记录、其余 Skill、Kafka 和前端仍排除。阶段 15 已经把下一阶段指向本文件，核对这句话仍在即可。

测试通过后按仓库规则提交一次中文 commit。阶段 16 的计划只保留本文件。
