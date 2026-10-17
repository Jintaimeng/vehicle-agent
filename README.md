# Vehicle Guard AI

疲劳驾驶报警研判 MVP。LangGraph 负责确定性业务流程，轻量 Harness 统一约束模型调用、工具参数、重试、预算、输出校验和执行轨迹。

## 架构

```text
FastAPI
  -> LangGraph: validate -> context -> history -> retrieve -> score -> report
                                                                      \-> review -> report
  -> Harness: ModelAdapter + ToolRegistry + budget + trace
  -> 内存仓储 / 本地法规摘录 / SQLite checkpoint
```

风险分数、风险等级和是否人工复核由代码规则计算。模型只根据已收集证据生成总结和处置建议。

## MVP 边界

本阶段包含一条疲劳驾驶报警研判闭环、本地法规摘录检索、人工复核和检查点恢复。命中法规时证据带来源；未命中或检索失败时降低置信度并进入人工复核，研判不因此失败。

本阶段不包含 Kafka、Qdrant、Neo4j、YOLO、视觉大模型、MCP 和前端。法规检索走 `RegulationIndex`，当前实现读取仓库内 Markdown。工具通过 `ToolRegistry` 注册，后续可以原样封装成 MCP Server。

## 环境

使用机器上的共享 uv 环境，不创建项目虚拟环境。

```powershell
uv pip install -e ".[dev]" --python D:\uv_venv
```

## 演示

演示脚本使用固定模型输出，不需要本地 GPU 或在线模型：

```powershell
D:\uv_venv\Scripts\python.exe scripts\demo.py
```

脚本会提交一条粤B12345的高速疲劳驾驶报警，并打印高风险研判结果。

## 启动 API

默认模型适配器读取以下环境变量，并调用 OpenAI-compatible `chat/completions`：

- `VEHICLE_AGENT_MODEL_BASE_URL`，默认 `http://127.0.0.1:8001/v1`
- `VEHICLE_AGENT_MODEL_API_KEY`，默认 `local`
- `VEHICLE_AGENT_MODEL_NAME`，默认 `qwen`

```powershell
D:\uv_venv\Scripts\python.exe -m vehicle_agent.api.main
```

创建研判：

```powershell
curl http://127.0.0.1:8000/api/v1/judgements -H "Content-Type: application/json" -d "{\"id\":\"alarm_001\",\"vehicle_id\":\"vehicle_001\",\"driver_id\":\"driver_001\",\"device_id\":\"device_001\",\"alarm_type\":\"driver_fatigue\",\"alarm_time\":\"2026-10-09T10:30:00Z\",\"longitude\":113.26,\"latitude\":23.12,\"speed_kmh\":85,\"raw_confidence\":0.9}"
```

查询任务：

```powershell
curl http://127.0.0.1:8000/api/v1/judgements/<task_id>
```

证据不足时状态为 `awaiting_review`，提交人工复核：

```powershell
curl http://127.0.0.1:8000/api/v1/judgements/<task_id>/review -H "Content-Type: application/json" -d "{\"reviewer\":\"ops\",\"decision\":\"confirm\",\"comment\":\"人工确认\"}"
```

## 检查

```powershell
D:\uv_venv\Scripts\python.exe -m ruff check src tests scripts
D:\uv_venv\Scripts\python.exe -m pytest
```
