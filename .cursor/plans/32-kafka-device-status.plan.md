---
name: 32 kafka device status
overview: 阶段 32 把现有的 device_001 心跳编成 JSON，发到 Kafka 主题 device.status，再交回内存去重。默认测试用假发送器，不改设备诊断和疲劳研判。
todos:
  - id: status-payload
    content: 在 ingest.py 增加 device_status_payload、device_status_from_payload 和 publish_device_status：假发送器发往 device.status，解析后仍由 MemoryDeviceStatusTopic 按 event_id 去重
    status: completed
  - id: status-docs
    content: 复用现有 Kafka 发布读回逻辑增加 exchange_device_status 和可选实机测试，更新 README 与阶段 31 指向，测试通过后提交中文 commit
    status: completed
isProject: false
---

# 32 Kafka 设备状态

上一阶段是已完成的 Kafka 报警主题，见 [.cursor/plans/31-kafka-alarm-topic.plan.md](.cursor/plans/31-kafka-alarm-topic.plan.md)。本文件是阶段 32，落地到 [.cursor/plans/32-kafka-device-status.plan.md](.cursor/plans/32-kafka-device-status.plan.md)。确认本计划后，把正文写入该路径；若 Cursor 计划工具在用户全局 `~/.cursor/plans/` 留下副本，以仓库内文件为准并删除全局副本。下一阶段再写 `.cursor/plans/33-alarm-event-judgement.plan.md`，本阶段不创建该文件。延续 [docs/vehicle-risk-agent-implementation-plan.md](docs/vehicle-risk-agent-implementation-plan.md) 第 12.1 节的 `device.status`。驾驶员汇总、企业汇总、路线偏离、独立的超速报警类型、手机使用研判、`vehicle.location`、`media.ready`、`judgement.requested`、`judgement.completed`、`workorder.created` 和前端仍不纳入。

本阶段只证明一件事：`device_status_event()` 可以编成 JSON，发到名为 `device.status` 的主题，再解析回来交给现有 [MemoryDeviceStatusTopic.deliver](src/vehicle_agent/ingest.py)。同一个 `event_id` 投递两次，收件箱仍是一条，状态仍是 `online`。幂等留在现有收件箱，不使用 Kafka 事务。模型不参与。不写入定样本仓储，不调用 `diagnose_device`，不调用研判图。`risk_score`、复核条件、是否建单和 `prompt_version`（`report-v6`）不变。不新增工具，不新增 HTTP 接口，不新增依赖，不改 Compose。工具数仍是十一个。`vehicle.alarm` 的 JSON 和主题名不变。默认测试不连接经纪人。

## 流程

```mermaid
flowchart LR
  sample[定样本心跳] --> payload[设备状态JSON]
  payload --> topic[device.status]
  topic --> parse[解析回DeviceStatusEvent]
  parse --> inbox[MemoryDeviceStatusTopic]
```

疲劳驾驶研判图仍是 `validate → context → history → retrieve → graph → vision → score → report/review`。应用启动不连接 Kafka。

## 载荷

放在 [src/vehicle_agent/ingest.py](src/vehicle_agent/ingest.py)。`device_status_payload(event)` 返回 UTF-8 JSON 字节。字段是 `event_id`、`device_id`、`online_status`、`last_heartbeat_at`、`schema_version`。`last_heartbeat_at` 写成 `2026-10-09T10:30:00Z`。`device_status_event()` 的载荷解析后是：

- `event_id` 为 `evt_20261009_000002`，`device_id` 为 `device_001`。
- `online_status` 为 `online`，`schema_version` 为 `1.0`。

`device_status_from_payload(payload)` 解析回 `DeviceStatusEvent`。非法 JSON、空 `event_id`、版本不是 `1.0`，或状态不是 `online`/`offline` 时抛出 `ValueError`，不调用 `deliver`。

`publish_device_status(event, send)` 只调用一次 `send`。主题名是 `device.status`，正文是上面的字节。`send` 由调用方传入。默认测试传入假发送器。解析这些字节后 `deliver` 两次，后一次即使改成 `offline`，收件箱仍是 `device_001`，状态仍是 `online`。定样本仓储里的 `device_001` 仍是原来的对象，收件箱里的设备是另一条。`device_003` 仍是 `offline`。

## 经纪人

实机发送和消费仍只在验收测试里 import `confluent_kafka`。把 [src/vehicle_agent/kafka_alarm.py](src/vehicle_agent/kafka_alarm.py) 里「先订阅、对准当前水位、再发布两次并读回这两条」收成按主题名调用的内部函数。`exchange_vehicle_alarm` 的对外行为不变：仍把同一条疲劳报警发到 `vehicle.alarm` 并读回两份相同字节。新增 `exchange_device_status`，对 `device.status` 做同样的两次发布。不新开端口，仍连接 `127.0.0.1:9092`。

[tests/test_kafka_acceptance.py](tests/test_kafka_acceptance.py) 仍由 `VEHICLE_AGENT_RUN_KAFKA=1` 决定是否运行。增加一条：把 `device_status_event()` 发布两次，读回后交给 `MemoryDeviceStatusTopic`，收件箱仍是一条且状态为 `online`。未设置该变量时整文件跳过。实现时若本机 Docker 可用，沿用已有的 `docker compose up -d kafka` 跑这两条；不可用则保持跳过，并在交付说明里写明原因。

## 验收

- `device_status_event()` 的 JSON 与上面的字段一致，`last_heartbeat_at` 以 `Z` 结尾。
- 假发送器只被调用一次，主题是 `device.status`。解析后投递两次，收件箱只有 `device_001`，状态仍是 `online`。
- 非法 JSON 或状态不是 `online`/`offline` 时抛出 `ValueError`，收件箱仍为空。
- `fatigue_alarm_event()` 的载荷仍含 `location`，车速仍是 `82.5`，主题名仍是 `vehicle.alarm`。定样本仓储的报警条数不变。阶段 08 至 31 的疲劳研判保持通过，`risk_score` 不变。高速无图研判的 trace 仍是原来四个查询工具。
- 默认测试不连接 Kafka。不新增工具。

更新 [README.md](README.md)：`device.status` 可以编成这条心跳的 JSON 再交给现有收件箱；同一个 `event_id` 只留一条。默认测试用假发送器。实机与 `vehicle.alarm` 共用 `VEHICLE_AGENT_RUN_KAFKA=1` 和 `127.0.0.1:9092`。研判图和设备诊断都不消费它。`vehicle.location`、`media.ready`、`judgement.requested`、`judgement.completed`、`workorder.created`、驾驶员汇总、企业汇总、路线偏离和前端仍排除。把阶段 31 计划里的下一阶段指向本文件。

测试通过后按仓库规则提交一次中文 commit。阶段 32 的计划只保留仓库内的 `.cursor/plans/32-kafka-device-status.plan.md`。
