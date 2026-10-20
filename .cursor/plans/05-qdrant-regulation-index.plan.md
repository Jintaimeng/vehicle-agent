---
name: 05 qdrant regulation index
overview: 用 Qdrant 实现现有 RegulationIndex。默认仍读 Markdown；切换到 Qdrant 后，验收必须对真实库跑通装配、研判、冲突和失败降级。不改评分规则，不做向量召回。
todos:
  - id: qdrant-index
    content: 增加 QdrantRegulationIndex、固定 payload 过滤、幂等种子和 regulation_backend 配置
    status: completed
  - id: wire-backend
    content: regulation_backend=qdrant 时把 QdrantRegulationIndex 装入研判工具，并在 Compose 中增加 Qdrant
    status: completed
  - id: qdrant-acceptance
    content: 补齐假客户端测试与必须执行的 Qdrant 研判验收，更新 README 与阶段 04 指向
    status: completed
isProject: true
---

# 05 Qdrant 法规索引

上一阶段是已实现的 Neo4j 图谱索引，见 [.cursor/plans/04-neo4j-graph-index.plan.md](.cursor/plans/04-neo4j-graph-index.plan.md)。本文件是阶段 05。下一阶段是 [.cursor/plans/06-hybrid-regulation-retrieval.plan.md](.cursor/plans/06-hybrid-regulation-retrieval.plan.md)。延续 [docs/vehicle-risk-agent-implementation-plan.md](docs/vehicle-risk-agent-implementation-plan.md) 第 15 节的可引用摘录；混合检索、重排和评测留到阶段 06。MCP、视觉、工单和 Kafka 继续留到后续。

本阶段只证明一件事：现有 `RegulationIndex` 可以由 Qdrant 提供相同摘录，且不改变「代码评分、模型只写总结」。

## 流程

研判图保持 `validate → context → history → retrieve → graph → score → report/review`。[src/vehicle_agent/graph/nodes.py](src/vehicle_agent/graph/nodes.py) 的 `retrieve` 仍只调用 `search_regulations`，入参仍只有 `alarm_type`。连接失败、查询失败和预算耗尽继续由 Harness 转成工具错误，节点写入 `regulation_status=degraded` 后进入评分。空结果仍是 `miss`。

## 法规索引

- 保持 [src/vehicle_agent/knowledge/index.py](src/vehicle_agent/knowledge/index.py) 的 `RegulationIndex.search(alarm_type) -> list[RegulationSnippet]` 和 `MarkdownRegulationIndex`。新增 `QdrantRegulationIndex`，实现同一协议。摘录仍来自现有 Markdown，正文继续用项目自拟文字。
- 集合名固定为 `regulations`。每个章节一个点，点 ID 由 `document_id` 与 `section` 稳定生成。Payload 保存 `document_id`、`title`、`section`、`excerpt` 和 `alarm_types`。向量只是满足 Qdrant 点结构的一维常量 `[1.0]`，查询不计算相似度，也不引入 Embedding 模型。
- 查询使用固定的 payload 过滤：`alarm_types` 包含传入的 `alarm_type`。模型不生成查询。结果按 `document_id`、`section` 排序。`MarkdownRegulationIndex.search` 改为同一顺序，使两个后端的列表可以直接比较。现有单条命中断言保持成立。
- 启动 Qdrant 后端时，集合不存在则创建，再用幂等 upsert 写入 `packaged_regulation_index()` 的全部章节。重复启动不新增重复点。
- 在 [pyproject.toml](pyproject.toml) 增加 `qdrant-client`，并用 `uv pip install` 装进 `D:\uv_venv`。在 [src/vehicle_agent/config.py](src/vehicle_agent/config.py) 增加 `regulation_backend`（`markdown` 或 `qdrant`，默认 `markdown`）和 `qdrant_url`（默认 `http://127.0.0.1:6333`）。默认 Markdown 后端使日常演示和默认 pytest 不依赖数据库。
- [src/vehicle_agent/api/main.py](src/vehicle_agent/api/main.py) 按 `regulation_backend` 构造索引，并传入 `build_vehicle_tools`。`qdrant` 时工具仍是 `search_regulations`，入参和出参不变。客户端在首次查询时连接，切换测试不主动连库。`build_default_app` 在 Qdrant 后端上先调用 `ensure_ready`。应用关闭时关闭客户端。
- 在 [deployments/docker-compose.yml](deployments/docker-compose.yml) 增加 `qdrant` 服务，映射 `6333`。本阶段验收只启动该服务。现有 Neo4j 服务保留。不提交 `.env`。

## 验收

假客户端测试只固定过滤条件和结果映射，不代替真实后端。交付前启动 Compose 中的 Qdrant，并实际跑通下列切换后的用例。服务不可用时本阶段视为未通过，不能跳过。图谱验收继续使用内存图，从而只依赖 Qdrant。

- 假客户端：`driver_fatigue` 与 `device_offline` 得到与 Markdown 索引相同的 `RegulationSnippet`；未知报警类型为空；客户端异常向上抛出；同一批章节 upsert 两次后点数不变。
- 切换：`regulation_backend=qdrant` 时应用装上 `QdrantRegulationIndex`，工具仍是 `search_regulations`。`markdown` 时仍是 Markdown 索引。
- 命中：`vehicle_001` 走完整研判，状态为 `completed`，`risk_score == 1.0`，证据同时含 `document` 与 `graph`，法规摘录与 Markdown 种子一致。
- 冲突：`vehicle_003` 的 `camera_occlusion` 与法规摘录同时出现，状态为 `awaiting_review`，两类证据都保留，风险分与无故障时的同一车速规则一致。
- 失败：Qdrant 连接失败或查询失败时，`regulation_status=degraded`，错误被记录，状态为 `awaiting_review`，整单不标为 `failed`。
- 未命中：未知报警类型的法规结果为空，并进入现有人工复核。
- 阶段 01 至 04 的完成、未命中、降级、冲突、预算和检查点测试保持通过。评分、冲突规则和 `prompt_version` 不改。
- 默认 pytest 在没有 Qdrant 时仍只跑 Markdown 后端。阶段交付前必须另跑一轮上述 Qdrant 验收，用 `VEHICLE_AGENT_RUN_QDRANT=1` 标记，写法对齐 [tests/test_neo4j_acceptance.py](tests/test_neo4j_acceptance.py)。
- 更新 [README.md](README.md)：法规可通过配置使用 Qdrant；默认仍读仓库内 Markdown。仍然不包含混合检索、YOLO、MCP、Kafka 和前端。

测试通过后按仓库规则提交一次中文 commit。
