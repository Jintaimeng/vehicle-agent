# Gemini 真实链路验收（2026-10-29）

## 配置与调用

- 配置文件：`deployments/online.env`（Git 忽略，不提交密钥）。
- 文本和视觉端点：`https://generativelanguage.googleapis.com/v1beta/openai`。
- 文本和视觉模型：`gemini-3.5-flash-lite`，保留用户提供的两个 Key。
- `gemini-3.8-flash` 模型列表鉴权成功，但实际调用超时或返回 HTTP 503；
  `gemini-2.5-flash` / `gemini-2.5-flash-lite` 实际调用返回 HTTP 404。
- 替换模型后，独立文本请求返回 `{"status":"ok"}`，独立图片请求成功返回 labels。

## 最终在线容器验收

执行 `.venv\Scripts\python.exe scripts/run_live_acceptance.py models`：
**2 passed in 17.38s**。API、Kafka、Neo4j、Qdrant 均运行，API 健康。

完整图片任务：`task_4275e059f0ed4892b96e34b1192b5219`。

1. YOLO：真实 `yolov8n.pt`，检出 4 个 person → driver 映射框。
2. VLM：`vlm_status=hit`，`vision_agreement=match`；返回 driver、mobile_phone，
   调用耗时约 3.69 秒，1190 tokens。
3. Agent：报告使用真实 Gemini 输出，耗时约 3.99 秒，1888 tokens；
   `status=completed`，`risk_score=1.0`，`confidence=0.95`，`errors=[]`。
4. 工单：`wo-task_4275e059f0ed4892b96e34b1192b5219`，状态 open，
   通过工单 GET 接口确认 task_id 和 summary 与研判一致。
5. 同一服务连续的纯文本任务 `task_6d91eb8b5415422087c140c5c4b6cc81`
   同样 completed，并创建 `wo-task_6d91eb8b5415422087c140c5c4b6cc81`。

## 修复与回归

- 明确 VLM 的 driver 是 person 类别映射，不推断人物身份。
- 明确报告 recommendations 为字符串数组，避免真实模型返回字符串导致校验失败。
- 按 task_id 隔离工具、模型、token 和耗时预算，避免常驻服务连续任务耗尽共享预算。
- 在线测试要求无错误、视觉证据含复核事实、同一任务的工单可读取；降级不视为通过。
- `ruff check src tests scripts` 通过；`git diff --check` 通过。
- 完整默认回归：**209 passed, 28 skipped**；跳过项为需显式开启的外部服务验收，
  不能计作实机通过；本次在线验收单独开启并实际执行。

## 验证边界

样图是公交车旁的行人，并非驾驶室疲劳样本；只能证明技术链路接通，不能证明疲劳、
闭眼或打哈欠识别准确率。driver 不表示确认驾驶员身份。风险分由现有规则和报警历史
计算，Gemini 仅复核画面并生成文字，不决定风险分。
