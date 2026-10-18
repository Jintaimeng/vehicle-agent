---
name: 04 neo4j graph index
overview: 用 Neo4j 实现现有 GraphIndex。默认仍是内存图；切换到 Neo4j 后，验收必须对真实库跑通装配、研判、冲突和失败降级。不改评分规则。
todos:
  - id: neo4j-index
    content: 增加 Neo4jGraphIndex、固定 Cypher、幂等种子和 graph_backend 配置
    status: pending
  - id: wire-backend
    content: graph_backend=neo4j 时把 Neo4jGraphIndex 装入研判工具，并提供仅含 Neo4j 的 Compose
    status: pending
  - id: neo4j-acceptance
    content: 补齐假驱动测试与必须执行的 Neo4j 研判验收，更新 README 与阶段 03 指向
    status: pending
isProject: true
---

# 04 Neo4j 图谱索引

上一阶段是已实现的内存图谱证据，见 [.cursor/plans/03-graph-evidence.plan.md](.cursor/plans/03-graph-evidence.plan.md)。本文件是阶段 04。下一阶段替换法规索引时使用 [.cursor/plans/05-qdrant-regulation-index.plan.md](.cursor/plans/05-qdrant-regulation-index.plan.md)，本阶段不创建该文件。延续 [docs/vehicle-risk-agent-implementation-plan.md](docs/vehicle-risk-agent-implementation-plan.md) 第 15.4 节：固定关系、一到两跳、固定查询模板、图谱结果变成可引用证据。Qdrant、MCP、视觉、工单和 Kafka 留到后续。

本阶段只证明一件事：现有 `GraphIndex` 可以由 Neo4j 提供相同事实，且不改变「代码评分、模型只写总结」。

## 流程

研判图保持 `validate → context → history → retrieve → graph → score → report/review`。[src/vehicle_agent/graph/nodes.py](src/vehicle_agent/graph/nodes.py) 的 `query_graph` 仍只调用 `query_vehicle_graph`。连接失败、查询失败和预算耗尽继续由 Harness 转成工具错误，节点写入 `graph_status=degraded` 后进入评分。空结果仍是 `miss`。

## 图谱实现

- 保持 [src/vehicle_agent/knowledge/graph.py](src/vehicle_agent/knowledge/graph.py) 的 `GraphIndex.query(vehicle_id, device_id) -> list[GraphFact]`、`MemoryGraphIndex` 和现有种子。新增 `Neo4jGraphIndex`，实现同一协议。
- 关系仍只有 `drives`、`installed_with`、`has_fault`。标签使用 `Vehicle`、`Driver`、`Device`、`Fault`，以 `id` 唯一。不增加维修记录、固件或企业。
- 查询使用一条固定 Cypher，参数只有 `vehicle_id` 和 `device_id`。模型不生成语句。结果与内存图一致：该车的驾驶关系、指向给定设备的安装关系，以及该设备上的故障。说明文字在 Python 中按现有种子句式生成。
- 启动 Neo4j 后端时用幂等 `MERGE` 导入与 `seed_graph_index()` 相同的七条事实，并保证四个 `id` 唯一约束。
- 在 [pyproject.toml](pyproject.toml) 增加 `neo4j` 驱动。在 [src/vehicle_agent/config.py](src/vehicle_agent/config.py) 增加 `graph_backend`（`memory` 或 `neo4j`，默认 `memory`）、`neo4j_uri`、`neo4j_user`、`neo4j_password`。默认内存后端使日常演示和默认 pytest 不依赖数据库。
- [src/vehicle_agent/api/main.py](src/vehicle_agent/api/main.py) 在 `graph_backend=neo4j` 时构造 `Neo4jGraphIndex` 并传入 `build_vehicle_tools`。工具名、入参和出参不变。
- 增加只含 Neo4j 的 [deployments/docker-compose.yml](deployments/docker-compose.yml)。开发口令写在该文件中并标明仅限本地，不提交 `.env`。

## 验收

假驱动测试只固定 Cypher 参数和结果映射，不代替真实后端。交付前启动 Compose 中的 Neo4j，并实际跑通下列切换后的用例。服务不可用时本阶段视为未通过，不能跳过。

- 假驱动：同一查询参数得到与内存图相同的 `GraphFact`；未知车辆为空；`vehicle_001` 不会带上 `device_003` 的遮挡故障；驱动异常向上抛出。
- 切换：`graph_backend=neo4j` 时应用装上 `Neo4jGraphIndex`，工具仍是 `query_vehicle_graph`。`memory` 时仍是内存图。
- 命中：`vehicle_001` 走完整研判，状态为 `completed`，`risk_score == 1.0`，证据同时含 `document` 与 `graph`，图谱事实与内存种子一致。
- 冲突：`vehicle_003` 的 `camera_occlusion` 与法规摘录同时出现，状态为 `awaiting_review`，两类证据都保留，风险分与无故障时的同一车速规则一致。
- 失败：Neo4j 连接失败或查询失败时，`graph_status=degraded`，错误被记录，状态为 `awaiting_review`，整单不标为 `failed`。
- 未命中：未知车辆的图谱结果为空，并进入现有人工复核。`vehicle_001` 查询不会附带 `device_003` 的故障。
- 阶段 01 至 03 的完成、未命中、降级、冲突、预算和检查点测试保持通过。评分、冲突规则和 `prompt_version` 不改。
- 默认 pytest 在没有 Neo4j 时仍只跑内存后端。阶段交付前必须另跑一轮上述 Neo4j 验收。
- 更新 [README.md](README.md)：图谱可通过配置使用 Neo4j；默认仍是内存关系。仍然不包含 Qdrant、YOLO、MCP、Kafka 和前端。

测试通过后按仓库规则提交一次中文 commit。
