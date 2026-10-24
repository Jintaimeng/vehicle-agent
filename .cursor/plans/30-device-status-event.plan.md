---
name: 30 device status
overview: 阶段 30 在内存里增加 device.status：一条固定心跳按 event_id 只留一条设备状态。不连接 Kafka，不改设备诊断和疲劳研判。
todos:
  - id: status-event
    content: 在 ingest.py 增加 DeviceStatusEvent、device_status_event 和 MemoryDeviceStatusTopic：按 event_id 投递，重复不再生成设备状态，不写入定样本仓储
    status: completed
  - id: tests-docs
    content: 补定样本、重复投递和非法事件测试，更新 README 与阶段 29 的下一阶段指向，测试通过后提交中文 commit
    status: completed
isProject: false
---

# 30 设备状态事件

上一阶段是已完成的神经网络向量，见 [.cursor/plans/29-neural-embedding.plan.md](.cursor/plans/29-neural-embedding.plan.md)。本文件是阶段 30，落地到 [.cursor/plans/30-device-status-event.plan.md](.cursor/plans/30-device-status-event.plan.md)。确认本计划后，把正文写入该路径；若 Cursor 计划工具在用户全局 `~/.cursor/plans/` 留下副本，以仓库内文件为准并删除全局副本。下一阶段再写 `.cursor/plans/31-kafka-alarm-topic.plan.md`，本阶段不创建该文件。其后才是把 `device.status` 接到同一经纪人，再让 `vehicle.alarm` 触发研判。延续 [docs/vehicle-risk-agent-implementation-plan.md](docs/vehicle-risk-agent-implementation-plan.md) 第 12.1 节的 `device.status`。驾驶员汇总、企业汇总、路线偏离、独立的超速报警类型、手机使用研判、Kafka 经纪人、`vehicle.location`、`media.ready`、`judgement.requested`、`judgement.completed`、`workorder.created` 和前端仍不纳入。

本阶段只证明一件事：一条设备心跳可以编成事件，投递到内存中的 `device.status` 后变成一条 [Device](src/vehicle_agent/domain/models.py)；同一个 `event_id` 再投递一次，收件箱里仍然只有这一条。不启动 Kafka，不安装客户端。模型不参与。不写入 [src/vehicle_agent/repositories/memory.py](src/vehicle_agent/repositories/memory.py) 的定样本，因此 `get_device_status` 和 `device-diagnosis` 仍读原来的在线状态。`device_003` 仍是离线。`risk_score`、复核条件、是否建单和 `prompt_version`（`report-v6`）不变。不新增工具，不新增 HTTP 接口，不调用研判图，也不调用 `diagnose_device`。工具数仍是十一个。[MemoryAlarmTopic](src/vehicle_agent/ingest.py) 的行为不变。

## 流程

```mermaid
flowchart LR
  sample[定样本心跳] --> topic[device.status]
  topic --> inbox[收件箱]
  inbox --> once[同一event_id只留一条]
```

疲劳驾驶研判图仍是 `validate → context → history → retrieve → graph → vision → score → report/review`。

## 事件

放在 [src/vehicle_agent/ingest.py](src/vehicle_agent/ingest.py)。`DeviceStatusEvent` 的字段是 `event_id`、`device_id`、`online_status`、`last_heartbeat_at`、`schema_version`。`online_status` 只能是 `online` 或 `offline`。`schema_version` 只能是 `1.0`。`event_id` 去掉首尾空白后不能为空。

`device_status_event()` 返回这一条，不复用报警事件的 `event_id`：

- `event_id` 是 `evt_20261009_000002`，`device_id` 是 `device_001`。
- `online_status` 是 `online`，`last_heartbeat_at` 是 `2026-10-09T10:30:00Z`。
- `schema_version` 是 `1.0`。

`MemoryDeviceStatusTopic` 的主题名是 `device.status`。`deliver(event)` 把事件记入日志，并按 `event_id` 写入收件箱。第一次返回新的 `Device`：`id` 取 `device_id`，在线状态和心跳取事件上的字段。同一个 `event_id` 再次 `deliver` 时，即使后一次是 `offline`，也返回第一次的在线设备，收件箱长度仍是 1。另一个 `event_id` 再生成一条。

非法版本、空 `event_id` 或不是 `online`/`offline` 的状态在写入前抛出 `ValueError`，日志和收件箱都不变。

## 验收

- `device_status_event()` 的字段与上面这条定样本一致。
- 投递一次后收件箱只有 `device_001`，状态是 `online`，心跳是 `2026-10-09T10:30:00Z`。
- 同一 `event_id` 改成 `offline` 再投递一次，收件箱仍是这一条，状态仍是 `online`。
- 另一个 `event_id` 会增加第二条。空 `event_id`、`schema_version` 不是 `1.0` 时抛出 `ValueError`，收件箱仍为空。
- 投递之后，定样本仓储里的 `device_001` 仍是原来的对象，收件箱里的设备是另一条。该对象的心跳本来就是 `2026-10-09T10:30:00Z`，不能靠时间不同来判断未写入。`device_003` 仍是 `offline`。`vehicle.alarm` 的定样本投递结果不变。
- 阶段 08 至 29 的疲劳研判保持通过，`risk_score` 不变。高速无图研判的 trace 仍是原来四个查询工具。
- 不连接 Kafka。不新增工具。

更新 [README.md](README.md)：`device.status` 在内存中接收这条 `device_001` 心跳，同一个 `event_id` 只留下一条，且不写入定样本仓储。研判图和设备诊断都不消费它。排除项改为仍排除 Kafka 经纪人，以及 `vehicle.location`、`media.ready`、`judgement.requested`、`judgement.completed`、`workorder.created`。驾驶员汇总、企业汇总、路线偏离和前端仍排除。把阶段 29 计划里的下一阶段指向本文件。

测试通过后按仓库规则提交一次中文 commit。阶段 30 的计划只保留仓库内的 `.cursor/plans/30-device-status-event.plan.md`。
