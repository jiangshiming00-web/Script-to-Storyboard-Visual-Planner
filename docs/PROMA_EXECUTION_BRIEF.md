# Proma Execution Brief

## 当前任务目标

Codex 已复审通过 provider health check / fallback / fail-closed no-residue 修复。请 Proma 进入下一步：**OpenAI / Anthropic adapter skeleton**。

本轮只做 adapter 骨架和配置门槛，不调用真实模型，不新增必需 SDK，不接 executor。

## Next Step: OpenAI / Anthropic Adapter Skeleton

目标：

- 在现有 `planner.providers` 框架下预留 OpenAI / Anthropic provider 形态。
- 每个 adapter 必须实现 `BaseProvider` 的五个规划方法和 `health_check()`。
- 本轮 adapter 默认不可用 / unhealthy，除非配置门槛满足；即使配置满足，也不允许调真实 API。
- `health_check()` 只能检查本地配置、环境变量、可选依赖是否存在，不能发起网络请求、不能登录、不能调用模型、不能产生费用。
- production 仍然 fail-closed：provider unhealthy 时抛 `ProviderUnavailableError`，不 fallback。
- development 若允许 fallback，可以可审计地回退 deterministic，并在 `run_summary.json` 记录 `requested_provider` / `effective_provider` / `fallback_used` / `fallback_reason` / `provider_health`。

建议实现：

```text
planner/providers/
  openai_adapter.py
  anthropic_adapter.py
```

建议命名：

- `openai`
- `anthropic`

建议 health check 行为：

- OpenAI adapter：
  - 检查是否有必要配置，例如 `OPENAI_API_KEY` 或未来明确的 `PLANNER_OPENAI_API_KEY`。
  - 检查可选 SDK 是否可 import，但 SDK 不得加入 `pyproject.toml` 的必需依赖。
  - 缺配置或缺 SDK 时返回 `ProviderHealth(healthy=False, reason=...)`。
- Anthropic adapter：
  - 同理检查 `ANTHROPIC_API_KEY` 或未来明确的 `PLANNER_ANTHROPIC_API_KEY`。
  - 缺配置或缺 SDK 时返回 unhealthy。

规划方法行为：

- 本轮不要调用真实 API。
- 可以直接抛 `ProviderUnavailableError` / `NotImplementedError`，但正常 pipeline 不应走到这些方法，因为 `_select_provider()` 会在 unhealthy 时 fail-closed 或 fallback。
- 错误信息必须清楚说明“adapter skeleton exists but real model calls are not implemented in Phase 1”。

测试要求：

- 现有 51 个测试继续通过。
- 新增测试覆盖：
  - `openai` / `anthropic` provider 能注册进 registry。
  - 缺配置时 `health_check()` 返回 unhealthy，reason 清楚。
  - development 请求 `openai` / `anthropic` 且 fallback 允许时，实际使用 deterministic，`run_summary` 审计字段完整。
  - production 请求 `openai` / `anthropic` 时 fail-closed，不产生 `out_dir` 残留。
  - adapter skeleton 不改变 production executor hard boundary。
  - `pyproject.toml` 不新增 OpenAI / Anthropic SDK 到必需依赖。

禁止范围：

- 不调用真实 LLM API。
- 不新增真实 API key、cookie、账号或 `.env.production`。
- 不把 OpenAI / Anthropic SDK 作为必需依赖。
- 不做 opt-in `probe` 网络探活；probe 是下一轮单独设计。
- 不接 Flowith/libTV/可灵/即梦/ComfyUI。
- 不往仓库根目录 `runs/production/` 写产物。
- 不恢复 `runs/development/.gitkeep` 或 `runs/production/.gitkeep`。

完成后必须更新：

- `CHANGELOG.md`
- `HANDOFF.md`
- `PROJECT_STATUS.json`

并附上：

- 变更摘要。
- 测试命令和结果。
- dev/prod smoke 结果。
- adapter health check 示例输出。
- 仍未实现部分。

---

## 历史当前任务目标

Codex 二次复审已通过。请 Proma 先做收口，再进入下一步。

执行顺序必须是：

1. Phase-1 closeout：只做复审后的微收尾与最终验证。
2. Next step：实现 LLM provider 抽象层的代码结构，但本轮仍不调用真实 LLM。

## Phase-1 Closeout

必须完成：

- 修正 `planner/env.py` 中 `_production_locked_keys()` 的过期注释：实际策略是 production 下 locked key **显式报错 / rejected loudly**，不是 silently ignored。
- 重新运行 `python3 -m pytest`，确认 24 个测试仍全部通过。
- 如需手动验证 production，请把输出写到 `/tmp`、`/private/tmp` 或 CI 临时目录，不要写入仓库根目录 `runs/production/`。
- 更新 `CHANGELOG.md`、`HANDOFF.md`、`PROJECT_STATUS.json`，记录 closeout 已完成。

Closeout 不允许做：

- 不重构业务逻辑。
- 不改 schema 字段。
- 不接入真实模型。
- 不接入 executor 或 Web 自动化。
- 不生成仓库内 production run 产物。

## Next Step: LLM Provider Abstraction

本轮只做 provider 抽象层，不做真实 LLM 调用。

目标：

- 把当前 deterministic 抽取/规划/编译能力包装到 provider 接口后面。
- 为未来 OpenAI / Anthropic / 本地模型等 provider 预留接口。
- 保持现有 CLI、schema、JSON 输出行为兼容。
- 默认 provider 必须仍是 deterministic。

建议实现：

```text
planner/
  providers/
    __init__.py
    base.py
    deterministic.py
    registry.py
```

建议接口职责：

- `build_bibles(script_text, script_id)`
- `extract_beats(script_path, episode_id)`
- `generate_shots(...)`
- `compile_image_prompts(...)`
- `compile_video_prompts(...)`

`pipeline.py` 应通过 provider 调用这些能力，而不是直接绑定具体启发式实现。第一轮可以让 `deterministic` provider 复用现有 `bible.py`、`beats.py`、`shots.py`、`prompts.py`，不要大规模搬代码。

配置建议：

- 在 `config/development.json` 和 `config/production.example.json` 中增加类似 `planner_provider: "deterministic"`。
- 未配置时默认 `deterministic`。
- 未知 provider 必须给清楚错误。
- production 仍必须遵守 `pending_manual_approval`、禁止覆盖、禁止付费提交等硬边界。

测试要求：

- 现有 24 个测试必须继续通过。
- 新增测试覆盖：
  - 默认 provider 是 deterministic。
  - 配置 `planner_provider: "deterministic"` 时输出仍能 validate。
  - 未知 provider 返回清楚错误。
  - provider 抽象不改变 production executor 状态和 `tool=None` 约定。

禁止范围：

- 不调用真实 LLM API。
- 不新增真实 API key、cookie、账号或 `.env.production`。
- 不把 OpenAI / Anthropic / 任何模型 SDK 作为必需依赖。
- 不把生成任务直接放入 `pending` 队列。
- 不接入 Flowith/libTV/可灵/即梦/ComfyUI。

完成后必须更新：

- `CHANGELOG.md`
- `HANDOFF.md`
- `PROJECT_STATUS.json`

并附上：

- 变更摘要。
- 运行命令。
- 测试命令。
- 测试结果。
- 仍未实现部分。

---

## 历史任务目标

请基于当前文档创建项目初始代码骨架，实现第一阶段 Planner MVP。

Zcode 也会参与开发和测试。Proma 与 Zcode 必须共享同一数据合同和状态文件，避免重复实现或互相覆盖。

本轮是 Proma 第一阶段第一步：先创建可运行、可测试、可扩展的项目骨架。重点不是模型效果，而是目录、配置、命令、schema、测试和环境隔离要正确。

## 必须先读

1. `README.md`
2. `docs/ARCHITECTURE.md`
3. `specs/DATA_CONTRACTS.md`
4. `docs/ROADMAP.md`
5. `PROJECT_STATUS.json`

## 实现范围

第一轮只做本地 planner，不做 Web 自动化。

需要实现：

- 项目基础目录。
- 开发环境与生产环境隔离。
- 剧本输入读取。
- schema 类型定义。
- JSON 输出目录。
- 一个 sample script。
- 一个 dry-run 命令。
- 基础测试。

## 环境隔离要求

必须用同一套代码，通过环境参数切换开发与生产，不要复制两套代码。

建议支持：

```bash
planner run --env development --script data/development/input_scripts/sample_ep01.txt --out runs/development/sample_ep01
planner validate --env development --run runs/development/sample_ep01
```

需要创建或预留：

```text
config/
  development.json
  production.example.json
data/
  development/
    input_scripts/
    bibles/
    shots/
    prompts/
    manifests/
  production/
    input_scripts/
    bibles/
    shots/
    prompts/
    manifests/
assets/
  development/
    reference/
    images/
    videos/
  production/
    reference/
    images/
    videos/
runs/
  development/
  production/
logs/
  development/
  production/
```

生产环境边界：

- `production` 默认不允许覆盖已有 run。
- `production` 不要放真实密钥、cookie、账号信息。
- `production` executor task 默认停在 `pending_manual_approval`。
- 本轮不要提交任何付费生成任务。
- 如需环境变量，只提交 `.env.example`，不要提交 `.env.production`。

建议 CLI：

```bash
planner run --env development --script data/development/input_scripts/sample_ep01.txt --out runs/development/sample_ep01
planner validate --env development --run runs/development/sample_ep01
```

## 核心产物

运行后至少输出：

```text
runs/development/sample_ep01/
  script_parse.json
  character_bible.json
  location_bible.json
  prop_bible.json
  story_beats.json
  shot_list.json
  image_prompts.json
  video_prompts.json
  asset_manifest.json
```

## 实现边界

必须遵守：

- 不接入 Flowith/libTV。
- 不做浏览器自动化。
- 不做付费任务提交。
- 不把 Web 工具字段写死进核心 schema。
- executor 只能作为接口或空实现。

## 与 Zcode 协作

- 如果 Zcode 已经实现某个模块，请先 review 现有实现再继续。
- 如果 Proma 负责主流程，Zcode 可优先负责测试、schema validation、continuity audit 或 bugfix。
- 每次完成后都要更新 `PROJECT_STATUS.json`，说明当前负责的模块和测试状态。
- 不要在没有说明的情况下覆盖 Zcode 的改动。

## 测试要求

至少覆盖：

- 样例剧本可读取。
- 输出 JSON 文件齐全。
- 每个 shot 引用已存在的 character/location/prop id。
- prompt 中包含人物、场景、道具、镜头语言。
- schema validation 失败时返回清楚错误。

## 完成后更新

完成实现后请更新：

- `CHANGELOG.md`
- `HANDOFF.md`
- `PROJECT_STATUS.json`

并附上：

- 运行命令。
- 测试命令。
- 测试结果。
- 仍未实现的部分。
