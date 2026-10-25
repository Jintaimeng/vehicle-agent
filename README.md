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

法规检索走 `RegulationIndex`。默认实现读取仓库内 Markdown。把 `VEHICLE_AGENT_REGULATION_BACKEND` 设为 `qdrant` 后，同一工具改为读取 Qdrant 中的相同摘录。排序是字符二元组哈希向量、词项重叠、RRF 和固定加权重排，查询短语由报警类型查表得到。模型只使用已经校验过的 `alarm_type`。`neural_embed` 在该哈希上加一层固定线性变换和 `tanh`，维度仍是 128。研判和 Qdrant 不使用它。

图谱查询走 `GraphIndex`。默认实现是内存中的固定关系，不依赖数据库。把 `VEHICLE_AGENT_GRAPH_BACKEND` 设为 `neo4j` 后，同一工具改为读取 Neo4j 中的相同事实。查询语句是固定 Cypher，模型只使用已经校验过的 `vehicle_id` 和 `device_id`。

工具通过 `ToolRegistry` 注册。默认 `VEHICLE_AGENT_TOOL_BACKEND=local`，研判直接调用进程内 handler。设为 `mcp` 后，同名工具改为 MCP `tools/call`。`VEHICLE_AGENT_MCP_TRANSPORT` 默认 `memory`，在进程内走 MCP 协议；设为 `stdio` 时拉起 `python -m vehicle_agent.mcp`。调用方角色是 `VEHICLE_AGENT_MCP_ROLE`，默认 `monitor`。十一个工具里，车辆、报警、法规、图谱、图片、视频、设备状态和维修记录这八个是只读，可以安全重试。图片检测和短视频聚合也在其中；`tool_backend=mcp` 时它们同样走 `tools/call`。`get_device_status` 读取定样本设备的在线状态和最后心跳时间。`get_device_maintenance` 读取固件版本和最近一次维修记录。研判图不调用这两个工具。画面复核仍由研判节点比较观察标签，不进入 MCP。工单有 `create_workorder`、`get_workorder` 和 `update_workorder`。读取是只读；创建和更新是写操作。研判只在 `risk_level=high` 且不需要人工复核时，经已授权策略调用 `create_workorder`。同一 `task_id` 重复创建返回原来的工单。创建失败时研判结果仍保留，状态保持 `completed`。中风险和人工复核路径不建单。

图片检测走 `VisionIndex`。报警可以带可选 `image_id`。没有图片时跳过检测，风险分、置信度和复核条件与之前一致。有图片时返回归一化检测框、类别和置信度，并作为 `vision` 证据。画面复核读取与检测分开的观察标签，由代码比较类别；不一致时进入人工复核，不改风险分。`POST /api/v1/vision/images` 用同一检测器，并把图片按 `image_id` 存到本地目录。

默认 `VEHICLE_AGENT_REVIEW_BACKEND=fixture`，观察来自仓库内定样本，不调用视觉大模型。设为 `vlm` 后，研判读取已保存的关键帧，交给 `VEHICLE_AGENT_VLM_BASE_URL` 上的 `VEHICLE_AGENT_VLM_MODEL_NAME`。返回的标签走同一比较。默认测试不调用视觉大模型。

短视频走定样本时间轴。报警可以带可选 `video_id`；同时有 `image_id` 时以视频为准。连续闭眼或打哈欠达到 2 秒、平均置信度不低于 0.6、车速不低于 60，且窗口内没有遮挡时，才把关键帧时间戳写入证据。单帧闭眼不形成事件。`POST /api/v1/vision/videos` 用同一聚合，并按 `video_id` 保存文件。默认定样本时间轴不解码视频文件。

默认 `VEHICLE_AGENT_VISION_BACKEND=fixture`，读取仓库内定样本，不解码像素。设为 `yolo` 后，研判按 `image_id` 读取 `VEHICLE_AGENT_VISION_STORE_DIR` 里已保存的图片字节，再使用 `VEHICLE_AGENT_YOLO_WEIGHTS`（默认 `data/yolo.pt`）。缺文件或空文件时进入人工复核，不改风险分。YOLO 依赖 `ultralytics`，不在默认安装里。

短视频默认 `VEHICLE_AGENT_VIDEO_BACKEND=fixture`，仍读定样本时间轴，上传的文件只保存、不解码。设为 `ffmpeg` 后，研判读取已保存的视频，每秒抽出一帧 JPEG，写入同一媒体目录，再走现有连续帧聚合和画面复核。默认测试不调用 FFmpeg。

疲劳驾驶研判由 `alarm-verification` 声明。触发条件是 `driver_fatigue`。执行仍是上面的固定图，模型不选择步骤，也不改风险分或是否建单。Skill 只包含六个只读工具：车辆资料、历史报警、法规检索、图谱查询、图片检测和短视频聚合。有视频时只分析视频，否则有图片时才分析图片。建单仍在报告节点里，不在这份 Skill 中。

设备诊断由 `device-diagnosis` 声明。入参只有设备标识。它读取在线状态、图谱里的摄像头遮挡故障、1 日内的报警次数、这些报警是否带有 `video_id`、置信度是否低于 0.6、固件版本和最近一次维修记录。离线、遮挡、频繁报警和视频缺失优先。在线且没有遮挡时，1 日内至少 3 次报警的原因是 `frequent_alarms`。不足 3 次时，1 日内至少 2 次且全部缺少 `video_id` 的原因是 `missing_video`。仍不足时，1 日内至少 2 次且全部为带视频、置信度低于 0.6 的原因是 `algorithm_drift`，否则是 `no_device_fault`。等于 0.6 不算低。没有视频的报警不算低置信度。有图片没有视频仍算缺少视频。固件和维修只写入证据，不改变原因。`device_003` 离线且有遮挡，原因仍是离线。`device_004` 在线且有遮挡，即使 1 日内 3 次报警都没有视频，原因仍是遮挡。`device_005` 在线、无遮挡，1 日内 3 次报警都没有视频，原因仍是频繁报警。`device_006` 在线、无遮挡，1 日内 2 次报警都没有视频，原因是视频缺失。`device_007` 同窗口里有一次带视频，原因仍是无设备故障。`device_008` 在线、无遮挡，1 日内 2 次报警都有视频且置信度都是 0.4，原因是算法漂移。`device_009` 同样有 3 次低置信度视频报警，原因仍是频繁报警。`device_010` 两次里只有一次低于 0.6，原因仍是无设备故障。疲劳研判图不调用这次诊断，也不因此改变风险分。

车辆风险画像由 `vehicle-risk-profile` 声明。入参只有车辆标识。它读取 7 日内的疲劳报警和该车设备的最近一次维修。近期是其中 1 日内的次数，趋势只比较近期和较早：近期更多是上升，更少是下降，相等是持平。未关闭维修的来源是 `device_fault`。否则有车速不低于 80 的报警时来源是 `high_speed_fatigue`，至少 2 次报警时是 `driver_fatigue`，其余是 `no_recent_risk`。建议由代码按来源写成一句。`vehicle_001` 的 7 日内 2 次里有一次车速 81，1 日内是 0 次，趋势下降，来源是高速疲劳。`vehicle_005` 的 7 日内 4 次里有 3 次在 1 日内，车速都是 70，趋势上升，来源是疲劳。`vehicle_003` 没有报警但有未关闭维修，来源是设备故障。`vehicle_012` 有一次车速 85，仍因未关闭维修判为设备故障。`vehicle_011` 只有 1 次车速 70 的报警，来源是无近期风险。疲劳研判图不调用这份画像，也不因此改变风险分。

报警根因由 `alarm-root-cause-analysis` 声明。入参只有报警标识。它读取该报警车辆和设备的图谱、该报警类型的法规摘录，以及 7 日内其余同类报警。有摄像头遮挡故障时根因是 `camera_occlusion`。否则 7 日内另有同类报警时是 `repeated_driver_fatigue`，法规能命中时是 `driver_fatigue`，其余是 `unexplained`。建议由代码按根因写成一句。`alarm_004_1` 所在设备已有遮挡，即使 7 日内另有同类报警，根因仍是遮挡。`alarm_hist_001` 无遮挡且 7 日内另有 1 次同类报警，根因是重复疲劳。`alarm_011_1` 无遮挡且没有其余同类报警，根因是单次疲劳。疲劳研判图不调用这份根因分析，也不因此改变风险分。

运营报告由 `operation-report` 声明。入参只有 `day` 或 `week`。它汇总该窗口内的全部疲劳报警，并和上一同等窗口比较：本窗口更多是上升，更少是下降，相等是持平。处置只统计这些报警关联的工单。有未关闭工单时效果是 `pending`，否则有已关闭工单时是 `improved`，没有工单时是 `no_disposition`。建议由代码按效果写成一句。日窗口是 19 次对上一窗口 4 次，趋势上升；`alarm_011_1` 的工单已关闭，效果是保持处置。周窗口是 24 次对上一窗口 0 次，趋势上升；另有 `alarm_hist_001` 的未关闭工单，效果是先关闭未完成工单。这批工单只给报告使用。疲劳研判图不调用这份报告，也不因此改变风险分。

检测可视化把已有归一化框画到 100×100 的 PPM 上，不解码原图。背景是黑色，框边是一像素白色。`image_closed_eye` 的框边落在左 40、上 20、右 62、下 55。没有检测框时整张图保持黑色。疲劳研判图不调用这次绘制，也不因此改变风险分。

目标跟踪有两条路径。定样本只比较相邻帧，同一类别且交并比不低于 0.5 时共用轨迹编号。`video_fatigue` 的四帧闭眼是轨迹 1。`vision_backend=yolo` 时，一段抽出的帧走 YOLO 自带的 `track`，这段结束后清空跟踪状态；单张图片仍走 `predict`。疲劳研判图不调用定样本跟踪，也不因此改变风险分。

`video_backend=ffmpeg` 时，已保存文件、`rtsp://` 和 `jt1078://013800138000/1` 走同一个抽帧入口。这条 JT1078 只映射到 `rtsp://camera/live`，不解析码流。默认定样本时间轴不变。研判图仍调用 `analyze_video`，风险分规则不变。

`VEHICLE_AGENT_INGEST=kafka` 时，进程常驻消费 `vehicle.alarm`。这条定样本的风险分是 `0.95`，没有图片和视频，所以不跑视觉，`task_id` 是 `evt_20261009_000001`。同一个 `event_id` 不再研判第二次，车速仍是 `82.5`。默认 `VEHICLE_AGENT_INGEST=off`，不连接经纪人。默认测试用假发送器。实机经纪人只在 `VEHICLE_AGENT_RUN_KAFKA=1` 时连接 `127.0.0.1:9092`。`device.status` 仍不消费。

`device.status` 可以编成这条心跳的 JSON 再交给现有收件箱；同一个 `event_id` 只留一条。默认测试用假发送器。实机与 `vehicle.alarm` 共用 `VEHICLE_AGENT_RUN_KAFKA=1` 和 `127.0.0.1:9092`。研判图和设备诊断都不消费它。

lite 容器用 `docker compose -f deployments/docker-compose.yml up -d api` 启动。它使用定样本和固定模型输出，不启动 Neo4j、Qdrant、Kafka，也不调用在线模型。演示脚本那条车速 85、置信度 0.9 的报警风险分是 `1.0`。

本阶段不包含工单落盘、冒烟流水线、驾驶员汇总、企业汇总、路线偏离、`vehicle.location`、`media.ready`、`judgement.requested`、`judgement.completed`、`workorder.created`、Kubernetes 和前端。

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

本机进程默认 `VEHICLE_AGENT_MODEL_BACKEND=openai`，监听 `127.0.0.1:8000`，并调用 OpenAI-compatible `chat/completions`：

- `VEHICLE_AGENT_MODEL_BASE_URL`，默认 `http://127.0.0.1:8001/v1`
- `VEHICLE_AGENT_MODEL_API_KEY`，默认 `local`
- `VEHICLE_AGENT_MODEL_NAME`，默认 `qwen`

```powershell
D:\uv_venv\Scripts\python.exe -m vehicle_agent.api.main
```

常驻消费 `vehicle.alarm` 时设置 `VEHICLE_AGENT_INGEST=kafka`。经纪人地址是 `VEHICLE_AGENT_KAFKA_BOOTSTRAP`，默认 `127.0.0.1:9092`。先启动 Kafka，再启动本进程。lite 容器的 `api` 服务不设置这项。

不需要在线模型时，用 lite 容器。它把 `VEHICLE_AGENT_MODEL_BACKEND` 设为 `fixture`，监听 `0.0.0.0:8000`，检查点写到卷里的 `/data/checkpoints.sqlite`。`GET /health` 只返回进程可用，不调用模型。

```powershell
docker compose -f deployments/docker-compose.yml up -d api
```

容器验收：

```powershell
$env:VEHICLE_AGENT_RUN_COMPOSE="1"
D:\uv_venv\Scripts\python.exe -m pytest tests/test_compose_acceptance.py
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
