# AI Collaboration

## 角色分工

### Codex

负责：

- 架构设计。
- 数据合同设计。
- 风险边界。
- 代码审计。
- 测试结果复核。
- 给 Proma 写明确任务。

不负责：

- 在未确认边界时盲目大改代码。
- 直接批量提交付费生成任务。

### Proma

负责：

- 创建项目骨架。
- 实现代码。
- 编写测试。
- 运行测试。
- 根据 Codex review 修复。

执行要求：

- 严格按 `specs/DATA_CONTRACTS.md` 实现。
- 每次改动更新 `CHANGELOG.md`。
- 每次阶段完成更新 `PROJECT_STATUS.json`。
- 不把 Flowith/libTV 逻辑写进核心 planner。

### Zcode

负责：

- 参与代码开发。
- 参与测试实现和测试运行。
- 修复 Codex review 或测试暴露的问题。
- 编写辅助验证工具，例如 schema validator、continuity checker、sample runner。

执行要求：

- 与 Proma 遵守同一数据合同。
- 改动前先读取 `PROJECT_STATUS.json` 和 `HANDOFF.md`。
- 完成后更新 `CHANGELOG.md`、`HANDOFF.md`、`PROJECT_STATUS.json`。
- 不与 Proma 重复改同一块逻辑，除非任务明确要求修复或重构。
- 不绕过 Codex 已明确的边界约束。

### Harness Engineering

负责：

- 把 release 验收变成可重复运行的 smoke / regression harness。
- 验证 wheel 安装、CLI、GUI、fake model、agent scenario、权限门禁。
- 维护 fake provider / fake model server，避免测试依赖真实付费模型。
- 维护 trace replay 和 golden scenario，防止 agent 行为漂移。
- 确认项目可以脱离 AI 开发工具独立运行。

不负责：

- 创意方向判断。
- 生产付费任务授权。
- 修改核心业务边界。
- 替代 Codex 复审。
- 绕过 human approval。

执行要求：

- 只通过正式 CLI/API/测试入口验证项目，不依赖 Codex/Proma/Zcode 的会话状态。
- 不读取或输出 API key 明文。
- 发现边界被绕过时必须 fail closed。
- 每次新增 agent 能力时同步新增 permission / approval / trace replay 场景。

### Human

负责：

- 创意方向确认。
- 角色定妆确认。
- 关键场景确认。
- Web 工具账号和权限。
- 付费任务提交确认。

## 交接规范

每次 AI 交接前必须更新：

- `PROJECT_STATUS.json`
- `HANDOFF.md`
- `CHANGELOG.md`

交接信息必须包括：

- 当前完成了什么。
- 下一步做什么。
- 哪些不能做。
- 有哪些阻塞。
- 测试是否通过。

## 项目状态接口

`PROJECT_STATUS.json` 是新 AI 的第一入口。它应该保持短、准、机器可读。

不要把长篇解释塞进 `PROJECT_STATUS.json`；详细解释放到 docs。
