# Vehicle Guard AI

疲劳驾驶报警研判 MVP。LangGraph 负责确定性业务流程，轻量 Harness 统一约束模型调用、工具参数、重试、预算、输出校验和执行轨迹。

## 架构

```text
FastAPI
  -> LangGraph: validate -> context -> history -> retrieve -> graph -> vision -> score -> report
                                                                                      \-> review -> report
  -> Harness: ModelAdapter + ToolRegistry + budget + trace
  -> 内存仓储 / 法规摘录（默认 Markdown，可切换 Qdrant） / 图谱关系（默认内存，可切换 Neo4j） / 图片检测（默认定样本，可切换 YOLO） / SQLite checkpoint
  -> 工具默认在进程内调用，可改为 MCP 进程内传输或 stdio 子进程
```

风险分数、风险等级和是否人工复核由代码规则计算。模型只根据已收集证据生成总结和处置建议。

## MVP 边界

本阶段包含一条疲劳驾驶报警研判闭环、法规摘录检索、图谱关系和人工复核、检查点恢复。法规命中时证据带来源；图谱命中时另附车辆、驾驶员和设备关系。未命中、检索失败，或法规与摄像头遮挡故障冲突时，降低置信度并进入人工复核，研判不因此失败。

法规检索走 `RegulationIndex`。默认实现读取仓库内 Markdown。把 `VEHICLE_AGENT_REGULATION_BACKEND` 设为 `qdrant` 后，同一工具改为读取 Qdrant 中的相同摘录。排序是字符二元组哈希向量、词项重叠、RRF 和固定加权重排，查询短语由报警类型查表得到。模型只使用已经校验过的 `alarm_type`。

图谱查询走 `GraphIndex`。默认实现是内存中的固定关系，不依赖数据库。把 `VEHICLE_AGENT_GRAPH_BACKEND` 设为 `neo4j` 后，同一工具改为读取 Neo4j 中的相同事实。查询语句是固定 Cypher，模型只使用已经校验过的 `vehicle_id` 和 `device_id`。

工具通过 `ToolRegistry` 注册。默认 `VEHICLE_AGENT_TOOL_BACKEND=local`，研判直接调用进程内 handler。设为 `mcp` 后，同名工具改为 MCP `tools/call`。`VEHICLE_AGENT_MCP_TRANSPORT` 默认 `memory`，在进程内走 MCP 协议；设为 `stdio` 时拉起 `python -m vehicle_agent.mcp`。调用方角色是 `VEHICLE_AGENT_MCP_ROLE`，默认 `monitor`。十一个工具里，车辆、报警、法规、图谱、图片、视频、设备状态和维修记录这八个是只读，可以安全重试。图片检测和短视频聚合也在其中；`tool_backend=mcp` 时它们同样走 `tools/call`。`get_device_status` 读取定样本设备的在线状态和最后心跳时间。`get_device_maintenance` 读取固件版本和最近一次维修记录。研判图不调用这两个工具。画面复核仍由研判节点比较观察标签，不进入 MCP。工单有 `create_workorder`、`get_workorder` 和 `update_workorder`。读取是只读；创建和更新是写操作。研判只在 `risk_level=high` 且不需要人工复核时，经已授权策略调用 `create_workorder`。同一 `task_id` 重复创建返回原来的工单。创建失败时研判结果仍保留，状态保持 `completed`。中风险和人工复核路径不建单。

图片检测走 `VisionIndex`。报警可以带可选 `image_id`。没有图片时跳过检测，风险分、置信度和复核条件与之前一致。有图片时返回归一化检测框、类别和置信度，并作为 `vision` 证据。画面复核读取与检测分开的观察标签，由代码比较类别；不一致时进入人工复核，不改风险分。`POST /api/v1/vision/images` 用同一检测器，并把图片按 `image_id` 存到本地目录。

默认 `VEHICLE_AGENT_REVIEW_BACKEND=fixture`，观察来自仓库内定样本，不调用视觉大模型。设为 `vlm` 后，研判读取已保存的关键帧，交给 `VEHICLE_AGENT_VLM_BASE_URL` 上的 `VEHICLE_AGENT_VLM_MODEL_NAME`。返回的标签走同一比较。默认测试不调用视觉大模型。

短视频走定样本时间轴。报警可以带可选 `video_id`；同时有 `image_id` 时以视频为准。连续闭眼或打哈欠达到 2 秒、平均置信度不低于 0.6、车速不低于 60，且窗口内没有遮挡时，才把关键帧时间戳写入证据。单帧闭眼不形成事件。`POST /api/v1/vision/videos` 用同一聚合，并按 `video_id` 保存文件。默认定样本时间轴不解码视频文件。

默认 `VEHICLE_AGENT_VISION_BACKEND=fixture`，读取仓库内定样本，不解码像素。设为 `yolo` 后，研判按 `image_id` 读取 `VEHICLE_AGENT_VISION_STORE_DIR` 里已保存的图片字节，再使用 `VEHICLE_AGENT_YOLO_WEIGHTS`（默认 `data/yolo.pt`）。缺文件或空文件时进入人工复核，不改风险分。YOLO 依赖 `ultralytics`，不在默认安装里。

短视频默认 `VEHICLE_AGENT_VIDEO_BACKEND=fixture`，仍读定样本时间轴，上传的文件只保存、不解码。设为 `ffmpeg` 后，研判读取已保存的视频，每秒抽出一帧 JPEG，写入同一媒体目录，再走现有连续帧聚合和画面复核。默认测试不调用 FFmpeg。

疲劳驾驶研判由 `alarm-verification` 声明。触发条件是 `driver_fatigue`。执行仍是上面的固定图，模型不选择步骤，也不改风险分或是否建单。Skill 只包含六个只读工具：车辆资料、历史报警、法规检索、图谱查询、图片检测和短视频聚合。有视频时只分析视频，否则有图片时才分析图片。建单仍在报告节点里，不在这份 Skill 中。

设备诊断由 `device-diagnosis` 声明。入参只有设备标识。它读取在线状态、图谱里的摄像头遮挡故障、固件版本和最近一次维修记录。原因仍由代码给出：`offline`、`camera_occlusion` 或 `no_device_fault`。固件和维修只写入证据，不改原因。离线优先于遮挡：`device_003` 两者都有，原因仍是离线，遮挡留在证据里，最近维修是未关闭的那条。`device_004` 在线且有遮挡，没有维修记录。疲劳研判图不调用这次诊断，也不因此改变风险分。

本阶段不包含频繁报警、视频缺失、其余 Skill、神经网络 Embedding、目标跟踪、Kafka 和前端。

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

## Neo4j

默认后端是内存图。需要 Neo4j 时，只启动下面这个服务。其中的口令仅限本机开发，写在 Compose 文件里，不要另建 `.env`。

```powershell
docker compose -f deployments/docker-compose.yml up -d neo4j
```

然后设置：

- `VEHICLE_AGENT_GRAPH_BACKEND=neo4j`
- `VEHICLE_AGENT_NEO4J_URI`，默认 `bolt://127.0.0.1:7687`
- `VEHICLE_AGENT_NEO4J_USER`，默认 `neo4j`
- `VEHICLE_AGENT_NEO4J_PASSWORD`，默认 `vehicle-agent-local`

默认测试不连接数据库。图库起来之后，用下面的命令跑 Neo4j 研判验收：

```powershell
$env:VEHICLE_AGENT_RUN_NEO4J="1"
D:\uv_venv\Scripts\python.exe -m pytest tests/test_neo4j_acceptance.py
```

## Qdrant

默认后端读取仓库内 Markdown，并用同一套混合排序。需要 Qdrant 时，只启动下面这个服务。已有 `regulations` 集合的向量维度不是 128 时，启动后会删除并重建。

```powershell
docker compose -f deployments/docker-compose.yml up -d qdrant
```

然后设置：

- `VEHICLE_AGENT_REGULATION_BACKEND=qdrant`
- `VEHICLE_AGENT_QDRANT_URL`，默认 `http://127.0.0.1:6333`

默认测试不连接数据库。向量库起来之后，用下面的命令跑 Qdrant 研判验收：

```powershell
$env:VEHICLE_AGENT_RUN_QDRANT="1"
D:\uv_venv\Scripts\python.exe -m pytest tests/test_qdrant_acceptance.py
```

## MCP

默认不启动 MCP 进程。需要独立进程时：

```powershell
$env:VEHICLE_AGENT_TOOL_BACKEND="mcp"
$env:VEHICLE_AGENT_MCP_TRANSPORT="stdio"
```

默认测试使用进程内工具。stdio 研判验收：

```powershell
$env:VEHICLE_AGENT_RUN_MCP="1"
D:\uv_venv\Scripts\python.exe -m pytest tests/test_mcp_acceptance.py
```

## 图片视觉

默认定样本，不需要权重。需要 YOLO 时安装可选依赖，并准备权重和一张本地图片：

```powershell
uv pip install -e ".[vision]" --python D:\uv_venv
$env:VEHICLE_AGENT_RUN_YOLO="1"
$env:VEHICLE_AGENT_YOLO_WEIGHTS="data/yolo.pt"
$env:VEHICLE_AGENT_YOLO_IMAGE="data/vision-sample.jpg"
D:\uv_venv\Scripts\python.exe -m pytest tests/test_yolo_acceptance.py
```

权重或图片不存在时，这条验收会跳过。默认 `pytest` 不加载 YOLO。

视觉大模型验收需要兼容的对话接口和一张已能读取的本地图片。默认测试不调用它。

```powershell
$env:VEHICLE_AGENT_RUN_VLM="1"
$env:VEHICLE_AGENT_VLM_BASE_URL="http://127.0.0.1:8001/v1"
$env:VEHICLE_AGENT_VLM_MODEL_NAME="qwen-vl"
$env:VEHICLE_AGENT_VLM_IMAGE="data/vision-sample.jpg"
D:\uv_venv\Scripts\python.exe -m pytest tests/test_vlm_acceptance.py
```

需要 FFmpeg 抽帧验收时，准备本机 `ffmpeg` 和一条本地短视频。验收检查 JPEG 和时间戳间隔，并交给现有聚合。默认测试不调用它。研判里启用抽帧要另设 `VEHICLE_AGENT_VIDEO_BACKEND=ffmpeg`。

```powershell
$env:VEHICLE_AGENT_RUN_FFMPEG="1"
$env:VEHICLE_AGENT_FFMPEG_VIDEO="data/vision-sample.mp4"
D:\uv_venv\Scripts\python.exe -m pytest tests/test_ffmpeg_acceptance.py
```

## 检查

```powershell
D:\uv_venv\Scripts\python.exe -m ruff check src tests scripts
D:\uv_venv\Scripts\python.exe -m pytest
```
