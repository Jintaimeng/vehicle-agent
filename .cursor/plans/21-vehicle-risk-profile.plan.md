---
name: vehicle risk profile
overview: 阶段 21 新增车辆维度的风险画像：用 7 日疲劳报警和未关闭维修定一个来源、一个趋势和一句建议。疲劳研判的风险分不变，不新增工具。
todos:
  - id: profile-seed
    content: 增加 vehicle_011 的 1 次车速 70 报警，以及 vehicle_012 的 1 次车速 85 报警和未关闭维修
    status: completed
  - id: profile-rule
    content: 新增 vehicle-risk-profile：7 日报警分成近期和较早，未关闭维修优先于高速疲劳和至少 2 次疲劳
    status: completed
  - id: tests-docs
    content: 补画像与失败路径测试，更新 README 和阶段 20 的下一阶段指向，测试通过后提交中文 commit
    status: completed
isProject: false
---

# 21 车辆风险画像

上一阶段是已完成的算法漂移，见 [.cursor/plans/20-algorithm-drift.plan.md](.cursor/plans/20-algorithm-drift.plan.md)。本文件是阶段 21，落地到 [.cursor/plans/21-vehicle-risk-profile.plan.md](.cursor/plans/21-vehicle-risk-profile.plan.md)。下一阶段是 [.cursor/plans/22-alarm-root-cause.plan.md](.cursor/plans/22-alarm-root-cause.plan.md)。延续 [docs/vehicle-risk-agent-implementation-plan.md](docs/vehicle-risk-agent-implementation-plan.md) 第 4.4 节和第 14.2 节的 `vehicle-risk-profile`。本阶段只做单车画像。驾驶员汇总、企业汇总、路线偏离、独立的超速报警类型、根因分析、运营报告、Kafka 和前端仍不纳入。

本阶段只证明一件事：给定车辆标识，代码可以汇总固定时间窗里的疲劳报警和该车设备的未关闭维修，写出一个来源、一个趋势和一句建议。报警类型仍只有 `driver_fatigue`。车速不低于 80 的疲劳报警写成来源 `high_speed_fatigue`，不新增报警类型。80 与 [src/vehicle_agent/graph/scoring.py](src/vehicle_agent/graph/scoring.py) 的高速阈值相同。统计复用只读工具 `list_recent_alarms` 和 `get_device_maintenance`，不改它们的入参，也不调用 `diagnose_device`。设备标识从仓库里的车辆记录读取。模型不选择步骤，也不改来源、趋势或建议。疲劳驾驶研判图不调用这份 Skill。`risk_score`、复核条件、是否建单和 `prompt_version`（`report-v6`）不变。`alarm-verification` 仍是六个只读工具。工具数仍是十一个。

## 流程

```mermaid
flowchart LR
  caller[直接调用] --> skill[vehicle-risk-profile]
  skill --> alarms[list_recent_alarms]
  alarms --> maint[get_device_maintenance]
  maint --> rule[代码写来源和趋势]
```

疲劳驾驶研判图仍是 `validate → context → history → retrieve → graph → vision → score → report/review`。不新增节点，不新增 HTTP 接口。

## 汇总

`as_of` 固定为 `2026-10-09T10:30:00Z`，`within_days=7`。返回后只保留这辆车的报警。其中 `alarm_time` 不早于 `as_of` 减 1 日的计为近期，其余计入较早。趋势只比较这两个次数：近期更多是 `rising`，近期更少是 `falling`，相等是 `flat`。两次都是 0 也是 `flat`。

来源优先级固定：

- 最近一次维修是 `open`：`device_fault`。报警次数和趋势仍写入证据。
- 否则 7 日内有车速不低于 80 的报警：`high_speed_fatigue`。
- 否则 7 日内至少 2 次报警：`driver_fatigue`。
- 其余：`no_recent_risk`。

建议由代码按来源写成一句，模型不生成：

- `device_fault`：先处理未关闭的维修。
- `high_speed_fatigue`：复核高速疲劳报警。
- `driver_fatigue`：复核近期疲劳报警。
- `no_recent_risk`：保持例行监控。

报警历史失败时整次 `degraded`，来源、趋势和建议为空，不调用维修工具。维修读取失败时同样 `degraded`，不根据报警猜测来源，但保留已经读到的报警次数证据。未知车辆抛出 `KeyError`，不返回画像。

证据用现有类型。报警证据来源 `alarm_history`，写明 7 日内次数和其中 1 日内次数，并写明趋势是上升、下降还是持平。维修成功时再写一条 `context` / `maintenance`：有未关闭维修，或无未关闭维修。

## 定样本

不改 `vehicle_001` 到 `vehicle_010` 的报警、车速和维修，避免疲劳研判的历史次数、`risk_score` 和设备诊断原因变化。

- `vehicle_001`：7 日内 2 次，1 日内 0 次，趋势 `falling`。其中一次车速 81，来源 `high_speed_fatigue`。
- `vehicle_005`：7 日内 4 次，1 日内 3 次，车速都是 70，趋势 `rising`，来源 `driver_fatigue`。
- `vehicle_003`：7 日内 0 次，趋势 `flat`。设备有未关闭维修 `repair_003_open`，来源 `device_fault`。
- `vehicle_002`：7 日内 0 次，无维修，趋势 `flat`，来源 `no_recent_risk`。
- 新增 `vehicle_011` 与 `device_011`。在线，固件 `1.4.0`，无维修，图谱只有 `installed_with`。1 日内 1 次疲劳报警，车速 70。趋势 `rising`，来源 `no_recent_risk`。
- 新增 `vehicle_012` 与 `device_012`。同样在线，固件 `1.4.0`，图谱只有 `installed_with`。1 日内 1 次疲劳报警，车速 85，另有一条未关闭维修。来源仍是 `device_fault`。

这些新报警不进入 `vehicle_001` 或 `vehicle_002` 的查询结果。车辆、设备和报警加在 [src/vehicle_agent/repositories/memory.py](src/vehicle_agent/repositories/memory.py)，图谱加在 [src/vehicle_agent/knowledge/graph.py](src/vehicle_agent/knowledge/graph.py)。

## 实现

新 Skill 放在 [src/vehicle_agent/skills/vehicle_risk_profile.py](src/vehicle_agent/skills/vehicle_risk_profile.py)，由 [src/vehicle_agent/skills/__init__.py](src/vehicle_agent/skills/__init__.py) 导出。声明的工具只有 `list_recent_alarms` 和 `get_device_maintenance`，两步都只读。编译时核对 `get_device_maintenance` 仍不在 `alarm-verification` 里。直接调用 `profile_vehicle`，不进入研判图。一次画像只调用这两个工具。测试不要在同一个 Harness 上连续画超过 4 辆车，默认工具调用上限是 8。

## 验收

- `vehicle_001` 来源 `high_speed_fatigue`，趋势 `falling`，建议是复核高速疲劳报警。
- `vehicle_005` 来源 `driver_fatigue`，7 日内 4 次，1 日内 3 次，趋势 `rising`。
- `vehicle_003` 来源 `device_fault`，趋势 `flat`，证据里能看到未关闭维修。
- `vehicle_012` 来源 `device_fault`，即使有一次车速 85。
- `vehicle_011` 来源 `no_recent_risk`，趋势 `rising`。
- `vehicle_002` 来源 `no_recent_risk`，趋势 `flat`。
- `vehicle_002` 的报警历史抛错时整次 `degraded`，不调用 `get_device_maintenance`。维修读取抛错时来源为空。
- 未知车辆抛错。
- 阶段 08 至 20 的疲劳研判保持通过，`risk_score` 不变。高速无图研判的 trace 仍是原来四个查询工具。
- 不调用模型。不新增工具。

更新 [README.md](README.md)：`vehicle-risk-profile` 按单车汇总 7 日疲劳报警和未关闭维修，代码写来源、趋势和建议。删掉「本阶段不包含风险画像」。驾驶员汇总、企业汇总、路线偏离、根因分析、运营报告、Kafka 和前端仍排除。把阶段 20 计划里的下一阶段指向本文件。

测试通过后按仓库规则提交一次中文 commit。阶段 21 的计划只保留本文件。Cursor 计划工具若把副本写到用户全局 `~/.cursor/plans/`，生成后立刻以本文件为准，并删除全局副本。
