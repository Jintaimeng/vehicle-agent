---
name: 35 alarm ingest
overview: 阶段 35 让进程在打开开关后常驻消费 vehicle.alarm，同一 event_id 只进入现有疲劳研判图一次，定样本风险分仍是 0.95。lite 容器默认仍不连接 Kafka。
todos:
  - id: consume-batch
    content: 在 ingest.py 增加 consume_vehicle_alarms：解析 vehicle.alarm，同一 event_id 只调用一次 judge，非法 JSON 在进入研判前抛出 ValueError
    status: completed
  - id: process-loop
    content: Settings 增加 ingest 与 kafka_bootstrap；仅在 ingest=kafka 时由 API lifespan 守护线程消费 vehicle.alarm，以 event_id 为 task_id 调用现有研判图，检查点已有状态则跳过
    status: completed
  - id: ingest-docs
    content: 补默认测试与可选 Kafka 实机测试，更新 README 与阶段 34 指向；api 服务仍不连接 Kafka；测试通过后提交中文 commit
    status: completed
isProject: false
---

# 35 报警消费研判

上一阶段是已完成的 lite 容器，见 [.cursor/plans/34-lite-compose.plan.md](.cursor/plans/34-lite-compose.plan.md)。本文件是阶段 35，落地到 [.cursor/plans/35-kafka-alarm-ingest.plan.md](.cursor/plans/35-kafka-alarm-ingest.plan.md)。确认本计划后，把正文写入该路径；若 Cursor 计划工具在用户全局 `~/.cursor/plans/` 留下副本，以仓库内文件为准并删除全局副本。下一阶段是 [.cursor/plans/36-workorder-sqlite.plan.md](.cursor/plans/36-workorder-sqlite.plan.md)。延续 [docs/vehicle-risk-agent-implementation-plan.md](docs/vehicle-risk-agent-implementation-plan.md) 第 10.2 节「根据规则触发 Agent 任务」和第 12.1 节的 `vehicle.alarm`。工单落盘、在线模型切换、冒烟流水线、`device.status` 触发诊断、`vehicle.location`、`media.ready`、`judgement.requested`、`judgement.completed`、`workorder.created`、手机使用、独立超速类型、驾驶员汇总、企业汇总、路线偏离、Redis、MinIO、StarRocks、Kubernetes、CI 和前端仍不纳入。

本阶段只证明一件事：打开消费开关后，`fatigue_alarm_event()` 的 JSON 被进程收下，进入现有疲劳研判图，`risk_score` 是 `0.95`。同一个 `event_id` 再来一次，不再调用研判，车速仍是 `82.5`。图仍是 `validate → context → history → retrieve → graph → vision → score → report/review`。模型只写总结，不改分数。不新增工具，不新增 HTTP 路径。工具数仍是十一个。`prompt_version` 仍是 `report-v6`。`media_ids` 仍不写入 `image_id` 或 `video_id`，视觉节点跳过。默认测试不连接 Kafka。`device.status` 不读取、不触发诊断。

## 流程

```mermaid
flowchart LR
  topic[vehicle.alarm] --> parse[解析并按event_id去重]
  parse --> once[同一event_id只研判一次]
  once --> graph[现有疲劳研判图]
  graph --> lookup[GET该event_id]
```

消费触发时，`task_id` 就是 `event_id`。已有的 `GET /api/v1/judgements/{task_id}` 因此能查到这条结果。`POST /api/v1/judgements` 仍使用 `task_{uuid}`。

## 消费

在 [src/vehicle_agent/ingest.py](src/vehicle_agent/ingest.py) 增加 `consume_vehicle_alarms(payloads, judge, topic=None, judged=None)`。

- 每条载荷用现有 `alarm_event_from_payload` 解析，再交给传入的 [MemoryAlarmTopic.deliver](src/vehicle_agent/ingest.py)。未传入收件箱时使用一个新的空收件箱。
- 以 `event_id` 为键。第一次调用 `judge(alarm)`，并记下返回的 `task_id`。`judge` 由调用方传入，本函数不建图。
- 同一个 `event_id` 的后续载荷不再调用 `judge`，返回第一次的 `task_id`。收件箱里的车速仍是第一次的值。
- 非法 JSON 抛出 `ValueError`，该条不调用 `judge`。批处理函数不吞掉这个错误。
- 不读取 `device.status`。

进程侧在 [src/vehicle_agent/config.py](src/vehicle_agent/config.py) 增加：

- `ingest`：`off` 或 `kafka`，默认 `off`。环境变量是 `VEHICLE_AGENT_INGEST`。
- `kafka_bootstrap`：默认 `127.0.0.1:9092`。环境变量是 `VEHICLE_AGENT_KAFKA_BOOTSTRAP`。

[src/vehicle_agent/api/main.py](src/vehicle_agent/api/main.py) 只在 `ingest=kafka` 时，于现有 lifespan 里启动守护线程。线程只订阅 `vehicle.alarm`，`confluent_kafka` 仍放在线程内导入。每条消息交给 `consume_vehicle_alarms`。`judge` 复用 `POST /api/v1/judgements` 的 `graph.invoke`，`task_id` 使用 `event_id`。调用前若该 `thread_id` 的检查点已有状态，则不再 `invoke`。非法 JSON 由线程捕获后跳过，进程继续；处理完（含跳过和重复）再提交偏移。不使用 Kafka 事务。`ingest=off` 时 lifespan 与现在相同，不连接经纪人。检查点连接已是 `check_same_thread=False`，线程可以读写真有 SQLite 检查点。

[deployments/docker-compose.yml](deployments/docker-compose.yml) 的 `api` 服务不设置 `VEHICLE_AGENT_INGEST`，也不增加 `depends_on`。`docker compose up -d api` 仍只启动 fixture API。

## 验收

默认测试不构建镜像、不连接 Kafka：

- 假发送器把定样本发出两次，第二次只把车速改成 `10`。两条都交给 `consume_vehicle_alarms`，`judge` 内部用 [tests/test_graph.py](tests/test_graph.py) 的 `_run`。`judge` 只被调用一次。`risk_score` 是 `0.95`，`risk_level` 是 `high`，状态是 `completed`，`requires_human_review` 是假，`vision_status` 是 `skipped`。查询工具仍是 `get_vehicle_context`、`list_recent_alarms`、`search_regulations`、`query_vehicle_graph`，另外按原规则多一次 `create_workorder`。
- 返回的 `task_id` 是 `evt_20261009_000001`。用这个标识再取检查点，能读到同一次结果。
- 非法 JSON 抛出 `ValueError`，`judge` 不被调用。定样本仓储的报警条数不变。
- 默认 `ingest` 是 `off`，默认 `kafka_bootstrap` 是 `127.0.0.1:9092`。`GET /health` 仍只返回 `ok`。`device.status` 仍不进入该消费函数。
- 阶段 08 至 34 的既有疲劳研判保持通过。高速无图样例的 `risk_score` 仍是 `1.0`。

[tests/test_kafka_acceptance.py](tests/test_kafka_acceptance.py) 仍由 `VEHICLE_AGENT_RUN_KAFKA=1` 决定是否运行。增加一条：向 `127.0.0.1:9092` 的 `vehicle.alarm` 发布这条定样本两次，短时消费后交给 `consume_vehicle_alarms`，`judge` 只跑一次，风险分仍是 `0.95`。未设置该变量时整文件跳过。实现时若本机 Docker 可用，沿用已有的 `docker compose up -d kafka` 跑这一条；不可用则保持跳过，并在交付说明里写明原因。

更新 [README.md](README.md)：`VEHICLE_AGENT_INGEST=kafka` 时，进程常驻消费 `vehicle.alarm`，这条定样本的风险分是 `0.95`，`task_id` 是 `evt_20261009_000001`。同一个 `event_id` 不再研判第二次。默认是 `off`。lite 容器的 `api` 服务仍不连接 Kafka。`device.status` 仍不消费。排除项里删掉「研判图不读取」之外、已经由本阶段完成的消费缺口表述；工单落盘、在线模型、冒烟流水线、驾驶员汇总、企业汇总、路线偏离、`vehicle.location`、`media.ready`、`judgement.requested`、`judgement.completed`、`workorder.created`、Kubernetes 和前端仍排除。把阶段 34 计划里的下一阶段指向本文件。

测试通过后按仓库规则提交一次中文 commit。阶段 35 的计划只保留仓库内的 `.cursor/plans/35-kafka-alarm-ingest.plan.md`。
