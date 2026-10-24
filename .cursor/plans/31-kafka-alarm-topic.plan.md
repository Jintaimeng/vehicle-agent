---
name: 31 kafka alarm topic
overview: 阶段 31 只把第 12.2 节那条疲劳报警编成 JSON，发到 Kafka 主题 vehicle.alarm，再交回现有的内存去重。默认测试用假发送器，不改研判。
todos:
  - id: alarm-payload
    content: 把 fatigue_alarm_event 编成第 12.2 节 JSON，经传入的 send 发往 vehicle.alarm，再解析回 MemoryAlarmTopic 按 event_id 去重
    status: completed
  - id: kafka-docs
    content: Compose 增加 KRaft Kafka，安装 confluent-kafka，补假发送器与可选实机测试，更新 README 和阶段 30 指向，测试通过后提交中文 commit
    status: completed
isProject: false
---

# 31 Kafka 报警主题

上一阶段是已完成的设备状态事件，见 [.cursor/plans/30-device-status-event.plan.md](.cursor/plans/30-device-status-event.plan.md)。本文件是阶段 31，落地到 [.cursor/plans/31-kafka-alarm-topic.plan.md](.cursor/plans/31-kafka-alarm-topic.plan.md)。确认本计划后，把正文写入该路径；若 Cursor 计划工具在用户全局 `~/.cursor/plans/` 留下副本，以仓库内文件为准并删除全局副本。下一阶段是 [.cursor/plans/32-kafka-device-status.plan.md](.cursor/plans/32-kafka-device-status.plan.md)。再往后才让 `vehicle.alarm` 触发研判。延续 [docs/vehicle-risk-agent-implementation-plan.md](docs/vehicle-risk-agent-implementation-plan.md) 第 12.1 节的 `vehicle.alarm` 和第 12.2 节的报警事件。驾驶员汇总、企业汇总、路线偏离、独立的超速报警类型、手机使用研判、`device.status` 的经纪人投递、`vehicle.location`、`media.ready`、`judgement.requested`、`judgement.completed`、`workorder.created` 和前端仍不纳入。

本阶段只证明一件事：`fatigue_alarm_event()` 可以编成第 12.2 节的 JSON，发到名为 `vehicle.alarm` 的主题，再解析回来交给现有 [MemoryAlarmTopic.deliver](src/vehicle_agent/ingest.py)。同一个 `event_id` 投递两次，收件箱仍是一条，车速仍是 `82.5`。幂等留在现有收件箱，不使用 Kafka 事务。模型不参与。不写入定样本仓储，不调用研判图。`risk_score`、复核条件、是否建单和 `prompt_version`（`report-v6`）不变。不新增工具，不新增 HTTP 接口。工具数仍是十一个。`device.status` 仍只在内存中。默认测试不连接经纪人。

## 流程

```mermaid
flowchart LR
  sample[定样本报警] --> payload[第12.2节JSON]
  payload --> topic[vehicle.alarm]
  topic --> parse[解析回AlarmEvent]
  parse --> inbox[MemoryAlarmTopic]
```

疲劳驾驶研判图仍是 `validate → context → history → retrieve → graph → vision → score → report/review`。应用启动不连接 Kafka。

## 载荷

放在 [src/vehicle_agent/ingest.py](src/vehicle_agent/ingest.py)。`alarm_event_payload(event)` 返回 UTF-8 JSON 字节。字段与第 12.2 节一致，经纬度放进 `location`，不在顶层重复。`occurred_at` 写成 `2026-10-09T10:30:00Z`。`fatigue_alarm_event()` 的载荷解析后是：

- `event_id` 为 `evt_20261009_000001`，`alarm_id` 为 `alarm_000001`，`alarm_type` 为 `driver_fatigue`。
- 车、驾驶员、设备分别是 `vehicle_001`、`driver_001`、`device_001`。
- `location.longitude` 是 `113.2644`，`location.latitude` 是 `23.1291`，`speed_kmh` 是 `82.5`。
- `media_ids` 是 `["media_001"]`，`source_confidence` 是 `0.71`，`schema_version` 是 `1.0`。

`alarm_event_from_payload(payload)` 解析回 `AlarmEvent`。缺 `location`、非法 JSON、空 `event_id` 或版本不是 `1.0` 时抛出 `ValueError`，不调用 `deliver`。

`publish_vehicle_alarm(event, send)` 只调用一次 `send`。主题名是 `vehicle.alarm`，正文是上面的字节。`send` 由调用方传入。默认测试传入假发送器，记录主题和字节。解析这些字节后 `deliver` 两次，收件箱仍是 `alarm_000001`，车速仍是 `82.5`。

## 经纪人

在 [deployments/docker-compose.yml](deployments/docker-compose.yml) 增加单节点 KRaft 的 `kafka` 服务，宿主机端口 `9092`，对外公告 `127.0.0.1:9092`。副本因子相关项设为 1。`CLUSTER_ID` 使用 Kafka 文档中的 `MkU3OEVBNTcwNTJENDM2Qk`。不提交 `.env`。

在 [pyproject.toml](pyproject.toml) 增加 `confluent-kafka`，并用 `uv pip install` 装进 `D:\uv_venv`。实机发送和消费放在单独函数里，只在验收测试中 import。默认测试不 import 该客户端，也不打开套接字。

`tests/test_kafka_acceptance.py` 用 `VEHICLE_AGENT_RUN_KAFKA=1` 才运行，写法对齐 [tests/test_qdrant_acceptance.py](tests/test_qdrant_acceptance.py)。它把同一条定样本发布两次，从 `vehicle.alarm` 读回这两条，再交给 `MemoryAlarmTopic`。收件箱仍是一条，车速仍是 `82.5`。未设置该变量时整文件跳过。实现时若本机 Docker 可用，执行 `docker compose up -d kafka` 后跑这一条；不可用则保持跳过，并在交付说明里写明原因。

## 验收

- `fatigue_alarm_event()` 的 JSON 与上面的字段一致，`occurred_at` 以 `Z` 结尾，经纬度在 `location` 里。
- 假发送器只被调用一次，主题是 `vehicle.alarm`。解析后投递两次，收件箱只有 `alarm_000001`，车速仍是 `82.5`。
- 非法 JSON 或缺少 `location` 时抛出 `ValueError`，收件箱仍为空。
- 定样本仓储的报警条数不变。`device_status_event()` 的 `event_id` 仍是 `evt_20261009_000002`。阶段 08 至 30 的疲劳研判保持通过，`risk_score` 不变。高速无图研判的 trace 仍是原来四个查询工具。
- 默认测试不连接 Kafka。不新增工具。

更新 [README.md](README.md)：`vehicle.alarm` 可以编成第 12.2 节的 JSON 再交给现有收件箱；同一个 `event_id` 只留一条。默认测试用假发送器。实机经纪人只在 `VEHICLE_AGENT_RUN_KAFKA=1` 时连接 `127.0.0.1:9092`。研判图不消费它。删掉排除项里的「Kafka 经纪人」。`device.status` 仍只在内存中。`vehicle.location`、`media.ready`、`judgement.requested`、`judgement.completed`、`workorder.created`、驾驶员汇总、企业汇总、路线偏离和前端仍排除。把阶段 30 计划里的下一阶段指向本文件。

测试通过后按仓库规则提交一次中文 commit。阶段 31 的计划只保留仓库内的 `.cursor/plans/31-kafka-alarm-topic.plan.md`。
