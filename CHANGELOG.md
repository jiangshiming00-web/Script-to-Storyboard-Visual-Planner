# Changelog

所有重要项目变更都记录在这里。格式遵循"日期 - 变更 - 影响 - 验证状态"。

## 2026-07-14 (Proma Phase 3 P2 - review-run 完整实现)

### Background

Phase 3 P1.6 status cleanup 通过 Codex 第四轮复审后放行进入 Phase 3 P2。P2 从 `review-run` 完整实现起步（review-batch 继续留 stub）。双轮验证流程：Explore SubAgent 收集 planner/agent/ 结构 + harness 契约 + bibles/prompts 数据结构 + 红线；Plan SubAgent 独立设计验证（4 规则 + graceful + 不委托 validate_run + cross-episode 语义澄清）；主会话整合 + 独立判断（`_render_markdown` 用 title 参数而非 key 推断、graceful code 复用 diagnose）。

### 实现

- 新增 `planner/agent/review.py`：`ReviewRunReport` model + `review_run_dir()` engine + 4 规则（rv1 header-bible 双向比对 / rv2 video 字段 / rv3 placeholder error / rv4 shot_id 对齐）+ graceful degradation + redact 出口（`_add_finding` 统一 `_safe_text`）。
- 修改 `planner/agent/cli.py`：`review_run_cmd` 从 stub 改为调 engine，加 `--expected-env`/`--format`/`--verbose`；`_render_markdown` 泛化（title 参数 + version 兼容 diagnose_version/review_version）。review-batch 仍 stub。
- 修改 `harness/agent_scenarios/run_all.py`：review-run replay 从 stub 断言改为 full 断言（implementation_status=full + review_version=1.0 + 非空 tool_invocations + 每条 finding 有 evidence）。
- 修改 `harness/agent_scenarios/review_prompt_refs.json`：checks 加 shot_id_alignment，assertions 加 review_version。
- 新增 `tests/test_agent_review.py`（26 engine 测试）+ 修改 `tests/test_agent_cli.py`（+6 CLI 测试）。

### cross-episode 语义

review-run 是单 run 内 prompt-bible 一致性检查；cross-episode 留给 review-batch。PROJECT_STATUS 的 feature track 名含 "cross_episode" 是命名，不是 review-run 功能范围。

### 红线守门

- `pyproject.toml [project]` 基础依赖未动：仍只 `pydantic + click`。
- 379 pytest（347 + 26 review + 6 cli），零回归。
- 仓库 `runs/` 仍只含根 `.gitkeep`。
- production fail-closed + redact + read-only 全部保留。

### 验证

- `python3 -m pytest` -- 379 passed, 2 warnings。
- `python3 harness/agent_scenarios/run_all.py` -- 7 scenarios 全过（review-run full replay）。
- review-run smoke 对真 dev run：status=ok, impl=full, 8 tool calls, 0 findings, counts 正确。

## 2026-07-14 (Proma Phase 3 P1.6 status cleanup round 3 - Codex 手工复审第三轮 4 findings 收口)

### Background

用户（shiming jiang）作为 Codex 手工对手方对 Phase 3 P1.6 status cleanup（commit `afea771`）做**第三轮手工 Codex 复审**。verdict **暂不建议进入下一步**：4 个 stale 点方向对但状态入口未完全收口。本轮按"很小的 P3/P2 收口"原则全部修齐。

### Findings -> Fixes

#### P2：`PROJECT_STATUS.json` `next_actions` 仍混有大量已完成事项

**Bug**: `next_actions`（机器可读入口）含 61 条，其中大量已完成项（旧 review / Phase 2 harness / P1/P1.5 落地项 / `phase3_p1_339_pytest` / phase0 baseline 等），会误导 Proma/Zcode 下一步。

**Fix**: 精简到 15 条未完成项（Phase 3 P2 五项 + phase0 git push + opt-in probe + core3/core4/core6/pkg1/ci1/zcode/phase3 executor adapter）。`completed_steps` 末尾补 3 条 phase3 里程碑（P1 骨架落地 / P1.5+P1.6 两轮复审 / round3 status cleanup）。

#### P3：`PROJECT_STATUS.json` agent 测试清单与 verification 数字 stale

**Bug**: `artifacts.tests` 清单写 `test_agent_diagnose.py (22 tests...)`，`verification.v10_phase3_p1_test_count` 写 `27 test_agent_diagnose`；上一轮 P1.6 CHANGELOG 拆分写 `5 redact / 25 diagnose` 也错。`pytest --collect-only` ground truth：redact 17 / readers 13 / tools 9 / diagnose 29 / cli 7。

**Fix**: 清单 5 行数字同步到 17/13/9/29/7 + 描述微调；verification 拆分改 `271 baseline + 17 redact + 13 readers + 9 tools + 29 diagnose + 7 cli + 1 boundaries = 347`（无需 overlap 修正，精确等于 347）。

#### P3：`HANDOFF.md` 仍写 339 pytest + 把"复审 Phase 3 P1"列为下一轮

**Bug**: `HANDOFF.md` 写 `339 pytest = 271 + 68`，且"下一轮"第一条仍是"复审 Phase 3 P1"--与当前复审状态冲突（P1/P1.5/P1.6 已复审完）。

**Fix**: 标题改 `2026-07-14 + P1/P1.5/P1.6 + status cleanup`；首段补"P1.5/P1.6 三轮 Codex 手工复审均已通过；347 pytest 全绿"；测试数改 `347 = 271 + 76`；"下一轮"删除"复审 Phase 3 P1"条目，保留 Phase 3 P2 / phase0 push / opt-in probe。

#### P3 / 卫生项：`tests/test_boundaries.py` symlink 测试在真实仓库 `runs/` 下残留 `linked_target`

**Bug**: `test_is_inside_repo_helper` 的 `link_target = project_root / "runs" / "linked_target"` + `mkdir` 会在真实仓库 `runs/` 下创建目录，跑完残留（被 `.gitignore` 忽略故 `git status` 干净，但违背"`runs/` 只保留根 `.gitkeep`"红线描述）。

**Fix**: `link_target` 改用 repo 内已存在路径（`project_root` 本身），删 `mkdir`。`is_inside_repo` docstring 明确 path 不存在会抛 OSError，故 target 必须预先存在；复用 repo 根既满足语义（symlink 指向 repo 内 -> resolve 跟随 -> inside）又不产生残留。手动删除磁盘上已残留的 `runs/linked_target`。

### Verification

- `python3 -m pytest tests/test_boundaries.py` -- 11 passed，`runs/` 仅剩 `.gitkeep`。
- `python3 -m pytest --collect-only -q` -- 347 tests collected（agent: redact 17 / readers 13 / tools 9 / diagnose 29 / cli 7 = 75；+ 271 baseline + 1 phase3 boundaries）。
- `PROJECT_STATUS.json` -- JSON 合法；`next_actions` 15 条；拆分总和 347。
- `git status` -- 干净（`runs/linked_target` 已删且不再生成）。

### 红线守门

- `pyproject.toml [project]` 基础依赖未动：仍只 `pydantic + click`。
- 仓库 `runs/` 仍只含根 `.gitkeep`（symlink 测试不再残留）。
- production fail-closed contract 保留（本轮只改文档/状态/测试卫生，零代码逻辑改动）。
- API key redaction 出口未动（本轮无 diagnose/redact 代码改动）。

## 2026-07-13 (Proma Phase 3 P1.6 — Codex 手工复审第二轮 5 findings 修齐)

### Background

按 `agent-roles.md` 双层结构，用户（shiming jiang）作为 Codex 手工对手方对 Phase 3 P1.5 做**第二次手工 Codex 复审**。命中 1 个**残留 P1（红线）** + 1 个 P2（harness 覆盖不全） + 3 个 P3（文档/状态 stale）。本轮全部按"本轮顺手补"原则修齐（含 P1 必修），交付用户第三次手工 Codex 复审。

### Findings → Fixes

#### P1 (红线)：secret redaction 仍漏 `provider_runtime` + R8 dev message 出口

**Bug**: `planner/agent/diagnose.py:678` 的 `ProviderRuntimeSummary(model=, base_url=, api_key_env=, ...)` 4 字段原样复制，没走 redact。`planner/agent/diagnose.py:423` 的 R8 dev 分支 message 直接输出 `api_key_env` env var name（如 `'PLANNER_OPENAI_API_KEY'`）。

**Fix**:
- `planner/agent/diagnose.py` `ProviderRuntimeSummary` 4 字段：3 个字符串字段（model / base_url / api_key_env）全部走 `_safe_text`（bool 字段 `enable_real_model_calls` 不需要 redact）
- `planner/agent/diagnose.py` R8 dev 分支 message：`f"api_key_env={api_key_env!r} "` → `f"api_key_env={_safe_text(repr(api_key_env))} "`

**Tests**（2 个新 test 钉住 behavior）：
- `tests/test_agent_diagnose.py::test_provider_runtime_fields_are_redacted_in_report` — 注入 3 个 token（`sk-runtime-model-secret-...` / `Bearer RUNTIMESECRET-...` URL query / `sk-runtime-env-secret-...` env var name）→ 断言 serialized report 不含 raw token + runtime 字段都被 redact
- `tests/test_agent_diagnose.py::test_r8_dev_message_redacts_api_key_env_name` — env var name 故意含 `sk-leak-...` 子串 → 断言 R8 message 含 `<redacted>` 不含 raw leak

#### P2：harness secret 注入只覆盖 `fallback_reason` + `provider_health`，没覆盖 `provider_runtime`

**Bug**: `diagnose_secret_redaction` scenario 第一轮注入的 secret 集合是 `{fallback_reason, provider_health.*.reason, provider_health.*.details}`。所以这次残留 P1（ProviderRuntimeSummary 出口）没被 harness 抓到——harness 跑完仍 pass。

**Fix**: `harness/agent_scenarios/run_all.py` `validate_live_agent_replay()` secret_redaction 分支：
- 新增 3 个 fake token：`runtime_model = "sk-runtime-model-secret-replay-..."` / `runtime_url = "https://...?key=Bearer RUNTIMESECRET-..."` / `runtime_env = "PLANNER_OPENAI_API_KEY_with_sk-runtime-env-secret-..."`
- 注入到 `summary["provider_runtime"]`（model / base_url / api_key_env / enable_real_model_calls=True）
- stdout / `--write-report` 文件 / stderr 三个出口的 needle 断言从 `(secret, other)` 扩到 `(secret, other, *extra_needles)`
- 这样新增任何 exit 表面忘记 redact，harness 会立即 fail

#### P3-1：`harness/agent_scenarios/run_all.py:16` 顶部 docstring 矛盾

**Bug**: docstring 写 "We do NOT call into an agent (the product-side agent is out of scope for v1.0)"，但 `validate_live_agent_replay()` 已经真跑 agent CLI。

**Fix**: 重写 docstring 第 2 步为 "Static + live cross-check"，新增第 3 步 "Live agent replay (added Phase 3 P1.5 — round-2 Codex review)"，明确写三个分支：diagnose_* / diagnose_secret_redaction / review_* 的具体断言。

#### P3-2：`PROJECT_STATUS.json` `v10_phase2_no_product_agent` 与现状矛盾

**Bug**: line 538 `v10_phase2_no_product_agent: "pass (planner/agent/ 不存在; harness 仅固化场景 + 校验 shape)"`——planner/agent/ 在 Phase 3 P1 已落地，但这条 verification 字段没更新。

**Fix**: 改写为 "historical, pre-Phase 3 P1; planner/agent/ now exists with 6 flat files + 13 rules + 3 Click subcommands; see v10_phase3_p1_agent_module_landed for current state"。

#### P3-3：`PROJECT_STATUS.json` pytest 数字 stale

**Bug**: `v10_phase3_p1_test_count: "pass (339 pytest = ...)"` —— 当前是 347（345 + 2 个 P1.6 tests）。

**Fix**: 改写为 "pass (347 pytest after P1.6 fix = 271 baseline + 5 test_agent_redact + 12 test_agent_readers + 9 test_agent_tools + 25 test_agent_diagnose + 7 test_agent_cli + 1 added test_boundaries + ~15 baseline overlap; 0 regression)"。`completed_steps` line 243 的 `phase3_p1_339_pytest_passed_...` 历史快照保留（描述当时 P1 那个 commit 的状态）。

### Verification

- `python3 -m pytest` —— **347 passed, 2 warnings in 20.73s**（345 + 2 新 P1.6 tests；0 回归）
- `python3 harness/agent_scenarios/run_all.py` —— **7 scenarios 全过**（每个含 live cross-check + live agent replay 两轮），`diagnose_secret_redaction` 现在能 catch `provider_runtime` 出口的 secret leak
- e2e: 注入 7 个 fake secret（fallback_reason + provider_health.* + provider_runtime.*）的 sample run + diagnose + `--write-report`：stdout / 文件 / stderr 三个出口全部 redact ✓

### Files Changed

#### 修改（3 个）

- `planner/agent/diagnose.py` — 5 处 redact（ProviderRuntimeSummary 4 字段 + R8 dev message 1 字段）
- `harness/agent_scenarios/run_all.py` — `validate_live_agent_replay` 注入 provider_runtime + 顶部 docstring 重写
- `tests/test_agent_diagnose.py` — 2 个新 P1.6 redact tests
- `PROJECT_STATUS.json` — 2 处 stale 修正（v10_phase2_no_product_agent 上下文 + pytest 347）
- `CHANGELOG.md` — 本段

#### 未改动

- `planner/agent/redact.py`（regex 已正确；只是漏调用）
- `planner/agent/{readers,tools,__init__,cli}.py`（无 secret 字段直接复制）
- `HANDOFF.md`（本轮是 fix-up；状态描述仍准确）

### Outstanding / Still TODO

- 不进 Phase 3 P2。等用户第三次手工 Codex 复审放行。
- Phase 0 git push to GitHub：仍 blocked on user URL。
- opt-in probe + Phase Core-3 跨集连续性 + pkg/CI 路线：不变。

## 2026-07-13 (Proma Phase 3 P1.5 — Codex 手工复审 4 findings 修齐)

### Background

按 `agent-roles.md` 顶部（2026-07-13 强化版）的双层结构，用户（shiming jiang）作为 Codex 手工对手方对 Phase 3 P1 做了**手动 Codex 复审**（不在 Proma 内部、用 Codex 工具直接对代码 + scenario + 项目状态扫描），命中 4 findings（1 P1 红线 + 1 P2 + 2 P3）。本轮全部按"本轮顺手补"原则处理（含 P1 必修），交付用户第二次手工 Codex 复审前一次性补齐。

### Findings → Fixes

#### P1 (红线)：Agent 报告 secret redact 不完整

**Bug**: `planner/agent/diagnose.py:679` 直接复制 `summary.get("fallback_reason")` 到 `provider.fallback_reason`，没走 `redact_secrets_text`。`HealthRecord.reason` / `HealthRecord.details` value / `ValidationSummary.errors` / `ValidationSummary.warnings` 同样直接复制。`--write-report` 写的 dict 含这些字段，stderr 打印这些字段——所有出口都泄漏 secret。

**Fix**: 5 处 redact 全部到位：
- `planner/agent/diagnose.py` `ProviderSummary(fallback_reason=_safe_text(...))`
- `planner/agent/diagnose.py` `HealthRecord(reason=_safe_text(...), details={k: redact_secrets_text(str(v)) ...})`
- `planner/agent/diagnose.py` `ValidationSummary(errors=[_safe_text(e) ...], warnings=[_safe_text(w) ...])`

**Tests**（3 个新 test 钉住 behavior）：
- `tests/test_agent_diagnose.py::test_provider_fallback_reason_is_redacted_in_report`
- `tests/test_agent_diagnose.py::test_provider_health_reason_and_details_are_redacted_in_report`
- `tests/test_agent_diagnose.py::test_validation_errors_warnings_are_redacted_in_report`

每个 test 注入 fake Bearer + sk- secret，断言 serialized report 不含 raw secret。

#### P2：Harness scenarios 不真跑 agent CLI

**Bug**: `harness/agent_scenarios/run_all.py:16` 明确说"does not call into an agent"，只 cross-check artifact 存在性。所以 `diagnose_secret_redaction.json` 通过了，但实际 agent 在 `provider.fallback_reason` 泄漏 secret。

**Fix**: 新增 `validate_live_agent_replay()` 函数（`harness/agent_scenarios/run_all.py:204-340`）：
- 对每个 `diagnose_*` scenario：跑 `python -m planner agent diagnose <sample_run_dir>`，断言 exit code 0 + stdout JSON 合法 + `implementation_status="full"`
- 对 `diagnose_secret_redaction` 特殊处理：注入 `Bearer eyJ...` 到 `fallback_reason` + `sk-proj-...` 到 `provider_health.*.details`，跑两次 diagnose（一次 stdout、一次 `--write-report`），断言 stdout / 文件 / stderr 都不含 raw secret
- 对 `review_prompt_refs` / `batch_continuity`：跑 stub (`review-run` / `review-batch`)，断言 `implementation_status="not_implemented"` + `tool_invocations=[]`
- `approval_required_write` 仍是 shape-only（已由 `validate_approval_gate_shape` 覆盖）

现在每个 scenario **两轮验证**：先 artifact existence (`validate_live_cross_check`)，再真跑 agent (`validate_live_agent_replay`)。P1 这种运行时缺陷会被第二轮抓到。

#### P3-1：PROJECT_STATUS.json 数字 stale + next_actions 噪音

**Bug**: status string 含 `339_tests_stable`（实际 342 / 现在 345）。`next_actions` 段保留 9 条 v1.0 RC P1 fix 风格条目（已完成但未清出）。

**Fix**:
- `status` → `v10_phase3_p1_product_agent_skeleton_read_only_complete_with_p1_5_redact_harness_replay_fix`
- 清 9 条过时 next-action（`proma_read_docs_*` / `fix_p1_*` / `fix_p2_*` / `add_*` / `codex_review_v10_phase2_*`）
- 加 4 条 Phase 3 P1.5 next-action 锚点（`phase3_p1_5_*`）
- `completed_steps` 总数保持 103（"清除"只是把条目从 next_actions 移到 completed_steps 概念上，实际未增加 step；用户偏好"已完成的应该已经在 completed_steps 里"——之前手工补，这次只清 next_actions）

#### P3-2：`_build_summary_zh` 文档说"no emojis"但 emit `⚠`

**Fix**: `planner/agent/diagnose.py:832 + :839` `⚠` → `[RED LINE]`。docstring "no emojis, no exclamation marks" 现在与实际一致。

### Verification

- `python3 -m pytest` —— **345 passed**（342 + 3 新 redact test；0 回归）
- `python3 harness/agent_scenarios/run_all.py` —— **7 scenarios 全过** + **6 真跑 agent CLI replay 全过**（P1 secret leak 现在会被 `diagnose_secret_redaction` 抓）
- e2e smoke: `python3 -m planner agent diagnose <modified-run-with-secrets>` 现在 stdout / `--write-report` 文件 / stderr 都 redact secret
- git 工作区准备 commit；baseline `91b3d2a` + Phase 3 P1 `b8a8f57` / `b178571` 已固化

### Files Changed

#### 修改（4 个）

- `planner/agent/diagnose.py` — 5 处 redact + 2 处 `⚠` → `[RED LINE]`
- `harness/agent_scenarios/run_all.py` — 新增 `validate_live_agent_replay` + main 接入
- `tests/test_agent_diagnose.py` — 3 个新 redact test
- `PROJECT_STATUS.json` — status 更新 + next_actions 清 9 条过时 + 加 4 条 P1.5 锚点

#### 未改动

- `planner/agent/redact.py`（regex 已正确，复用即可）
- `planner/agent/readers.py` / `tools.py` / `__init__.py` / `cli.py`（无 secret 字段直接复制）
- `harness/agent_scenarios/*.json`（shape 不变；replay 在 Python 端注入 secret）
- `CHANGELOG.md`（本段即是）
- `HANDOFF.md`（本轮是 fix-up 不影响 Phase 3 P1 主线；状态描述仍然准确）

### Outstanding / Still TODO

- 不进 Phase 3 P2。等用户第二次手工 Codex 复审放行后再决定（Phase 3 P2 候选：review-run 完整实现 / review-batch 完整实现 / GUI agent 面板）。
- Phase 0 git push to GitHub：仍 blocked on user URL。
- opt-in probe + Phase Core-3 跨集连续性 + pkg/CI 路线：不变。

## 2026-07-13 (Proma Phase 0 + Phase 3 P1 — git baseline + 产品内只读 Agent 最小骨架)

### Background

按 `docs/PROMA_V1_REVIEW_AGENT_HARNESS_PLAN.md` 第三部分（产品内 agent read-only / guided）首次落地，配套完成 Phase 0 (git init + baseline)。本轮交付：

1. **Phase 0**：仓库从无 git → 2 个 commit (`b9f8dc9` + `91b3d2a`)，112 个文件 tracked；`.gitignore` 覆盖 runs/ + *.egg-info + build/ + .DS_Store + __pycache__ + `config/production.json` 等噪音与敏感文件；保留 runs/.gitkeep + 18 个 subdirectory .gitkeep（用 `git add -f` 绕过 .gitignore，因为 git 的 `!` re-include 不支持 `**`）。
2. **`planner/agent/` 子包**：flat 6 文件布局，无 sub-package；6 个 read-only 工具；13 条诊断规则；3 个 Click 子命令（diagnose 完整 + review-run/review-batch stub）；约 380 行实现 + 339 pytest。
3. **3 个新 harness scenario**：`diagnose_fallback_used` / `diagnose_partial_run` / `diagnose_secret_redaction`，与现有 4 个 scenario 共 7 个全部通过 `python3 harness/agent_scenarios/run_all.py`。

### Added

#### Phase 0: git baseline

1. **`.gitignore`**（首次系统化写）：覆盖 Python 工具链 + IDE/OS + pytest 缓存 + env/secrets + project-specific（`runs/*` + `logs/**` + `assets/**` + `data/**` + production config 排除）。`.env.example` + `samples/v1/*.txt` + 19 个 `.gitkeep` 通过 `git add -f` 强制 add。
2. **`git init -b main`**：项目根初始化 git 仓库；`git config user.name "shiming jiang"` + `user.email "shimingjiang@users.noreply.localhost"`（项目本地，不污染 global）。
3. **2 个 commit**：`b9f8dc9` (init .gitignore) + `91b3d2a` (baseline v1.0 + Phase 2，112 文件)。HANDOFF.md / PROJECT_STATUS.json 三件套留 baseline commit hash 用于后续复审 / 回滚。

#### `planner/agent/` 子包（Phase 3 P1 最小骨架）

4. **`planner/agent/redact.py`**（~50 行）：4 条 secret regex（Bearer / sk- / sk-ant- / gho_），**Anthropic 在 OpenAI 之前匹配**（因为 sk-ant- 前缀会被 sk- 错匹配）。UUID / 短串 / 普通词不会被误命中。复制自 `planner/providers/openai_compatible_adapter.py:177-188` 的 `_redact_secrets`。
5. **`planner/agent/readers.py`**（~110 行）：5 个 graceful reader（`load_run_summary` / `load_artifact` / `list_artifacts` / `load_batch_summary` / `list_runs_in_batch`）。`load_*_summary` 返回 `(data, error)` 元组（缺/坏 JSON → None + 错误信息），`load_artifact` 抛 `ValueError` / `FileNotFoundError`（让 diagnose 出 finding）。50 MB size cap。
6. **`planner/agent/tools.py`**（~110 行）：6 个 read-only tool（`read_run_summary` / `list_artifacts` / `read_artifact` / `validate_run` / `read_batch_summary` / `list_runs_in_batch`）+ `TOOL_REGISTRY` + `TOOL_ARTIFACT_MAP`（必须与 `harness/agent_scenarios/run_all.py:_TOOL_ARTIFACT_MAP` 同步——PR 改任一边必须改两边）。
7. **`planner/agent/diagnose.py`**（~580 行）：核心引擎。Pydantic models (`DiagnoseReport` / `DiagnoseFinding` / `EvidenceRef` / `ToolInvocation` / `ProviderSummary` / `ProviderRuntimeSummary` / `ValidationSummary` / `HealthRecord`) + 13 条规则 + 中文 `_build_summary_zh` + `build_not_implemented_report`（stub factory）+ `diagnose_run_dir` 入口（graceful degradation）。
8. **`planner/agent/cli.py`**（~270 行）：Click group `agent` + 3 子命令。`--write-report` 走 `_check_and_write_report` 政策（production + 仓内 → rc=2 + 不写；dev + 仓内 → warn + 写；run_summary 缺失 env 默认 production fail-closed）。
9. **`planner/agent/__init__.py`**（~50 行）：导出 `diagnose_run_dir` / `build_not_implemented_report` / 全部 Pydantic models / `TOOL_REGISTRY` / `TOOL_ARTIFACT_MAP`。
10. **`planner/cli.py`** 改 ~10 行：顶部 import `from .agent.cli import agent_group` + `@cli.group()` 后 `cli.add_command(agent_group)`。

#### 13 条诊断规则（Phase 3 P1）

| # | code | severity | 实现方式 | 说明 |
|---|---|---|---|---|
| R1 | `production_fallback_used` | error | **委托 validate_run** | production run 用 provider fallback = 红线违规 |
| R2 | `dev_fallback_used` | warning | 独立 | development run 用 fallback 是 expected，不是 error |
| R3 | `all_providers_unhealthy` | warning | 独立 | provider_health 全 false |
| R4 | `executor_tool_hardcoded` | error | 独立（红线） | executor_tasks[].tool != None 触发 |
| R5 | `env_mismatch` | warning | **委托 validate_run** | run_summary.env ≠ expected_env |
| R6 | `script_source_mismatch` | error | **委托 validate_run** | script_parse.source_path ≠ run_summary.script |
| R7 | `production_executor_status_wrong` | error | 独立 | production + executor_status ≠ pending_manual_approval |
| R8 | `api_key_env_unset` | warning | 独立 | runtime.api_key_env 声明但 os.environ 为空；**production 下 message 不 echo env var 名** |
| R9 | `real_calls_disabled_but_not_deterministic` | warning | 独立 | runtime.enable_real_model_calls=False 但 effective ≠ deterministic |
| R10 | `missing_run_summary` | error | 入口处理 | run_summary.json 不存在 |
| R11 | `corrupted_run_summary` | error | 入口处理 | run_summary.json 坏 JSON（graceful，不抛） |
| R12 | `partial_run_missing_artifact` | warning | 独立 | run 已 done 但 ≥1 核心 artifact 缺失；**列出哪个缺 + 不下结论 + 不重建** |
| R13 | `image_prompts_count_mismatch` / `video_prompts_count_mismatch` | warning | 独立 | counts.shots 与 counts.image/video 不一致 |

#### 新 pytest（5 个文件 + 1 个追加）

11. **`tests/test_agent_redact.py`**（5 tests）：4 条 regex 各覆盖 + UUID/短串不误命中 + Bearer 保留前缀 + 多 secret 同字符串。
12. **`tests/test_agent_readers.py`**（12 tests）：缺文件 / 坏 JSON / 正常 / size cap / 未知 name / batch 子目录过滤。
13. **`tests/test_agent_tools.py`**（8 tests）：6 个 tool + TOOL_REGISTRY/TOOL_ARTIFACT_MAP 键对齐 + KeyError 契约 + validate_run 委托（monkeypatch `planner.validate.validate_run`）。
14. **`tests/test_agent_diagnose.py`**（22 tests）：13 条规则每条至少 1 个 fixture + dev/prod 矩阵 + status 推导 + stub `tool_invocations=[]`。
15. **`tests/test_agent_cli.py`**（7 tests, subprocess）：diagnose exit 0 + missing dir exit 2 + --write-report /tmp + dev 仓内 warn + production 仓内 rc=2 + stubs rc=0 + `--help` 正常。
16. **`tests/test_boundaries.py`** 追加 1 条：`test_agent_cli_does_not_leak_traceback`（subprocess 跑 fail 路径，stderr 不含 `Traceback`，镜像 `test_cli_friendly_error_when_production_config_missing` 精神）。

#### 3 个新 harness scenario

17. **`harness/agent_scenarios/diagnose_fallback_used.json`**：覆盖 R1 + R2 + redaction of fallback_reason。
18. **`harness/agent_scenarios/diagnose_partial_run.json`**：覆盖 R10 + R12；显式 forbid `recreate_missing_artifact` / `delete_partial_run`。
19. **`harness/agent_scenarios/diagnose_secret_redaction.json`**：覆盖 redact 出口（stdout / stderr / --write-report / finding message）；显式 forbid `read_api_key_value` / `echo_secret_to_*`。

### Hard-Boundary Preservation

- `pyproject.toml [project]` 基础依赖**未动**：仍只 `pydantic + click`。`planner/agent/` 不引入新 SDK / LLM / 网络依赖。
- `pip install -e .` 基础安装**未回归**：339 pytest = 271 baseline + 68 新增。
- production fail-closed contract 保留：R1/R4/R7 三条红线独立 emit error；`--write-report` 在 production + 仓内 → hard refuse rc=2（用 `is_inside_repo` 共享 helper）。
- `executor_tasks.json.tool` 仍 None：agent 不重写 executor，不接 Flowith/libTV/可灵/即梦/ComfyUI。R4 是 error 类规则，任何 plugin 误填 tool 都被立即发现。
- API key 永不写盘：`run_summary.json` 只存 `api_key_env` 名；R8 在 production 下 message sanitization；redact 模块覆盖所有 finding / summary / `--write-report` 出口。
- GUI 与 agent 进程隔离：agent 不查 `planner/web/run_registry.py`（in-memory dict）。CLI 拒绝查 run_id，统一要求路径。
- 仓库 `runs/` 仍只含根 `.gitkeep`：所有 smoke 产物走 `/tmp`；harness scenarios 用 `tempfile.mkdtemp` 自动清理。

### Verification

- `python3 -m pytest` —— **339 passed, 2 warnings in 16.82s**（271 baseline + 5 + 12 + 8 + 22 + 7 + 1 边界追加）。
- `python3 harness/agent_scenarios/run_all.py` —— **7 scenario 全过**（4 旧 + 3 新）。
- e2e smoke（手测 + 自动化）：
  ```
  $ python3 -m planner agent diagnose /tmp/sample-run
  exit=0; status=ok; findings=0  (dev run 一切正常)
  
  $ python3 -m planner agent diagnose /tmp/sample-run --write-report runs/test.json
  exit=0; stderr yellow WARNING; runs/test.json 落盘 1662 bytes
  
  $ python3 -m planner agent diagnose /tmp/prod-run --write-report runs/test-prod.json
  exit=2; stderr "production diagnose refuses to write inside the project repository";
         runs/test-prod.json 不存在（hard refuse 不留残留）
  
  $ python3 -m planner agent review-run /tmp/sample-run
  exit=0; implementation_status=not_implemented; tool_invocations=[]
  
  $ python3 -m planner agent diagnose /no/such/path
  exit=2; stderr 无 Traceback（友好 Click Usage 消息）
  ```
- git baseline 验证：
  ```
  $ git log --oneline
  91b3d2a Phase 0: baseline v1.0 + Phase 2 Harness Engineering
  b9f8dc9 Phase 0: init repo with .gitignore
  
  $ git status
  On branch main
  nothing to commit, working tree clean
  ```

### Files Changed

#### 新增（11 个文件）

- `.gitignore`
- `planner/agent/__init__.py`
- `planner/agent/cli.py`
- `planner/agent/redact.py`
- `planner/agent/readers.py`
- `planner/agent/diagnose.py`
- `planner/agent/tools.py`
- `tests/test_agent_redact.py`
- `tests/test_agent_readers.py`
- `tests/test_agent_tools.py`
- `tests/test_agent_diagnose.py`
- `tests/test_agent_cli.py`
- `harness/agent_scenarios/diagnose_fallback_used.json`
- `harness/agent_scenarios/diagnose_partial_run.json`
- `harness/agent_scenarios/diagnose_secret_redaction.json`

#### 修改（2 个文件）

- `planner/cli.py`（顶部 import agent_group + add_command）
- `tests/test_boundaries.py`（追加 1 条 traceback-leak 测试）

### Outstanding / Still TODO

- 仍按 Phase 3 P1 严格不做：调任何 LLM / 接 executor / arbitrary shell / 把 production.json commit / 在 production 下静默放宽红线。
- Phase 3 P2 候选（harness scenarios 已固化）：
  - `review-run` 完整实现（跨 shot 间 prompt 一致性 + character bible 比对）
  - `review-batch` 完整实现（跨 episode 共享人物 / 场景 / 道具连续性）
  - harness scenarios 加 `review_run_implementation` / `review_batch_implementation` 覆盖新实现
- 不在 Phase 3 P1 范围（保留下一轮）：
  - opt-in `probe`（与 `health_check` 严格分离）
  - Phase Core-3 跨集连续性（bible merge）
  - GUI agent 面板（按"核心先于壳层"原则留到最后）
  - PyInstaller / GitHub Actions release

### Next（待 review 子会话复审）

- 派 review 子会话（扮演 Codex-style 角色，对 `docs/PROMA_V1_REVIEW_AGENT_HARNESS_PLAN.md` 第三部分的落地做对抗式独立审查）
- 重点核对：read-only 边界 / `--write-report` 政策是否破得了 / redact 是否覆盖所有出口 / 13 条规则与 validate_run 复用切分 / stub 真没读 / harness scenario 真没漂移 / git baseline 干净
- 复审 verdict → 主会话修齐 → 用户主对话 verdict 放行 Phase 3 P2

## 2026-07-13 (Proma v1.0 RC P1/P2 修复 - model config 接入 / GUI 闭环 / launcher 修复)

### Background

按 `docs/PROMA_V1_REVIEW_AGENT_HARNESS_PLAN.md` 第一/二部分，修复 v1.0 复审发现的 P1/P2 产品使用缺口。**第三部分（产品内置 agent）和 Harness 脚本不做**（用户明确要求只更新协作文档 + 预留验收项）。

### P1 修复

1. **P1-1 model config 接入运行链路**（`planner/providers/base.py` / `registry.py` / `pipeline.py` / `cli.py` / `batch.py` / `web/run_service.py`）：
   - `BaseProvider.__init__(settings=None)` + `registry.get_provider(name, settings=None)` 统一 settings 注入口。
   - `pipeline._select_provider(config, model_config=None)` 从 `ModelProviderConfig` 解析 `ProviderRuntimeSettings` 注入 provider 实例；deterministic 忽略 settings；skeleton 接受但忽略。
   - `pipeline.run(..., model_config=None)` 加参数；`run_summary.json` 加 `provider_runtime` 审计字段（model/base_url/api_key_env/enable_real_model_calls，**不含 key 明文**）。
   - CLI `run` / `batch` 加 `--model-config`（缺省读 OS app-data `default_config_path()`）；model_config 的 `planner_provider` 非 deterministic 时覆盖 env config。
   - `run_service.start_run` 加 `model_config_path` 参数；GUI `/api/runs` 路径也注入。
   - `RunResult` 加 `provider_runtime` 字段。
   - 8 个端到端测试（`tests/test_model_config_pipeline.py`）：monkeypatch `http_post` 验证 provider 真收到 configured base_url/model/api_key；production 缺 key / real calls off 时 fail-closed 不留空 dir；dev fallback 保留 `provider_runtime` audit；CLI `--model-config` 端到端；`ProviderOutputError` 错误信息 redact key。

2. **P1-2 GUI artifact dict 渲染**（`planner/web/static/app.js`）：
   - `renderDrawer` 改 `Array.isArray` + `Object.keys` 兼容 dict（`run_summary.artifacts` 是 `{name: path}`，旧代码 `.map()` 会崩）。
   - 测试 `test_app_js_render_drawer_handles_dict_artifacts` 钉住 dict 兼容。

3. **P1-3 desktop launcher shutdown**（`planner/web/launcher.py`）：
   - `launch_desktop` 重构持有 `uvicorn.Server` 实例（`_build_server` + `_signal_ready_when_started`）。
   - `finally` 设 `server.should_exit=True` + `server_thread.join(timeout)`；超时 log warning 不静默。
   - 2 个测试（`test_launch_desktop_stops_server_on_window_close` + `test_launch_desktop_logs_warning_when_server_join_times_out`）用 fake webview + fake/slow server。

### P2 修复

4. **P2-1 GUI model config 保存/读取 API**（`planner/web/routes.py` / `static/app.js` / `static/index.html`）：
   - `GET /api/model-config` 读 `default_config_path()` 返回 config + path（不含 key）。
   - `PUT /api/model-config` 写 `default_config_path()`，拒绝字面 key（`save_model_config` redact guard）。
   - `RunRequest` 加 `model_config_path` 字段（`ConfigDict(protected_namespaces=())` 消除 pydantic `model_` 前缀 warning）。
   - 前端启动 `GET /api/model-config` 填表单 + Save 按钮 `PUT` + run 时携带 path。
   - 6 个测试（GET defaults / PUT round-trip / 拒绝字面 key / batch 端到端 / production 拒绝 repo out_dir / missing scripts_dir）。

5. **P2-2 GUI batch endpoint**（`planner/web/routes.py` / `static/app.js` / `static/index.html`）：
   - `POST /api/batches` 同步跑 `run_batch()`（FastAPI threadpool 不阻塞 event loop），返回完整 `BatchSummary` + `summary_path`。
   - production 拒绝 repo 内 out_dir（403，`resolve_out_dir` 守）。
   - `BatchRequest` pydantic model（env/scripts_dir/out_dir/force/fail_fast/skip_validation/model_config_path）。
   - 前端 `batch-btn` 启用 + 调 `/api/batches`；`batch-scripts-dir` input。

6. **P2-3 planner batch --project**（`planner/cli.py`）：
   - `batch` 加 `--project DIR`；`--env/--scripts/--out` 改 optional。
   - 从 `project.json` 读 scripts_dir / output_dir / default_env / default_provider；优先级：显式 CLI > project.json > error。
   - `project.default_provider` 非 deterministic 时覆盖 env config；model_config 仍最高优先。
   - 3 个测试（batch --project 读 defaults / 显式 flag 覆盖 / 缺 env+project 报错）。

### Harness Engineering（只更新文档 + 预留，不实现）

7. **`harness/README.md`** 占位：列出 6 类未来 harness 场景（wheel/cli/fake model/gui/agent/permission）+ v1.0 临时覆盖映射到现有测试 + 红线。**不建 harness 脚本**（用户明确要求不展开实现）。
8. `docs/AI_COLLABORATION.md` 已含 Harness Engineering 角色（用户/Codex 已加，本轮确认对齐 plan 第四部分）。

### Hard-Boundary Preservation

- `pyproject.toml [project]` 基础依赖**未动**：仍只 `pydantic + click`。`openai` / `anthropic` SDK 仍未进必需依赖。
- 基础 `pip install -e .` 后 267 测试零回归（红线 #6 仍成立）。
- 仓库 `runs/` 仍只含根 `.gitkeep`；smoke 走 `/tmp`。
- `executor_tasks.json.tool` 仍 `None`。
- production fail-closed contract（pending_manual_approval / tool=None / submit_paid_jobs=False / allow_overwrite_runs=False / allow_provider_fallback=False）保留；model_config 无法绕过（`allow_provider_fallback` 由 env config 锁定）。
- API key 永不写盘：`run_summary.json` 只记 `api_key_env`；`ProviderOutputError` 错误信息 `_redact_secrets` 自动 redact Bearer/sk-/sk-ant-/gho-。

### Verification

- `python3 -m pytest` -- **267 passed**（247 base + 8 P1-1 + 1 P1-2 + 2 P1-3 + 6 P2-1/P2-2 + 3 P2-3）。
- `python3 -m pip wheel . --no-deps -w /tmp/storyboard-wheel` -- wheel 含 `planner/providers/openai_compatible_adapter.py` / `planner/web/static/{index.html,app.js,style.css}` / `planner/web/launcher.py`。
- `planner batch --project /tmp/...` 端到端 smoke 通过（project init -> validate -> batch --project 2/2 done）。

### Files Changed

- 修改：`planner/providers/base.py` / `registry.py` / `pipeline.py` / `cli.py` / `batch.py` / `web/run_service.py` / `web/routes.py` / `web/launcher.py` / `web/static/{index.html,app.js}`
- 修改：`tests/test_provider_health.py`（stub provider `__init__` 接受 settings）/ `test_web_static_ui.py`（endpoint 列表 + stub 按钮测试更新）/ `test_web_api.py`（+6 P2 测试）/ `test_project.py`（+3 P2-3 测试）
- 新增：`tests/test_model_config_pipeline.py`（8 P1-1 端到端测试）/ `harness/README.md`（Harness 预留占位）

## 2026-07-11 (Proma v1.0 P2 + P3 + P4 — 模型配置 / 静态 UI / 项目工作流 / 导出)

### Background

按 `docs/PROMA_V1_RELEASE_PLAN.md` 的优先级，本轮把 v1.0 P2-P4 一次推完：结构化模型配置层 + `openai_compatible` runtime provider + 静态前端 + `planner-web` 启动器 + `docs/GUI.md` + project.json + export 命令。P5 (smoke + 样例 + 三件套) 同步收口。

### Added (P2 — 模型配置 + provider)

1. **`planner/model_config.py`** —— v1.0 结构化模型配置层：
   - `OpenAICompatibleConfig` —— 字段 `base_url / model / api_key_env / timeout_seconds / temperature / max_tokens` + 字段验证器（`api_key_env` 必须 UPPER_SNAKE，`base_url` 必须 http(s))。
   - `ModelProviderConfig` —— 顶层配置 + `enable_real_model_calls=False` 默认 + `allow_provider_fallback=False` 默认；`extra="forbid"` 防意外字段。
   - `ProviderRuntimeSettings` —— 解析后的运行时设置；只有 env var 名，**不存 key**。
   - `default_config_path()` —— OS app-data：`~/Library/Application Support/ShortDramaPlanner/config.json` / `%APPDATA%` / `$XDG_DATA_HOME`。
   - `load_model_config / save_model_config` —— round-trip；`save_model_config` 拒绝字面 API key（regex 防御）。
   - `resolve_runtime_settings(cfg, provider_name)` —— 解析每个 provider 段为 `ProviderRuntimeSettings`。

2. **`planner/providers/openai_compatible_adapter.py`** —— v1.0 第一个**真实 runtime** provider：
   - `@register("openai_compatible")` 的 `OpenAICompatibleProvider`。
   - 走 OpenAI Chat Completions 接口（OpenAI 官方 / vLLM / Ollama / 第三方网关都覆盖）。
   - HTTP client 用 stdlib `urllib.request`，**SDK 不进必需依赖**。
   - `health_check()` 仅检查本地配置 + key env + enable_real_model_calls。
   - 没 `enable_real_model_calls=true` 直接 `healthy=False`（操作员必须显式开启）。
   - 五个规划方法走真实调用，每个方法用内部 envelope + 既有 Pydantic schema 校验。
   - JSON 解析失败抛 `ProviderOutputError(PlannerError)`，**永不静默 fallback**。
   - 错误信息包含 provider/model/step + truncated + **secret-redacted** payload excerpt（`Bearer XXX` / `sk-...` / `sk-ant-...` / `gho_...` 自动替换成 `<redacted>`）。
   - 暴露 `OFFICIAL_OPENAI_BASE_URL` / `OFFICIAL_ANTHROPIC_BASE_URL` / `ALIGNMENT_HINT` 给 GUI / openai skeleton 引用。

3. **`planner/exceptions.py`** —— 新增 `ProviderOutputError(PlannerError)`，用于 LLM 输出解析失败。

4. **`planner/providers/openai_adapter.py` + `anthropic_adapter.py` v1.0 对齐**：
   - 三个 health_check 分支的 reason 末尾追加 `ALIGNMENT_HINT`，引导操作员用 `provider=openai_compatible`。
   - module docstring 顶部加 "v1.0 alignment with `openai_compatible`" 段落，明确 skeleton 与 runtime 的关系。
   - **不**改成 thin wrapper（避免破坏 Phase-1 implementation gate hard contract）。

5. **`tests/test_model_config.py`（24 tests）** —— 覆盖：默认值、字段验证、save/load round-trip、拒绝字面 key、原子写、跨 OS 默认路径、resolve_runtime_settings、api_key() 从 env 读。
6. **`tests/test_openai_compatible_adapter.py`（23 tests）** —— 覆盖：注册、健康门（4 种组合）、HTTP success/error 各种 path、错误信息 redact API key、5 个规划方法 round-trip、与 openai/anthropic skeleton 的对齐 hint、canonical base URL 暴露。

### Added (P3 — 静态 UI + 启动器)

7. **`planner/web/static/index.html` / `app.js` / `style.css`** —— v1.0 GUI 客户端：
   - 顶部环境切换（development / production + warning banner）。
   - 模型设置面板（provider / model / base_url / api_key_env / enable real model calls toggle / probe 按钮）。
   - 剧本上传 + 单集运行 + batch 入口（v1.0 batch 入口提示用 CLI）。
   - Run 历史列表 + 详情抽屉（counts / audit / fallback banner / artifacts / 下载链接）。
   - Toast region（人话错误，无 traceback）。
   - 4s 后台刷新，页面隐藏时暂停。

8. **`planner/web/launcher.py`** —— `launch_desktop` (pywebview) + `launch_server_only` (uvicorn)：
   - pywebview 是 optional dep；缺失时 `launch_desktop` 报清楚错误。
   - 端口占用 preflight → `RuntimeError("already in use")`。
   - 后台 thread `daemon=False`，window 关闭时 server 干净停掉。

9. **`planner/web/scripts_entry.py` + `__main__.py`** —— `planner-web` console_script + `python -m planner.web`。
10. **`pyproject.toml`** —— 注册 `[project.scripts] planner-web = "planner.web.scripts_entry:main"`。
11. **`docs/GUI.md`** —— 同事上手手册（安装 / 启动 / 模型配置 / API key / 单集 / 多集 / 查看 / 导出 / 常见错误 / 红线）。

12. **`tests/test_web_static_ui.py`（11 tests）** —— 静态文件存在 + DOM id 必备 + `app.js` 只调已记录 endpoint + static bundle 进 wheel + FastAPI mount `/`。
13. **`tests/test_web_launcher_import.py`（8 tests）** —— launcher / scripts_entry / `__main__` import smoke + `launch_server_only` 真起 server + `/api/health` 200 + 端口占用 preflight + `planner-web` console_script 注册校验。

### Added (P4 — project.json + export)

14. **`planner/project.py`** —— v1.0 项目抽象：
    - `Project` Pydantic 模型（`project_name / script_dir / default_env / default_provider / output_dir / created_at / updated_at`）。
    - `init_project(dir, project_name, overwrite)` —— 创建目录树 + 原子写 `project.json`。
    - `load_project(dir)` / `validate_project(dir)` —— 读 + pre-flight。
    - `ProjectValidationReport` —— 错误 / 警告 / 脚本计数。

15. **`planner/export.py`** —— Markdown / HTML / CSV 导出：
    - `export_run(run_dir, fmt, output=None)` / `export_batch(batch_dir, fmt, output=None)`。
    - 三种格式内容一致：provider audit / fallback banner / bibles / beats / shots / prompts / executor tasks。
    - CSV 多 section（`### section` 分隔），方便 spreadsheet pivot。
    - HTML 单文件 inline CSS，可邮件发送 / 离线打开。
    - **绝不写明文 API key**（CSV/MD/HTML 都不出现）。

16. **`planner/cli.py`** —— 新增 `planner project init / validate` + `planner export --run/--batch --format`。

17. **`tests/test_project.py`（21 tests）** —— Project 默认值、字段验证、init_project 拒绝 overwrite、原子写、validate happy path + 各种 warning / error。
18. **`tests/test_export.py`（22 tests）** —— load_run / load_batch、3 种格式 round-trip、CLI integration、API key 不泄露、production audit 字段透出。

### Added (P5 — samples + smoke)

19. **`samples/v1/EP01.txt / EP02.txt / EP03.txt`** —— v1.0 验收样例，跨集共享人物 (lin_xia, chen_mo, zhou_jie) / 场景 (office_night, street_rain) / 道具 (blue_contract_folder, paper_cup_coffee)，为 Phase Core-3 跨集连续性验证打底。

20. **README.md** —— 重写为 v1.0 上手文档：5 分钟 setup、命令一览、核心契约（红线条款）、目录结构、AI 分工。

### Hard-Boundary Preservation

- `pyproject.toml [project]` 基础依赖**未动**：仍只 `pydantic>=2,<3` + `click>=8,<9`。`openai` / `anthropic` SDK 仍未进必需依赖。
- `pip install -e .`（基础安装）后 243 测试全绿（原 127 + 7 wheel + 3 config + 24 model_config + 22 openai_compatible + 11 static_ui + 8 launcher + 21 project + 22 export - 2 重叠）= **243 passed**。
- 基础安装仍不依赖 fastapi / uvicorn / pywebview（红线 #6 仍成立）。
- `/api/config` 修复未动 production fail-closed contract；新增 `production` smoke 走 `/tmp/v10-smoke`（不在仓库根）。
- `executor_tasks.json.tool` 仍为 `None`；export 测试断言报告不含 `flowith / libtv / keling / jiemeng / comfyui`。
- `run_summary.json` API key 处理：`OpenAICompatibleProvider` 错误信息自动 redact Bearer/sk-/sk-ant-/gho_。

### Verification

- `python3 -m pytest` —— **244 passed in 12.57s**（127 original + 117 v1.0 release tests；CHANGELOG §P2 写 22 实测 23 openai_compatible 已校正）。
- `python3 -m pip wheel . --no-deps -w /tmp/wheel` —— 34+ 文件，含 `planner/web/static/{index.html, app.js, style.css}`。
- CLI smoke (`/tmp/v10-smoke`)：`planner batch --env development --scripts ...` → 3/3 done，`provider_health.deterministic.healthy=true`，`executor_status=pending`。
- GUI smoke：`planner-web --no-window --port 18766` → `GET /api/health` 200, `GET /` 4122 bytes, `GET /app.js` 10030 bytes。
- Export smoke：`planner export --batch --format markdown|html|csv` 全部成功，分别 11405 / 14055 / 14532 bytes。
- Project smoke：`planner project init` + `project validate` 成功（3 scripts ready, 0 errors, 0 warnings）。
- 仓库 `runs/` 仍只含根 `.gitkeep`，smoke 产物全部在 `/tmp/v10-smoke` 已记录，未污染。

### Files Changed (cumulative delta from P2-P5)

- 新增：`planner/model_config.py`、`planner/providers/openai_compatible_adapter.py`、`planner/web/static/{index.html, app.js, style.css}`、`planner/web/launcher.py`、`planner/web/scripts_entry.py`、`planner/web/__main__.py`、`planner/project.py`、`planner/export.py`、`docs/GUI.md`、`samples/v1/{EP01,EP02,EP03}.txt`
- 修改：`pyproject.toml`、`planner/exceptions.py`、`planner/providers/{openai_adapter,anthropic_adapter}.py`、`planner/providers/__init__.py`、`planner/cli.py`、`planner/web/__init__.py`、`planner/web/routes.py`、`planner/web/run_service.py`、`tests/test_openai_anthropic_adapter.py`、`README.md`
- 新增测试：`tests/test_wheel_packaging.py`、`tests/test_model_config.py`、`tests/test_openai_compatible_adapter.py`、`tests/test_web_static_ui.py`、`tests/test_web_launcher_import.py`、`tests/test_project.py`、`tests/test_export.py`
- 修改测试：`tests/test_web_api.py`（+3 P1.2 repo_root 测试）

## 2026-07-11 (Proma v1.0 P1 — wheel 漏包 + GUI repo_root 修复)

### Background

`docs/PROMA_V1_RELEASE_PLAN.md` 把 wheel 漏子包与 `/api/config` 依赖 CWD 列为 v1.0 客户端安装的 #1 / #2 阻断问题。本轮修这两个 + 同步 adapter 测试注释与三件套旧语义。

### Changed (v1.0 P1 fixes)

1. **`pyproject.toml` —— wheel 不再漏掉 `planner/providers/` 与 `planner/web/`**（阻断 #1）：
   旧 `[tool.setuptools] packages = ["planner"]` 只装顶层 17 个 .py；改成 `[tool.setuptools.packages.find] include = ["planner*"]` 后 wheel 文件数从 22 → 34，包含全部 12 个 subpackage .py 与 `entry_points.txt`。

2. **`pyproject.toml` —— 预先声明 `package-data` for `planner.web`**：
   `static/*` + `static/**/*` 让 Phase 3 静态 UI 第一次落盘就能进 wheel，避免"先写 static/ → 改 package-data → 重打 wheel"的二段式踩坑。

3. **`planner/web/run_service.py::RunService.repo_root` 公开访问器**：
   之前 `routes.py:106` 直接 `service._repo_root`（私有属性）；新增 public `repo_root` property + `get_repo_root()` facade，routes 改走 facade。

4. **`planner/web/routes.py::/api/config` 显式 `project_root`，不再依赖 CWD**（阻断 #2）：
   - 旧实现：production preflight 只在 `_repo_root is not None` 时触发；否则 `load_config` 会回退到 `Path.cwd()`，导致"打包后从任意目录启动会读错 config"。
   - 新实现：
     - cfg_path 为空时，**显式**从 `service.repo_root` 拼 `<repo>/config/<env>.json`，不再让 `load_config` 默认到 CWD。
     - production preflight **永远**触发（即使 `repo_root is None`），告诉操作员用 `?config_path=` 显式传。
     - 任何路径都 `load_config(project_root=repo_root, config_path=cfg_path)`。
   - 404 vs 400 区分保留：production 缺 config → 404 + "copy from example" 提示；存在但内容非法 → 400。

5. **`tests/test_openai_anthropic_adapter.py` 顶部注释同步**（阻断 #3）：
   - 旧注释 "With both preconditions satisfied the adapter reports healthy" 已过时（P1 fix 后即使 key + SDK 齐全仍 `healthy=False`）。
   - 新注释显式说明 Phase-1 implementation gate、`healthy=False` 是 Phase-1 的契约（直到规划方法真实现才翻 True）、`NotImplementedError` 作 defense in depth。

### Added

- `tests/test_wheel_packaging.py`（4 个测试）：
  - `test_pyproject_packages_find_includes_subpackages` —— structural 校验 `[tool.setuptools.packages.find]` + `planner*` + 旧 `packages = ["planner"]` 不再出现。
  - `test_pyproject_declares_static_package_data` —— structural 校验 `package-data` + `planner.web` + `static/`。
  - `test_find_packages_returns_all_planner_subpackages` —— 用 `setuptools.find_packages(include=["planner*"])` 模拟 find 行为。
  - `test_wheel_includes_all_subpackages` —— subprocess 跑 `python -m pip wheel . --no-deps -w tmp`（与 plan §1 验收命令一致），断言 12 个必备模块全在 + entry_points.txt 注册 `planner` 脚本 + wheel 不带 tests/ data/ assets/ logs/ 等污染。
- `tests/test_web_api.py`（+3 个测试，覆盖 P1.2 验收）：
  - `test_get_config_uses_explicit_repo_root_not_cwd` —— `monkeypatch.chdir(unrelated_dir)` 模拟"从非 repo CWD 启动"，`create_app(repo_root=repo)` 后 GET development 仍读 repo 的 config；production 缺 config 仍 404。
  - `test_get_config_invalid_existing_returns_400` —— config 文件存在但 `planner_provider` 引用未注册 provider → 400（区分 "missing" 与 "broken"）。
  - `test_get_config_packaged_mode_without_repo_root` —— `create_app(repo_root=None)` + mock `detect_repo_root` → None 模拟 PyInstaller 模式，dev/prod 都返 actionable 错误（dev 400 / prod 404），提示用 `?config_path=`。

### Hard-Boundary Preservation

- `pyproject.toml` 基础 `[project]` 依赖**未动**：仍只 `pydantic>=2,<3` + `click>=8,<9`。`openai` / `anthropic` SDK 仍未进必需依赖。
- `pip install -e .`（基础安装）后 134 测试全绿；`pip install -e ".[gui,dev]"` 仍在覆盖范围内（红线 #6 仍成立）。
- `/api/config` 修复未改 production fail-closed contract：`test_get_config_production_missing_returns_404` 仍断言 404 + "copy from example" 提示。
- `production` 仍 fail-closed：`test_boundaries.py` 8 条 + `test_provider_health.py` 13 条 + `test_providers.py` 11 条 + `test_openai_anthropic_adapter.py` 25 条全部仍过。

### Verification

- `python3 -m pytest` —— **134 passed in 7.75s**（原 127 + 4 wheel + 3 config）。
- wheel 验证：
  ```
  /tmp/storyboard-wheel/script_to_storyboard_planner-0.1.0-py3-none-any.whl
  → 34 files (was 22)
  → includes planner/providers/{__init__,anthropic_adapter,base,deterministic,openai_adapter,registry}.py
  → includes planner/web/{__init__,app,errors,routes,run_registry,run_service}.py
  ```
- GUI repo_root 验证：从 `/tmp/unrelated_workdir` 启动 + `create_app(repo_root=/path/to/repo)` → `GET /api/config?env=development` 读 repo 的 `config/development.json`；`GET /api/config?env=production` 缺 config → 404。
- 仓库 `runs/` 仍只含根 `.gitkeep`，smoke 产物与 `config/production.json` 均未污染。

### Files Changed

- 修改：`pyproject.toml`、`planner/web/run_service.py`、`planner/web/routes.py`、`tests/test_openai_anthropic_adapter.py`
- 新增：`tests/test_wheel_packaging.py`
- 修改：`tests/test_web_api.py`（追加 3 测试）
- 同步：HANDOFF.md（"当前状态（2026-07-11）"段）、PROJECT_STATUS.json（phase 推到 v10_p1_done）

## 2026-07-10 (Proma Phase Core-1 Step-2 — Codex 复审 + P2/P3 polish)

### Review

- **Codex 子会话复审 Core-1 planner batch**：verdict **CHANGES**，2 P2 + 2 P3 + 4 open questions。子会话严格只读不写。

### Changed (P2 fixes, applied same-round per user preference)

1. **`planner/schema.py` — `EpisodeRunSummary` 加 `provider_health` 字段**（P2-#1，红线 #8）：
   原模型只透出 4 个审计字段，缺 `provider_health`；加上后 GUI 可以渲染与单 run endpoint 完全一致的审计卡片。

2. **`planner/batch.py::run_one_episode` 复制 `result.provider_health`**（P2-#1 续）：
   `RunResult.provider_health` 已经是 `Dict[str, dict]`，直接 `dict()` 复制即可（不需 `_health_to_dict` 转换）。pipeline 和 batch 各保留一份独立转换函数以避免依赖私有工具。

### Changed (P3 polish)

3. **`planner/cli.py` + `planner/batch.py` — `run_batch` 接受可选 `config` 参数**（P3-#1）：
   原来 CLI 路径 `load_config` 一次 + `run_batch` 内部又 load 一次；改成 CLI 把预加载的 config 传入 `run_batch(..., config=config)`，省一次磁盘 IO 和一次 Provider 校验。

### Added

- `tests/test_batch.py` 加 1 个 provider_health 断言（`ep.provider_health["deterministic"]["healthy"] is True`），覆盖 P2 修复。

### Verification

- **127 passed, 1 skipped**（原 111 + 新 16 Core-1）
- 基础 `pip install -e .` 后 92 测试零回归（红线 #6 仍成立）
- CLI smoke 3 集全 done，batch_summary.json 完整

### Files Changed (delta vs Core-1 Step-1)

- `planner/schema.py` —— `EpisodeRunSummary.provider_health: Optional[dict] = None`
- `planner/batch.py` —— `run_one_episode` 复制 `provider_health`；`run_batch` 接 `config=` 参数
- `planner/cli.py` —— `batch_cmd` 传 `config=config` 给 `run_batch`
- `tests/test_batch.py` —— 加 provider_health 断言

## 2026-07-10 (Proma Phase Core-1 Step-1 — planner batch / 多集驱动)

### Added

- `planner/batch.py`（约 270 行）—— 多集批处理驱动：
  - `BatchOptions` dataclass（env / scripts_dir / out_dir / fail_fast / config_path / repo_root / skip_validation）
  - `discover_scripts()` —— 排序遍历 `.txt`（确定性顺序）
  - `derive_episode_id()` —— `^(EP\d+)` 正则解析，缺则回退大写 stem
  - `run_one_episode()` —— 调 `planner.pipeline.run` 每集；`PlannerError` / 未知异常都捕获成 `EpisodeRunSummary(status="failed")`，**stderr 无 traceback**（红线 #6）
  - `run_batch()` —— 主入口；`--fail-fast` 默认，`--no-fail-fast` 显式继续；`batch_id` 微秒 + `secrets.token_hex(2)` 后缀防冲突
  - `_compute_totals()` —— 聚合每集状态 + 计数
- `planner/schema.py` 加 2 个 Pydantic 模型：
  - `EpisodeRunSummary` —— 单集审计记录（run_id / episode_id / run_dir / status / counts / 5 审计字段 / validation 状态）
  - `BatchSummary` —— 整批摘要（batch_id / env / scripts_dir / episodes[] / totals）
- `planner/cli.py` 加 `@cli.command("batch")`：
  - 选项：`--env` / `--scripts` / `--out` / `--config` / `--force` / `--no-fail-fast` / `--skip-validation`
  - `production` 拒绝 `--force`（与 `run` 一致）
  - 失败 ≥1 时 `sys.exit(2)`；全 done 时 `sys.exit(0)`
- `tests/test_batch.py` —— 16 个测试：
  - episode-id 解析（3）：lowercase、带后缀、无匹配
  - `discover_scripts`（3）：排序、缺目录、空目录
  - happy path（2）：3 集、1 集
  - fail-fast（2）：默认中断 vs `--no-fail-fast` 继续
  - production 拒绝（2）：repo 内拒绝 + 外部成功
  - summary shape（1）：含审计字段 + totals
  - CLI 集成（3）：`--help` exit 0、端到端 3 集、失败非零退出无 traceback

### Decisions

- **GUI 依赖严格 optional**（红线 #6）：`planner/batch.py` 在 `planner/cli.py` 顶部做 `from .batch import ...`，基础安装 `pip install -e .` 仍只装 pydantic + click。CLI 走本地 import。
- **审计字段一进一出**：每集 `EpisodeRunSummary` 都带 `requested_provider` / `effective_provider` / `fallback_used` / `fallback_reason` / `provider_health`，与单 run endpoint 形状一致，前端不必另开 schema。
- **路径穿越防护**：`discover_scripts` 显式 `is_file() + suffix.lower() == ".txt"`，不接受其他扩展名。
- **生产环境防漏**：CLI 的 `BatchOptions.resolved_out_dir` 与 GUI service 镜像 —— production 写 repo 内路径直接 `EnvironmentBoundaryError`，不创建任何目录。
- **失败不静默**（红线 #10）：每集 failure 写进 `batch_summary.json` 的 `error_type` + `error_message`，操作员从 batch summary 直接看到所有尝试过的 episodes。
- **`--skip-validation`** 默认 False —— 校验每集是核心契约，跳过要显式 opt-in。
- **`--force`** 默认 False 且 production 拒绝 —— 与 `planner run` 同语义。
- **episode_id 解析重复**（技术债）：`batch.derive_episode_id` 复制 `pipeline._episode_id_from_path` 的逻辑。Phase Core-X 抽到 `planner/episodes.py` 统一。

### Verification

- 基础 `pip install -e .` 后 92 测试零回归（原 76 + 新 16 batch；web 测试 importorskip）
- `pip install -e ".[gui,dev]"` 后 127 测试全绿
- CLI smoke 3 集 → exit 0 / `batch_summary.json` 含每集审计 + totals
- 红线 #3 自检：`test_run_batch_production_refuses_repo_path` 断言 production + repo 内路径直接 403 + 不创建目录
- 红线 #6 自检：`test_cli_batch_failure_exits_nonzero` 断言 stderr 无 `Traceback`
- 红线 #8 自检：`test_batch_summary_includes_audit_and_validation` 断言 5 审计字段全在
- 红线 #10 自检：`test_run_batch_no_fail_fast_records_and_continues` 断言失败写进 summary 不静默
- **Codex 复审门**：本轮交付前必经 Codex 复审（已完成，verdict CHANGES，已修 2 P2 + 1 P3）

### Files Changed

- 新增：`planner/batch.py`、`tests/test_batch.py`
- 修改：`planner/schema.py`（加 2 模型）、`planner/cli.py`（加 `batch_cmd`）

## 2026-07-10 (Proma Phase-2 Step-2 — Codex 复审 + P3 polish)

### Review

- **Codex 子会话复审 Phase-2 GUI backend**：verdict **PASS**，无 P1/P2，仅 3 个 P3 polish 项 + 4 个 open questions。
- 子会话严格只读不写，所有 10 条红线均验证通过。

### Changed (P3 polish, applied same-round per user preference)

1. **`planner/web/routes.py:108` 消除 substring matching**：
   原代码用 `"not found" in str(exc).lower()` 区分 404 vs 400 太脆弱；改为 routes 端在 `load_config` 之前先做文件系统 preflight（检查 `<repo_root>/config/production.json` 是否存在），缺失则直接返 404 + 提示，`load_config` 抛错统一按 400 处理。语义干净。
2. **`planner/web/run_service.py::generate_run_id()` 加微秒 + 随机后缀**：
   旧实现只到秒，并发 dev POST 同秒会撞名（默认 out_dir 用 run_id 作目录名）。新格式 `YYYYMMDD-HHMMSS-microseconds-xxxx`（最后 4 字符 `secrets.token_hex(2)`），200 次连发 200 个唯一 id。
3. **`planner/web/run_service.py` 新增 facade 方法 `get_run()` / `list_runs()`**：
   `routes.py` 之前有 5 处直接访问 `service._registry`（私有字段），改用 facade；内部 `RunRecord` 仍返回 dataclass（含 `out_dir` as Path），routes 自己调 `.to_dict()` 仅在出 HTTP body 时。

### Added

- `tests/test_web_run_service.py` 加 3 个 P3 测试：
  - `test_generate_run_id_uniqueness_under_burst`（200 连发 200 唯一）
  - `test_generate_run_id_format`（格式断言）
  - `test_run_service_facade_methods`（facade API 存在且语义对）

### Verification

- **111 passed in 2.69s**（原 108 + 新 3 个 P3 覆盖测试）
- 基础安装 `pip install -e .` 后原 76 测试零回归（红线 #6 仍成立）
- Codex 子会话报告已写入会话级 context（按子会话工作流约定）

### Files Changed (delta vs Step-1)

- `planner/web/routes.py` —— `get_config` 重写 preflight；5 处 `_registry` 替换为 facade
- `planner/web/run_service.py` —— `generate_run_id` 升级；`RunService.__init__` 接收 `repo_root`；加 `get_run` / `list_runs` facade
- `planner/web/app.py` —— 创建 `RunService` 时传 `repo_root`
- `tests/test_web_run_service.py` —— 加 3 个 P3 测试

## 2026-07-10 (Proma Phase-2 Step-1 — GUI 后端)

### Added

- `planner/web/` 子包（FastAPI 后端 + 错误中间件 + 服务层 + 内存 run registry）：
  - `__init__.py` —— PEP 562 懒加载导出 `create_app` / `launch_desktop`。
  - `app.py` —— FastAPI app factory `create_app()`，挂载路由、`PlannerError` 异常处理器、静态文件挂载点。
  - `routes.py` —— 8 个 API endpoint：`/api/health`、`/api/config`、`/api/runs`（GET 列表 + POST 启动）、`/api/runs/{id}/summary`、`/api/runs/{id}/artifacts/{name}`、`/api/runs/{id}/validate`、`/api/upload-script`。
  - `run_service.py` —— 薄服务层；只做 out_dir 策略 + 调 `planner.pipeline.run` + 调 `planner.validate.validate_run` + 注册表更新 + 失败清理空目录。**零业务逻辑**。
  - `run_registry.py` —— 线程安全的内存 `run_id → RunRecord` 映射，UI 轮询用。
  - `errors.py` —— `PlannerError` 子类 → HTTP 状态码 + `{error, message}` JSON 映射；中间件不向 HTTP body 泄露 traceback。
- `pyproject.toml` 新增 optional extras：
  - `gui` —— `fastapi + uvicorn[standard] + pywebview + python-multipart`（桌面 GUI 壳）
  - `server` —— `fastapi + uvicorn + python-multipart`（仅服务端，浏览器自开）
  - `build` —— `pyinstaller`（CI 打 `.app` / `.exe` 用）
  - `dev` —— 加 `httpx`（FastAPI TestClient 后端）
- 新增测试：
  - `tests/test_web_api.py`（16 个）：8 个 endpoint 全覆盖 + 路径穿越防护 + 上传空文件拒绝 + 列表排序 + 不阻塞后台 run。
  - `tests/test_web_run_service.py`（16 个）：out_dir 策略矩阵 + dev/prod 默认值 + prod 拒绝 repo 内路径 + `--force` 在 dev/prod 差异 + 后台线程非 daemon + 失败清理 + run_summary 字段完整。

### Decisions

- **GUI 后端零业务逻辑**：所有规则（provider 抽象、fail-closed、run_summary 审计字段、validate 检查）都在 `planner/` 核心包，GUI 只是 HTTP ↔ function 的翻译层。三件套（CLI / GUI / test）共享同一组 `planner.pipeline / planner.validate / planner.env` 函数。
- **GUI 依赖严格 optional**：基础 `pip install -e .` 仍只装 `pydantic + click`，原有 76 测试零改动；`pip install -e ".[gui,dev]"` 才装 fastapi/pywebview。这是红线 #6 的硬约束，已通过测试自检。
- **out_dir 策略**（用户拍板）：
  - `development` 默认 `<repo_root>/runs/development/<run_id>/`（gitignored 但本地可见）。
  - `production` 默认 `<os_app_data>/ShortDramaPlanner/runs/<run_id>/`（Mac: `~/Library/Application Support/...`；Windows: `%APPDATA%/...`），**绝不写进 repo**。
  - `production` 显式指定 repo 内路径 → 直接 `EnvironmentBoundaryError`（403），不创建目录。
- **错误不回传 traceback**：FastAPI 全局 `PlannerError` 异常处理器只回 `{error, message}`，完整 traceback 仅入服务端日志。`test_boundaries.py::test_cli_friendly_error_when_production_config_missing` 的契约在 GUI 路径同样成立。
- **后台线程非 daemon**：让 uvicorn 退出时能 wait in-flight run 收尾，避免半成品 run 残留。
- **失败时清理空 run 目录**：保持 `no-residue` 不变式 —— 失败时若 `out_dir` 为空就 `rmdir`，有产物就保留（方便用户排查失败时的人工检查）。
- **审计字段一进一出**：`run_summary.json` 已是事实标准；`/api/runs/{id}/validate` 与 `/api/runs/{id}/summary` 都返回 `requested_provider` / `effective_provider` / `fallback_used` / `fallback_reason` 字段，前端不必另开 schema。
- **路径穿越防护**：`/api/runs/{id}/artifacts/{name}` 的 `name` 只接受白名单（11 个已知 JSON 文件名），未知 → 404 而不是去读 `../../etc/passwd`。
- **Phase 3 暂不做**：静态 UI + pywebview 启动器 + `docs/GUI.md` 留到下一轮，本轮先把后端骨架定下让 Codex 复审。

### Verification

- 基础安装仍工作：`pip install -e . && pytest -q --ignore=tests/test_web_api.py --ignore=tests/test_web_run_service.py` → **76 passed**（红线 #6 自检通过）。
- GUI 安装：`pip install -e ".[gui,dev]"` → **108 passed**（原 76 + 新 32）。
- 红线 #3 自检：`test_post_runs_production_outside_repo_default` + `test_post_runs_production_inside_repo_is_rejected` 断言 production 默认/显式 out_dir 都不写 repo。
- 红线 #5 自检：`test_get_config_production_missing_returns_404` 断言 production 缺 `config/production.json` 时返回 404 + "copy from example" 提示。
- 红线 #7 自检：所有 endpoint 在 PlannerError 时返回 `{error, message}` JSON，无 traceback。
- **Codex 复审门**：本轮交付前必经 Codex 复审（在 Phase 3 启动前），重点核对 API 是否真的只是"瘦壳"、out_dir 策略是否真破不了、run_service 是否持有任何业务逻辑。

### Files Changed

- 新增：`planner/web/{__init__.py, app.py, routes.py, run_service.py, run_registry.py, errors.py}`
- 新增：`tests/test_web_api.py`、`tests/test_web_run_service.py`
- 修改：`pyproject.toml`（`[project.optional-dependencies]` 加 `gui` / `server` / `build` 三组 + `dev` 加 `httpx`）

## 2026-07-04

### Added

- 创建项目初始设计文档。
- 明确项目第一阶段定位：剧本理解、视觉圣经、分镜规划、prompt 生成、资产状态管理。
- 建立 AI 协作边界：Codex 负责设计和审计，Proma 与 Zcode 负责代码执行和测试。
- 增加 `PROJECT_STATUS.json`，方便更换 AI 工具时读取当前状态。
- 补充 Zcode 为参与开发、测试、修复的执行型 AI 工具。
- 将 Proma 第一阶段第一步明确为创建代码骨架，并同步建立开发/生产环境隔离。

### Decisions

- 核心 planner 不直接绑定 Flowith 或 libTV。
- Web 自动化放在 executor 层，通过统一任务接口对接。
- 人物、场景、道具设定必须先结构化冻结，再生成镜头 prompt。
- 同一套代码通过环境参数切换 `development` 与 `production`，数据、资产、runs 和 logs 必须分目录隔离。

### Verification

- 当前仅完成文档初始化，尚未开始代码实现。

## 2026-07-04 (Proma Phase-1 Step-1)

### Added

- 创建 Python 包 `planner/`：
  - `cli.py` —— Click CLI，提供 `planner run` / `planner validate`。
  - `env.py` —— `development` / `production` 环境加载与硬约束。
  - `schema.py` —— 与 `specs/DATA_CONTRACTS.md` 对齐的 Pydantic v2 模型。
  - `parser.py` —— 支持 `[meta:*]` 注释、`EPxx_Syy` 标题、`[BEAT: ...]` 标记、中文/半角冒号对白的剧本解析器。
  - `annotations.py` —— `[meta:*]` 内联注释解析。
  - `bible.py` —— 基于 meta 种子 + 对白/场景抽样的确定性 character/location/prop 抽取与去重。
  - `beats.py` —— 故事节拍抽取。
  - `shots.py` —— 每个场景生成 3 个镜头（远景 / 中景 / 特写），引用 bible id。
  - `prompts.py` —— 合成 image/video prompt，每条都显式带"场景/人物/道具 + 镜头语言"。
  - `manifest.py` —— 生成 `asset_manifest.json` 与 `executor_tasks.json` 骨架。
  - `pipeline.py` —— 编排 run 流程，写入 9 个 JSON 产物并做引用完整性检查。
  - `validate.py` —— 验证一个 run 目录的引用、prompt 内容覆盖、manifest 完整性。
- 创建目录树：
  - `config/development.json`、`config/production.example.json`。
  - `data/{development,production}/{input_scripts,bibles,shots,prompts,manifests}/`。
  - `assets/{development,production}/{reference,images,videos}/`。
  - `runs/{development,production}/`、`logs/{development,production}/`。
- 添加 `pyproject.toml`，暴露 `planner` 命令行。
- 添加 `.gitignore`、`.env.example`（不提交任何真实密钥/生产配置）。
- 添加样例剧本 `data/development/input_scripts/sample_ep01.txt`（含 2 角色、2 场景、1 道具、3 剧情节拍）。
- 添加基础测试 `tests/`：`test_env.py`、`test_parser.py`、`test_pipeline.py`、`test_schema_errors.py`，共 16 个用例。

### Decisions

- Phase-1 不调用任何 LLM；bibles、shots、prompts 全部由确定性规则生成，便于先验证骨架与契约。
- `[meta:*]` 内联注释块承担"结构化冻结"的角色，未来切到 LLM 时只需替换抽取器，schema 不变。
- 角色/场景去重按"中文显示名 → 已存在 seed id"匹配，避免 `林夏` 和 `lin_xia` 被算作两个角色。
- `production` 边界硬约束：
  - 禁止覆盖已有 run 目录；
  - `executor_default_status` 默认 `pending_manual_approval`；
  - `submit_paid_jobs` 强制 `false`；
  - 即使通过 `PLANNER_SUBMIT_PAID_JOBS=true` 也无法突破。
- 真实生产配置 `config/production.json` 被 `.gitignore` 排除；模板使用 `config/production.example.json` 提交。

### Verification

- `planner run --env development --script data/development/input_scripts/sample_ep01.txt --out runs/development/sample_ep01` 成功生成 9 个 JSON 产物（2 角色 / 2 场景 / 1 道具 / 3 节拍 / 6 镜头）。
- `planner validate --env development --run runs/development/sample_ep01` 通过，errors=0、warnings=0。
- `planner run --env production ...` 同样通过，且 `executor_tasks.json` 的 status 全部为 `pending_manual_approval`。
- `planner validate --env production ...` 通过。
- `python3 -m pytest` —— 16 passed。

### Outstanding / Still TODO

- LLM provider 抽象层：未来要把 `bible.py`、`shots.py`、`prompts.py` 中的确定性规则替换成可调用 LLM 的 provider，但接口形状保持不变。
- Executors：仍按 Phase 3 计划，未实现真实 Flowith / libTV / 可灵 / 即梦 适配器。
- Continuity audit（角色漂移、场景漂移、道具漂移的自动检查）属于 Phase 2，目前 validator 只做引用完整性。
- Zcode 尚未参与；预留测试与 validation 工具的并行开发位。

## 2026-07-04 (Proma Phase-1 Review Fixes)

### Changed (in response to Codex review)

- `planner/env.py`：production 现在对 `PLANNER_EXECUTOR_DEFAULT_STATUS`、`PLANNER_SUBMIT_PAID_JOBS`、`PLANNER_ALLOW_OVERWRITE_RUNS` 三个 key **显式拒绝**：env-var 一旦被设置即抛 `ConfigError`，不再静默忽略。`_enforce_boundaries` 同时把 `allow_overwrite_runs` 列为硬约束，并要求 `executor_default_status` 必须等于 `pending_manual_approval`（不再允许 `pending`）。
- `planner/manifest.py`：`build_executor_tasks` 的 `tool` 参数默认从 `"flowith"` 改为 `None`，与"核心 planner 不写死 Flowith/libTV"约定对齐。
- `planner/cli.py`：`run` 和 `validate` 命令把 `load_config()` 也纳入 `try/except PlannerError`；`ConfigError` 现在以 `config error: <message>` 形式输出到 stderr，不会再泄露 Python traceback。
- `planner/validate.py`：`validate_run` 支持 `expected_env` 参数；`run_summary.json` 中的 `env` 与 CLI `--env` 不一致时会作为 warning 暴露，`cli` 在 stderr 打印 `⚠ env mismatch`。
- 清理工作区：`runs/production/` 和 `runs/development/` 下的历史产物已删除；后续 production 验证建议在 `/tmp` 或专门 CI 目录进行，避免污染开发工作区。

### Added

- `tests/test_boundaries.py`：8 个新用例覆盖以下场景：
  - `PLANNER_EXECUTOR_DEFAULT_STATUS=pending` 在 production 下抛 `ConfigError`。
  - `PLANNER_SUBMIT_PAID_JOBS=1` 在 production 下抛 `ConfigError`。
  - `PLANNER_ALLOW_OVERWRITE_RUNS=true` 在 production 下抛 `ConfigError`。
  - `build_executor_tasks` 默认 `tool=None`，不写死 flowith。
  - `planner run` 在 production 配置缺失时输出 `config error: ...` 而不是 traceback。
  - `planner validate --env production` 在 development run 上会打印 `env mismatch` warning。
- `tests/test_boundaries.py::test_development_still_accepts_env_var_executor_status` 保护：development 不受影响，仍可通过 `PLANNER_EXECUTOR_DEFAULT_STATUS` 调整。

### Verification

- `python3 -m pytest` —— **24 passed**（原 16 + 新 8）。
- 三种 env-var 攻击复现脚本全部按预期抛 `config error: ...` 并退出码非零，无 traceback、无污染产物。
- clean production run：executor task 的 `tool` 字段为 `None`，`status` 为 `pending_manual_approval`。

### Outstanding / Still TODO (unchanged)

- 仍在 Phase-1 范围内的：LLM provider 抽象层、continuity audit、executor adapters。

## 2026-07-04 (Proma Phase-1 Closeout)

### Changed

- `planner/env.py::_production_locked_keys()` docstring：去掉"silently ignored"的过期措辞，明确写"rejected loudly"（抛 `ConfigError`），并解释 `_enforce_boundaries` 是第二道防线。
- 其它代码未动；本轮不对业务逻辑 / schema / 边界策略做任何调整。

### Verification

- `python3 -m pytest` —— **24 passed**（与复审修复后一致）。
- 在 `/tmp/planner_prod_smoke` 跑了一次干净 production run：`run_summary.json` 显示 `env=production`、`executor_status=pending_manual_approval`，`executor_tasks.json` 的 `tool=null`。产物只在 `/tmp`，未在仓库 `runs/production/` 留下污染。
- 仓库 `runs/` 下只有 `.gitkeep` 占位，无 production 产物。

### Next

- 进入 LLM provider 抽象层：实现 provider 接口、`DeterministicProvider` 复用现有 `bible/beats/shots/prompts`，配置 `planner_provider: "deterministic"`。详见 `docs/PROMA_EXECUTION_BRIEF.md` 的"Next Step"章节。

## 2026-07-04 (Proma LLM Provider Abstraction)

### Added

- 新增 `planner/providers/` 包：
  - `base.py` —— 抽象 `BaseProvider`，定义五个能力签名（`build_bibles` / `extract_beats` / `generate_shots` / `compile_image_prompts` / `compile_video_prompts`），所有 provider 必须遵循同一接口，pipeline 和 schema 不感知差异。
  - `registry.py` —— 注册表，装饰器 `register(name)`、工厂 `get_provider(name)`；未知 provider 抛 `ConfigError` 并附可用列表；同 name 重复注册抛 `RuntimeError`；空 name 抛 `ConfigError`。
  - `deterministic.py` —— `@register("deterministic")` 的 `DeterministicProvider`，是现有 `bible/beats/shots/prompts` 的薄包装，**不调用任何 LLM**。
  - `__init__.py` —— 暴露 `BaseProvider`、`DeterministicProvider`、`get_provider`、`register`、`available_providers`；`import planner.providers` 自动注册 `deterministic`。
- `tests/test_providers.py` —— 11 个新用例，覆盖：默认 provider=deterministic、显式配置可工作、未知 provider 抛 ConfigError 且错误信息列出可用名、`tool=None` / `status=pending_manual_approval` 在 provider 路径下不变、第三方 plugin provider 可被 config 选中且产物 validate 通过、注册表反 footgun（duplicate / 非 BaseProvider 子类）。

### Changed

- `planner/env.py`：
  - 新增 `planner_provider` 字段（`PlannerConfig` / `as_dict()`），默认 `DEFAULT_PLANNER_PROVIDER = "deterministic"`。
  - 允许 `PLANNER_PLANNER_PROVIDER` env override；**不在** `_production_locked_keys`（provider 选择不会触碰硬边界——硬边界由 `_enforce_boundaries` 与 `manifest.build_executor_tasks` 各自继续兜底）。
  - 新增 `_validate_provider(cfg)`，在 `_enforce_boundaries` 之前实例化注册表里的 provider，未知 provider 在 config 加载时即抛 `ConfigError`。
- `planner/pipeline.py`：移除 `bible` / `beats` / `shots` / `prompts` 的直接 import，改为 `get_provider(config.planner_provider)` 的统一调用；schema 与 JSON 产物形状保持不变。
- `config/development.json` / `config/production.example.json`：增加 `"planner_provider": "deterministic"`。

### Hard-Boundary Preservation

- `planner/env.py::_enforce_boundaries`（executor status / submit_paid_jobs / allow_overwrite_runs）未被任何 provider 调用绕过。
- `planner/manifest.py::build_executor_tasks` 仍默认 `tool=None`、`status=pending_manual_approval`；provider 只负责规划层，不直接产出 executor task。
- `tests/test_boundaries.py` 8 条边界用例全部仍通过；`tests/test_providers.py::test_provider_abstraction_keeps_production_tool_none` 进一步断言 provider 路径不会放松 production executor 约定。

### Verification

- `python3 -m pytest` —— **35 passed** in 0.82s（24 老 + 11 新）。
- `/tmp/planner_prod_smoke2` 跑走 provider 路径的 production run：`run_summary.json` `env=production`、`executor_status=pending_manual_approval`，`executor_tasks.json[0]` 的 `tool=null`、`status=pending_manual_approval`。
- dev run：`python3 -m planner run --env development --script data/development/input_scripts/sample_ep01.txt --out runs/development/_tmp_dev` 仍生成 9 个 JSON 产物，`validate` 通过。
- 仓库 `runs/` 只含 `.gitkeep` 占位，仓库内未污染。

### Outstanding / Still TODO

- 仍按 brief 严格不做：调用真实 LLM、新增真实 API key / cookie / 账号、把 OpenAI / Anthropic / 任何模型 SDK 作为必需依赖、把生成任务直接放入 `pending` 队列、接入 Flowith/libTV/可灵/即梦/ComfyUI。
- 下一步候选（待 Codex 复审）：在 `planner/providers/` 下加 OpenAI / Anthropic adapter 占位（仅接口形态 + 配置门槛，不调真模型），并设计"provider 健康检查"——这样如果 LLM provider 在 production 中挂了，仍可以切回 deterministic 而不丢 schema 兼容。

## 2026-07-04 (Codex Second Review and Next Proma Task)

### Review Result

- Codex 二次复审通过 Proma 的 Phase-1 review fixes。
- 确认三种 production env-var 降级攻击均被显式拦截。
- 确认 executor task 默认 `tool=None`，production status 为 `pending_manual_approval`。
- 确认 `validate --env` 已能提示 run env mismatch。

### Assigned To Proma

- 先做 Phase-1 closeout：修正 `planner/env.py` 中 `_production_locked_keys()` 的过期注释，并重新跑完整测试。
- 然后进入 LLM provider abstraction：新增 provider 接口与 deterministic provider 包装，默认仍不调用真实 LLM。

### Boundary

- 下一步不接入真实 LLM API。
- 不新增真实密钥、cookie、账号或 `.env.production`。
- 不把 OpenAI / Anthropic / 本地模型 SDK 作为必需依赖。
- 不接入 executor、Flowith/libTV、可灵、即梦或 ComfyUI。
- 不把 production 验证产物写入仓库根目录 `runs/production/`。

## 2026-07-04 (Proma Script-Parse Artifact + Provider Tracking)

### Background

- Codex 复审 provider 抽象层后指出两处审计/契约缺口：
  1. `script_parse.json` 是 `docs/PROMA_EXECUTION_BRIEF.md` 与 `docs/ARCHITECTURE.md` 列出的核心产物，但 `planner/pipeline.py` 从未写出它。
  2. `planner_provider` 已经可配置但 `run_summary.json` 不记录，下游 audit / 复审无法区分 deterministic 与 LLM run。

### Added

- `runs/.../script_parse.json` 现在是 9 个核心 JSON 产物之一，由 `planner.pipeline.run()` 调用现有 `parser.parse_script()` 产出，挂在 `artifacts` 字典首项（`script_parse`）。
- `planner/validate.py::ValidationReport` 新增字段 `planner_provider`，从 `run_summary.json.planner_provider` 透出；同时新增 `script_parse.json.source_path` 与 `run_summary.script` 一致性校验（不一致即 error），以及 `stats.script_blocks` 计数。
- `tests/test_pipeline.py` 新增 2 个用例：
  - `test_script_parse_artifact_records_source_and_blocks` 断言 `script_parse.json` 存在、`source_path == 脚本路径`、`blocks` 非空、kind ∈ {scene, dialogue, action}。
  - `test_run_summary_records_planner_provider` 断言 `run_summary.planner_provider == "deterministic"`、artifacts 含 `script_parse` 条目。
- `EXPECTED_ARTIFACTS` 加入 `script_parse.json` / `executor_tasks.json` / `run_summary.json`，与实际产物集合对齐。

### Changed

- `planner/pipeline.py`：
  - 顶部 `from .parser import parse_script`；移除未使用的 `_parse_script` / `ScriptParse` import。
  - 在 `run()` 内 `# 0. Script parse.` 段调用 `parse_script(script_path, script_id=episode_id)` 并写入 `script_parse.json`，保证 on-disk 与下游 provider 共享同一 source_span 基准。
  - `run_summary.json` 新增 `"planner_provider": config.planner_provider` 字段。
  - `load_run()` 的 `files` 列表加入 `"script_parse.json"`，让所有走 `load_run` 的 validate 路径都强制覆盖它。

### Hard-Boundary Preservation

- `production` executor status 仍硬绑 `pending_manual_approval`、`tool=None`、`submit_paid_jobs=False`、`allow_overwrite_runs=False`，无任何变化。
- `script_parse` 是脚本归一化的中间产物，与 provider 解耦——无论未来切到 OpenAI / Anthropic / 本地模型，写出的 `script_parse.json` 形状都一致，validate 与审计可直接对齐 source_span。
- `planner_provider` 仍不在 `_production_locked_keys`：它是规划层选择，硬边界继续由 `_enforce_boundaries` 与 `manifest.build_executor_tasks` 兜底。

### Verification

- `python3 -m pytest` —— **37 passed** in 0.75s（24 老 + 11 provider + 2 新增）。
- `/tmp/planner_smoke_v2_dev` development run：写出 10 个 JSON 产物（含 `script_parse.json`），`validate` ok、stats 含 `script_blocks=60`、`run_summary.planner_provider="deterministic"`。
- `/tmp/planner_smoke_v2_prod` production run：写出 10 个 JSON 产物，`executor_status=pending_manual_approval`，`executor_tasks[0].tool=null`、`status="pending_manual_approval"`，`run_summary.planner_provider="deterministic"`。
- 仓库 `runs/` 仅含 `.gitkeep`，`config/production.json` 已删除，未污染。

### Outstanding / Still TODO

- 仍按 brief 严格不做：调用真实 LLM、新增真实 API key / cookie / 账号、把 OpenAI / Anthropic / 任何模型 SDK 作为必需依赖、把生成任务直接放入 `pending` 队列、接入 Flowith/libTV/可灵/即梦/ComfyUI。
- 后续候选（待 Codex 复审）：OpenAI / Anthropic adapter skeleton（仅接口形态，不调真模型）；provider 健康检查与 fallback；Phase 2 continuity audit。

## 2026-07-04 (Proma Provider Fallback / Health Check)

### Background

- Codex 通过本轮 `script_parse` + `planner_provider` 记录后，建议下一步在不动真实 LLM 的前提下先做 fallback / health check 的最小骨架，让未来 LLM provider 接入时能 fail-closed。
- 目标：未来 LLM provider 不可用时，开发期可审计地切回 deterministic，production 必须 fail-closed；fallback 不能改变 executor 边界。

### Added

- `planner/providers/base.py`：
  - 新增 `ProviderHealth` dataclass（`name` / `healthy` / `reason` / `details`）。
  - `BaseProvider` 新增抽象方法 `health_check()`；所有 provider 必须实现，约束明确：**不得发起真实昂贵请求**（无 model 推理、无账号登录、无付费探活），只检查 config / 依赖存在。
- `planner/providers/deterministic.py`：实现 `health_check()` 永远返回 `healthy=True`，附 `reason="deterministic provider has no external dependencies"`、`details={"external_calls": "none", "phase": "1"}` —— deterministic 是 fallback 目标，它必须永远 healthy。
- `planner/providers/registry.py`：新增 `unregister(name)` 帮手，给 test teardown 清理 stub 用；空 name 抛 `ValueError`，未知 name 静默 no-op。
- `planner/providers/__init__.py`：导出 `ProviderHealth` 和 `unregister`。
- `planner/exceptions.py`：新增 `ProviderUnavailableError(PlannerError)`，表示请求的 provider 不健康且环境 fail-closed。
- `planner/env.py`：
  - 新增 `PlannerConfig.allow_provider_fallback: bool = False`（默认 fail-closed）。
  - `allow_provider_fallback` 加入 env-var 覆盖列表。
  - 加入 `_production_locked_keys`，production 下 `PLANNER_ALLOW_PROVIDER_FALLBACK=...` env-var 显式拒绝。
  - `_enforce_boundaries` 增加 production 二次防御：`allow_provider_fallback=True` 在 production 下抛 `ConfigError`。
- `config/development.json`：`allow_provider_fallback=true`（开发期允许可审计 fallback）。
- `config/production.example.json`：`allow_provider_fallback=false`（生产显式 fail-closed）。
- `planner/pipeline.py`：
  - 新增 `_select_provider(config)`：先调请求 provider 的 `health_check()`，不健康且允许 fallback → 切到 deterministic；不健康且 fail-closed → 抛 `ProviderUnavailableError`。
  - `pipeline.run()` 改走 `_select_provider`，拿到 `(provider, requested_name, effective_name, fallback_used, fallback_reason, provider_health)`。
  - `RunResult` 增加 5 个新字段：`requested_provider` / `effective_provider` / `fallback_used` / `fallback_reason` / `provider_health`。
  - `run_summary.json` 增加 audit 字段：`requested_provider` / `effective_provider` / `fallback_used` / `fallback_reason` / `provider_health`，保留 `planner_provider` 作为 backward-compat 别名（值等于 `requested_provider`）。
- `planner/validate.py`：`ValidationReport` 增加 4 个字段，并在 `validate_run` 中：
  - 读取新 audit 字段并在报告里透出。
  - **production 下若 `fallback_used=True` 直接报 error**（fail-closed 不允许 fallback）。
  - 新 run 缺 `effective_provider` / `fallback_used` 时发 warning（兼容历史 run）。

### Tests

- 新增 `tests/test_provider_health.py`，13 个用例覆盖：
  - `test_deterministic_provider_health_check_is_healthy`
  - `test_unhealthy_stub_health_check_reports_unhealthy`
  - `test_base_provider_requires_health_check_subclass`（ABC 兜底）
  - `test_unknown_provider_still_rejected_at_load`（向后兼容）
  - `test_production_rejects_allow_provider_fallback_true`（config 路径）
  - `test_production_env_var_allow_provider_fallback_rejected`（env-var 路径）
  - `test_development_falls_back_to_deterministic_and_records_swap`
  - `test_fallback_run_passes_validation`
  - `test_fallback_preserves_script_parse_and_references`（fallback 后产物字节级等于 clean run）
  - `test_development_fallback_disabled_fails_closed`
  - `test_production_fails_closed_when_requested_provider_unhealthy`
  - `test_fallback_design_does_not_change_production_executor_defaults`
  - `test_unregister_helper_removes_provider`
- 修 `tests/test_providers.py::_EchoProvider`：补 `health_check()` 返回 healthy（之前漏了这个抽象方法）。
- 新增一个 `_UnhealthyStubProvider` 在 `test_provider_health.py` 用 `@register("unhealthy_stub")` 注册；五个抽取方法委托给 deterministic，保证 fallback 路径产物有可比性；测试结束由 `_isolate_registry` 自动清场。

### Hard-Boundary Preservation

- `production` 仍然：`executor_default_status=pending_manual_approval` / `tool=None` / `submit_paid_jobs=False` / `allow_overwrite_runs=False`。
- 新增硬约束：`production` 下 `allow_provider_fallback` 必须 False，env-var 也被锁，与现有三连保持同样的"rejected loudly"风格。
- `_select_provider` 只影响 **规划层** provider 选择；不接触 `manifest.build_executor_tasks` 的 tool/status 默认值。
- fallback 后 `script_parse.json` 与所有下游产物字节级等于直接走 deterministic 的 clean run（由 `test_fallback_preserves_script_parse_and_references` 守住）。

### Verification

- `python3 -m pytest` —— **50 passed** in 0.81s（37 老 + 13 新）。
- `/tmp/planner_smoke_v3_dev` development smoke：
  - 10 个 JSON 产物，`run_summary.json` 含全部 audit 字段：
    ```json
    {
      "env": "development",
      "planner_provider": "deterministic",
      "requested_provider": "deterministic",
      "effective_provider": "deterministic",
      "fallback_used": false,
      "fallback_reason": null,
      "provider_health": {
        "deterministic": {
          "name": "deterministic",
          "healthy": true,
          "reason": "deterministic provider has no external dependencies",
          "details": {"external_calls": "none", "phase": "1"}
        }
      },
      "executor_status": "pending"
    }
    ```
  - `validate` ok, 0 errors / 0 warnings, stats 含 `script_blocks=60`。
- `/tmp/planner_smoke_v3_prod` production smoke：
  - 10 个 JSON 产物，`run_summary.json` 同上 audit 字段，`fallback_used=false`、`fallback_reason=null`、`executor_status=pending_manual_approval`。
  - `validate` ok，`executor_tasks[0].tool=null` / `status=pending_manual_approval`。
- 仓库 `runs/` 仅含 `.gitkeep`，smoke 产物与 `config/production.json` 已删除，无污染。

### Outstanding / Still TODO

- 仍按 brief 严格不做：调用真实 LLM、新增真实 API key / cookie / 账号、把 OpenAI / Anthropic / 任何模型 SDK 作为必需依赖、把生成任务直接放入 `pending` 队列、接入 Flowith/libTV/可灵/即梦/ComfyUI。
- 本轮只到"骨架"层：未来真接入 OpenAI / Anthropic 时，`health_check` 只需检查配置与 SDK 存在性即可，**不要把它做成"调一次 ping"**（那是下一步 Codex 复审通过后再单独设计 probe）。
- 后续候选：OpenAI / Anthropic adapter skeleton；provider 健康检查 probe（opt-in）；Phase 2 continuity audit。

## 2026-07-04 (Proma Codex-Review Fixup)

### Background

- Codex 复审 fallback / health check 骨架后，结论"主体通过，但有 1 个 P2 阻断 + 1 个文档提醒"：
  - **\[P2\]** `pipeline.run` 顺序问题：`out_dir.mkdir(...)` 在 `_select_provider(config)` 之前，production provider unhealthy 时抛 `ProviderUnavailableError` 后会留下空的 `out_dir`，下一次同路径重跑会被 production overwrite guard 拦下，必须手动清理。
  - **\[次要\]** `runs/` 只剩根 `.gitkeep`，没有 `runs/development/.gitkeep` 与 `runs/production/.gitkeep`；是 declarative convention 还是 regressions 与 `.gitignore` 不齐。新 AI 可能会困惑。

### Changed

- `planner/pipeline.py::run()`：把 `_select_provider(config)` 提到 `out_dir.mkdir(...)` 之前；保留 `out_dir.exists()` overwrite guard 在 mkdir 之前。新增注释段说明 preflight 检查顺序（health check → overwrite guard → mkdir → 写产物），并明确"fail-closed leaves no residue"是 hard contract。
- `tests/test_provider_health.py::test_production_unhealthy_provider_leaves_no_out_dir`：新增测试：
  - 前置：`out_dir` 不存在。
  - 触发：production + unhealthy provider。
  - 后置：`ProviderUnavailableError` 被抛出，**且** `out_dir.exists()` 为 False。
  - 重跑：再调一次正常 production run 能在同一路径成功，证实前一次失败没有阻断路径。

### Documentation Alignment

- `HANDOFF.md` "下一步建议 #6" 之后补一段说明：`runs/` 只保留根 `.gitkeep` 是 **有意** 约定（与 `.gitignore` 的 `runs/` + `!runs/.gitkeep` 完全对齐），`runs/development/` 与 `runs/production/` 子目录刻意**不预留占位**，因为任何 run 都走 CLI 显式 `--out`，production smoke 永远走 `/tmp`。对照之下 `logs/` 与 `assets/` 的子目录保留 `.gitkeep`，因为它们是开发期常态产物。明确告知**新 AI 不要恢复那两个 .gitkeep**，会把生产路径语义搞混。

### Hard-Boundary Preservation

- `production` 硬边界不变（`pending_manual_approval` + `tool=null` + `submit_paid_jobs=False` + `allow_overwrite_runs=False` + `allow_provider_fallback=False`）。
- 新增 contract：**fail-closed 不留残留**——任何抛出 `ProviderUnavailableError`、`EnvironmentBoundaryError`、`BrokenReferenceError` 的失败路径，都必须在 `out_dir` 创建前完成判定，确保失败后下一轮调用能用同一路径。
- `tests/test_boundaries.py` 8 条边界用例仍全过；新增 P2 修复未改动任何 `_production_locked_keys` / `_enforce_boundaries` 逻辑。

### Verification

- `python3 -m pytest` —— **51 passed** in 0.78s（37 老 + 13 provider_health + 1 新增 "no residue" 用例）。
- `/tmp/planner_smoke_v4_dev` + `/tmp/planner_smoke_v4_prod` smoke 均通过：10 个产物，`run_summary` audit 字段正确，`validate` ok。
- 手动复现 Codex 报告的 P2 场景（production + unhealthy_stub + 不允许 fallback）：
  ```
  before: False
  raised: ProviderUnavailableError - Provider 'residue_check_stub' failed health check
  after : False   ← 关键：out_dir 没有残留
  ```
- 仓库 `runs/` 仅含根 `.gitkeep`，smoke 产物与 `config/production.json` 已删除，无污染。

### Outstanding / Still TODO

- 仍按 brief 严格不做：调用真实 LLM、新增真实 API key / cookie / 账号、把 OpenAI / Anthropic / 任何模型 SDK 作为必需依赖、把生成任务直接放入 `pending` 队列、接入 Flowith/libTV/可灵/即梦/ComfyUI、把生成的 provider 写成会触发真实网络/付费请求的方法。
- 后续候选（待 Codex 复审）：OpenAI / Anthropic adapter skeleton（仅接口形态，不调真模型）；opt-in `probe`（与 `health_check` 严格区分）；Phase 2 continuity audit。

## 2026-07-04 (Codex Adapter Skeleton Assignment)

### Review Result

- Codex 复审通过 Proma 的 provider health check / fallback / fail-closed no-residue 修复。
- 确认 `python3 -m pytest` 为 51 passed。
- 确认 production smoke 仍为 `pending_manual_approval` + `tool=null`，`fallback_used=false`。
- 确认仓库 `runs/` 只保留根 `.gitkeep` 是当前约定。

### Assigned To Proma

- 下一步执行 OpenAI / Anthropic adapter skeleton。
- 只做接口形态、registry 注册、配置门槛和本地 `health_check()`。
- 不调用真实 LLM，不新增必需 SDK，不新增真实密钥，不做网络 probe。

### Boundary

- development 可以 fallback 到 deterministic，并记录审计字段。
- production 必须 fail-closed，且失败不留下 `out_dir` 残留。
- adapter skeleton 不得改变 executor task 的 `tool=None` 和 production `pending_manual_approval` 边界。

## 2026-07-04 (Proma OpenAI / Anthropic Adapter Skeleton)

### Background

- Codex 把下一轮交给 Proma：在不调真实 LLM 的前提下，OpenAI / Anthropic 各做一个 adapter 骨架，让 `planner_provider` 配置这两个名字时 registry 不报 unknown。`health_check()` 仅看本地 env / 可选 SDK；production fail-closed，development 可 fallback。

### Added

- `planner/providers/openai_adapter.py` —— `@register("openai")` 的 `OpenAIProvider`，`health_check()` 只检测 `PLANNER_OPENAI_API_KEY` / `OPENAI_API_KEY` 与可选 `openai` SDK 的存在（`importlib.util.find_spec`），任一缺失即 `healthy=False` 并给出可执行的 `reason`。planner 命名空间优先于 provider 命名空间；空字符串 / 空白等同于未配置。五个规划方法抛 `NotImplementedError` 并写明"Phase-1 skeleton 不接真模型"。
- `planner/providers/anthropic_adapter.py` —— `@register("anthropic")` 的 `AnthropicProvider`，镜像 `OpenAIProvider`，env 名换成 `PLANNER_ANTHROPIC_API_KEY` / `ANTHROPIC_API_KEY`、可选 SDK 名换成 `anthropic`。
- `planner/providers/__init__.py` —— 包级 import 触发两个新 adapter 的 `@register`，`available_providers()` 现在是 `{anthropic, deterministic, openai}`；`__all__` 增加 `OpenAIProvider` / `AnthropicProvider`。
- `tests/test_openai_anthropic_adapter.py` —— 20 个新用例，覆盖：
  - registry 注册 / 类身份；
  - 缺 env、key 在 SDK 缺失、SDK 在 key 缺失三种 unhealthy 分支；
  - key + SDK 同时齐备 → healthy 且 `details` 记录 namespace 与 SDK 模块名；
  - planner 命名空间 vs provider 命名空间的优先级；
  - 空串 / 空白等同 missing；
  - 五个规划方法抛 `NotImplementedError` 且文案明确禁止真调用；
  - **end-to-end** parametrized over `openai` / `anthropic`：
    - development fallback：`script_parse.json` + 6 个规划产物字节级等于 clean deterministic run；`run_summary.json` audit 字段全；`executor_tasks.json` 仍是 `tool=null` / `status=pending`。
    - production fail-closed：CLI 退出码 1，`out_dir.exists()` 为 False；同一路径再用 deterministic 配置重跑成功，无残留。

### Changed

- `planner/providers/__init__.py` 文档段新增 OpenAI / Anthropic 骨架说明，强调"real model adapters must add their own SDK as **optional** dependencies"。

### Hard-Boundary Preservation

- `production` 边界不变：`executor_default_status=pending_manual_approval` / `tool=None` / `submit_paid_jobs=False` / `allow_overwrite_runs=False` / `allow_provider_fallback=False`。对 OpenAI / Anthropic 的失败 raise 走 `ProviderUnavailableError`，与现有 unhealthy provider 一致。
- `pyproject.toml` 不新增必需依赖；`openai` / `anthropic` 仅在 adapter 内通过 `importlib.util.find_spec` 检查，未安装时 health check 报 unhealthy，不会偷偷 `import openai` / `import anthropic`。
- `health_check()` 严格 side-effect free：只读 env 与 `find_spec`，不发起任何网络 / 推理 / 登录请求；没有引入 opt-in `probe`，那一步留到下一轮单独设计。
- 五个规划方法即使在 `healthy=True` 时也抛 `NotImplementedError`：任何绕过 `_select_provider` 健康门禁的直接调用都会拿到语义清晰的失败，绝不会变成付费推理请求。
- `tests/test_boundaries.py` 8 条 + `tests/test_provider_health.py` 13 条 + `tests/test_providers.py` 11 条全部仍通过；新测试 20 条无回归，总计 71 passed。

### Verification

- `python3 -m pytest` —— **71 passed** in 0.86s（24 老 + 11 provider + 2 pipeline + 13 provider_health + 1 no-residue + 20 adapter skeleton）。
- `available_providers()` 在 import 时即给出 `{anthropic, deterministic, openai}`。
- dev smoke（`PLANNER_PROVIDER=openai` 且 `allow_provider_fallback=true`）→ `run_summary` 显示 `requested_provider=openai`、`effective_provider=deterministic`、`fallback_used=true`、`fallback_reason="OpenAI adapter is not configured: set PLANNER_OPENAI_API_KEY..."`、`provider_health.{openai,deterministic}` 完整；`executor_status=pending`、`tool=null`。
- dev smoke（`anthropic`）→ 同上对称。
- prod smoke（`openai` + `allow_provider_fallback=false`）→ CLI 退出码 1，`out_dir.exists()=false`；随后用同一路径 + 默认 prod 配置重跑成功生成 10 个 JSON 产物。`anthropic` 同上对称。
- 仓库 `runs/` 仅含根 `.gitkeep`，未引入 `runs/development/` 或 `runs/production/`，smoke 产物全部在 `/tmp` 已清理。

### Adapter health check 示例输出（dev 测试 env，缺失 key）

```text
openai:    healthy=False, reason="OpenAI adapter is not configured: set PLANNER_OPENAI_API_KEY (preferred) or OPENAI_API_KEY before requesting this provider."
anthropic: healthy=False, reason="Anthropic adapter is not configured: set PLANNER_ANTHROPIC_API_KEY (preferred) or ANTHROPIC_API_KEY before requesting this provider."
```

### Outstanding / Still TODO

- 仍按 brief 严格不做：调用真实 LLM、新增真实 API key / cookie / 账号、把 OpenAI / Anthropic SDK 作为必需依赖、把生成任务直接放入 `pending` 队列、接入 Flowith/libTV/可灵/即梦/ComfyUI、把 provider `health_check` 改成会发起付费网络/推理请求的方法。
- 下一轮（仍待 Codex 复审）：
  - 设计 opt-in `probe`（与 `health_check` 严格区分；可能发起一次最小推理或账户握手，仅在显式 `--probe` 或 env 开启下运行）。
  - 真正接通 OpenAI / Anthropic 时再把 `NotImplementedError` 替换成实际规划逻辑；先用 `probe` 替代 `health_check` 里的"真网络可达"信号，确保生产仍 fail-closed。
  - Phase 2 continuity audit（Zcode）。
  - Phase 3 executor adapter interface（只设计，不接入）。

### Next (待 Codex 复审)

- Codex 复审 OpenAI / Anthropic adapter skeleton：
  - 是否仍 fail-closed（含 production + unhealthy 路径不留 `out_dir` 残留）；
  - 是否仍无真实 API 调用、无必需 SDK、无真实密钥；
  - 是否未改变 executor 硬边界（`tool=None` / production `pending_manual_approval`）；
  - `health_check()` 是否仍是本地信号（无网络 / 无 SDK import / 无付费探活）。

## 2026-07-04 (Proma P1 Codex Review Fix — Skeleton Always Unhealthy)

### Background

Codex 第一次复审命中 1 个 P1 阻断：

> `planner/providers/openai_adapter.py` / `anthropic_adapter.py` 在 key + SDK 都存在时返回 `healthy=True`。但规划方法抛 `NotImplementedError`（不是 `PlannerError`），CLI 顶层 `try/except PlannerError` 会漏掉 traceback；production 会先 `mkdir` 再 `build_bibles()` 抛错，留下空 `out_dir`，破坏 `fail-closed leaves no residue` contract。

**修复策略**：在 health check 里加一道 *Phase-1 implementation gate*。即使 API key + 可选 SDK 都就位，skeleton 也必须 `healthy=False`；details 里记录所有前置信号（`api_key_present` / `api_key_env` / `sdk_installed` / `implemented=false` / `real_calls=disabled`），reason 说明"planning methods are not implemented in Phase 1"。`Configured 但未实现` 和 `未配置` 在 pipeline 视角下都属于"不能跑"，统一走 `_select_provider` 现有的 fail-closed / fallback 路径。

### Changed

- `planner/providers/openai_adapter.py`：
  - `health_check()` 的最终分支（key + SDK 都通过）由 `healthy=True` 改为 `healthy=False`；`details` 新增 `implemented="false"`；reason 改为
    `"OpenAI adapter skeleton is configured locally (api_key_env=..., sdk=openai installed) but the planning methods are not implemented in Phase 1. The pipeline will refuse to run this provider (fail-closed in production; auditable fallback to deterministic in development with allow_provider_fallback=true) until the implementation lands."`
  - 模块 docstring / 类 docstring / `health_check` 内 docstring 三处说明"Phase-1 skeleton stays unhealthy even with full prerequisites"，并写明原 P1 review 触发的具体失败链（select → mkdir → NotImplementedError → leak past CLI → 空 out_dir）。
- `planner/providers/anthropic_adapter.py`：镜像上述改动。

### Tests

- `tests/test_openai_anthropic_adapter.py`：
  - 把原来的"healthy when key+SDK present"测试改成 `unhealthy even when key and SDK present`：
    - `test_openai_health_check_unhealthy_even_when_key_and_sdk_present`
    - `test_anthropic_health_check_unhealthy_even_when_key_and_sdk_present`
    - 断言 `healthy=False`、`"planning methods are not implemented"` 在 reason 里、`details["implemented"] == "false"`、`api_key_present=true`、`sdk_installed=true`、`real_calls=disabled`、`phase=1-skeleton`。
  - 原 `test_openai_prefers_provider_native_namespace_as_fallback` / `..._anthropic_...` / `test_openai_planner_env_wins_when_both_set`：依然断言 namespace bookkeeping 正确，但 `healthy=False`。
  - `test_openai_planning_methods_raise_not_implemented` / `test_anthropic_...`：补充 `health_check()` 也断言 `healthy=False`（与新 contract 对齐）。
  - 新增 2 组 parametrized end-to-end（共 4 个 case），**专门压** P1 路径：
    - `test_development_key_and_sdk_present_still_falls_back` —— dev + 设置 `PLANNER_OPENAI_API_KEY` / `PLANNER_ANTHROPIC_API_KEY` + monkeypatch SDK 检测为 True，验证 `fallback_used=True`、`fallback_reason` 含 `"planning methods are not implemented"`、`provider_health[provider].details.implemented == "false"`、artifact 字节级等于 clean deterministic run、executor 仍是 `tool=None` / `status=pending`。
    - `test_production_key_and_sdk_present_fails_closed_with_no_residue` —— prod + 同上设置，验证 `ProviderUnavailableError` 抛出、`out_dir.exists()=False`、用默认 prod 配置在同路径重跑能成功，证明路径没被污染。

### Hard-Boundary Preservation

- `production` 边界不变；P1 修复让 production + 已配 key+SDK 的开发者也走 fail-closed 路径：
  - 任何 `openai` / `anthropic` 选型在 Phase 1 都 raise `ProviderUnavailableError`；
  - `out_dir` 在抛出前不存在，`mkdir` 只在 `_select_provider` 通过后才执行；
  - 同一路径用 deterministic 配置重跑成功，证实失败未污染路径。
- `pyproject.toml` 仍只列 `pydantic` + `click`；`openai` / `anthropic` 不进必需依赖。
- `health_check()` 仍是 pure local signal：无网络、无 SDK import（仅 `find_spec`）、无付费探活。
- 5 个规划方法继续抛 `NotImplementedError`（语义清晰）；但因为 `_select_provider` 永不选 unhealthy 的骨架，这些方法在正常 pipeline flow 中不可达。
- `tests/test_boundaries.py` 8 条 + `tests/test_provider_health.py` 13 条 + `tests/test_providers.py` 11 条全部仍通过。

### Verification

- `python3 -m pytest` —— **75 passed** in 0.89s（24 老 + 11 provider + 2 pipeline + 13 provider_health + 1 no-residue + 20 老 adapter + 4 新增 key+SDK-present parametrized）。
- 健康检查直接对比（key 存在 + 真实 SDK 已安装）：
  ```text
  openai SDK actually installed: True
  healthy: False
  reason: 'OpenAI adapter skeleton is configured locally
          (api_key_env=PLANNER_OPENAI_API_KEY, sdk=openai installed)
          but the planning methods are not implemented in Phase 1...'
  details keys: ['api_key_env', 'api_key_present', 'implemented',
                 'phase', 'real_calls', 'sdk_installed', 'sdk_module']
  ```
- dev smoke：设 `PLANNER_OPENAI_API_KEY` 跑 dev run → `requested_provider=openai`、`effective_provider=deterministic`、`fallback_used=True`、`fallback_reason` 含 skeleton gap，`provider_health.openai.{healthy=false,details.implemented=false,api_key_present=true,sdk_installed=true}`，executor 仍是 `tool=null` / `status=pending`。
- prod smoke：设 `PLANNER_OPENAI_API_KEY` 跑 prod run → CLI 退出码 1、`out_dir.exists()=False`；同一路径用默认 prod 配置重跑成功（10 个 JSON 产物），证实 `fail-closed leaves no residue` contract 恢复。
- 仓库 `runs/` 仍只含根 `.gitkeep`，未引入 `runs/development/` / `runs/production/`。

### Adapter health check 示例输出（key + 真实 SDK present）

The two adapters are mirrors: namespace (`PLANNER_OPENAI_API_KEY` vs
`PLANNER_ANTHROPIC_API_KEY`) and SDK module (`openai` vs `anthropic`)
are the only deltas. Both report `healthy=False` until Phase-2 ships
real planning methods.

```text
openai: healthy=False
  reason="OpenAI adapter skeleton is configured locally
          (api_key_env=PLANNER_OPENAI_API_KEY, sdk=openai installed)
          but the planning methods are not implemented in Phase 1.
          The pipeline will refuse to run this provider..."
  details={
    "phase": "1-skeleton",
    "real_calls": "disabled",
    "api_key_env": "PLANNER_OPENAI_API_KEY",
    "api_key_present": "true",
    "sdk_module": "openai",
    "sdk_installed": "true",
    "implemented": "false"
  }

anthropic: healthy=False
  reason="Anthropic adapter skeleton is configured locally
          (api_key_env=PLANNER_ANTHROPIC_API_KEY, sdk=anthropic installed)
          but the planning methods are not implemented in Phase 1.
          The pipeline will refuse to run this provider..."
  details={
    "phase": "1-skeleton",
    "real_calls": "disabled",
    "api_key_env": "PLANNER_ANTHROPIC_API_KEY",
    "api_key_present": "true",
    "sdk_module": "anthropic",
    "sdk_installed": "true",
    "implemented": "false"
  }
```

### Outstanding / Still TODO

- 仍按 brief 严格不做：调用真实 LLM、新增真实 API key / cookie / 账号、把 OpenAI / Anthropic SDK 作为必需依赖、把生成任务直接放入 `pending` 队列、接入 Flowith/libTV/可灵/即梦/ComfyUI、把 provider `health_check` 改成会发起付费网络/推理请求的方法。
- 下一轮（仍待 Codex 复审）：
  - 设计 opt-in `probe`（与 `health_check` 严格区分；可能发起一次最小推理或账户握手，仅在显式 `--probe` 或 env 开启下运行）。
  - 真正接通 OpenAI / Anthropic 时再把 `NotImplementedError` 替换成实际规划逻辑；先用 `probe` 替代 `health_check` 里的"真网络可达"信号，确保生产仍 fail-closed。
  - Phase 2 continuity audit（Zcode）。
  - Phase 3 executor adapter interface（只设计，不接入）。

### Next (待 Codex 复审)

- Codex 二次复审 P1 修复：
  - skeleton 即使 key + SDK 齐全，`healthy=False` 是否成立；`details` 是否记录 `api_key_present` / `api_key_env` / `sdk_installed=true` / `implemented=false` / `real_calls=disabled`；reason 是否清晰。
  - 4 个新增 parametrized 测试是否钉住 dev fallback + prod fail-closed 路径。
  - 其他红线（fail-closed / no real calls / no required SDK / executor boundary）是否仍守。

## 2026-07-10 (Proma Codex Sub-Session Review + P3 Polish)

### Background

按用户 2026-07-10 指令，Proma 通过新建子会话（Proma collaboration `delegate_agent` role=review）派 Codex-style 审查子 agent，对 P1 修复做对抗式独立审查。子会话结论：**Verdict = PASS**，无 P1/P2，仅 4 个 P3 磨平项。

### Sub-Session Review Summary

- **Confirmed contract points** (10 条)：Phase-1 implementation gate 是 load-bearing；details 完整；`pyproject.toml` 未新增 SDK 依赖；无真实 API；production fail-closed 守住（`ProviderUnavailableError` 在 `mkdir` 前抛出、`out_dir` 不留）；executor 边界未动；pipeline preflight 顺序未动；dev fallback audit 字段完整；registry 契约稳；三件套对齐；`runs/` 只含 `.gitkeep`。
- **P3 findings** (4 条)：empty-string 测试只覆盖 OpenAI；`_sdk_available` helper 的 monkeypatch 契约需文档化；`details["implemented"]` 字符串哨兵可常量化；CHANGELOG 示例缺 Anthropic 类比。
- **Open questions**：(a) Phase-2 实施时 test rename + flip 规划；(b) Anthropic smoke 因本机 SDK 未装走 SDK-missing 分支，是否需 HANDOFF 加 smoke harness 提醒；(c) 保留 `NotImplementedError` 作为纵深防御。

### Proma Polish (4 个 P3)

- **P3 #1**：`tests/test_openai_anthropic_adapter.py::test_empty_string_env_var_is_treated_as_missing` parametrize 跨 `openai` 和 `anthropic`，helper 由 `_adapter_module_for(provider_name)` 选 module；两个 adapter 都覆盖。
- **P3 #2**：`planner/providers/openai_adapter.py::_openai_sdk_available` 与 `anthropic_adapter.py::_anthropic_sdk_available` 各加 `.. note::` 段，明确 monkeypatch 契约（`setattr(<module>, "_<provider>_sdk_available", lambda: True)`），并警告未来 refactor 时若改名 / 改绑需同步测试。
- **P3 #3**：两个 adapter 模块顶部各加 `IMPLEMENTED_FALSE = "false"` 常量；`details["implemented"] = IMPLEMENTED_FALSE` 替换字面量。`planner/providers/base.py::ProviderHealth.details` docstring 增加一段说明：values 是 string sentinels（`"true"` / `"false"`），不是 bool，理由是 JSON round-trip 稳定 + `run_summary.json` 字面可读。
- **P3 #4**：`CHANGELOG.md` "key + 真实 SDK present" 示例块从单 OpenAI 扩到 OpenAI + Anthropic 镜像，块头加一句"The two adapters are mirrors"声明对称。

### Verification

- `python3 -m pytest` —— **76 passed** in 0.98s（24 老 + 11 provider + 2 pipeline + 13 provider_health + 1 no-residue + 25 adapter skeleton；P3 #1 parametrize 把 empty-string 1 → 2 用例）。
- 仓库 `runs/` 仍只含根 `.gitkeep`，未引入 `runs/development/` / `runs/production/`。
- `PROJECT_STATUS.json` 合法；phase 推到 `phase1_adapter_skeleton_complete_designing_opt_in_probe`；`next_actions[0]` 改为 `design_provider_optional_probe_separate_from_health_check_no_default_paid_call`。

### Open Question Decisions（用户 2026-07-10 拍板）

- (a) **Phase-2 实施时 rename + flip**：保留作为 Phase-2 任务的"先决条件"显式条款，落到 `docs/PROMA_EXECUTION_BRIEF.md` 的下一节。
- (b) **Anthropic smoke harness 提醒**：在 `HANDOFF.md` "下一轮任务"加一条，明确未来复审者需要 `pip install anthropic` 后再跑 manual smoke 才能触达 implementation gate。
- (c) **保留 `NotImplementedError`**：作为 `_select_provider` 健康门之外的第二道防线。任何未来 refactor 重新引入 P1 退化时都会被这层捕获。

### Next（转向 opt-in probe 设计）

- Codex 子会话已确认 Phase-1 implementation gate 守住 + 4 P3 磨平；OpenAI / Anthropic adapter skeleton **正式放行**。
- 下一轮：**设计 opt-in `probe`** —— 与 `health_check()` 严格分离，只在显式 `--probe` / env 开启下发起最小推理或账户握手；不复用 `health_check()` 走付费请求。设计要点（先不进代码，只出 brief）：
  - `BaseProvider.probe()` 新抽象方法（默认 `NotImplementedError`），adapter 自己实现；
  - 入口控制：CLI `--probe` flag 或 `PLANNER_PROVIDER_PROBE=1` env；默认 off；
  - 失败语义：probe 抛错时直接 raise `ProviderProbeError(PlannerError)`，让 CLI 顶层 `try/except PlannerError` 捕获，不走 production 的 fail-closed 路径；
  - probe 永不写入 `run_summary.json`，只在 stderr 输出结构化结果；
  - 不进 `pyproject.toml` 必需依赖；真接 SDK 走 optional；
  - 与 `health_check()` 互不影响：probe 不修改 `ProviderHealth`，`health_check` 不调用 probe。

### Outstanding / Still TODO

- 仍按 brief 严格不做：调用真实 LLM、新增真实 API key / cookie / 账号、把 OpenAI / Anthropic SDK 作为必需依赖、把生成任务直接放入 `pending` 队列、接入 Flowith/libTV/可灵/即梦/ComfyUI、把 provider `health_check` 改成会发起付费网络/推理请求的方法。
- Phase 2 continuity audit（Zcode）继续并行，不依赖 probe 设计。
- Phase 3 executor adapter interface 设计待 probe 落地后启动。
- 后续生产验证仍走 `/tmp` 或 CI 目录；**不要再写** `runs/production/`、`runs/development/.gitkeep`、`runs/production/.gitkeep`。

## 2026-07-13 (Proma Phase 2 — Harness Engineering 落地)

### Background

按 `docs/PROMA_V1_REVIEW_AGENT_HARNESS_PLAN.md` 第四部分，把 Harness
Engineering 从"占位 README"推进到"可重复运行的验收脚本"。Phase 2
只做 harness 落地，**不实现完整 planner/agent 产品功能**（用户明确
要求：暂时不让产品内 Agent 自动执行危险动作，也不接入 arbitrary shell）。

### Harness 脚本（5 个全部落地）

1. **`harness/smoke_cli.py`** — CLI 端到端 smoke：
   - `planner --help` 列出 `run/validate/batch/project/export`。
   - `planner run --env development` 跑 `samples/v1/EP01.txt`，产出 11 个 artifacts（10 planning + `run_summary.json`），`fallback_used=False`，`executor_status=pending`。
   - `planner validate --env development` → `ok=true`。
   - `planner batch --env development --scripts samples/v1/` → 3/3 done。
   - `planner project init / validate / batch --project` round-trip → 3/3。
   - `planner export --run / --batch` Markdown/HTML/CSV，secret-leak guard（拒绝 `sk-` / `sk-ant-` / `Bearer sk-` 写入导出报告）。
   - 所有产物落 `/tmp/smoke_cli_<pid>`。

2. **`harness/smoke_gui.py`** — GUI 端到端 smoke：
   - 启 `planner-web --no-window --port 18766 --repo-root <repo>`，等 `/api/health` 200。
   - `/api/health` 返回 `providers=[anthropic, deterministic, openai, openai_compatible]`。
   - `/`, `/app.js`, `/style.css` 静态资产 ≥ 100 bytes。
   - `/api/config?env=development` 返回 dev config；`?env=production` 404 带 `production.example.json` 复制 hint。
   - `GET /api/model-config` 返回 defaults；`PUT /api/model-config` round-trip；拒绝 `api_key_env="sk-..."` 字面值。
   - `POST /api/runs` 异步起 dev run，poll summary 至 `counts.shots > 0`。
   - `POST /api/batches` 同步跑 3 集 batch → 3/0 done/failed。
   - 进程 `_stop_server` 用 SIGTERM + `wait(timeout=8)`，守 P1-3 修复。

3. **`harness/fake_model_e2e.py`** — fake model 端到端：
   - `load_model_config` 拒绝 `api_key_env="sk-..."`（schema validator 拦截）。
   - 配置 `planner_provider=openai_compatible` + `enable_real_model_calls=true` + `PLANNER_SMOKE_FAKE_KEY=...`。
   - monkeypatch `planner.providers.openai_compatible_adapter.http_post` 到 in-process `_FakeOpenAIServer`：解析 user prompt 中关键词分发到 `_bibles_envelope / _beats_envelope / _shots_envelope / _image_prompts_envelope / _video_prompts_envelope`，每个 envelope 嵌入 sentinel 字符串（`SmokeFake-MC` / `SmokeFake-Stage` / `SmokeFake-Prop`）。
   - 跑 `pipeline.run` 后断言 `effective_provider=openai_compatible`、`fallback_used=False`、`provider_runtime.api_key_env=PLANNER_SMOKE_FAKE_KEY`、artifacts 真含 sentinel、`bearer_present_count >= 1`。
   - production + `enable_real_model_calls=false` → `ProviderUnavailableError` 抛出后 `out_dir.exists()=False`（无残留）。

4. **`harness/permission_boundaries.py`** — 权限 / 边界：
   - 4 个 PLANNER_* env-var downgrade attempts（`EXECUTOR_DEFAULT_STATUS=pending` / `SUBMIT_PAID_JOBS=1` / `ALLOW_OVERWRITE_RUNS=true` / `ALLOW_PROVIDER_FALLBACK=true`）在 production 下全部被拒绝（subprocess 隔离 env mutation）。
   - 篡改 `production.json` 让 `allow_overwrite_runs=true / submit_paid_jobs=true / executor_default_status=pending / allow_provider_fallback=true` → 仍抛 `ConfigError`（`_enforce_boundaries` 二次防御生效）。
   - production `--out <repo>/runs/...` 被拒，无 directory 创建。
   - production `--out <appdata>/...` 正常落 run，request `run_dir` 在 repo 外、`run_summary.json` 存在、`requested_provider=deterministic`、`fallback_used=False`。
   - `save_model_config` + `load_model_config` 拒绝 `api_key_env="sk-..."`（UPPER_SNAKE_CASE validator）。
   - 即使 env 含 `PLANNER_PERM_KEY=sk-supersecretsentinelkey-...`，`run_summary.json` 内**不含** sentinel key 字符串（API key value 永不落盘）。
   - `executor_tasks.json` 所有 task `tool=None`，且 `run_summary.json` / `asset_manifest.json` 不含 `flowith / libtv / keling / jiemeng / comfyui` 任一字面值。
   - agent placeholder 步骤列 7 条静态规则（不执行 agent）：no arbitrary shell / no api_key value / no paid job without approval / no production silent fallback / 必带 EvidenceRef / approval denied 时不执行 / 不碰 repo runs/。

5. **`harness/agent_scenarios/`** — 4 个 JSON 场景 + `run_all.py`：
   - `diagnose_failed_run.json` — diagnose / read_only，3 expected tools / 6 forbidden。
   - `review_prompt_refs.json` — review / read_only，4 expected / 5 forbidden。
   - `batch_continuity.json` — review / read_only，6 expected / 6 forbidden（跨集 bible 一致性）。
   - `approval_required_write.json` — approval_gate / requires_approval，0 expected tools、1 approval request shape、5 forbidden（写动作必须停 approval request，deny 后不执行）。
   - `run_all.py` 校验：每个 scenario 必含 `scenario_id/version/description/category/risk_level/expected_outcome/input/expected_tool_calls/forbidden_tool_calls`；`expected ∩ forbidden = ∅`；`risk_level=requires_approval` 必有非空 `expected_approval_requests` 且每个含 `action/must_list_side_effects/must_list_revert_path`；诊断 / review 类跑真 run + batch 验证 `expected_tool_calls` 引用的 artifact 真存在。

### Hard-Boundary Preservation

- **不实现产品内 agent**。`planner/agent/` 不存在；harness 只固化场景定义 + 校验 JSON shape，不跑任何 agent runtime。
- **不接入 arbitrary shell**。agent scenario 显式禁止 `execute_arbitrary_shell`；harness 自身只跑被显式 allowlist 的 `python3 -m planner / planner.web` 子命令。
- **不调用真实 LLM**。`fake_model_e2e.py` 的 `_FakeOpenAIServer` 完全 in-process monkeypatch `http_post`；production smoke 仍走 deterministic。
- **API key 不落盘**。fake model config 用 `api_key_env=PLANNER_SMOKE_FAKE_KEY`（env var name），env var value `fake-key-do-not-use-in-prod`；`run_summary.json` 不含 key 字面值。
- **仓库 `runs/` 仍只含根 `.gitkeep`**。所有 harness smoke 产物落 `/tmp/smoke_*_<pid>` / `/tmp/permission_boundaries_<pid>` / `/tmp/agent_scenarios_<pid>` / `/tmp/fake_model_e2e_<pid>`。
- **production fail-closed contract 保留**。`permission_boundaries.py` 的 4 个 env-var attempts 全部 fail closed；tampered production.json 仍 fail closed；`executor_tasks.json.tool=None`。
- **pyproject `[project]` 基础依赖未动**：仍只 `pydantic + click`。新增 harness 脚本只依赖 stdlib（`subprocess / urllib / json / socket / tempfile / signal`）。

### Verification

- `python3 -m pytest` -- **267 passed**（v1.0 RC P1/P2 后的全量回归未受影响，harness 不新增 pytest 单元测试，验收走独立 harness 脚本入口）。
- `python3 harness/smoke_cli.py` -- 6 steps 全过：help / run dev / validate / batch / project / export。
- `python3 harness/smoke_gui.py` -- 8 steps 全过：help / health / 静态资产 / config dev / production 404 / model-config round-trip / POST runs / POST batches。
- `python3 harness/fake_model_e2e.py` -- 3 steps 全过：load rejects literal / dev run via fake / production fail-closed。
- `python3 harness/permission_boundaries.py` -- 8 steps 全过：4 env-var rejects / tampered config reject / repo out_dir reject / app-data out_dir success / api-key hygiene / run_summary no leak / executor tool neutral / agent rules printed。
- `python3 harness/agent_scenarios/run_all.py` -- 4 scenarios 全过：3 shape + 3 approval-gate shape + 3 live cross-check。

### Files Changed

- 新增：`harness/smoke_cli.py` / `harness/smoke_gui.py` / `harness/fake_model_e2e.py` / `harness/permission_boundaries.py`
- 新增：`harness/agent_scenarios/{diagnose_failed_run,review_prompt_refs,batch_continuity,approval_required_write}.json` / `harness/agent_scenarios/run_all.py`
- 修改：`harness/README.md`（v1.0 harness 落地说明 + 5 脚本清单 + 验收命令）
- 修改：`PROJECT_STATUS.json`（`phase` 改 `v10_phase2_harness_engineering_smoke_scripts_implemented` / `status` / `next_actions` / `verification` 加 harness 段落）
- 修改：`HANDOFF.md`（顶部加 "Phase 2 Harness Engineering 落地" 段；移除旧"harness 预留未实现"描述）

## 2026-07-13 (Proma Phase 2 Harness Rework — 复审 findings 全部修齐)

### Background

Codex-style 复审 Phase 2 harness 落地发现 6 项问题（3 项 P1 + 3 项 P2）：
CLI run 缺 repo-internal 守卫、smoke_gui 污染真实 OS app-data、smoke_gui
产物落仓库 repo、smoke_install 缺失、port 选择不可靠、`permission_boundaries`
的 repo-internal 测试路径在 /tmp 下不触发。本轮一次性修齐。

### 修复

1. **`planner/env.py::is_inside_repo(path, repo_root)`** — 共享 helper，
   复用 `Path.resolve().is_relative_to`，供 CLI run / batch / GUI 共同使用。
2. **`planner/cli.py::run_cmd`** — `planner run --env production --out <repo>/...`
   在 CLI 边界 rc=2 拒绝，友好文案复用 GUI resolve_out_dir 文案；
   在 `mkdir` 之前触发，不留空 dir 残留。
3. **`planner/batch.py::BatchOptions.resolved_out_dir`** — 改用共享
   `is_inside_repo` helper（之前用 try/except ValueError）。
4. **`planner/model_config.py::default_config_path`** — 加 `PLANNER_MODEL_CONFIG_PATH`
   env var override，让 GUI smoke 可以把模型配置重定向到 /tmp。
5. **`planner/web/run_service.py::os_app_data_dir`** — 加 `PLANNER_APP_DATA_ROOT`
   env var override，让 GUI smoke 的 uploads + runs 都走 /tmp。
6. **`harness/smoke_gui.py`** —
   - 启动 server 前设 `PLANNER_APP_DATA_ROOT=<scratch>` + `PLANNER_MODEL_CONFIG_PATH=<scratch>`。
   - `_find_free_port` 改 `bind(0)` fallback（preferred busy 时由 kernel 分配）。
   - `step_post_run` / `step_post_batch` 接受显式 `out_dir` 参数；响应里
     `out_dir` / `summary_path` 不在 repo 内的硬断言。
7. **`harness/permission_boundaries.py`** —
   - `step_production_refuses_repo_batch_dir` 用真实 `<PROJECT_ROOT>/runs/perm_guard`
     作为 output_dir，让 BatchOptions 守卫真触发。
   - 新增 `step_production_cli_run_refuses_repo_out_dir`，验证 CLI 新守卫。
8. **`harness/smoke_install.py`** — 新增。`pip wheel .` → venv create +
   ensurepip → `pip install wheel` → `planner --help` + `planner-web --help` →
   probe 验证 base install 没有 optional deps。

### 新增测试

- `tests/test_boundaries.py::test_cli_run_production_refuses_repo_internal_out`
- `tests/test_boundaries.py::test_is_inside_repo_helper`（repo-internal /
  external / symlink 三种 case）
- `tests/test_web_run_service.py::test_os_app_data_dir_env_override`
- `tests/test_web_run_service.py::test_default_config_path_env_override`

### Hard-Boundary Preservation

- 仓库 `runs/` 仍只含根 `.gitkeep`（rework 期间的临时 perm_guard / perm_cli_run_guard
  子目录用后即 `shutil.rmtree` 清理）。
- 用户真实 OS app-data 不被写（`smoke_gui` 通过 env var 隔离，`smoke_install`
  用全新 venv）。
- production fail-closed contract 保留：CLI run + batch 都有 repo-internal 拒绝守卫。
- `pyproject.toml [project]` 基础依赖未动（仍只 `pydantic + click`）。

### Verification

- `python3 -m pytest` -- **271 passed**（267 baseline + 4 新增 CLI/边界/env-override）。
- `python3 harness/smoke_cli.py` -- 6 steps 全过。
- `python3 harness/smoke_gui.py` -- 8 steps 全过；POST runs/batches 显式 /tmp out_dir。
- `python3 harness/fake_model_e2e.py` -- 3 steps 全过。
- `python3 harness/permission_boundaries.py` -- 10 steps 全过（新增
  `production batch refuses repo-internal output_dir` + `production CLI run
  refuses repo-internal --out`）。
- `python3 harness/agent_scenarios/run_all.py` -- 4 scenarios 全过。
- `python3 harness/smoke_install.py` -- 5 steps 全过（wheel bundles 5 required
  files + base install 不拉 fastapi / uvicorn / pywebview）。
- 仓库 `runs/` 验证：只有 `.gitkeep`。

### Files Changed

- 修改：`planner/env.py`（`is_inside_repo` helper）/ `planner/cli.py`
  （`run_cmd` repo-internal 守卫 + `from .exceptions import
  EnvironmentBoundaryError`）/ `planner/batch.py`（共享 helper）/ 
  `planner/model_config.py`（env override）/ `planner/web/run_service.py`
  （env override）
- 修改：`harness/smoke_cli.py`（显式 `--model-config` 隔离 OS app-data）/
  `harness/smoke_gui.py`（env override + 显式 /tmp out_dir + port fallback）
  / `harness/permission_boundaries.py`（真 repo 路径 + 新 CLI guard 步骤）
- 新增：`harness/smoke_install.py`
- 修改：`tests/test_boundaries.py`（+3 tests）/ `tests/test_web_run_service.py`
  （+2 tests）
- 修改：`PROJECT_STATUS.json`（phase 改 `v10_phase2_harness_engineering_complete_ready_for_review` /
  status / verification 全部 harness 段标记 pass）/ `HANDOFF.md`（顶部
  Phase 2 段更新到 rework 完工）/ `CHANGELOG.md`（本节）
