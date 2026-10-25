---
name: 37 smoke judgement
overview: 阶段 37 用同一次应用启动把健康检查、定样本研判和落盘工单串起来。风险分仍是 0.95，重启后工单仍是 open。不改研判、存储和 Compose。
todos:
  - id: smoke-test
    content: 新增 tests/test_smoke.py：同一次 fixture 应用先 GET /health，再经 handle_alarm_payload 研判定样本，风险分 0.95，重启后 wo-evt_20261009_000001 仍是 open
    status: completed
  - id: kafka-smoke
    content: Kafka 验收增加一条：实机发布定样本两次，同一次应用只研判一次，并写入同一张工单；未设置 VEHICLE_AGENT_RUN_KAFKA 时跳过
    status: completed
  - id: smoke-docs
    content: 更新 README 与阶段 36 指向；不改 Compose 和研判代码；测试通过后提交中文 commit
    status: completed
isProject: false
---

# 37 研判冒烟

上一阶段是已完成的工单落盘，见 [.cursor/plans/36-workorder-sqlite.plan.md](.cursor/plans/36-workorder-sqlite.plan.md)。本文件是阶段 37，落地到 [.cursor/plans/37-smoke-judgement.plan.md](.cursor/plans/37-smoke-judgement.plan.md)。确认本计划后，把正文写入该路径；若 Cursor 计划工具在用户全局 `~/.cursor/plans/` 留下副本，以仓库内文件为准并删除全局副本。下一阶段是 [.cursor/plans/38-kafka-compose-ingest.plan.md](.cursor/plans/38-kafka-compose-ingest.plan.md)。延续 [docs/vehicle-risk-agent-implementation-plan.md](docs/vehicle-risk-agent-implementation-plan.md) 第 28 节第 2 条和第 12 条：一条报警走完工作流，并且 Compose 仍能启动演示环境。在线模型切换、`device.status` 触发诊断、`vehicle.location`、`media.ready`、`judgement.requested`、`judgement.completed`、`workorder.created`、手机使用、独立超速类型、驾驶员汇总、企业汇总、路线偏离、Redis、MinIO、StarRocks、Kubernetes、CI 和前端仍不纳入。

本阶段只证明一件事：同一次 `create_app` 里，`GET /health` 返回 `ok` 之后，`fatigue_alarm_event()` 进入现有研判，`risk_score` 是 `0.95`，工单 `wo-evt_20261009_000001` 写在检查点旁边的 `workorders.sqlite`。关掉这次应用再打开同一对文件，工单状态仍是 `open`。不新增工具，不新增 HTTP 路径，不改分数和建单规则。工具数仍是十一个。`prompt_version` 仍是 `report-v6`。模型仍是 `fixture`，不调用在线模型。不改 [deployments/docker-compose.yml](deployments/docker-compose.yml)。`docker compose up -d api` 仍只启动 fixture API，不连接 Kafka。

## 流程

```mermaid
flowchart LR
  health[GET health] --> alarm[定样本vehicle.alarm]
  alarm --> score[风险分0.95]
  score --> order[workorders.sqlite]
  order --> restart[再次打开仍是open]
```

阶段 36 已经分别证明落盘。本阶段不改 [SqliteWorkOrderStore](src/vehicle_agent/workorders.py)，也不改研判图。冒烟只把现有的 `GET /health`、[handle_alarm_payload](src/vehicle_agent/api/main.py) 和工单查询放进同一次应用。

## 验收

默认测试放在 [tests/test_smoke.py](tests/test_smoke.py)，不构建镜像、不连接 Kafka：

- `create_app` 使用 `model_backend=fixture` 和临时目录里的检查点。`GET /health` 返回 `{"status":"ok"}`。
- 假发送器发出 `fatigue_alarm_event()` 的 JSON，交给现有 `handle_alarm_payload`。`task_id` 是 `evt_20261009_000001`。`GET /api/v1/judgements/evt_20261009_000001` 的 `risk_score` 是 `0.95`，`risk_level` 是 `high`，状态是 `completed`，`requires_human_review` 是假。
- `GET /api/v1/workorders/wo-evt_20261009_000001` 的状态是 `open`。关掉应用后再 `create_app` 一次，路径不变，这张单仍是 `open`。
- 不新增生产代码。阶段 08 至 36 的既有测试保持通过。高速无图样例的 `risk_score` 仍是 `1.0`。`device.status` 仍不消费。

[tests/test_kafka_acceptance.py](tests/test_kafka_acceptance.py) 仍由 `VEHICLE_AGENT_RUN_KAFKA=1` 决定是否运行。增加一条：向 `127.0.0.1:9092` 的 `vehicle.alarm` 发布这条定样本两次，两次都交给同一次应用上的 `handle_alarm_payload`。只研判一次，风险分仍是 `0.95`，工单号仍是 `wo-evt_20261009_000001`。未设置该变量时整文件跳过。实现时若本机 Docker 可用，沿用已有的 `docker compose up -d kafka` 跑这一条；不可用则保持跳过，并在交付说明里写明原因。既有的「发布两次、用 `_run` 得到 `0.95`」那条不删。

更新 [README.md](README.md)：冒烟是 `pytest tests/test_smoke.py`。它在同一次 fixture 应用里检查健康、这条定样本的 `0.95`，以及重启后仍为 `open` 的 `wo-evt_20261009_000001`。实机经纪人仍只在 `VEHICLE_AGENT_RUN_KAFKA=1` 时连接。lite 容器的启动命令不变。排除项里删掉冒烟流水线；在线模型切换、驾驶员汇总、企业汇总、路线偏离、`vehicle.location`、`media.ready`、`judgement.requested`、`judgement.completed`、`workorder.created`、Kubernetes、CI 和前端仍排除。把阶段 36 计划里的下一阶段指向本文件。

测试通过后按仓库规则提交一次中文 commit。阶段 37 的计划只保留仓库内的 `.cursor/plans/37-smoke-judgement.plan.md`。
