---
name: 27 alarm event
overview: 阶段 27 把文档第 12.2 节的一条疲劳报警编成 vehicle.alarm 事件，在内存里按 event_id 投递；重复的 event_id 不再生成第二条报警。不连接 Kafka，不改疲劳研判。
todos:
  - id: alarm-event
    content: 在 ingest.py 增加 AlarmEvent、fatigue_alarm_event 和 MemoryAlarmTopic：按 event_id 投递，重复不再生成报警，不写入定样本仓储
    status: completed
  - id: tests-docs
    content: 补定样本、重复投递和非法事件测试，更新 README 与阶段 26 的下一阶段指向，测试通过后提交中文 commit
    status: completed
isProject: false
---

# 27 报警事件

上一阶段是已完成的 RTSP 抽帧，见 [.cursor/plans/26-rtsp-stream.plan.md](.cursor/plans/26-rtsp-stream.plan.md)。本文件是阶段 27，落地到 [.cursor/plans/27-vehicle-alarm-event.plan.md](.cursor/plans/27-vehicle-alarm-event.plan.md)。确认本计划后，把正文写入该路径；若 Cursor 计划工具在用户全局 `~/.cursor/plans/` 留下副本，以仓库内文件为准并删除全局副本。下一阶段是 [.cursor/plans/28-video-source.plan.md](.cursor/plans/28-video-source.plan.md)。延续 [docs/vehicle-risk-agent-implementation-plan.md](docs/vehicle-risk-agent-implementation-plan.md) 第 12.1 节的 `vehicle.alarm` 和第 12.2 节的报警事件。驾驶员汇总、企业汇总、路线偏离、独立的超速报警类型、神经网络 Embedding、其余主题、Kafka 经纪人、JT1078 和前端仍不纳入。

本阶段只证明一件事：第 12.2 节那条疲劳报警可以编成一个事件，投递到内存中的 `vehicle.alarm` 后变成一条报警；同一个 `event_id` 再投递一次，收件箱里仍然只有这一条。不启动 Kafka，不安装客户端。模型不参与。不把事件写入 [src/vehicle_agent/repositories/memory.py](src/vehicle_agent/repositories/memory.py) 的定样本，因此日报告仍是 19 对 4、周报告仍是 24 对 0。`risk_score`、复核条件、是否建单和 `prompt_version`（`report-v6`）不变。不新增工具，不新增 HTTP 接口，不调用研判图。工具数仍是十一个。

## 流程

```mermaid
flowchart LR
  sample[定样本事件] --> topic[vehicle.alarm]
  topic --> inbox[收件箱]
  inbox --> once[同一event_id只留一条]
```

疲劳驾驶研判图仍是 `validate → context → history → retrieve → graph → vision → score → report/review`。

## 事件

新模块放在 [src/vehicle_agent/ingest.py](src/vehicle_agent/ingest.py)。`AlarmEvent` 字段与第 12.2 节一致：`event_id`、`alarm_id`、`alarm_type`、`vehicle_id`、`driver_id`、`device_id`、`occurred_at`、`longitude`、`latitude`、`speed_kmh`、`media_ids`、`source_confidence`、`schema_version`。`alarm_type` 只能是 `driver_fatigue`。`schema_version` 只能是 `1.0`。`event_id` 去掉首尾空白后不能为空。

`fatigue_alarm_event()` 返回文档中的那一条：

- `event_id` 是 `evt_20261009_000001`，`alarm_id` 是 `alarm_000001`。
- 车、驾驶员、设备分别是 `vehicle_001`、`driver_001`、`device_001`。
- `occurred_at` 是 `2026-10-09T10:30:00Z`。经度 `113.2644`，纬度 `23.1291`，车速 `82.5`。
- `media_ids` 是 `["media_001"]`，`source_confidence` 是 `0.71`，`schema_version` 是 `1.0`。

`MemoryAlarmTopic` 的主题名是 `vehicle.alarm`。`deliver(event)` 把事件记入日志，并按 `event_id` 写入收件箱。第一次返回新的 [Alarm](src/vehicle_agent/domain/models.py)：`id` 取 `alarm_id`，`alarm_time` 取 `occurred_at`，经纬度、车速和 `raw_confidence` 取对应字段，`source` 是 `device`，`status` 是 `open`。`media_ids` 留在事件上，不写入 `image_id` 或 `video_id`，两者都是空。同一个 `event_id` 再次 `deliver` 时，即使后一次的车速不同，也返回第一次的报警，收件箱长度仍是 1。另一个 `event_id` 再生成一条。

非法版本、空 `event_id` 或非疲劳类型在写入前抛出 `ValueError`，日志和收件箱都不变。

## 验收

- `fatigue_alarm_event()` 的字段与上面这条定样本一致。
- 投递一次后收件箱只有 `alarm_000001`，车速是 `82.5`，置信度是 `0.71`，`image_id` 和 `video_id` 都是空。
- 同一 `event_id` 用不同车速再投递一次，收件箱仍是这一条，车速仍是 `82.5`。
- 另一个 `event_id` 会增加第二条。空 `event_id`、`schema_version` 不是 `1.0` 时抛出 `ValueError`，收件箱仍为空。
- 定样本仓储的报警条数不变。阶段 08 至 26 的疲劳研判保持通过，`risk_score` 不变。高速无图研判的 trace 仍是原来四个查询工具。
- 不连接 Kafka。不新增工具。

更新 [README.md](README.md)：`vehicle.alarm` 在内存中接收第 12.2 节那条疲劳报警，同一个 `event_id` 只留下一条，且不写入定样本仓储。研判图不消费它。删掉排除项里的「Kafka」，改成仍排除 Kafka 经纪人及其余主题。驾驶员汇总、企业汇总、路线偏离、神经网络 Embedding 和前端仍排除。把阶段 26 计划里的下一阶段指向本文件。

测试通过后按仓库规则提交一次中文 commit。阶段 27 的计划只保留仓库内的 `.cursor/plans/27-vehicle-alarm-event.plan.md`。
