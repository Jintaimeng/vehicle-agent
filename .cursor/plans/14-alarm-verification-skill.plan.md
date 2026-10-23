---
name: alarm verification skill
overview: 阶段 14 把现有疲劳驾驶研判声明为 alarm-verification Skill。图仍按原节点执行，风险分、复核和建单规则不变。
todos:
  - id: skill-definition
    content: 增加 alarm-verification 定义：触发、步骤、六个只读工具、输出、失败处理和四条评测案例；编译图时与节点顺序核对
    status: completed
  - id: bind-validate
    content: validate 在触发匹配时写入 skill_id；不匹配则 skill_not_applicable，不改评分、复核和建单
    status: completed
  - id: tests-docs
    content: 补四条评测与工具列表测试，更新 README，测试通过后提交中文 commit
    status: completed
isProject: false
---

# 14 报警核验 Skill

上一阶段是已完成的工单 MCP，见 [.cursor/plans/13-workorder-mcp.plan.md](.cursor/plans/13-workorder-mcp.plan.md)。本文件是阶段 14，落地到 [.cursor/plans/14-alarm-verification-skill.plan.md](.cursor/plans/14-alarm-verification-skill.plan.md)。下一阶段是 [.cursor/plans/15-device-online-status.plan.md](.cursor/plans/15-device-online-status.plan.md)。延续 [docs/vehicle-risk-agent-implementation-plan.md](docs/vehicle-risk-agent-implementation-plan.md) 第 14.2 节的 `alarm-verification`：组合车辆查询、历史报警、视觉分析、规则检索和图谱查询，判断报警真实性。风险画像、设备诊断、根因分析、运营报告、`get_visual_analysis`、事件入库、目标跟踪、OpenCV、RTSP、Kafka 和前端仍不纳入。

本阶段只证明一件事：现有疲劳驾驶研判可以声明成一份 Skill。触发、步骤、工具、输出、失败处理和评测案例都在这份定义里。执行仍是 [src/vehicle_agent/graph/builder.py](src/vehicle_agent/graph/builder.py) 的固定图，工具仍经 Harness。模型不能选择步骤，也不能改风险分或决定是否建单。`risk_score`、复核条件和 `prompt_version`（`report-v6`）不变。

## 流程

```mermaid
flowchart LR
  alarm[driver_fatigue报警] --> skill[alarm-verification]
  skill --> graph[现有研判图]
  graph --> tools[六个只读工具]
  tools --> score[代码评分]
  score --> report[report]
```

研判图仍是 `validate → context → history → retrieve → graph → vision → score → report/review`。不新增节点，不注册新工具，不把 Skill 交给模型编排。

## Skill 定义

新增 [src/vehicle_agent/skills/alarm_verification.py](src/vehicle_agent/skills/alarm_verification.py)。用 Pydantic 固定一份定义，字段对应第 14.2 节：

- 标识：`alarm-verification`。
- 触发：`alarm_type` 为 `driver_fatigue`。这是 [Alarm](src/vehicle_agent/domain/models.py) 目前唯一允许的类型。
- 输入：现有报警字段。`image_id` 与 `video_id` 仍可选。
- 步骤：与图的节点顺序一致。`validate`、`score`、`review`、`report` 不调用工具。`context`、`history`、`retrieve`、`graph` 分别对应 `get_vehicle_context`、`list_recent_alarms`、`search_regulations`、`query_vehicle_graph`。`vision` 在有 `video_id` 时调用 `analyze_video`，否则有 `image_id` 时调用 `analyze_image`，两者都没有则不调用。两者都有时仍以视频为准。
- 工具：只列出上述六个只读工具。`create_workorder`、`get_workorder`、`update_workorder` 不在 Skill 工具列表里。建单仍留在 [report](src/vehicle_agent/graph/nodes.py) 中，条件不变。
- 输出：沿用 [JudgementResult](src/vehicle_agent/domain/models.py) 的字段。
- 失败：`validate`、`context`、`history` 失败仍让整单 `failed`。法规、图谱、视觉失败仍降级并进入复核，整单不标为 `failed`。工单创建失败仍保持 `completed`，并记 `workorder_status=degraded`。这条失败不属于 Skill 步骤。
- 评测案例：四条，都复用现有定样本，不新增媒体，不调用模型以外的 `report`。

`build_graph` 在编译前核对 Skill 步骤与图的节点顺序一致，工具名都在已注册的只读工具里。不一致时启动失败，避免定义和图各写一套。

`validate` 在报警合法且触发匹配时把 `skill_id=alarm-verification` 写入状态。现有合法报警都会匹配。触发不匹配时状态为 `failed`，错误码 `skill_not_applicable`，不调用后续工具。测试用绕过 `Alarm` 枚举的原始字典覆盖这条路径；API 仍只接受 `driver_fatigue`。

## 评测案例

- 无图完成：车速 40 的现有中风险路径为 `completed`，`skill_id` 为 `alarm-verification`，trace 含四个查询工具，不含 `analyze_image`、`analyze_video` 和 `create_workorder`。`risk_score` 与阶段 13 相同。
- 有图高风险：`image_closed_eye` 配合高速报警仍为 `completed`，`risk_score == 1.0`。trace 在四个查询工具之后含 `analyze_image`。`create_workorder` 仍出现，但它不在 Skill 工具列表中。
- 观察冲突：`image_closed_eye_mismatch` 仍为 `awaiting_review`，没有工单，`risk_score` 与观察一致时相同。
- 视觉降级：检测工具抛错时 `vision_status=degraded`，进入复核，整单不标为 `failed`。

阶段 08 至 13 的既有测试保持通过。无图且在 `retrieve` 之前耗尽工具预算的测试不因本阶段多一次调用。模型调用仍只发生在 `report`，以及 `review_backend=vlm` 时的那一次视觉模型调用。

## 验收与文档

更新 [README.md](README.md)：疲劳驾驶研判由 `alarm-verification` 声明；执行仍是固定图；Skill 只包含六个只读工具。删掉「本阶段不包含 Skill」里对这个 Skill 的排除，其余 Skill、Kafka 和前端仍排除。

阶段 13 已经把下一阶段指向本文件，核对这句话仍在即可。

测试通过后按仓库规则提交一次中文 commit。阶段 14 的计划只保留本文件。
