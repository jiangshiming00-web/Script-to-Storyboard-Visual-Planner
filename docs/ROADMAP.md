# Roadmap

## Phase 0: 设计和合同冻结

目标：

- 确认架构。
- 确认数据合同。
- 确认 AI 协作方式。
- 准备样例剧本和期望输出。

验收：

- 文档齐全。
- `PROJECT_STATUS.json` 可被新 AI 读取。
- Proma 能根据 brief 开始实现。

## Phase 1: 本地 Planner MVP

目标：

- 支持读取一段短剧剧本。
- 输出人物、场景、道具、剧情节奏、分镜、prompt。
- 使用固定 schema 保存 JSON。

建议技术：

- Python 或 TypeScript 均可。
- 先用命令行接口，后续再做 UI。
- 所有 LLM 调用封装为 provider，避免绑定单一模型。

验收：

- 输入一个样例剧本。
- 生成 8 个核心产物。
- 产物可重复读取和审计。
- 同一人物、场景、道具 id 在镜头中稳定引用。

## Phase 2: 质量门禁

目标：

- 加入 schema validation。
- 加入 continuity audit。
- 检查角色漂移、场景漂移、道具漂移、镜头缺字段。

验收：

- 能指出不合格镜头。
- 能生成修复建议。
- 不合格镜头不会进入 executor 队列。

## Phase 3: Executor 原型

目标：

- 做一个可插拔 executor。
- 先支持 dry-run：只生成任务文件，不实际提交。
- 再支持人工确认后提交到某个 Web/API 工具。

验收：

- `executor_tasks.json` 可被读取。
- 每个任务有状态：pending、submitted、completed、failed、approved、rejected。
- 下载结果能写入 `asset_manifest.json`。

## Phase 4: Flowith / Liblib 接入

目标：

- 优先查 API。
- 无 API 时再做浏览器自动化。
- 登录、上传、下载、失败重试独立封装。

验收：

- 可以完成一个 5-10 镜头的小批量流程。
- 失败任务可重试。
- 资产路径和工具任务 id 可追踪。

## Phase 5: 批量生产辅助

目标：

- 支持整集处理。
- 支持人工审核队列。
- 支持版本化重跑。

验收：

- 一集短剧可形成完整镜头包。
- 能清楚知道每个镜头处于哪个状态。
- 能替换生图/生视频工具而不重写 planner。

