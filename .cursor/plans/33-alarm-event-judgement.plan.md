---
name: 33 alarm event judgement
overview: 阶段 33 把 vehicle.alarm 的 JSON 解析成现有报警，交给现在的疲劳研判图。不新增接口，不连接 Kafka，风险分仍由原规则计算。
todos:
  - id: accept-event
    content: 在 ingest.py 增加 alarm_from_published_event，把 vehicle.alarm JSON 解析后交给 MemoryAlarmTopic，并返回那条报警
    status: completed
  - id: judge-docs
    content: 用现有 _run 验收 0.95 分、重复 event_id 和非法 JSON，更新 README 与阶段 32 指向，测试通过后提交中文 commit
    status: completed
isProject: false
---

# 33 报警事件研判

上一阶段是已完成的 Kafka 设备状态，见 [.cursor/plans/32-kafka-device-status.plan.md](.cursor/plans/32-kafka-device-status.plan.md)。本文件是阶段 33，落地到 [.cursor/plans/33-alarm-event-judgement.plan.md](.cursor/plans/33-alarm-event-judgement.plan.md)。确认本计划后，把正文写入该路径；若 Cursor 计划工具在用户全局 `~/.cursor/plans/` 留下副本，以仓库内文件为准并删除全局副本。下一阶段本阶段不创建。延续 [docs/vehicle-risk-agent-implementation-plan.md](docs/vehicle-risk-agent-implementation-plan.md) 第 12.2 节的报警事件和第 10.2 节里「根据规则触发 Agent 任务」。驾驶员汇总、企业汇总、路线偏离、独立的超速报警类型、手机使用研判、`device.status` 触发诊断、`vehicle.location`、`media.ready`、`judgement.requested`、`judgement.completed`、`workorder.created` 和前端仍不纳入。

本阶段只证明一件事：`fatigue_alarm_event()` 编成的 JSON 可以解析后进入现有疲劳研判图，并得到与直接提交同一条 [Alarm](src/vehicle_agent/domain/models.py) 相同的风险分。图仍是 `validate → context → history → retrieve → graph → vision → score → report/review`。模型只写总结，不改分数。不新增 HTTP 接口，不新增工具。工具数仍是十一个。默认测试不连接 Kafka。`device.status` 不触发研判，也不触发设备诊断。

## 流程

```mermaid
flowchart LR
  sample[定样本JSON] --> parse[解析并按event_id去重]
  parse --> graph[现有疲劳研判图]
  graph --> score[原有风险分]
```

`media_ids` 仍不写入 `image_id` 或 `video_id`，因此视觉节点跳过，不调用 `analyze_image` 或 `analyze_video`。

## 研判

在 [src/vehicle_agent/ingest.py](src/vehicle_agent/ingest.py) 增加 `alarm_from_published_event(payload, topic)`。它用现有 `alarm_event_from_payload` 解析，再交给传入的 [MemoryAlarmTopic.deliver](src/vehicle_agent/ingest.py)。未传入收件箱时使用一个新的空收件箱。返回的报警就是收件箱里的那一条。

测试用现有 [tests/test_graph.py](tests/test_graph.py) 的 `_run` 跑这张图，不另写评分。定样本是第 12.2 节那条：`vehicle_001`、车速 `82.5`、置信度 `0.71`、没有图片和视频。7 日内另有 `alarm_hist_001` 和 `alarm_hist_002`，所以这条的 `risk_score` 是 `0.95`，`risk_level` 是 `high`，状态是 `completed`，不进入人工复核。高风险且不复核时，报告节点仍按原规则调用 `create_workorder`。同一个 `event_id` 再用车速 `10` 的载荷投递一次，收件箱仍返回第一条，车速仍是 `82.5`，因此再跑一次研判分数仍是 `0.95`。

非法 JSON 在进入图之前抛出 `ValueError`。定样本仓储的报警条数不变。`prompt_version` 仍是 `report-v6`。

## 验收

- 假发送器发出的定样本 JSON 经 `alarm_from_published_event` 进入 `_run` 后，`risk_score` 是 `0.95`，`risk_level` 是 `high`，状态是 `completed`，`requires_human_review` 是假。
- 视觉证据为空。查询工具仍是 `get_vehicle_context`、`list_recent_alarms`、`search_regulations`、`query_vehicle_graph`，另外按原规则多一次 `create_workorder`。
- 同一 `event_id`、车速改成 `10` 的第二次载荷，收件箱车速仍是 `82.5`，再次研判的 `risk_score` 仍是 `0.95`。
- 非法 JSON 抛出 `ValueError`，研判函数不被调用。
- 定样本仓储的报警条数不变。阶段 08 至 32 的既有疲劳研判保持通过，其中高速无图样例的 `risk_score` 仍是原来的 `1.0`。`device_status_event()` 的主题仍是 `device.status`，本阶段不拿它跑图。
- 不连接 Kafka。不新增工具，不新增 HTTP 接口。

更新 [README.md](README.md)：`vehicle.alarm` 的 JSON 可以进入现有疲劳研判图。这条定样本的风险分是 `0.95`，没有图片和视频，所以不跑视觉。同一个 `event_id` 不改第一次的车速。研判图不读取 `device.status`。排除项不变。把阶段 32 计划里的下一阶段指向本文件。

测试通过后按仓库规则提交一次中文 commit。阶段 33 的计划只保留仓库内的 `.cursor/plans/33-alarm-event-judgement.plan.md`。
