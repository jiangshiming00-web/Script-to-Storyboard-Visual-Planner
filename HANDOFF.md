# Handoff

## 当前状态（2026-07-15 - Phase 3 P2 probe Round 2 landed：tests + harness + P3 wording，等 Codex 复审放行后 push）

按 Codex 手工对手方对 probe Round 1 Codex fix (`856a2d2`) 的复审 verdict **PASS**，本轮按 Round 2 brief §4 落地 18 unit + 10 cli tests + 2 harness scenarios + 顺手修 Codex 标注的 non-blocking P3 wording（"outer wall-clock timeout" → "socket timeout"）。`git push` 在 Codex 复审放行后即可执行。

### Round 2 落地清单

- **`tests/test_provider_probe.py` 18 unit**（brief §4.1）：
  - 3 env gate（`_probe_gate_open()` strict `"1"` 匹配，parametrize 覆盖 `""` / `"0"` / `"1.0"` / `"true"` / `"True"` / `"yes"` / `"on"` / `"1 "` / `" 1"`）
  - 4 endpoint pinning（默认 OpenAI / Ollama / vLLM / 末尾 `/` rstrip 吃掉）
  - 3 happy / unhealthy / timeout（200 success + 404 unhealthy + `TimeoutError` 异常类名进 reason）
  - 3 NotImplementedError（deterministic + openai skeleton + anthropic skeleton reason 对齐）
  - 2 redaction（body 含 `sk-...` / `Bearer ...`，4xx path body excerpt 仍 redact）
  - 3 invariants（cwd 5 次跑后无新文件 / `ProviderHealth` before-after byte-identical / `inspect.getsource` 静态扫 `probe` 不调 `health_check` 反之亦然）
  - bonus 1（base abstract default 在 third-party subclass override 时仍 raise NotImpl + 接受 round-2 `timeout_ms` kwarg）
  - 共 27 测试用例（18 functions + 9 parametrize expansion）
- **`tests/test_cli_provider_probe.py` 10 subprocess**（brief §4.2）：
  - 4 distinct exit code 表（gate closed=2 / unhealthy=2 / healthy=0 / NotImpl=1）
  - 1 no-`--probe`-flag 守卫（`planner run --probe` 拒绝 + `provider-probe --help` 不含 `--probe` flag）
  - 5 misc（env-only-no-subcommand sanity / no-subcommand-no-env sanity / stderr secret redaction / 不创建 run dir / 3 种 failure mode stderr 全无 `Traceback`）
  - 本地 `http.server.HTTPServer` + `threading` 在 `127.0.0.1:<free_port>` 起 ephemeral 服务器
- **2 harness scenarios**（brief §4.3）：
  - `provider_probe_opt_in.json`（`category=probe` / `risk_level=opt_in_network` 新增 / 1 expected + 12 forbidden）
  - `provider_probe_gate_closed.json`（`category=probe` / `risk_level=read_only` / 1 expected + 13 forbidden）
  - `harness/agent_scenarios/run_all.py` 加 `probe` category 分支 + `_run_planner_probe_cli` + `_spawn_local_probe_server` + `_stop_local_probe_server` + `_write_model_config` + 2 个 `_validate_probe_*_replay` helper
  - `VALID_CATEGORIES += {"probe"}` + `VALID_RISK_LEVELS += {"opt_in_network"}`
  - **7 scenarios → 9 scenarios**
- **P3 wording 顺手修**（Codex 标注 non-blocking）：`planner/cli.py` / `planner/providers/base.py` / `planner/providers/openai_compatible_adapter.py` 三处 "outer wall-clock timeout" → "socket timeout"。**不动 brief**（Round 2 锁定，后续 round 单独对齐外层 guard）。

### 红线守门

- `pyproject.toml [project]` 基础依赖未动：仍只 `pydantic + click`；harness + CLI tests 全用 stdlib（`subprocess` / `urllib` / `http.server` / `socket` / `threading` / `tempfile`）。
- **473 pytest**（436 Round 1 + 1 base-abstract-default + 1 skipped 消除 + 36 Round 2 = 27 unit + 10 cli 测试项展开），零回归。
- 仓库 `runs/` 仍只含根 `.gitkeep`；harness + CLI tests 产物落 `/tmp` 或 `tmp_path`。
- production fail-closed + redact + read-only 全部保留：probe scenario `forbidden_tool_calls` 显式 forbid `submit_paid_job` / `write_run_dir` / `call_real_llm` / `call_paid_api` / `open_socket` / `http_get`；replay helper 在 probe 不发真网络（仅本机 http.server）。
- 4 路径 CLI smoke + 9 harness scenarios 全 0 traceback、exit code 与 brief §2.3 表完全对齐。

### 下一轮

- **Codex 手工复审 Phase 3 P2 probe Round 2**（重点看）：
  1. `test_provider_probe.py` 18 unit 是否真覆盖 brief §4.1 列表 + bonus 测试是否过度
  2. `test_cli_provider_probe.py` 10 cli subprocess 是否用真本地 http.server（无 mock 偷懒）+ 4 路径 exit code 表对齐 brief §2.3
  3. `provider_probe_opt_in.json` / `provider_probe_gate_closed.json` shape + assertions + forbidden tools 是否完整
  4. `run_all.py` 7 → 9 scenarios 接入是否破既有 7 个 scenario
  5. P3 wording 是否仍残留旧 "outer wall-clock" 字眼
  6. 三件套是否对齐
- **Codex 通过后**：
  - `git push origin main`（本地 ahead origin 1 commit → 2 commits）
  - 启动 Round 3：Phase 3 P2 收口（不接 GUI 面板 / 不动 core3 bible merge / opt-in probe 已闭环）
- **Codex 未通过**：按反馈再修，单独 commit + 再次复审。

### 候补 next_actions

1. `phase3_p2_provider_probe_round2_codex_manual_re_review_pending` → 本 round
2. `phase3_p2_optional_planner_agent_subcommand_inside_planner_web_for_gui_panel` → 等 probe 全闭环
3. `design_provider_optional_probe_separate_from_health_check_no_default_paid_call` → 已闭环
4. `core3_add_planner_bible_merge_for_cross_episode_continuity` → 最大范围，独立 user-ack

## 当前状态（2026-07-15 - Phase 3 P2 probe Round 1 Codex fix landed，等 Codex 复审放行后再 push + 进 Round 2）

按 Codex 手工对手方对 probe Round 1 implementation (`0128dd1`) 的复审结论，verdict **暂不建议进入 Round 2，也先不要 push**。本轮按"Round 1 Codex fix"模板全部修齐 + 补最小回归测试，等 Codex 复审放行后 push + 进 Round 2。

### Round 1 Codex fix（3 finding 全部收口）

- **P1（红线）probe URL 泄露 secret**：`OpenAICompatibleProvider.probe()` 在 reason / details["endpoint"] 写 raw URL。如果 operator 把 `sk-...` key 嵌进 `base_url` path，secret 通过 probe result 流到 CLI stdout/stderr。Codex 复现：`base_url='https://example.com/v1/sk-probe-secret-1234567890'` → result 含 raw token。
  - 修法：`safe_url = _redact_secrets(url)`；3 处出口（happy + unhealthy + exception 路径）全部用 safe_url；真实 HTTP 请求仍用 raw URL。
  - 沿用现有 4 条 redact regex（Bearer / sk- / sk-ant- / gho_）；不引入新 pattern。
- **P2（1/2）`--timeout-ms` 未生效**：CLI option 默认 5000ms，但 adapter 写死 `timeout_seconds = 5.0`。Brief 已把 `--timeout-ms` 列为契约。
  - 修法：`BaseProvider.probe(*, timeout_ms: int = 5000)` 抽象加 kwarg；3 个 raise-NotImpl adapter 签名同步（无网络 round-trip，kwarg 忽略）；`OpenAICompatibleProvider.probe` 用 `max(timeout_ms, 1) / 1000.0` 计算 socket timeout；CLI `instance.probe(timeout_ms=timeout_ms)` 接通链路。
- **P2（2/2）`--provider openai_compatible` 无 model-config 时 settings=None**：CLI 注释承诺"Resolve from model_config or defaults"，但 `_load_model_config_for_cli()` 返回 None 时 settings 仍 None，`_require_settings()` 抛 "constructed without ProviderRuntimeSettings"。`health_check()` 已有 default fallback，probe 路径漏掉。
  - 修法：当 `_load_model_config_for_cli()` 返回 None + provider 是 openai_compatible 时，构造 `ModelProviderConfig(planner_provider="openai_compatible")` + `resolve_runtime_settings(...)` 作 defaults（与 `health_check()` fallback 镜像）。其他 provider 不需要 settings，保持现状。

### Tests（`tests/test_openai_compatible_adapter.py` +5 regression）

- `test_probe_redacts_secret_in_url_endpoint` —— P1 happy path，`base_url` 含 `sk-probe-secret-1234567890` → reason / details 不含 raw + `<redacted>` 已替换 + 真请求仍用 raw URL。
- `test_probe_redacts_secret_in_unhealthy_path` —— P1 unhealthy path，HTTP 401 → reason / details 仍 redact URL。
- `test_probe_uses_timeout_ms_kwarg` —— P2 timeout，`probe(timeout_ms=2500)` → `http_get` 收 `timeout=2.5`。
- `test_probe_default_timeout_is_five_seconds` —— `probe()` 不传 kwarg → 5s default。
- `test_probe_default_settings_when_none_uses_default_base_url` —— P2 default settings，从 `ModelProviderConfig(planner_provider="openai_compatible")` 解析 settings 后 probe 命中 `http://localhost:8000/v1/models`。

### 红线守门

- `pyproject.toml [project]` 基础依赖未动：仍只 `pydantic + click`。
- **436 pytest**（431 旧 + 5 新增 regression），零回归。
- 仓库 `runs/` 仍只含根 `.gitkeep`；smoke 产物走 `/tmp`。
- production fail-closed + redact + read-only 全部保留：
  - safe_url 在 4 条 redact regex 之外不引入新 pattern；
  - 真实请求仍走 raw URL（probe 语义要求真命中）；
  - base.py / 3 raise-NotImpl adapter 签名扩展是 kwarg-only，对所有现有调用方零侵入。
- 4 路径 CLI smoke 全 0 traceback、exit code 与 brief §2.3 表完全对齐。
- `python3 harness/agent_scenarios/run_all.py` —— **7 scenarios 全过**，live cross-check + live agent replay 都绿。

### 下一轮

- **Codex 手工复审 Phase 3 P2 probe Round 1 Codex fix**（重点看）：
  1. `safe_url = _redact_secrets(url)` 覆盖 happy + unhealthy + exception 三条出口；
  2. 真请求仍用 raw URL（probe 语义要求真命中 endpoint）；
  3. `BaseProvider.probe` 加 kwarg 对现有 3 个 raise-NotImpl adapter 签名兼容；
  4. `timeout_ms / 1000.0` 下界保护（`max(timeout_ms, 1)`）；
  5. CLI default settings fallback 与 `health_check()` 镜像契约一致；
  6. 5 个回归测试真匹配 regex / fixture 真有 secret / 默认 settings 真走到 defaults。
- **Codex 通过后**：
  - `git push origin main`（本地 ahead origin 1 commit → 2 commits）。
  - 启动 Round 2（`tests/test_provider_probe.py` +18 unit + `tests/test_cli_provider_probe.py` +10 cli + 2 个 harness scenario）。
- **Codex 未通过**：按 P1/P2 反馈再修，单独 commit + 再次复审。

### 候补 next_actions

1. `phase3_p2_provider_probe_round1_codex_fix_codex_manual_re_review_pending` → 本 round
2. `phase3_p2_provider_probe_round2_18_unit_10_cli_2_harness_scenarios` → 等本 round 复审通过
3. `phase3_p2_optional_planner_agent_subcommand_inside_planner_web_for_gui_panel` → 等 probe 全闭环
4. `core3_add_planner_bible_merge_for_cross_episode_continuity` → 最大范围，独立 user-ack

## 当前状态（2026-07-15 - Phase 3 P2 probe Round 1 implementation landed，等 Codex 复审 Round 1 + Round 2 tests/harness 启动）

Codex PASS on round-2 brief (`1576d20`)，本轮按"Round 1 实现 + 2 P3 文案/测试数顺手修" 落地。Round 1 = 生产代码 + test fixture 同步（不新增测试，新测试留给 Round 2）。

### Round 1 落地清单

- **`planner/exceptions.py`**：`ProviderProbeError(PlannerError)` 新增，docstring 列出 3 种失败模式（NotImpl/healthy=False/gate-closed）+ CLI 退出码分发
- **`planner/providers/base.py`**：
  - `ProviderProbeResult` dataclass (`frozen=True`)
  - `BaseProvider.probe()` abstract，默认 raise `NotImplementedError`
  - 严格隔离 invariants inline 注释（health_check/probe 不互相调用）
- **4 adapter override**（brief §3 范围）：
  - `deterministic` / `openai` skeleton / `anthropic` skeleton → raise NotImpl + ALIGNMENT_HINT
  - `openai_compatible` → 真实现 GET `{settings.base_url.rstrip("/")}/models`（brief §2.5 endpoint 契约：默认 OpenAI / Ollama / vLLM / trailing-slash 全部不双拼）；5s socket timeout + `_redact_secrets` 4 regex 出口
- **`planner/providers/__init__.py`**：re-export `ProviderProbeResult` + `http_get`
- **`planner/cli.py`**：顶级子命令 `planner provider-probe` + `_probe_gate_open()`：
  - env gate exactly `"1"`（strict exact match）
  - gate closed 时手动 `click.echo + ctx.exit(2)` 一行 stderr（**不**用 `Click.UsageError` 默认 multi-line usage）—— brief §2.2 同步修正
  - 退出码统一表：gate close=2 / NotImpl=1 / unhealthy=2 / healthy=0（brief §2.3）
  - stdout one-line JSON（redact reason + optional latency_ms + `--verbose` 时 details）
- **test fixture 同步**：`tests/test_providers.py::_EchoProvider` + `tests/test_provider_health.py::_UnhealthyStubProvider` + `tests/test_provider_health.py::_Ephemeral` 加 `probe()` raise NotImpl（mirror skeleton，避免"registry accepts third-party subclasses"被 probe 抽象静默回退）

### CLI smoke 实测（4 路径）

- `planner provider-probe`（env unset）→ **exit 2** + 一行 stderr policy refusal
- `PLANNER_PROBE=0 ...` → **exit 2**（同上）
- `PLANNER_PROBE=1 ... --provider deterministic` → **exit 1** + `probe not implemented for 'deterministic'`
- `PLANNER_PROBE=1 ... --provider openai` → **exit 1** + skeleton gate + ALIGNMENT_HINT
- 4 路径 stderr 全部 0 traceback

### Brief P3 顺手修（commit `0128dd1` 内联）

- §2.2 sample code: `click.UsageError` → `click.echo + ctx.exit(2)`（一行 stderr，brief 与 CLI 实现对齐）
- §4.1 header: `+14 unit` → `+18 unit`（实际表 18 条）
- §4.2 header: `+8 cli` → `+10 cli`（实际表 10 条）
- §8 implementation plan: `+14 unit / +8 cli` → `+18 unit / +10 cli`

### 红线守门

- `pyproject.toml [project]` 基础依赖未动：仍只 `pydantic + click` + stdlib `urllib`。
- 仓库 `runs/` 仍只含根 `.gitkeep`。
- pytest **431 passed**（与 `1576d20` 一致，0 回归，Round 1 0 测试新增）
- 4 路径 CLI smoke 全 0 traceback、exit code 与 brief §2.3 表完全对齐
- probe 永不写 `run_summary.json`；与 `health_check` 严格隔离（brief §2.7 invariants）

### 下一轮

- **Codex 复审 Round 1 实现 + 等 Codex PASS**：
  - 重点看 `BaseProvider.probe()` 默认 raise 的健壮性 + 4 adapter override scope 是否覆盖 brief §3
  - `_probe_gate_open()` strict `"1"` 是否过严 / 过宽
  - `openai_compatible.probe()` 5s timeout + `_redact_secrets` 出口是否覆盖 raw secret
  - `cli.py` ProviderProbeError vs NotImplementedError 分流是否清晰
  - test fixture 加 `probe()` 后原 8 个测试是否回归干净（已 verify: 25/25 passed）
- **Round 2（过审后启动）**：`tests/test_provider_probe.py` +18 unit + `tests/test_cli_provider_probe.py` +10 cli + 2 个 harness scenario + `run_all.py` 入口 7 → 9
- **Round 3**：三件套 + 三轮复审闭环 + push

### 候补 next_actions

1. `phase3_p2_provider_probe_round1_codex_review_pending` → 本 round
2. `phase3_p2_optional_planner_agent_subcommand_inside_planner_web_for_gui_panel` → 等 probe 完成后
3. `core3_add_planner_bible_merge_for_cross_episode_continuity` → 最大范围，独立 user-ack

## 当前状态（2026-07-15 - provider probe design brief round-2 fix landed，等 Codex 设计复审二审后启动实现 Round 1）

按 Codex manual review of round-1 brief (`4121276`) 的 4 findings（2 P1 + 1 P2 + 1 P3）全部收口，brief round-2 supersede round-1 入库到 `docs/design/provider_probe_design.md`。0 代码改动。

### Round-2 修了什么（4 finding 全部收口）

| Finding | 修法 | 位置 |
|---|---|---|
| **P1-1** endpoint 双 `/v1` 拼接（`base_url` 已含 `/v1`） | `GET {settings.base_url.rstrip("/")}/models`，mirror `openai_compatible_adapter.py:431` 现有模式 | brief §2.5 + §4.1 |
| **P1-2** brief 落 ephemeral workspace-files 路径 | brief 入库到 **`docs/design/provider_probe_design.md`**；round-1 旧文件 archived | brief §0 Reading Order |
| **P2** CLI / env gate / exit code 互打架 | 删 `--probe` flag（顶级子命令即 trigger）；AND gate 收缩到 `subcommand × env=1`；统一 exit 表（gate close=2 / NotImpl=1 / unhealthy=2 / healthy=0） | brief §2.2 + §2.3 + §4.2 |
| **P3** `next_actions` stale + 类名漂移 | 删 `diagnose_continuity_audit_codex_manual_re_review_pending`；全文统一 `ProviderProbeError` | CHANGELOG/HANDOFF/brief/PROJECT_STATUS |

### brief round-2 路径

- **新（入库）**：`docs/design/provider_probe_design.md`（committed to git，作为下一轮 review/implementation 主交付物）
- **旧（archived）**：`~/.proma/agent-workspaces/.../workspace-files/.context/plan/probe_design.md`（workspace ephemeral，brief §0 Reading Order 标注）

### 三件套 sync（本 commit 一并）

- `CHANGELOG.md` 顶部加 round-2 条目；4 finding 全部收口记录 + 红线守门
- `HANDOFF.md` 顶部章节更新指向新路径 + round-2 修复列表
- `PROJECT_STATUS.json`：`next_actions` 删 stale；verification 加 round-2 行

### 红线守门

- `pyproject.toml [project]` 基础依赖未动。
- 0 代码 / 0 测试修改。pytest **431 passed**（与 `4121276` 一致，0 回归）。
- 仓库 `runs/` 仍只含 `.gitkeep`。
- `docs/design/` 是新目录；与既有 `docs/ARCHITECTURE.md` / `docs/GUI.md` 同级。

### 下一轮

- **Codex 设计复审 probe brief round-2**（重点看）：
  1. §2.5 endpoint 拼接契约 `rstrip + /models` 是否覆盖默认 OpenAI / Ollama / vLLM 4 个用例
  2. §2.3 exit code 表（gate close=2 / NotImpl=1 / unhealthy=2 / healthy=0）是否清晰无歧义
  3. §2.2 顶级子命令 vs `--probe` flag 的取舍论据（alias 不污染 + 退出码语义清晰 + 与 CI 守卫对齐）
  4. §4.2 exit code 测试覆盖（10 个 CLI 测试 + 4 endpoint 守卫测试）
  5. §3 adapter scope（openai / anthropic skeleton 仍 raise NotImplementedError；与 Phase-1 implementation gate 对齐）
  6. 三件套 + 三处 `ProviderProbeError` 类名一致
- **Round 1 (过审后启动)**：`base.py` abstract + 4 adapter override + CLI 子命令
- **Round 2**：18 tests + 2 harness scenarios
- **Round 3**：三件套 + 三轮复审闭环 + push

### 候补 next_actions（本轮通过 Codex 二审后再启动哪个）

1. `phase3_p2_diagnose_continuity_audit_codex_manual_re_review_pending` → 本轮已删（已 PASS'd 在 `4426c14`）。
2. `phase3_p2_provider_probe_design_brief_codex_design_review_pending` → 本 round。
3. `phase3_p2_optional_planner_agent_subcommand_inside_planner_web_for_gui_panel` → 等 probe 落地后；GUI 面板可做"probe 状态 / 调用 probe" 按钮。
4. `core3_add_planner_bible_merge_for_cross_episode_continuity` → 最大范围，独立 user-ack 启动。

## 当前状态（2026-07-15 - Phase 3 P2 diagnose continuity-audit Codex 复审修复 P1 + P3 status cleanup，待 Codex 复审放行）

按 user 拍板从 `next_actions[0]` 启动 probe 设计阶段。Scope 严格限定为 **design brief**——本轮 0 代码修改、0 测试新增；过审后启动 Round 1（实现）+ Round 2（测试 + harness）+ Round 3（三件套 + Codex 三轮复审 + push）。

### 落地

- `workspace-files/.context/plan/probe_design.md`（8 KB 设计契约）：
  - **TL;DR**：`BaseProvider.probe()` opt-in 网络可达性探测；**CLI `--probe` × `PLANNER_PROVIDER_PROBE=1` env** 双开；默认 `NotImplementedError`；probe **绝不**写 `run_summary.json` / 不修改 `ProviderHealth` / 不被 `health_check()` 调用。
  - **Goals G1-G8**：8 条量化验收信号（抽象方法 / 双重 gate / 默认 raise / 零写入 / `ProviderProbeError(PlannerError)` / 4-regex redact / 不进 `[project]` 必需依赖）。
  - **2.1 抽象方法**：与 `health_check()` 对称；ABC；默认 raise。
  - **2.2 入口控制**：`_probe_gate_open()` 单点 AND 判定。CLI flag = trigger、env = gate，互不替代——避免 alias / `.bashrc` 误触。
  - **2.3 失败语义**：`not_implemented` exit 1；`healthy=False` exit 2；非 PlannerError 沿用 traceback 入 stderr。
  - **2.5 红线守门**：对齐 `CLAUDE.md` §红线 全部条目。
  - **2.6 与 `health_check` 严格分离**：8 维度对比表（调用频率 / 副作用面 / 输出 / 写盘 / 调用方 / 失败语义 / 默认 / Gate）；代码层两条都不互相调用。
  - **3. Adapter 范围**：4 adapter (`deterministic` / `openai_compatible` / `openai` skeleton / `anthropic` skeleton)。`openai_compatible` 真实现 GET `{base_url}/v1/models`，其余 raise。
  - **4. Test 范围**：3 文件 / 18 case（unit 12 + cli 6 + harness 2 个 scenario）。
  - **5. Open Questions**：4 条（子命令名 / local-only probe 边界 / details redact 复用 / 失败重试）留 user 拍板。
  - **8. Implementation Plan**：3 round skeleton（实现 / 测试 + harness / 三件套 + 三轮复审 + push）；预计 ~250 LOC 生产 + ~400 LOC 测试 + ~120 LOC harness。

### 为什么单独 design brief

- `BaseProvider.health_check` docstring 已写："Providers that need network reachability should expose an opt-in probe separately so the planner never silently spends money on a health check." 这是 base.py 的隐式 TODO，需要正式契约收。
- `HANDOFF.md` 2026-07-10 已沉淀 5 条 probe 原则（默认 NotImplemented / 入口双重 / ProviderProbeError / 不进 `[project]` / 与 health_check 解耦），本 brief 在此基础上扩到可落地全契约。
- probe 设计天花板低（接口数量小、Adapter 面固定），但红线密度高（任何别名 / 静默放宽都会破 fail-closed）。先 brief 后实现 = "Phase 收口前必经 Codex 复审"协作规范。

### 红线守门

- `pyproject.toml [project]` 基础依赖未动：仍只 `pydantic + click`（brief 阶段）。
- 0 代码 / 0 测试修改。`python3 -m pytest`：**431 passed**（与上轮 `4426c14` 一致）。
- 仓库 `runs/` 仍只含根 `.gitkeep`。
- brief 落 `workspace-files/.context/plan/probe_design.md`（不入库；与 R14/R15/R16 brief 同路径约定）。

### 下一轮

- **Codex 设计复审 probe brief**：重点看
  1. `BaseProvider.probe()` 抽象形状是否匹配现有 ABC 风格（与 `health_check()` 对称）
  2. `_probe_gate_open()` AND 判定是否过严 / 过宽
  3. `ProviderProbeError` 退出码语义（1 = not_implemented / 2 = unhealthy）
  4. 4 adapter override 范围（特别是 `openai` / `anthropic` skeleton 真保持 `NotImplementedError` 还是骨架时就实现最小 probe）
  5. harness 2 个新 scenario + 18 测试覆盖是否完整
  6. 三件套是否对齐 + Open Questions 4 条是否需要先拍板
- 通后启动 Round 1（实现）/ Round 2（测试 + harness）/ Round 3（三件套 + 三轮复审 + push）。

### 候补 next_actions(本轮 Codex 复审通过后再启动哪一个)

1. `phase3_p2_optional_planner_agent_subcommand_inside_planner_web_for_gui_panel` —— probe 通过后，agent 面板可以做" probe 状态 / 调用 probe"按钮
2. `design_provider_optional_probe_separate_from_health_check_no_default_paid_call` —— **本轮已启动**(等 Codex 设计复审)
3. `core3_add_planner_bible_merge_for_cross_episode_continuity` —— 最大范围，独立 user-ack 启动

## 当前状态（2026-07-15 - Phase 3 P2 diagnose continuity-audit Codex 复审修复 P1 + P3 status cleanup，待 Codex 复审放行）

按 Codex 手工对手方对 Phase 3 P2 diagnose continuity-audit (commit `610e5b7`) 的复审结论，verdict **暂不建议进入下一步**：功能测试全绿，但 R14/R15/R16 新增 finding 有 secret-redaction 红线漏洞。本轮按 **P1 必修 + P3 status cleanup 单独 commit** 模板收口。

### P1 修复（修齐 secret 泄露出口，3 处 → `_add_finding` 集中)

- **`planner/agent/diagnose.py` 新增 `_add_finding` helper**：与 `planner/agent/review.py::_add_finding` 镜像，集中对 `message + EvidenceRef.artifact + .path + .locator` 全部走 `_safe_text`；模块私有，不跨模块 import（依赖面扁平 + 避免后续 tweak 级联，与 `_safe_text` 现有镜像契约一致）。
- **`_check_bible_self_consistency` 3 处 inline finding 改走 helper**：
  - id conflict 分支（覆盖 L705 `cid!r` in message + L713 `cid!r` in locator）
  - name conflict 分支（覆盖 L733 `cname!r` in message + L741 `cname!r` in locator）
  - missing visual field 分支（覆盖 L797 `eid!r` in message）
- 保留 `missing_required` 分支原 append —— 那里 `eid`/`ename`/`missing_required` 全是字面/空，无 raw user content 可泄露；改 helper 反而无实际收益且会让 diff 失焦。
- 5 处 reviewer-flagged leak 全部封闭：`sk-` / `sk-ant-` / `gho_` / `Bearer <token>` 与现有 4 条 redact regex 对齐。

### Tests（`tests/test_agent_diagnose.py` +3 secret redaction 回归）

- `test_r14_redacts_secret_in_character_id_and_locator` —— R14 id_conflict，断言 secret 不在 serialized + `<redacted>` 已替换 + finding 仍 emit。
- `test_r15_redacts_secret_in_location_name_and_locator` —— R15 name_conflict，覆盖 message + locator 两处 exit。
- `test_r16_redacts_secret_in_prop_id_missing_visual_field` —— R16 missing_visual_field，覆盖 message 的 `id={eid!r}` exit。

### P3 status cleanup（单独 commit）

- 删 `PROJECT_STATUS.json:232` 的 stale `phase3_p2_review_batch_codex_manual_round2_re_review_pending`（review-batch round-2 早已 commit/push + 后续 round-3/4 复审均已通过；该行已脱钩）
- `phase` / `status` / `completed_steps` / `verification` 同步推进到 `codex_redaction_fixed`

### 红线守门

- `pyproject.toml [project]` 基础依赖未动：仍只 `pydantic + click`。
- **431 pytest**（428 + 3），零回归。
- 仓库 `runs/` 仍只含根 `.gitkeep`；smoke 产物走 `/tmp`。
- production fail-closed + redact + read-only 全部保留（helper 内 message + EvidenceRef 三字段全部 `_safe_text`；bible 缺失/损坏 skip 不重报）。
- harness 7 scenarios 全过 + live cross-check + live agent replay，包括 `diagnose_secret_redaction` scenario。

### 下一轮

- **Codex 手工复审 Phase 3 P2 diagnose continuity-audit 修复**（重点看 `_add_finding` 是否覆盖全部 raw user content 出口 / 与 review.py 镜像契约是否一致 / 3 个回归测试 fixture 是否真匹配 regex / `next_actions` 是否还有 stale / 三件套是否对齐）。
- **Phase 3 P2 可选 next**：GUI agent 面板（按"核心先于壳层"原则）；opt-in probe；Phase Core-3 跨集连续性。

## 当前状态（2026-07-14 - Phase 3 P2 diagnose continuity-audit R14/R15/R16 完工，待 Codex 复审）

按 user-ack 进入 `phase3_p2_extend_diagnose_rules_for_continuity_audit_character_scene_prop_drift`，范围限定**单 run 内 bible self-consistency**，落地在 `planner/agent/diagnose.py` 加 3 条独立规则；CLI 不变；不新增 harness scenario；不做 GUI 面板；跨集 drift 仍归 review-batch rb1-rb3。design brief 落 `.context/plan/continuity_audit_r14_r15_r16.md`。

### 实现（`planner/agent/diagnose.py`）

- 共享 helper `_safe_read_bible_for_self_consistency`（graceful 读 bible：missing/corrupted/non-dict → None）
- 共享 helper `_check_bible_self_consistency`（三类检查 + EvidenceRef locator + `_safe_text` redact）
- 3 条新规则（全 warning）：
  - **R14 character_bible_internal_id_conflict** / `character_bible_internal_name_conflict` / `character_bible_missing_visual_field` — critical_visual_fields = `appearance / positive_prompt / negative_prompt`
  - **R15 location_bible_internal_id_conflict** / `..._name_conflict` / `..._missing_visual_field` — critical_visual_fields = `space_layout / positive_prompt / negative_prompt`
  - **R16 prop_bible_internal_id_conflict** / `..._name_conflict` / `..._missing_visual_field` — critical_visual_fields = `visual / positive_prompt / negative_prompt`
- 接入 `diagnose_run_dir` Step 4 末尾，与 R2/R3/R4/R7/R8/R9/R12/R13 同段
- 模块顶部 docstring 13 → 16 条规则说明，加 R14/R15/R16 段
- `from .tools import` 加 `KNOWN_ARTIFACTS` + `read_artifact`（helper 用）

### 边界

- 与 R12 partial_run_missing_artifact 解耦（R12 是"有没有"，R14/R15/R16 是"有了之后内部自洽"）
- 与 review-run rv1 解耦（rv1 是 shot↔header 双向 missing+phantom，R14/R15/R16 是 bible↔bible 内部）
- 与 review-batch rb1-rb3 解耦（rb1-rb3 是 batch context 跨集，R14/R15/R16 是单集内）
- 与 review-batch rb4 orphan shot reference 解耦（rb4 是 shot→bible 引用方向，R14/R15/R16 是 bible 内部）

### Tests（`tests/test_agent_diagnose.py` +11 engine 测试）

- 9 个 happy/conflict/missing 样例（每规则 3 case）+ `test_r14_skip_when_character_bible_missing`（mirror R12 grace）+ `test_clean_bibles_no_self_consistency_finding`（happy path）

### 红线守门

- `pyproject.toml [project]` 基础依赖未动：仍只 `pydantic + click`。
- **428 pytest**；agent/boundary collect: redact 17 + readers 13 + tools 9 + diagnose 40 + cli 19 + review 58 + boundaries 11（合计 167）；其余为 baseline tests；零回归
- 仓库 `runs/` 仍只含根 `.gitkeep`；smoke 产物走 `/tmp`。
- production fail-closed + redact + read-only 全部保留（新规则所有 message + EvidenceRef locator 都走 `_safe_text`）
- harness 7 scenarios 全过（sample run 用 clean bible，新规则不触发 finding，避免噪音）

### 下一轮

- **Codex 手工复审 Phase 3 P2 R14/R15/R16**（重点看三类检查的语义边界 / EvidenceRef locator 格式 / 与 R12 / rv1 / rb1-rb4 的去重是否完整 / harness sample run 不触发冲突 / 三件套对齐）
- **Phase 3 P2 可选 next**：GUI agent 面板（按"核心先于壳层"原则）；opt-in probe；Phase Core-3 跨集连续性

## 当前状态（2026-07-14 - Phase 3 P2 review-batch Codex round-2 复审修复 P2 + P3，待 Codex round-3 复审）

Codex 手工对手方对 review-batch 完整实现做 round-2 复审，给出 1 P2（核心 batch membership 语义）+ 2 P3（stale 入口文档 + PROJECT_STATUS 测试拆分过时）。本轮按"P2 必修 + P3 顺手补"全部修齐，等 Codex round-3 复审放行。

### P2 修复（核心）：batch membership 以 batch_summary.episodes[] 为权威

- `planner/agent/review.py::review_batch_dir` 删 `list_runs_in_batch` 扫描 + `from .readers import ... list_runs_in_batch` import。
- Step 1 改读 `summary["episodes"]` 作权威清单（malformed → `corrupted_batch_summary` error）。
- Step 2 membership gate：每条 meta 逐项检查（dict 形态 → episode_id/run_dir 字符串 → status=="done" → run_dir 是目录）。失败项 emit `batch_episode_not_reviewable` warning + `episodes_skipped += 1`，**不**进 reviewable 集合。
- episode_id 走 batch_summary.json（不再用目录名）。
- counts：`episodes_total` / `episodes_reviewed` / `episodes_skipped`（`episodes` 保留作 back-compat alias）。
- `_build_batch_summary_zh` 用新 counts，反映"batch_summary 共 N 集；review R 集，跳过 S 集"。

### P3 顺手补

- `planner/agent/__init__.py` + `planner/agent/cli.py::agent_group` docstring：删"review-batch remains a stub" / "diagnose + review-run + stub"，改写为 review-run + review-batch 都 full。
- `PROJECT_STATUS.json`：line 300-301 测试拆分 `17 → 19 / 53 → 58`；line 567 总数 `414 → 417`；`phase` 推到 `v10_phase3_p2_review_batch_codex_round2_batch_membership_fixed`；`status` 推到 `...awaiting_codex_manual_round2_re_review`；`completed_steps` 补 round-2 锚点；`next_actions[0]` 加 `phase3_p2_review_batch_codex_manual_round2_re_review_pending`；`verification` 加 3 行 round-2 锚点。
- `tests/test_agent_review.py::test_batch_tool_invocations_recorded`：`10 → 9`（删 `list_runs_in_batch` 那 1 调），注释同步 batch membership 语义。

### 红线守门

- `pyproject.toml [project]` 基础依赖未动：仍只 `pydantic + click`。
- **417 passed**；agent/boundary collect: `redact 17 + readers 13 + tools 9 + diagnose 29 + cli 19 + review 58 + boundaries 11`（合计 156）；其余为 baseline tests，零回归。
- 仓库 `runs/` 仍只含根 `.gitkeep`；smoke 产物走 `/tmp`。
- production fail-closed + redact + read-only 全部保留。
- harness `validate_live_cross_check` 只验证 `_TOOL_ARTIFACT_MAP` 里的 artifact 是否存在（`list_runs_in_batch` 的 expected artifact 是 `batch_summary.json`，仍存在 → 过）；`validate_live_agent_replay` 只要求 `tool_invocations` 非空 + 每条 finding 有 evidence。删 `list_runs_in_batch` 调用后 7 scenarios 全过，`batch_continuity` full replay 不变。
- `harness/agent_scenarios/batch_continuity.json` `list_runs_in_batch` 仍是 declared expected tool（声明 agent 应能查 batch_summary.json），与 review_batch_dir 实际 tool_invocations 解耦——harness 不破。

### 新增 3 个回归测试（tests/test_agent_review.py）

- `test_review_batch_ignores_stale_subdir_not_in_batch_summary` — 磁盘残留 OLD_EP99 但 batch_summary 不含 → counts=1/1/0，无 rb1 漂移误报。
- `test_review_batch_warns_and_skips_when_episode_status_failed` — EP01 done + EP02 failed → counts=2/1/1，warning 含 "EP02" + "failed"。
- `test_review_batch_warns_and_skips_when_run_dir_missing` — EP01 done + EP02 done 但 run_dir 不存在 → counts=2/1/1，warning 含 "EP02" + "不存在"。

### 下一轮

- **Codex 手工 round-3 复审 Phase 3 P2 review-batch**（重点看 batch membership 语义：stale 不参与 / failed 跳过且 warning / missing run_dir 跳过且 warning / counts 不谎报 / harness batch_continuity full replay 不破）。
- **Phase 3 P2 扩展 diagnose 规则**（continuity audit：角色/场景/道具漂移）。
- **opt-in probe** + Phase Core-3 跨集连续性 + pkg/CI 路线。

## 当前状态（2026-07-14 - Phase 3 P2 review-batch 完工，待 Codex 复审）

Phase 3 P2 review-batch 从 stub 升级为完整实现（方向 B：cross-episode 一致性检查）。stale cleanup 已单独 commit（872f3b4）。review-batch 实现 + 测试 + harness + 三件套同步完成，待 Codex 手工复审。

### review-batch 实现（`planner/agent/review.py`）

- `ReviewBatchReport` model（镜像 ReviewRunReport，batch 级）
- `review_batch_dir()` engine + 4 cross-episode 规则：
  - rb1 `rb1_character_id_inconsistent_across_episodes` (warning)：同 character id 跨集 name 不一致（漂移）
  - rb2 `rb2_location_id_inconsistent_across_episodes` (warning)：同 location id 跨集 name 漂移
  - rb3 `rb3_prop_id_inconsistent_across_episodes` (warning)：同 prop id 跨集 name 漂移
  - rb4 `rb4_orphan_shot_reference` (warning)：shot 的 location_id/character_ids/prop_ids 不在本集 bible
- graceful degradation（missing/corrupted batch_summary -> error；per-episode artifact 缺失 -> warning + 跳过依赖规则）
- redact 出口（`_add_finding` 统一 `_safe_text`，复用 review-run 的 helper）
- 不委托 validate_run；不重复 per-run rv1-rv4；read-only
- CLI `review_batch_cmd` 从 stub 升级为 full（加 `--expected-env`/`--format`/`--verbose`，镜像 review_run_cmd）
- 单集 batch：rb1-rb3 跳过（<2 集无跨集可比），rb4 仍跑
- 14 次 tool_invocations（3 集：read_batch_summary + list_runs_in_batch + 4x3 read_artifact）

### 红线守门

- `pyproject.toml [project]` 基础依赖未动：仍只 `pydantic + click`。
- 410 pytest（391 + 17 batch engine + 2 cli），零回归。
- 仓库 `runs/` 仍只含根 `.gitkeep`；smoke 产物走 `/tmp`。
- production fail-closed + redact + read-only 全部保留（--write-report 政策 + is_inside_repo）。
- harness batch_continuity full replay：review-batch full + tool_invocations non-empty + 每条 finding 有 evidence。

### review 子会话复审修复（待 commit）

Proma 派 review 子会话（扮演 Codex-style 角色，delegation 5693ecef）做对抗式独立审查，verdict NEEDS_FIX，5 findings，本轮修齐 P2 + P3：

- **P2-1 rb4 误报**：`_rule_rb4_orphan_shot_reference` 在某 bible 缺失/损坏时用空 id 集判定 -> 误报 orphan。修复：每类 ref 仅在对应 bible 是 dict 时检查（None 跳过，已由 artifact_unreadable 报告）。
- **P2-2 evidence locator redact**：`_add_finding` 只对 message redact，EvidenceRef.locator 嵌 id 未 redact（secret-in-id 泄露）。修复：`_add_finding` 对 evidence 的 artifact/path/locator 都走 `_safe_text`（review-run + review-batch 共享，同步受益）。
- **P3-1 stale docstring**：`run_all.py` `validate_live_agent_replay` 内联 docstring 仍描述 review-batch stub。修复：改为 full 描述。
- **P3-3 _render_markdown + policy 测试**：`_render_markdown` fallback batch_dir/batch_id；补 review-batch `--write-report` production 仓内 refuse 测试 + markdown smoke 测试。
- **P3-2 harness 强度**（deferred）：live replay 不校验 expected_tool_calls 覆盖。open question。
- **P3-4 name->id 漂移**（open question）：rb1-rb3 仅查 id->name，未查 name->id。等用户拍板。

回归测试：+2 engine（P2-1 rb4 不误报 + P2-2 secret-in-id locator redact）+ +2 cli（policy + markdown）。414 pytest，0 回归。

### 下一轮

- **Codex 手工复审 Phase 3 P2 review-batch**（重点看 4 规则语义 / graceful / redact / read-only / harness full 断言 / 三件套对齐）。
- **Phase 3 P2 扩展 diagnose 规则**（continuity audit：角色/场景/道具漂移）。
- **opt-in probe** + Phase Core-3 跨集连续性 + pkg/CI 路线。

## 当前状态（2026-07-14 - Phase 3 P2 review-run Codex 第四轮复审通过，可提交）

Codex 第四轮复审 verdict 可以提交。顺手修了 `review.py:250` 顶部注释的 round2 旧语义。三轮 P2 演进收口（方向 E 终态）。

### 下一轮

- **Phase 3 P2 review-batch 完整实现**（bible merge across episodes，cross-episode 一致性）。
- **Phase 0 git push to GitHub**（blocked on user URL）。
- **opt-in probe** + Phase Core-3 跨集连续性 + pkg/CI 路线。

## 当前状态（2026-07-14 - Phase 3 P2 review-run Codex 第三轮复审修复 P2 方向 E）

Codex 第三轮复审第二轮修复后 verdict 仍不提交：P1/P3 收口，但 P2 方向2 过度修复漏掉真实 phantom。本轮采用 Codex 建议方向 E 恢复 bidirectional contract。

### 修复

- **P1 通过 / P3 通过**：非 dict 顶层 traceback 守卫 + `__init__.py` stub 文档均已收口。
- **P2（方向 E）**：`_consume_header_names` -> `_parse_prompt_header` 返回 `(consumed, extra)`。consumed = 前 expected 段；extra = 超出 expected 的 header label 段。`_rule_rv1` 加 extra phantom：extra 段 name 命中 bible 已知 name 且不在 expected -> 报 phantom；不命中 -> body 忽略。bible name 命中判定天然区分真实 header（纯 name）vs body prose（name+描述）。恢复 missing + primary phantom + extra phantom 三层 bidirectional contract。

### 红线守门

- `pyproject.toml [project]` 基础依赖未动：仍只 `pydantic + click`。
- 391 pytest（389 + 2 Codex 反例），零回归。
- 仓库 `runs/` 仍只含根 `.gitkeep`；smoke 产物走 `/tmp`。
- production fail-closed + redact + read-only 全部保留。
- review-batch 仍 stub。

### 下一轮

- **Codex 手工复审 Phase 3 P2 review-run 第三轮修复**（重点看方向 E extra phantom bible-name 命中 / bidirectional contract 完整性 / 三件套对齐）。
- **Phase 3 P2 review-batch 完整实现**（复审通过后启动）。
- **Phase 0 git push to GitHub**（blocked on user URL）。
- **opt-in probe** + Phase Core-3 跨集连续性 + pkg/CI 路线。

## 当前状态（2026-07-14 - Phase 3 P2 review-run Codex 第二轮复审修复 P2/P3）

Codex 第二轮复审第一轮修复后 verdict 暂不提交：P1 已通过，但 P2 原始复现样例仍误报（body 第一段以纯 header label 开头），P3 `__init__.py` 残留。本轮按 Codex 方向2 重写 P2 + 修 P3。

### 修复

- **P1 已通过**：合法 JSON 但顶层非 dict 的 artifact / run_summary 不再泄露 traceback（两处 isinstance 守卫）。
- **P2（重写）**：删除 `_extract_header` / `_parse_header_names`，新增 `_consume_header_names(prompt, n_scene, n_char, n_prop)`。按生成器 emit 顺序（场景->人物->道具）消费恰好 expected 数量的每个 label 段，剩余同名 label 归 body。彻底处理任意 body-label 开头（含同 label）。trade-off：多段不报 phantom，改为 name 不符报 phantom；`test_rv1_phantom_character` 改为 name-mismatch 语义。
- **P3（残留）**：`__init__.py:9-14` stale stub 文档清理。

### 红线守门

- `pyproject.toml [project]` 基础依赖未动：仍只 `pydantic + click`。
- 389 pytest（387 + 2 Codex 复现样例），零回归。
- 仓库 `runs/` 仍只含根 `.gitkeep`；smoke 产物走 `/tmp`。
- production fail-closed + redact + read-only 全部保留。
- review-batch 仍 stub。

### 下一轮

- **Codex 手工复审 Phase 3 P2 review-run 第二轮修复**（重点看方向2 _consume_header_names 边界 / phantom name-mismatch 语义 / Codex 原始复现 + 同 label 变体 / 三件套对齐）。
- **Phase 3 P2 review-batch 完整实现**（复审通过后启动）。
- **Phase 0 git push to GitHub**（blocked on user URL）。
- **opt-in probe** + Phase Core-3 跨集连续性 + pkg/CI 路线。

## 当前状态（2026-07-14 - Phase 3 P2 review-run Codex 复审修复 P1/P2/P3）

Codex 手工复审 Phase 3 P2 review-run（commit `43811bc`）后 verdict 暂不进 review-batch，给出 3 findings，本轮全部修齐。

### 修复

- **P1（阻断）graceful / no-traceback**：合法 JSON 但顶层非 dict（list/str/int）的 artifact 不再泄露 `AttributeError` traceback。`_read_artifact_safe` 加 `isinstance(payload, dict)` 守卫（6 个 review artifacts -> `artifact_corrupted` finding）；`review_run_dir` step 0 加 `isinstance(summary, dict)` 守卫（run_summary -> `corrupted_run_summary` error）。两处都走 finding + 跳过依赖规则，CLI 不再出现 traceback。
- **P2 rv1 header 解析**：新增 `_extract_header`（开头连续 `场景：/人物：/道具：` 段，遇非 header 段停止），`_parse_header_names` 只在 header 子串上跑正则。body 正文里的 `人物：xxx` / `场景：xxx` / `道具：xxx` 不再被误判为 phantom 引用。
- **P3 stale 文档**：`cli.py` 顶部 + `agent_group` docstring、`run_all.py` 顶部 + `validate_live_agent_replay` Coverage matrix + review_* 代码注释，全部更新为反映 review-run（P2 full）+ review-batch（仍 stub）。

### 红线守门

- `pyproject.toml [project]` 基础依赖未动：仍只 `pydantic + click`。
- 387 pytest（379 + 6 engine + 2 cli），零回归。
- 仓库 `runs/` 仍只含根 `.gitkeep`；smoke 产物走 `/tmp`。
- production fail-closed + redact + read-only 全部保留；P1 强化 graceful / no-traceback。
- review-batch 仍 stub（build_not_implemented_report 不动）。

### 下一轮

- **Codex 手工复审 Phase 3 P2 review-run 修复**（重点看 P1 isinstance 守卫覆盖是否完整 / P2 header 边界是否漏 header 段 / P3 docstring 是否还有遗漏 / 三件套对齐）。
- **Phase 3 P2 review-batch 完整实现**（bible merge across episodes，cross-episode 一致性）-- 复审通过后启动。
- **Phase 0 git push to GitHub**（blocked on user URL）。
- **opt-in probe** + Phase Core-3 跨集连续性 + pkg/CI 路线。

## 当前状态（2026-07-14 - Phase 3 P2 review-run 完工）

Phase 3 P2 第一步：`planner agent review-run` 从 stub 升级为完整实现。review-batch 仍 stub。双轮验证流程（Explore + Plan 子会话）产出 plan，审批后实施。

### review-run 实现（`planner/agent/review.py`）

- `ReviewRunReport` model（复用 diagnose 子模型，`review_version: Literal["1.0"]`，无 provider/validation 字段）
- `review_run_dir()` engine + 4 规则：
  - rv1 `image_prompt_bible_ref_mismatch` (warning)：解析 prompt header `场景：/人物：/道具：` + 交叉比对 shot 的 bible ID 引用，双向（缺失 + 幻影）
  - rv2 `video_prompt_missing_field` (warning)：motion/camera/avoid 非空
  - rv3 `unresolved_placeholder` (error)：`{word}`/`<WORD>`/`[[TBD]]` 占位符
  - rv4 `shot_id_misaligned` (warning)：shot_list/image_prompts/video_prompts shot_id 集合比对
- graceful degradation（missing/corrupted run_summary -> error；artifact 缺失/损坏 -> warning + 跳过依赖规则）
- redact 出口（`_add_finding` 统一走 `_safe_text`）
- 不委托 validate_run（完全独立；scenario 不含 validate_run；fail-fast 与 graceful 冲突）
- CLI 加 `--expected-env`/`--format`/`--verbose` 对齐 diagnose；`_render_markdown` 泛化（title 参数 + version 兼容）
- 8 次 tool_invocations（read_run_summary + list_artifacts + read_artifact x6）

### cross-episode 语义

review-run 是单 run 内 prompt-bible 一致性检查；cross-episode 留给 review-batch（其 docstring 明确 "Cross-episode continuity review of a batch"）。PROJECT_STATUS 的 `phase3_p2_review_run_implementation_cross_episode_prompt_consistency` 是 feature track 名，不是 review-run 功能范围。

### 红线守门

- `pyproject.toml [project]` 基础依赖未动：仍只 `pydantic + click`。
- 379 pytest = 271 baseline + 107 agent + 1 boundaries，**零回归**。
- 仓库 `runs/` 仍只含根 `.gitkeep`；smoke 产物走 `/tmp`。
- production fail-closed contract 保留（--write-report 政策 + is_inside_repo）。
- API key 永不写盘：redact 覆盖所有 finding message。
- review-run read-only：不写 run 产物。
- review-batch 仍 stub（build_not_implemented_report 不动）。

### 下一轮

- **Codex 手工复审 Phase 3 P2 review-run**（重点看 4 规则正确性 / header 解析鲁棒性 / redact 出口 / graceful / harness replay full 断言 / 三件套对齐）。
- **Phase 3 P2 review-batch 完整实现**（bible merge across episodes，cross-episode 一致性）。
- **Phase 0 git push to GitHub**（blocked on user URL）。
- **opt-in probe** + Phase Core-3 跨集连续性 + pkg/CI 路线。

## 当前状态（2026-07-14 - Phase 0 + Phase 3 P1/P1.5/P1.6 完工 + status cleanup 收口）

按 `docs/PROMA_V1_REVIEW_AGENT_HARNESS_PLAN.md` 第三部分（产品内 agent read-only / guided）首次落地，配套 Phase 0 (git init + baseline)。**用户已批准的边界（9 条）全部守住**：不接 LLM / 不接 executor / 不增加必需 SDK / 不静默放宽红线 / 不写仓内产物（production）/ 不保存 API key / 不执行任意 shell / 不接 GUI RunRegistry / review-run + review-batch 留 stub。Phase 3 P1 骨架 + P1.5（4 findings）/ P1.6（5 findings + status cleanup）三轮 Codex 手工复审均已通过；347 pytest 全绿。

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
- 347 pytest = 271 baseline + 76 新增 agent tests，**零回归**。
- 仓库 `runs/` 仍只含根 `.gitkeep`；smoke 产物走 `/tmp`。
- production fail-closed contract 保留（R1 / R4 / R7 + `--write-report` 政策 + `is_inside_repo` 共享 helper）。
- API key 永不写盘：redact 覆盖所有出口（finding message / summary / stderr / `--write-report` 文件）。
- `executor_tasks.json.tool` 仍 None；R4 红线独立 emit error。
- 不接 GUI RunRegistry（agent 进程独立地址空间）。
- Stub 命令 `tool_invocations=[]` 表明没做任何 read。

### 下一轮

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
