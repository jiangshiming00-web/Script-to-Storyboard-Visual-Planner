# Handoff

## 当前状态（2026-07-13 - Phase 0 + Phase 3 P1 完工）

按 `docs/PROMA_V1_REVIEW_AGENT_HARNESS_PLAN.md` 第三部分（产品内 agent read-only / guided）首次落地，配套 Phase 0 (git init + baseline)。**用户已批准的边界（9 条）全部守住**：不接 LLM / 不接 executor / 不增加必需 SDK / 不静默放宽红线 / 不写仓内产物（production）/ 不保存 API key / 不执行任意 shell / 不接 GUI RunRegistry / review-run + review-batch 留 stub。

### Phase 0 — git init + baseline

- `git init -b main`；`.gitignore` 覆盖 `runs/*` + `logs/**` + `assets/**` + `data/**` + `*.egg-info` + `build/` + `.DS_Store` + `__pycache__` + `config/production.json`；保留 19 个 `.gitkeep`（用 `git add -f` 绕过 git 对 `**` re-include 不支持的限制）。
- 2 个 commit：`b9f8dc9` (.gitignore) + `91b3d2a` (baseline v1.0 + Phase 2，112 文件)。
- 271 baseline pytest 仍全过；仓库 `runs/` 仍只含根 `.gitkeep`。

### Phase 3 P1 — `planner/agent/` 子包最小骨架

#### 包结构（flat 6 文件，不引入 sub-package）

```
planner/agent/
├── __init__.py     # 导出 diagnose_run_dir + build_not_implemented_report + Pydantic models + TOOL_REGISTRY
├── cli.py          # Click group "agent" + 3 子命令 + _check_and_write_report 政策
├── redact.py       # 4 条 regex (Bearer + sk- + sk-ant- + gho_; Anthropic 在 OpenAI 之前匹配)
├── readers.py      # 5 个 graceful reader (load_run_summary / load_artifact / list_artifacts / load_batch_summary / list_runs_in_batch)
├── diagnose.py     # Pydantic models + 13 条规则 + 中文摘要 + build_not_implemented_report
└── tools.py        # 6 个 read-only tool + TOOL_REGISTRY + TOOL_ARTIFACT_MAP (与 harness 同步)
```

#### 13 条诊断规则

| # | code | severity | 实现 | 说明 |
|---|---|---|---|---|
| R1 | `production_fallback_used` | error | 委托 validate_run | production run 用 fallback = 红线违规 |
| R2 | `dev_fallback_used` | warning | 独立 | dev fallback 是 expected |
| R3 | `all_providers_unhealthy` | warning | 独立 | provider_health 全 false |
| R4 | `executor_tool_hardcoded` | error | 独立（红线） | executor_tasks[].tool != None |
| R5 | `env_mismatch` | warning | 委托 validate_run | run_summary.env ≠ expected_env |
| R6 | `script_source_mismatch` | error | 委托 validate_run | script_parse.source_path ≠ run_summary.script |
| R7 | `production_executor_status_wrong` | error | 独立 | production + executor_status ≠ pending_manual_approval |
| R8 | `api_key_env_unset` | warning | 独立 | runtime.api_key_env 声明但 env 为空；**production 下 message sanitization** |
| R9 | `real_calls_disabled_but_not_deterministic` | warning | 独立 | runtime.enable_real_model_calls=False 但 effective ≠ deterministic |
| R10 | `missing_run_summary` | error | 入口 | run_summary.json 不存在 |
| R11 | `corrupted_run_summary` | error | 入口（graceful） | run_summary.json 坏 JSON |
| R12 | `partial_run_missing_artifact` | warning | 独立 | run 已 done 但 ≥1 核心 artifact 缺失；**列出哪个缺 + 不下结论 + 不重建** |
| R13 | `image_prompts_count_mismatch` / `video_prompts_count_mismatch` | warning | 独立 | counts.shots ≠ counts.image/video |

#### CLI 表面

- `planner agent diagnose <run-dir> [--expected-env X] [--format json|markdown] [--write-report P] [-v]`
- `planner agent review-run <run-dir> [--write-report P]`（stub）
- `planner agent review-batch <batch-dir> [--write-report P]`（stub）

#### 退出码语义

| 场景 | exit |
|---|---|
| `status="errors"` | 1 |
| `status="warnings"` 或 `status="ok"` | 0 |
| production + `--write-report` 仓内 | 2 (policy refusal) |
| 参数错误（missing dir） | 2 |
| Stub 命令（合法参数） | 0 |
| Stub 命令（不合法参数） | 2 |
| Traceback 泄露 | fail tests（不达用户） |

#### `--write-report` 政策（关键边界）

- dev + 仓内 → stderr 黄色 WARN + allow + 落盘
- production + 仓内 → stderr 红字 + rc=2 + **不写**（用 `is_inside_repo` 共享 helper）
- production + 仓外 → 正常写
- run_summary 缺失 → 默认按 production policy 处理（fail-closed）

### 7 个 harness scenario 全过

- 现有 4：`diagnose_failed_run` / `review_prompt_refs` / `batch_continuity` / `approval_required_write`
- 新增 3：`diagnose_fallback_used` / `diagnose_partial_run` / `diagnose_secret_redaction`
- `python3 harness/agent_scenarios/run_all.py` —— ALL AGENT SCENARIO STEPS PASSED ✔

### Red line 守门（与 Phase 2 一致 + 新增）

- `pyproject.toml [project]` 基础依赖**未动**：仍只 `pydantic + click`。
- 339 pytest = 271 baseline + 68 新增 agent tests，**零回归**。
- 仓库 `runs/` 仍只含根 `.gitkeep`；smoke 产物走 `/tmp`。
- production fail-closed contract 保留（R1 / R4 / R7 + `--write-report` 政策 + `is_inside_repo` 共享 helper）。
- API key 永不写盘：redact 覆盖所有出口（finding message / summary / stderr / `--write-report` 文件）。
- `executor_tasks.json.tool` 仍 None；R4 红线独立 emit error。
- 不接 GUI RunRegistry（agent 进程独立地址空间）。
- Stub 命令 `tool_invocations=[]` 表明没做任何 read。

### 下一轮

- **review 子会话（扮演 Codex-style 角色）复审 Phase 3 P1**（重点看 read-only 边界 / `--write-report` 政策 / redact 出口 / 13 条规则与 validate_run 复用切分 / stub 不假装 / harness scenario 形状 / 三件套对齐）。
- **Phase 3 P2**：`review-run` + `review-batch` 完整实现 + harness scenarios 加覆盖 + GUI agent 面板（按"核心先于壳层"原则排在最后）。
- **Phase 0 git push to GitHub**（blocked on user URL）。
- **opt-in probe**（与 `health_check` 严格分离）+ Phase Core-3 跨集连续性 + pkg/CI 路线。

## 当前状态（2026-07-13 - Phase 2 Harness Engineering 完工）

按 `docs/PROMA_V1_REVIEW_AGENT_HARNESS_PLAN.md` 第四部分，把 Harness
Engineering 从"占位 README"推进到"6 个可重复运行的验收脚本 + 4 个
read-only agent scenario JSON + 仓库内 CLI 守卫补齐"。**Phase 2 只做
harness 落地 + 复审 findings 修复，不实现完整 planner/agent 产品功能**。

### Harness 脚本（v1.0 全部就位）
- **`harness/smoke_cli.py`** — CLI 端到端 smoke：help / run dev / validate / batch / project / export，含 secret-leak guard。
- **`harness/smoke_gui.py`** — GUI 端到端 smoke：启 `planner-web --no-window`，hit 全部 API + 静态资产 + model-config round-trip + POST runs / batches；用 `PLANNER_APP_DATA_ROOT` + `PLANNER_MODEL_CONFIG_PATH` 隔离用户真实 OS app-data；显式 `/tmp` out_dir；`_find_free_port` fallback 到 `bind(0)`。
- **`harness/fake_model_e2e.py`** — monkeypatch `openai_compatible_adapter.http_post` 到 in-process `_FakeOpenAIServer`，sentinel 字符串验证 artifacts 真来自 fake model；production fail-closed 无残留。
- **`harness/permission_boundaries.py`** — 4 个 PLANNER_* env-var downgrade attempts + tampered production.json + production `--force` 拒绝 + production batch 拒绝 repo-internal output_dir + production CLI run 拒绝 repo-internal --out + app-data out_dir 成功 + run_summary 无 key 泄漏 + executor tool 中立性 + 7 条 agent 静态规则。
- **`harness/smoke_install.py`** — wheel 构建 + venv 创建 + ensurepip + 安装 wheel + 必需依赖 + `planner --help` + `planner-web --help` + base install probe 拒绝 `planner.web`（optional deps 缺失）。
- **`harness/agent_scenarios/{4 个 JSON} + run_all.py`** — 只读场景定义 + 静态形状校验 + 跑真 run/batch live cross-check。

### 仓库内 CLI 守卫补齐（复审 findings 修复）
- **`planner/env.py::is_inside_repo`** — 共享 helper（`Path.resolve().is_relative_to`）。
- **`planner/cli.py::run_cmd`** — `planner run --env production --out <repo>/...` rc=2 拒绝，带 friendly 提示复用 GUI 文案。
- **`planner/batch.py::BatchOptions.resolved_out_dir`** — 改用 `is_inside_repo` 共享 helper。
- 新增 3 个 `tests/test_boundaries.py` 测试（CLI run repo guard + is_inside_repo symlink + helper base case）+ 2 个 `tests/test_web_run_service.py` 测试（env-var overrides）。

### 不做（用户明确要求）
- **不实现产品内 agent**：`planner/agent/` 不存在；harness 只固化场景定义 + 校验 JSON shape。
- **不接入 arbitrary shell**：harness 自身只跑 allowlist 的 `python3 -m planner / planner.web` 子命令；agent scenario 显式 forbid `execute_arbitrary_shell`。
- **不调用真实 LLM**：fake_model_e2e 完全 in-process monkeypatch；production smoke 仍走 deterministic。

### 红线守门
- `pyproject.toml [project]` 基础依赖**未动**：仍只 `pydantic + click`；harness 脚本只用 stdlib（`subprocess / urllib / json / socket / tempfile / signal / venv`）。
- 271 测试全绿（267 baseline + 4 新增 CLI/边界/env-override 测试）。
- 仓库 `runs/` 仍只含根 `.gitkeep`（per-test 临时子目录用后即清）；smoke 产物落 `/tmp/smoke_*_<pid>` 等。
- 用户真实 OS app-data **不被写**：`smoke_gui` 通过 `PLANNER_APP_DATA_ROOT` + `PLANNER_MODEL_CONFIG_PATH` env var 隔离；`smoke_install` 用全新 venv。
- production fail-closed contract 保留（4 个 PLANNER_* env-var + tampered production.json + production run/batch repo-internal 全部 fail closed）。
- API key 永不写盘：`run_summary.json` 只记 `api_key_env`；fake model config 用 env var name 而非字面值。

### 下一轮
- **Codex-style 子会话复审 Phase 2 harness 完工**（重点看新增 repo-internal guard + GUI 隔离 + install smoke）。
- **Phase 0 git init + push**（blocked on user URL）。
- **产品内置 agent 第一阶段**（plan 第三部分，read-only / guided）—— harness 已固化场景定义，agent 落地时直接消费。
- **opt-in probe** + Phase Core-3 跨集连续性。

## 当前状态（2026-07-13 - v1.0 RC P1/P2 修复完成，回到正式 v1.0 ready）

按 `docs/PROMA_V1_REVIEW_AGENT_HARNESS_PLAN.md` 第一/二部分，本轮修复 v1.0
复审发现的 P1/P2 产品使用缺口。**第三部分（产品内置 agent）和 Harness 实现
脚本本轮不做**（用户明确要求只更新协作文档 + 预留验收项）。

### P1 修复（产品使用阻断）
- **P1-1 model config 接入运行链路**：`registry.get_provider(name, settings=None)` +
  `BaseProvider.__init__(settings=None)`；`pipeline._select_provider` 从
  `ModelProviderConfig` 解析 `ProviderRuntimeSettings` 注入 provider；CLI `run` /
  `batch` 加 `--model-config`（缺省读 OS app-data）；`run_summary.json` 加
  `provider_runtime` 审计字段（model/base_url/api_key_env/enable_real_model_calls，
  **不含 key 明文**）；8 个端到端测试（monkeypatch http_post 验证 provider 真收到
  configured settings + production fail-closed 不留空 dir + dev fallback 保留 audit）。
- **P1-2 GUI artifact dict 渲染**：`app.js renderDrawer` 改 `Array.isArray` +
  `Object.keys` 兼容 dict（`run_summary.artifacts` 是 `{name: path}`，旧代码
  `.map()` 会崩）。
- **P1-3 desktop launcher shutdown**：`launch_desktop` 重构持有 `uvicorn.Server`
  实例，`finally` 设 `server.should_exit=True` + `server_thread.join(timeout)`，
  超时 log warning 不静默。2 个测试（fake webview + fake/slow server）。

### P2 修复（产品使用闭环）
- **P2-1 GUI model config 保存/读取 API**：`GET/PUT /api/model-config`（读写 OS
  app-data `default_config_path()`，拒绝字面 key）；`RunRequest` 加
  `model_config_path`；前端启动加载填表单 + Save 按钮 + run 时携带 path。
  production 仍 fail-closed（`allow_provider_fallback` 由 env config 锁定）。
- **P2-2 GUI batch endpoint**：`POST /api/batches` 同步跑 `run_batch()`（FastAPI
  threadpool 不阻塞 event loop），返回完整 `BatchSummary` + `summary_path`；
  production 拒绝 repo 内 out_dir（403）；前端 `batch-btn` 启用。probe 保持
  disabled（v1.1 opt-in）。
- **P2-3 planner batch --project**：`--project DIR` 从 `project.json` 读
  scripts_dir / output_dir / default_env / default_provider；`--env/--scripts/--out`
  改 optional，显式 CLI > project.json > error。

### Harness Engineering（只更新文档 + 预留，不实现）
- `docs/AI_COLLABORATION.md` 已含 Harness Engineering 角色。
- 新建 `harness/README.md` 占位：列出 6 类未来 harness 场景 + v1.0 临时覆盖映射。
  **不建 harness 脚本**（用户明确要求不展开实现）。

### 不做（用户明确要求）
- **不实现第三部分产品内置 agent**：不新增 `planner/agent/`。
- **Harness 不展开实现**：只建 `harness/README.md` 占位。

### 红线守门
- `pyproject.toml [project]` 基础依赖**未动**：仍只 `pydantic + click`。
- 267 测试全绿（247 base + 20 P1/P2）；基础 `pip install -e .` 零回归。
- 仓库 `runs/` 仍只含根 `.gitkeep`；smoke 走 `/tmp`。
- production fail-closed contract 保留。
- API key 永不写盘：`run_summary.json` 只记 `api_key_env`；错误信息自动 redact。

### 下一轮
- **Phase 0 git init + push**（blocked on user URL）。
- **Codex-style 子会话复审 v1.0 formal release P1/P2 修复**。
- **Harness Engineering 实现 smoke 脚本**（本轮只预留）。
- **产品内置 agent 第一阶段**（plan 第三部分，read-only / guided）。
- **opt-in probe** + Phase Core-3 跨集连续性。

## 当前状态（2026-07-11 — v1.0 P2+P3+P4+P5 收口）

按 `docs/PROMA_V1_RELEASE_PLAN.md` 优先级，本轮把 v1.0 客户端从"修阻断"推到"可发版"。

### P2 — 模型配置 + provider
- `planner/model_config.py`：v1.0 结构化模型配置层（`OpenAICompatibleConfig` / `ModelProviderConfig` / `ProviderRuntimeSettings` + `default_config_path()` OS app-data + 拒绝字面 key 的 `save_model_config`）。
- `planner/providers/openai_compatible_adapter.py`：v1.0 第一个 **runtime** provider，走 OpenAI Chat Completions（OpenAI 官方 / vLLM / Ollama / 第三方都覆盖）；stdlib `urllib.request`，SDK 不进必需依赖；`health_check` 只检查本地配置；JSON 解析失败抛 `ProviderOutputError(PlannerError)`；错误信息自动 redact Bearer / sk- / sk-ant- / gho- 等 token。
- `planner/exceptions.py` 新增 `ProviderOutputError`。
- `planner/providers/openai_adapter.py` / `anthropic_adapter.py` 保留 Phase-1 implementation gate hard contract，**不**thin wrap `openai_compatible`；在三个 health_check 分支末尾追加 `ALIGNMENT_HINT` 引导操作员用 `provider=openai_compatible`。

### P3 — 静态 UI + 启动器
- `planner/web/static/{index.html, app.js, style.css}`：单页 SPA，env 切换 / 模型设置 / 剧本上传 / run 历史 / 详情抽屉 / toast region。app.js 只调已记录的 7 个 endpoint。
- `planner/web/launcher.py`：`launch_desktop` (pywebview) + `launch_server_only` (uvicorn)；pywebview 是 optional dep，缺失时 `launch_desktop` 报清楚错误；端口占用 preflight → `RuntimeError`；后台 thread `daemon=False`。
- `planner/web/scripts_entry.py` + `__main__.py`：`planner-web` console_script + `python -m planner.web`。
- `pyproject.toml`：`[project.scripts] planner-web = "planner.web.scripts_entry:main"`。
- `docs/GUI.md`：同事上手手册（安装 / 启动 / 模型配置 / API key / 单集 / 多集 / 查看 / 导出 / 常见错误 / 红线）。

### P4 — project.json + export
- `planner/project.py`：`Project` 模型 + `init_project / load_project / validate_project` + `ProjectValidationReport`。
- `planner/export.py`：`export_run / export_batch` 三种格式（Markdown / HTML / CSV），每种报告都包含 provider audit / bibles / beats / shots / prompts / executor tasks；CSV 多 section + HTML 单文件 inline CSS + MD plain text。
- `planner/cli.py`：`planner project init / validate` 子组 + `planner export --run/--batch --format {markdown|html|csv}`。

### P5 — samples + smoke
- `samples/v1/EP0{1,2,3}.txt`：3 集跨集共享人物 / 场景 / 道具的验收样例。
- 端到端 smoke 通过：`planner batch` 3/3 done；`planner export` md/html/csv 全部 11-14 KB；`planner project init/validate` 通过；`planner-web --no-window --port 18766` → `/api/health` 200，`/` 4122 bytes，`/app.js` 10030 bytes；wheel `pip wheel . --no-deps` 包含 34 文件 + static bundle。
- **243 passed in 12.57s**（原 127 + 116 v1.0 新增；P3 polish + 1 → 244）。

### 红线守门
- `pyproject.toml [project]` 基础依赖**未动**：仍只 `pydantic + click`，`openai` / `anthropic` SDK 仍未进必需依赖。
- 基础 `pip install -e .` 后 243 测试零回归。
- 仓库 `runs/` 仍只含根 `.gitkeep`；smoke 全部走 `/tmp/v10-smoke`，未污染。
- `executor_tasks.json.tool` 仍 `None`；export 测试断言报告不含 `flowith / libtv / keling / jiemeng / comfyui`。
- production fail-closed contract（pending_manual_approval / tool=None / submit_paid_jobs=False / allow_overwrite_runs=False / allow_provider_fallback=False）保留。

### 下一轮（v1.1+）
- **Codex-style 子会话复审 v1.0 完整闭环**（按 `agent-roles.md` 模板，禁止派 SubAgent 自己 Read）：复审 P1-P5 全部 116 个新测试 + 9 个新模块 + 三件套是否对齐 plan §1-§5。
- **Phase 0 git init + push**（blocked on user URL）—— 在 v1.0 release 之前先把代码推到 GitHub。
- **opt-in probe** —— 真实网络可达性的探测，与 `health_check` 严格分离；plan §5 已设计。
- **Phase Core-3 跨集连续性（bible merge）** —— samples/v1 已经准备好 3 集跨集共享人物的样例。
- **PyInstaller spec**（plan §8）—— Mac `.app` + Windows `.exe` 打包 spec 文件。
- **GitHub Actions 矩阵**（plan CI-1）—— tag-triggered release。

## 当前状态（2026-07-11 — v1.0 P1 阻断修复）

按 `docs/PROMA_V1_RELEASE_PLAN.md` 优先级，本轮先修 v1.0 客户端安装的两个阻断问题 + 同步注释。

- **wheel 漏包修复**（阻断 #1）：`pyproject.toml` 从 `[tool.setuptools] packages = ["planner"]` 改为 `[tool.setuptools.packages.find] include = ["planner*"]` + `[tool.setuptools.package-data] "planner.web" = ["static/*", "static/**/*"]`。wheel 文件数从 22 → 34，包含全部 `planner/providers/*` 与 `planner/web/*`。新建 `tests/test_wheel_packaging.py`（4 个测试，subprocess 跑 `pip wheel` 与 plan 验收命令一致），任何后续回归会立刻红。
- **GUI `/api/config` repo_root 修复**（阻断 #2）：`RunService.repo_root` 改 public facade，`routes.py::get_config` 显式 `load_config(project_root=repo_root, config_path=cfg_path)`，production preflight 永远触发。打包后从任意目录启动不会再"恰好读到当前目录下的 config"。新建 3 个测试覆盖：从非 repo CWD 启动、production 缺 config 返 404、config 文件存在但内容非法返 400、packaged 模式（`repo_root=None`）返 actionable 错误。
- **adapter 测试注释同步**（阻断 #3）：`tests/test_openai_anthropic_adapter.py` 顶部 docstring 反映当前 contract——即使 key + SDK 齐全仍 `healthy=False`（Phase-1 implementation gate），`NotImplementedError` 作 defense in depth，规划方法真实实现后才翻 `healthy=True`。
- **134 测试全绿**（原 127 + 4 wheel + 3 config），基础 `pip install -e .` 与 `pip install -e ".[gui,dev]"` 两套 install 验证都没动。
- `pyproject.toml [project]` 基础依赖**未动**：仍只 `pydantic + click`，`openai` / `anthropic` SDK 仍未进必需依赖。
- 仓库 `runs/` 仍只含根 `.gitkeep`，smoke 产物走 `/tmp`。

## 当前状态

项目已落地 LLM provider 抽象层（Phase-1 范围完工），并补齐了 Codex 复审指出的两处审计/契约缺口（`script_parse.json` 落盘 + `run_summary.planner_provider` 记录）以及 provider **health check / fallback 骨架**。当前用 `deterministic` provider 作为唯一实现，仍不调用任何真实 LLM，可以无缝切换到未来的 OpenAI / Anthropic / 本地模型 adapter。

- `planner/` 已实现：`cli run / validate`、环境配置加载、provider 抽象层 (`providers/`)、确定性 bible/shot/prompt 生成、引用完整性校验、manifest/executor task skeleton、**脚本解析中间产物 `script_parse.json`**、**provider health check + fallback**。
- **`script_parse.json` 现在是核心产物之一**，由 `pipeline.run()` 调 `parser.parse_script()` 写出，挂在 `artifacts` 首项；`validate` 会校验其 `source_path` 与 `run_summary.script` 一致。
- **`run_summary.json` 记录 provider 审计字段**：`planner_provider`（向后兼容别名 = `requested_provider`）/ `requested_provider` / `effective_provider` / `fallback_used` / `fallback_reason` / `provider_health`（dict：name → ProviderHealth）。
- **provider health check 与 fallback**：`BaseProvider.health_check()` 是抽象方法；deterministic 永远 healthy。`_select_provider` 在 pipeline 起始处运行 health check：development（`allow_provider_fallback=true`）不健康时切到 deterministic 并写审计字段；production（`allow_provider_fallback` 被硬锁为 False）不健康时抛 `ProviderUnavailableError`。
- **50 个单元测试全部通过**（24 老 + 11 provider + 2 script_parse / run_summary + 13 provider_health / fallback），无回归。
- `planner run --env development|production ...` 均可生成 10 个 JSON 产物（9 核心 + `executor_tasks.json`），schema 与产物形状不变。
- `production` 硬边界仍在：
  - `PLANNER_EXECUTOR_DEFAULT_STATUS` / `PLANNER_SUBMIT_PAID_JOBS` / `PLANNER_ALLOW_OVERWRITE_RUNS` / `PLANNER_ALLOW_PROVIDER_FALLBACK` 四个 env var 在 production 下被显式拒绝（`ConfigError`）。
  - executor task 仍为 `tool=None`、`status=pending_manual_approval`，与"核心 planner 不写死 Flowith/libTV"对齐。
  - `allow_provider_fallback` 在 production 下被 `_enforce_boundaries` 二次防御（即使绕过 env-var 也跑不掉）。
  - `planner_provider` / `requested_provider` / `effective_provider` 字段**不在** `_production_locked_keys`（它们是规划层选择）；fallback 切换不绕过 `_enforce_boundaries`，也绕过不了 `manifest.build_executor_tasks` 的 tool/status 默认值。
- CLI 错误处理已加固：`ConfigError` / `ProviderUnavailableError` 都不泄露 traceback。
- `planner validate` 校验 `run_summary.env` 与 `--env` 一致性，不一致时打印 `⚠ env mismatch` warning；`script_parse.source_path` 与 `run_summary.script` 不一致时报 error；**production 下若 `fallback_used=True` 直接报 error**（fail-closed 违反）。

Codex 已复审通过 fallback / health check 骨架及 fail-closed no-residue 修复。接下来让 Proma 做 OpenAI / Anthropic adapter skeleton（仍不调真模型）；Zcode 可以平行开发 continuity audit（Phase 2）。

## 下一步建议

1. Proma 设计 OpenAI / Anthropic adapter skeleton（仅接口形态 + 配置门槛，不调真模型）；其 `health_check` 只检查配置与 SDK 存在性，**不要做成"调一次 ping"**；probe 应该是 opt-in 单独设计。
2. Codex 复审 adapter skeleton 是否保持 fail-closed、无真实 API、无必需 SDK、无 executor 边界漂移。
3. 由 Zcode 接手 continuity audit 工具（Phase 2）：检查角色/场景/道具漂移、自动生成修复建议。`script_parse` 与 `provider_health` 字段可以成为跨集对齐的 ground truth。
4. 准备更多样例剧本（覆盖 1–2 集），验证单集与跨集连续性。
5. 保留 `pending_manual_approval` 人工门禁；下一轮再讨论是否引入自动重试。
6. 后续生产环境验证请在 `/tmp` 或 CI 目录进行，**不要再把 production 产物写到仓库根目录的 `runs/production/`**，避免污染语义。
   - 当前仓库 `runs/` 只保留根 `.gitkeep`；**没有** `runs/development/.gitkeep` 与 `runs/production/.gitkeep`。这是有意的（与 `.gitignore` 的 `runs/` + `!runs/.gitkeep` 对齐）：`runs/development/` 与 `runs/production/` 子目录刻意**不预留占位**，因为任何 run 都应走 CLI 显式的 `--out` 路径，production smoke 永远走 `/tmp`。对照之下 `logs/` 和 `assets/` 的子目录保留 `.gitkeep`，是因为它们是开发期常态产物。**新 AI 不要再恢复 `runs/development/.gitkeep` 或 `runs/production/.gitkeep`**，会把生产路径语义搞混。

## 暂不做

- 暂不直接做全自动网页登录。
- 暂不批量提交生成任务。
- 暂不把 Flowith/libTV 写死进核心数据结构。
- 暂不做无人值守成片生成。
- 暂不提交任何真实 `config/production.json`、真实密钥或账号。
- 暂不静默丢弃 production 下的 `PLANNER_*` 锁定 key 覆盖 —— 必须显式报错。
- 暂不调用真实 LLM API；新增的 `planner_provider` / `requested_provider` / `effective_provider` / `provider_health` 仅做配置门禁与产物记录。
- 暂不把 OpenAI / Anthropic / 任何模型 SDK 作为必需依赖。
- 暂不把 provider `health_check` 设计成会发起真实网络/付费请求的方法 —— 那是 opt-in `probe`，下一轮再说。

## 给新 AI 的阅读顺序

1. `README.md`
2. `PROJECT_STATUS.json`
3. `docs/ARCHITECTURE.md`
4. `specs/DATA_CONTRACTS.md`
5. `docs/PROMA_EXECUTION_BRIEF.md`
6. `docs/ROADMAP.md`
7. `planner/` 源码（建议从 `pipeline.py` 开始，逆序回到 `parser.py`）
8. `tests/test_boundaries.py` —— **必读**，了解 production 硬边界
9. `tests/` —— 了解已覆盖的行为
10. `CHANGELOG.md`

## 给 Proma 的当前任务（已完成）

请执行 `docs/PROMA_EXECUTION_BRIEF.md` 中的第一步：创建项目代码骨架、本地 CLI、基础 schema、sample script、dry-run/validate 命令、基础测试，并按 `development` / `production` 分离配置、数据、资产、runs 和 logs。

状态：**已完成 + 复审修复**（详见 `CHANGELOG.md` 中 2026-07-04 (Proma Phase-1 Step-1) 与 2026-07-04 (Proma Phase-1 Review Fixes) 两节）。

## 给 Proma 的下一轮任务

请按 `docs/PROMA_EXECUTION_BRIEF.md` 顶部的当前任务执行：OpenAI / Anthropic adapter skeleton。

- 只做 `planner.providers` 下的 adapter 骨架与 health check。
- 默认不调真实 LLM，不新增必需 SDK，不新增真实密钥。
- `health_check()` 只做本地配置/依赖检查，不做网络 probe。
- development 可 fallback deterministic 并写审计字段。
- production 必须 fail-closed，且失败不留下 `out_dir` 残留。
- 完成后跑完整测试和 `/tmp` smoke，并更新 `CHANGELOG.md`、`HANDOFF.md`、`PROJECT_STATUS.json`。

## 当前状态（2026-07-10 morning）

Proma 通过子会话完成 Codex 二次复审 → Verdict **PASS**（无 P1/P2，仅 4 个 P3 磨平项）。4 个 P3 已在同轮顺手补完。OpenAI / Anthropic adapter skeleton **正式放行**。下一步转 **opt-in probe 设计**。

- Phase-1 implementation gate 守住：skeleton 即使 key + SDK 齐全仍 `healthy=False`；`details` 完整记录 `api_key_present` / `api_key_env` / `sdk_installed` / `implemented=false`（常量化 `IMPLEMENTED_FALSE = "false"`）/ `real_calls=disabled` / `phase=1-skeleton`。
- `planner/providers/openai_adapter.py` / `anthropic_adapter.py` 的 `_sdk_available` helper 加了 `.. note::` 段，写明 monkeypatch 契约（`setattr(<module>, "_<provider>_sdk_available", lambda: True)`），未来 refactor 时不允许静默改名。
- `planner/providers/base.py::ProviderHealth.details` docstring 注明 string sentinel 约定（`"true"` / `"false"` 非 bool，理由是 JSON round-trip 稳定 + `run_summary.json` 字面可读）。
- `tests/test_openai_anthropic_adapter.py::test_empty_string_env_var_is_treated_as_missing` parametrize 跨 OpenAI + Anthropic，P3 对称补齐。
- `CHANGELOG.md` "key + 真实 SDK present" 示例块补 Anthropic 镜像，块头声明对称。
- pytest **76 passed in 0.98s**（24 老 + 11 provider + 2 pipeline + 13 provider_health + 1 no-residue + 25 adapter skeleton；P3 #1 parametrize 把 empty-string 1 → 2 用例）。
- 仓库 `runs/` 仍只含根 `.gitkeep`，未引入 `runs/development/` / `runs/production/`。

## 子会话复审要点（用户可回溯）

- 委托：`mcp__collaboration__delegate_agent` role=review，title `Codex-review: OpenAI/Anthropic adapter skeleton P1 fix`，delegation id `a5f1ceb6-038c-4810-8517-1ca20e9f5f66`，child session `b4572b72-c6aa-4573-a019-b41f88a1ec16`。
- 报告格式：`verdict / confirmed contract points / findings ranked most-severe first / smoke output / open questions`，子会话严格只读不写。
- 决定：(a) P3 本轮顺手补；(b) 下一步走 opt-in probe 设计；(c) `NotImplementedError` 保留作纵深防御。

## 下一轮任务（Proma 设计 opt-in probe）

按 `docs/PROMA_EXECUTION_BRIEF.md` 的"Next Step"重写版，写出 probe design brief（不进代码）：

1. `BaseProvider.probe()` 新抽象方法：默认 `NotImplementedError`，各 adapter 自实现；
2. 入口控制：CLI `--probe` flag 或 `PLANNER_PROVIDER_PROBE=1` env，默认 off；
3. 失败语义：probe 抛 `ProviderProbeError(PlannerError)`，CLI `try/except PlannerError` 捕获，stderr 输出结构化结果；
4. probe 永不写入 `run_summary.json`；不进 `pyproject.toml` 必需依赖；
5. `probe` 与 `health_check` 完全解耦，互不影响；probe 不修改 `ProviderHealth`，`health_check` 不调 probe。

**Smoke harness 提醒**（未来复审者）：Anthropic manual smoke 需要先 `pip install anthropic`，否则本机只会走 SDK-missing 分支而非 implementation gate；4 个新增 parametrized 单元测试已覆盖该路径（用 `monkeypatch.setattr(anthropic_adapter, "_anthropic_sdk_available", lambda: True)` 模拟），但端到端 smoke 仍需真实 SDK。

**其他并行任务**：

- Zcode：Phase 2 continuity audit 工具（角色 / 场景 / 道具漂移），`script_parse` + `provider_health` 可作为 ground truth；不依赖 probe 设计。
- 扩大样例剧本覆盖 1–2 集，验证单集与跨集连续性。
- Phase 3 executor adapter interface 设计待 probe 落地后启动。
- 后续生产验证仍走 `/tmp` 或 CI 目录；**不要再写** `runs/production/`、`runs/development/.gitkeep`、`runs/production/.gitkeep`。

## 当前状态（2026-07-10 afternoon — GUI 后端落地 + Core-1 batch + P2/P3 闭环）

Proma 已完成 **Phase-2 GUI 后端** + **Phase Core-1 多集批处理**。两轮都过了 Codex 子会话复审（PASS / CHANGES→修完）。

- **Phase-2 GUI 后端**（早一轮）：8 个 API endpoint，`planner-web --no-window` smoke 通；111 测试通过；Codex 复审 PASS，3 个 P3 顺手补（substring → filesystem preflight / generate_run_id 微秒后缀 / RunService facade）。
- **Phase Core-1 多集批处理**（本轮）：`planner batch --env X --scripts DIR --out ROOT` 一次跑多集，写每集子目录 + `batch_summary.json`；16 个新测试；Codex 复审 **CHANGES**（2 P2 + 1 P3），本轮已修：
  - **P2-#1** `EpisodeRunSummary.provider_health` 字段补齐（红线 #8）
  - **P2-#2** 三件套同步（本节即兑现）
  - **P3-#1** `run_batch(config=...)` 参数化，CLI 不再 double-load_config
- **127 测试全绿**（76 + 32 GUI web + 3 P3 + 16 batch）
- 基础安装红线 #6 仍成立：`pip install -e .` 后 92 测试零回归

### Phase Core-1 关键设计选择（解释给接手者）

- **`BatchOptions.resolved_out_dir` 与 GUI service 镜像**：production 写 repo 内路径直接 `EnvironmentBoundaryError`，不创建目录。这是把红线 #3 从 GUI 路径扩展到 CLI 路径的关键。
- **每集失败不静默**（红线 #10）：即使 `--no-fail-fast`，失败的 episode 也写进 `batch_summary.json` 的 `error_type` / `error_message`，操作员从 summary 直接看到所有尝试过的 episodes。
- **CLI stderr 不输出 traceback**（红线 #6）：`_log.warning(...)` 只打一行 friendly 消息；Python traceback 完全不出现在 stderr。`batch_summary.json` 的 `error_message` 字段承载用户可读的诊断信息。
- **`provider_health` 字段复制（不经 `_health_to_dict` 转换）**：`RunResult.provider_health` 已经是 `Dict[str, dict]`，不需要再次转换；pipeline 和 batch 各保留一份独立转换函数以避免依赖私有工具（技术债，Core-X 抽到 `planner/episodes.py` 统一）。

### Phase Core-1 已知技术债（留给下阶段）

- `derive_episode_id` 在 `batch.py` 和 `pipeline._episode_id_from_path` 重复 —— 应抽到 `planner/episodes.py` 统一（Core-3 跨集连续性会需要更复杂的 episode 工具）。
- `BatchOptions` 是 dataclass 而不是 Pydantic —— Core-2（`project.json`）会引入 Pydantic `Project` 模型；届时 `BatchOptions` 可以继承 pydantic.BaseModel 拿到校验。

## 当前状态（2026-07-10 afternoon — GUI 后端落地）

Proma 已完成 **Phase-2 Step-1：GUI 后端**。新增 `planner/web/` 子包（FastAPI + 8 个 API endpoint + 内存 run registry + PlannerError→HTTP 映射），`pyproject.toml` 加 `gui` / `server` / `build` 三个 optional extras + `dev` 加 `httpx`（FastAPI TestClient 需要）。

- **`pip install -e .`（基础安装）仍只装 pydantic + click**：原 76 测试零改动全绿（红线 #6 自检通过）。
- **`pip install -e ".[gui,dev]"` → 108 passed**（原 76 + 新 32 个 web 测试）。
- **8 个 API endpoint**：`GET /api/health`、`GET /api/config`、`GET /api/runs`、`POST /api/runs`、`GET /api/runs/{id}/summary`、`GET /api/runs/{id}/artifacts/{name}`、`POST /api/runs/{id}/validate`、`POST /api/upload-script`。
- **out_dir 策略已落地**（用户拍板）：
  - `development` 默认 `<repo_root>/runs/development/<run_id>/`。
  - `production` 默认 `<os_app_data>/ShortDramaPlanner/runs/<run_id>/`，**绝不写进 repo**。
  - `production` 显式指定 repo 内路径 → `EnvironmentBoundaryError`（403），不创建目录。
- **错误处理 100% 友好**：FastAPI 全局 `PlannerError` 异常处理器只返 `{error, message}` JSON，traceback 仅入服务端日志（红线 #7 延伸）。
- **后台线程非 daemon**：uvicorn 退出时能 wait in-flight run 收尾，避免半成品 run 残留。
- **路径穿越防护**：artifact 名称走 11-名白名单（`script_parse` / `character_bible` / ...），未知 → 404 而不是读 `../../etc/passwd`。
- **审计字段一进一出**：`run_summary.json` 是事实标准；`/api/runs/{id}/summary` 与 `/api/runs/{id}/validate` 都返回 `requested_provider` / `effective_provider` / `fallback_used` / `fallback_reason`，前端不必另开 schema。
- **108 测试覆盖**：dev/prod out_dir 矩阵、`--force` 标志差异、后台线程 daemon flag、失败时清理空目录、validate 透出审计字段、upload 原子写入 + sha256 去重、列表排序 / 不阻塞、production 缺 config 文件 404 提示。

## 下一轮任务（Phase-2 Step-2：Codex 复审 + Phase 3 启动）

按工作区纪律（CLAUDE.md + agent-roles），**Phase 收口前必经 Codex 复审**。本轮 GUI 后端交付需 Codex 通过后才进入 Phase 3。

**Codex 复审重点**（建议用 `codex-review` 工作流或子会话委派）：

1. **API 是否真的只是瘦壳**：`planner/web/routes.py` 与 `run_service.py` 必须 0 业务逻辑 —— provider 选择、health check、fallback、run_summary 字段、validate 检查、env 边界、no-residue 全部在 `planner/` 核心包。
2. **out_dir 策略是否真破不了**：production 显式 repo 内路径必须 403；production 默认必须出 repo；dev 默认必须在 repo 内但 gitignored。
3. **错误映射是否覆盖所有 PlannerError 子类**：`ConfigError` / `ScriptReadError` / `EnvironmentBoundaryError` / `ProviderUnavailableError` / `BrokenReferenceError` / `SchemaValidationError` 都有正确状态码。
4. **审计字段一进一出**：`/api/runs/{id}/summary` 与 `/api/runs/{id}/validate` 必须都返回 `requested_provider` / `effective_provider` / `fallback_used` / `fallback_reason` / `provider_health` 五个字段。
5. **基础安装不受影响**：`pip install -e .` 后 76 测试零回归。

**Codex 通过后，启动 Phase 3**：

### Phase-2 Step-2 结果（2026-07-10 afternoon 闭环）

Codex 子会话复审 Phase-2 GUI backend：**Verdict PASS**，无 P1/P2。

- 委派：`mcp__collaboration__delegate_agent` role=review，title `Codex-review: Phase-2 GUI backend (v2)`（首版子会话因 Explore 子智能体回调未回流而失败，continue_delegation 又遇"上一条仍在处理"错误；重派 v2 子会话，明确"不派 SubAgent 自己 Read"才成功），delegation id `c33d749c-9351-4907-8669-cdf197331921`，child session `a8f541f6-4fdd-4778-acb3-8338f1e67870`。
- 报告 10 条红线全部 ✅；3 个 P3 顺手补完（routes.py 改 substring → filesystem preflight；generate_run_id 加微秒 + 随机后缀避免并发冲突；RunService 加 get_run/list_runs facade 消除 service._registry 私有访问）；加 3 个新测试覆盖 P3，总测试数 76 + 32 + 3 = **111 passed**。
- 基础安装红线 #6 仍成立：基础 76 测试零回归。

**Codex 通过后，启动 Phase 3**：

- `planner/web/static/index.html` + `app.js` + `style.css` —— 单页 UI（顶栏 env 切换、上传区、run 历史表、详情抽屉、fallback 红 banner）。
- `planner/web/launcher.py` —— pywebview 启动器：后台线程跑 uvicorn + 原生窗口，window 关闭时干净关停 server。
- `planner/web/scripts_entry.py` —— `python -m planner.web` 与 `[project.scripts] planner-web` 的入口。
- `docs/GUI.md` —— 同事上手手册：装依赖、跑 `planner-web`、首启警告如何绕过、字段说明。
- `tests/test_web_launcher_import.py` —— 启动器 import smoke + `--no-window` 模式跑通 uvicorn。

**待用户确认的事项**：

1. **GitHub 仓库 URL**：Phase 5 CI 之前要先 `git init` + push。需要用户提供 GitHub repo URL（或确认稍后再处理，先继续 Phase 3 / 4）。
2. **图标资源**：PyInstaller 打包需要 `.icns`（Mac）+ `.ico`（Windows）。可以先用通用 Python 图标占位（计划已说明"placeholder is fine"），或用户提供品牌图标。
3. **同事邮件名单 / 通知方式**：CI Release 后通知谁？


## 关键风险

- 人物一致性：需要角色 bible、参考图、负面 prompt 和镜头引用机制。
- 场景连续性：同一场景必须复用 location id。
- 道具连续性：关键道具必须有固定视觉描述和出现镜头。
- 网页自动化稳定性：优先 API，其次浏览器自动化。
- 质量门禁：早期必须保留人工确认点。
- **生产边界降级**：任何把 production executor 状态降到 `pending`、打开 `submit_paid_jobs`、打开 `allow_overwrite_runs` 的尝试都必须被 `ConfigError` 拦截；CI / 后续 reviewer 在加新功能时不要"图省事"放宽 `_production_locked_keys` / `_enforce_boundaries`。

## 已沉淀的可复用约定

- **环境隔离**：永远通过 `--env development|production` 切换，**不要复制两份代码**；生产硬约束写在 `planner/env.py` 的 `_production_locked_keys` 与 `_enforce_boundaries`。
- **schema 不绑工具**：`executor_tasks.json` 只是骨架，`tool` 字段默认 `None`，由 Phase 3 executor adapter 在提交前填入具体工具名。
- **中文显示名 → id**：角色/场景抽取时会做映射，避免"林夏"和"lin_xia"被算成两个。
- **prompt 头**：每条 image/video prompt 必须显式包含 `场景：xxx / 人物：xxx / 道具：xxx`，便于验证器和人类快速核对。
- **错误友好性**：CLI 在顶层 try/except `PlannerError`，确保用户不会看到 traceback。
- **env 一致性**：`planner validate --env X` 必须和 `run_summary.json` 中的 `env` 一致；不一致时是 warning 而非 error，但会在 stderr 高亮。
- **Provider 抽象**：所有"非视觉智能"步骤（`build_bibles` / `extract_beats` / `generate_shots` / `compile_image_prompts` / `compile_video_prompts`）必须通过 `planner.providers.get_provider(name)` 调用，pipeline 不直接依赖具体实现；新增 provider 只需 `@register("name")` + 实现 `BaseProvider` 五方法，零业务代码改动。
- **脚本解析中间产物**：`pipeline.run()` 始终先调 `parser.parse_script()` 产出 `script_parse.json`，与下游 provider 解耦；`source_path` 必须等于 `run_summary.script`，validate 会断言一致性。
- **run_summary provider tracking**：`run_summary.json` 必含 `planner_provider` 字段；validate 缺失该字段会发 warning（兼容历史 run），新增产物一律带此字段。
- **provider health check**：`BaseProvider.health_check()` 是抽象方法，**必须**不发起真实昂贵请求（无 model 推理 / 无账号登录 / 无付费探活），只检查 config / 依赖存在；deterministic provider 永远 `healthy=True`，是 fallback 的安全目标。
- **fallback 策略（fail-closed）**：`pipeline._select_provider` 在跑任何抽取步骤前先做 health check。development（`allow_provider_fallback=true`，默认）不健康时切到 deterministic 并写审计字段；production / development 关掉 fallback 时不健康则抛 `ProviderUnavailableError`，不允许静默换 provider。fallback 只换规划层 provider，**不改 executor tool/status 默认值**。
- **provider audit fields**：`run_summary.json` 必含 `requested_provider` / `effective_provider` / `fallback_used` / `fallback_reason` / `provider_health`（dict）；`planner_provider` 保留作 backward-compat 别名（== `requested_provider`）。`validate.py` 报告透出全部字段，production 下若 `fallback_used=True` 直接 error。
- **GUI 瘦壳原则**：`planner/web/` 子包零业务逻辑 —— 所有规则（provider 选择、health check、fallback、run_summary 字段、validate、env 边界、no-residue）都在 `planner/` 核心包。GUI / CLI / test 共享同一组函数，**任何业务逻辑改动必须经三处调用方验证**，否则视为绕路。
- **GUI out_dir 策略**：development 默认 `<repo_root>/runs/development/<run_id>/`（gitignored 本地可见）；production 默认 `<os_app_data>/ShortDramaPlanner/runs/<run_id>/`（绝不写 repo）；production 显式 repo 内路径 → `EnvironmentBoundaryError`（403）。GUI 不可绕过。
- **GUI 错误回传原则**：FastAPI 全局 `PlannerError` 异常处理器只回 `{error, message}` JSON，traceback 仅入服务端日志。CLI 的 `try/except PlannerError` 契约在 GUI 路径同样成立。
- **环境隔离**：永远通过 `--env development|production` 切换，**不要复制两份代码**；生产硬约束写在 `planner/env.py` 的 `_production_locked_keys` 与 `_enforce_boundaries`。
- **schema 不绑工具**：`executor_tasks.json` 只是骨架，`tool` 字段默认 `None`，由 Phase 3 executor adapter 在提交前填入具体工具名。
- **中文显示名 → id**：角色/场景抽取时会做映射，避免"林夏"和"lin_xia"被算成两个。
- **prompt 头**：每条 image/video prompt 必须显式包含 `场景：xxx / 人物：xxx / 道具：xxx`，便于验证器和人类快速核对。
- **错误友好性**：CLI 在顶层 try/except `PlannerError`，确保用户不会看到 traceback。
- **env 一致性**：`planner validate --env X` 必须和 `run_summary.json` 中的 `env` 一致；不一致时是 warning 而非 error，但会在 stderr 高亮。
