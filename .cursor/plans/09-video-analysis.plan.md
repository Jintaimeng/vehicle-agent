---
name: 09 video analysis
overview: 阶段 09 用定样本时间轴把短视频收成一个候选事件。只有连续命中才进入证据，并带上关键帧时间戳；无视频报警和单张图片的风险分保持不变。
todos:
  - id: video-timeline
    content: 增加 video_id、定样本时间轴，并用固定阈值聚合连续帧与关键帧
    status: completed
  - id: vision-video-node
    content: vision 节点在有 video_id 时走聚合；单帧闭眼不形成事件，关键帧才进入现有评分
    status: completed
  - id: video-api-tests
    content: 增加视频上传与连续命中、单帧、遮挡、降级测试，更新 README 和阶段 08 指向
    status: completed
isProject: true
---

# 09 视频分析

上一阶段是已完成的图片视觉，见 [.cursor/plans/08-image-vision.plan.md](.cursor/plans/08-image-vision.plan.md)。本文件是阶段 09，落地到 [.cursor/plans/09-video-analysis.plan.md](.cursor/plans/09-video-analysis.plan.md)。下一阶段做微调实验时再写 `.cursor/plans/10-lora-experiment.plan.md`，本阶段不创建该文件。延续 [docs/vehicle-risk-agent-implementation-plan.md](docs/vehicle-risk-agent-implementation-plan.md) 第 8 周和第 16.2、16.3、16.4 节：抽帧、连续帧聚合、关键帧，以及报告可引用时间戳。`vision-mcp`、目标跟踪、工单、Skill、Kafka 和前端继续留到后续。

本阶段只证明一件事：带 `video_id` 的短视频能定位一段风险时间，并把关键帧时间戳写入证据，且不改变「代码评分、模型不能改风险分」。没有 `video_id` 的报警，包括阶段 08 的单张图片，保持现在的分数和状态。

默认不调用 FFmpeg、OpenCV 或 YOLO。帧和检测结果来自仓库内的时间轴目录。FFmpeg 只在 `VEHICLE_AGENT_RUN_FFMPEG=1` 时单独验收抽帧。

## 流程

```mermaid
flowchart LR
  alarm[video_id] --> timeline[定样本时间轴]
  timeline --> frames[按时间戳取帧]
  frames --> rules[连续帧规则]
  rules --> keyframe[关键帧]
  keyframe --> vlm[可见事实]
  vlm --> score[score]
```

研判图仍是 `validate → context → history → retrieve → graph → vision → score → report/review`。不新增节点，也不注册 MCP 工具。[src/vehicle_agent/graph/nodes.py](src/vehicle_agent/graph/nodes.py) 的 `vision` 在有 `video_id` 时改走视频聚合；只有 `image_id` 时仍调用现有 [VisionIndex.analyze](src/vehicle_agent/knowledge/vision.py)。两者都有时以 `video_id` 为准。

## 时间轴与聚合

在 [src/vehicle_agent/domain/models.py](src/vehicle_agent/domain/models.py) 为 `Alarm` 增加可选 `video_id`。新的 `src/vehicle_agent/knowledge/video.py` 读取包内 JSON。每条样本是按秒排列的帧，帧通过已有 `image_id` 复用阶段 08 的检测，不保存像素。至少四条：

- `video_fatigue`：连续多帧 `closed_eye`，时长达到阈值。
- `video_blink`：只有一帧 `closed_eye`。
- `video_occlusion`：窗口内有 `camera_occlusion`。
- `video_empty`：帧上没有检测。

未知 `video_id` 视为未形成事件，不是异常。

聚合是纯函数，阈值写死：

- 同一段里相邻命中帧间隔不超过 1.5 秒，首尾时间差至少 2.0 秒。
- 这段里 `closed_eye` 或 `yawn` 的平均置信度不低于 0.6。
- `alarm.speed_kmh` 不低于 60。
- 窗口内没有置信度不低于 0.6 的 `camera_occlusion`。
- 每帧带 `face_quality`，缺省为 1，低于 0.5 的帧不计入连续命中。

单帧闭眼不生成候选事件。达标时只保留置信度最高的那一帧作为关键帧，记下 `start_s`、`end_s` 和 `keyframe_timestamp_s`。视觉复核仍用现有 `complete_structured`，输入只有这一帧的检测 JSON，不传像素，也不按帧多次调用模型。

## 评分

`risk_score` 仍不因视觉改变。达标事件把关键帧检测交给现有评分：`closed_eye` 或 `yawn` 继续加 `_HIT_BONUS`。证据描述写明起止时间和关键帧时间戳。

- 未形成事件：有 `video_id` 但规则不满足时，按现有 `vision_status=miss` 降低置信度并进入复核，整单不标为 `failed`。
- 遮挡：法规为 `hit` 且窗口内有遮挡时，沿用画面冲突规则，状态为 `awaiting_review`，`risk_score` 与同一车速、无视频时一致。法规摘录和视频证据都保留。
- 失败：时间轴读取失败时 `vision_status=degraded`，错误写入 `errors`，状态为 `awaiting_review`，整单不标为 `failed`。

报告提示增加一句：可以引用证据里的关键帧时间戳，不要编造证据中没有的时间。`prompt_version` 改为 `report-v5`。无视频路径不增加模型调用。

`POST /api/v1/vision/videos` 接收 `video_id` 和文件。fixture 不解码文件，返回帧时间戳、是否形成事件，以及关键帧时间。研判节点调用同一个聚合函数。

## 验收

- 无视频：阶段 08 的无图、`closed_eye`、遮挡、未命中和检测失败测试保持通过，`risk_score` 不变。
- 连续命中：`video_fatigue` 配合 `vehicle_001` 的高速报警为 `completed`，`risk_score == 1.0`，证据含起止时间和关键帧时间戳，并含 `visible_facts`。
- 单帧：`video_blink` 不因一帧闭眼形成事件，状态为 `awaiting_review`，`risk_score` 与无视频时的同一车速规则一致。
- 遮挡：`video_occlusion` 与法规摘录同时出现，状态为 `awaiting_review`，两类证据都保留，风险分与无视频时一致。
- 上传：fixture 下 `POST /api/v1/vision/videos` 对 `video_fatigue` 返回关键帧时间戳。
- 失败：时间轴抛错时 `vision_status=degraded`，状态为 `awaiting_review`，整单不标为 `failed`。
- FFmpeg：默认 pytest 不运行。`VEHICLE_AGENT_RUN_FFMPEG=1` 且本机有 `ffmpeg` 和本地短视频时，断言抽出的时间戳间隔约为 1 秒。不安装 OpenCV，不往 Compose 增加服务。

更新 [README.md](README.md)：默认定样本时间轴；FFmpeg 可选且不在默认测试中。仍然不包含视觉 MCP、目标跟踪、工单 MCP、Kafka 和前端。把阶段 08 计划中的下一阶段指向本文件。

测试通过后按仓库规则提交一次中文 commit。
