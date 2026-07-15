# Provider Probe — Design Brief (Phase 3 P2 prep, Round 2)

> **Status**: design only, Round 2 revision. No code change in this round.
> Implementation is blocked on user / Codex review approval of this brief.
>
> **Round 1 → Round 2 changes** (this file supersedes the round-1 brief at
> `~/.proma/agent-workspaces/workspace-1783128405740/workspace-files/.context/plan/probe_design.md`,
> which is now archived):
>
> | # | Finding | Resolution |
> |---|---|---|
> | P1-1 | Endpoint double-`/v1` — original brief said `GET {base_url}/v1/models` which collides with default `base_url="https://api.openai.com/v1"` and the Ollama/vLLM `/v1` convention | §2.4 + §4.1 rewritten: `GET {base_url.rstrip("/")}/models` (mirror the existing `openai_compatible_adapter.py:431` pattern); 3 pinning tests added (default OpenAI URL, Ollama `localhost:11434/v1`, vLLM `/v1`) |
> | P1-2 | Brief stored at ephemeral workspace-files path, project repo could not find it | This file moved to **`docs/design/provider_probe_design.md`** and committed; reading order in §0 updated to repo-relative paths |
> | P2 | CLI trigger / env gate / exit code semantics conflicted (subcommand + `--probe` flag + env + Click `UsageError` exit 2 vs custom `ProviderProbeError` exit code conventions) | §2.2 rewritten: explicit trigger = **`planner provider-probe` subcommand** (no `--probe` flag); env `PLANNER_PROBE=1` is the gate; gate closed = **exit 2** policy refusal (matches `_production_locked_keys` "rejected loudly" style). §2.3 + §4.2 rewritten for single exit-code table |
> | P3 | `PROJECT_STATUS.next_actions` still listed already-PASS'd `phase3_p2_diagnose_continuity_audit_codex_manual_re_review_pending`; one CHANGELOG line had `PlannerProbeError` typo | Round-2 commit removes the stale entry + fixes the typo (this brief + CHANGELOG + PROJECT_STATUS all consistently use `ProviderProbeError`) |
>
> **Author**: Proma · **Date**: 2026-07-15 (round 1) → 2026-07-15 (round 2) · **Workspace**: 短剧自动化
>
> **Reading order** (per workspace CLAUDE.md):
> 1. `/Users/shimingjiang/.proma/agent-workspaces/workspace-1783128405740/CLAUDE.md`
> 2. `.claude/memory/MEMORY.md` index
> 3. `PROJECT_STATUS.json` (code repo, `next_actions[0]` = this brief)
> 4. `HANDOFF.md` (current round = Phase 3 P2 continuity-audit PASS, commit `4426c14`)
> 5. **This brief** at `docs/design/provider_probe_design.md` (round-2; in repo)
> 6. Archived round-1 brief at `~/.proma/.../workspace-files/.context/plan/probe_design.md`

---

## 0. TL;DR

给 `BaseProvider` 加一个 **opt-in** 网络可达性 / 真伪探测方法
`probe()`,打开条件（**AND**）：

1. **显式 trigger** = 运维 / 开发者调用顶级子命令 `planner provider-probe`（无 `--probe` flag）；
2. **gate env** = `PLANNER_PROBE=1`（与 trigger 互不替代）。

两者任一不满足 → 一行 stderr policy refusal + **exit 2**，**零**网络。
两者都满足，但 adapter 未实现 → `NotImplementedError` → CLI 包成
`ProviderProbeError` → **exit 1**（feature 缺席）。
两者都满足，adapter 实现了 → `ProviderProbeResult(healthy=...)` →
**exit 0** (healthy=True) 或 **exit 2** (healthy=False)。

probe 严格不写 `run_summary.json` / 不修改 `ProviderHealth` / 不被
`health_check()` 调用；三者完全解耦。Scope 覆盖 4 个现有 adapter：
`deterministic` / `openai_compatible` / `openai` skeleton /
`anthropic` skeleton。CLI 顶层 `try/except PlannerError` 捕获 probe
抛的 `ProviderProbeError(PlannerError)`，stderr 输出结构化结果，绝不让
traceback 漏给用户。

## 1. Context & Goals

### 1.1 现有约束

- `BaseProvider.health_check()` 已在 `planner/providers/base.py:166` 明确：
  *side-effect free w.r.t. paid services, no LLM call / no login /
  no paid probe*。这是红线 §19 的载体：provider 选型阶段 0 网络
  成本，0 付费风险。
- `docs/PROMA_EXECUTION_BRIEF.md` 把 probe 列为下一轮单独设计：
  > *不做 opt-in `probe` 网络探活；probe 是下一轮单独设计。*
- `HANDOFF.md` 历史快照（2026-07-10）已沉淀 probe 5 条设计原则，
  本 brief 在其基础上扩成可落地的契约。
- `planner/providers/openai_compatible_adapter.py:431` 已能真发起 HTTPS
  请求到 `base_url`：URL 拼接模式为 `settings.base_url.rstrip("/") + "/chat/completions"`（**已含** `/v1` 的 base_url 不重复拼接）。**没有**单独的"只验可达、不要 LLM"入口。
- `planner/model_config.py:68` 默认 `base_url="https://api.openai.com/v1"`，已被 `model_config._http_only` validator 强制 `rstrip("/")`。
- `.env` / `model_config` 里已经能配 `base_url` / `api_key_env` /
  `enable_real_model_calls`，缺一个轻量"我配的 endpoint 到底联得通吗"
  的诊断入口。

### 1.2 Goals

| # | 目标 | 验收信号 |
|---|---|---|
| G1 | `BaseProvider.probe()` 抽象方法，每个 adapter 自实现 | 4 adapter 都 override；abstract default raise |
| G2 | **AND gate**：显式 trigger = `planner provider-probe` + env `PLANNER_PROBE=1`；任一未开，probe "policy refusal" | 任何路径下默认 0 网络调用；任一未开 → exit 2 + stderr |
| G3 | 默认实现 `NotImplementedError`（provider 不想暴露 probe 能力） | deterministic / skeleton 全 raise → CLI 包成 `ProviderProbeError` → exit 1 |
| G4 | probe 输出**绝不**写 `run_summary.json` | `run_summary.json` byte-identical 关 probe 前后 |
| G5 | probe 不修改 `ProviderHealth`、不被 `health_check()` 调 | `health_check()` 跑过 N 次后 probe 调用成本 = 0 |
| G6 | probe 抛 `ProviderProbeError(PlannerError)`，CLI `try/except PlannerError` 捕获 | 结构化 JSON 进 stderr，无 traceback |
| G7 | probe 输出走 `redact_secrets_text`（沿用 `planner/agent/redact.py` 4 regex） | raw `sk-...` / `Bearer ...` 在 stderr 不出现 |
| G8 | probe 不引入 OpenAI / Anthropic SDK 进 `[project]` 必需依赖 | `pip install -e .` 后 `pytest` 全绿 |
| G9 | probe endpoint 与 adapter 现有 URL 拼接模式一致：`base_url.rstrip("/") + "/models"`（**不**写死 `/v1` 前缀） | 默认 OpenAI URL → `...com/v1/models`；Ollama `localhost:11434/v1` → `.../v1/models`；vLLM `/v1` → `.../v1/models`，都不双拼 |

### 1.3 Non-Goals

- **不**做自动重试 / 指数退避（probe 是诊断工具而非客户端）。
- **不**做 probe 持久化（不入 SQLite / 不入日志文件；如要痕迹，仅靠
  `--verbose` 时进 stderr，仍受 redact 约束）。
- **不**做 batch probe（一次只 probe 一个 provider；`--provider NAME`）。
- **不**做并发（顺序、串行；并发为未来优化）。
- **不**做 probe 结果聚合 / 历史比较 / 趋势分析（v1.x 不做）。
- **不**触发任何 executor / shot / image generation 调用。
- **不**在 `planner run` / `planner validate` / `planner batch` 等已存在
  子命令上加 `--probe` flag；probe 仅通过 `planner provider-probe`
  顶级子命令触发（见 §2.2 "为什么用顶级子命令而不是 `--probe` flag"）。

## 2. Design

### 2.1 `BaseProvider.probe()` 抽象

```python
# planner/providers/base.py — 新增
@abstractmethod
def probe(self) -> "ProviderProbeResult":
    """Optional network reachability check.

    Must NOT be implemented by default: providers that don't expose
    a real endpoint (deterministic / future local-only models)
    should leave this as ``raise NotImplementedError``. Implementations
    MUST:

    * remain side-effect-light (one HTTPS round-trip at most)
    * never issue LLM / paid inference calls
    * never modify ``run_summary.json`` or any on-disk artifact
    * return within ``timeout_ms`` seconds (the CLI enforces a
      separate outer timeout)
    * redact any secret that leaks through the response body /
      headers before returning
    """
```

```python
@dataclass(frozen=True)
class ProviderProbeResult:
    """Outcome of one provider probe.

    ``healthy`` reflects network reachability + auth sanity, NOT
    paid model quality. ``latency_ms`` is optional (None when the
    provider did not time itself). ``details`` mirrors
    ``ProviderHealth.details`` — string sentinels, free-form keys
    the provider owns; CLI may surface the whole dict to ``--verbose``
    but always redacted.
    """

    name: str
    healthy: bool
    reason: Optional[str] = None
    latency_ms: Optional[int] = None
    details: Dict[str, str] = field(default_factory=dict)
```

`ProviderProbeResult` 与 `ProviderHealth` 故意区分：

- `ProviderHealth` 是"配置 + 依赖 + 本地静态检查"快照，由
  `BaseProvider.health_check()` 产出，是 `_select_provider` 的
  fail-closed / fallback 输入。
- `ProviderProbeResult` 是"opt-in 网络真探"，由 `probe()` 产出，
  是 CLI 单一时刻的诊断快照，**永不**进入 `_select_provider`
  决策路径。

### 2.2 入口控制（顶级子命令 + env gate）

```python
# planner/cli.py — 新增顶级子命令
@cli.command(name="provider-probe")
@click.option("--provider", default=None,
              help="provider 名；缺省读 config.planner_provider")
@click.option("--timeout-ms", default=5000,
              help="probe outer timeout (default 5s)")
@click.option("--verbose", "-v", is_flag=True, default=False)
@click.pass_context
def provider_probe_cmd(ctx: click.Context, provider: Optional[str],
                      timeout_ms: int, verbose: bool) -> None:
    """Opt-in network reachability / sanity probe for one provider.

    Both this subcommand AND env var PLANNER_PROBE=1 must be active;
    missing either → exit 2 with one-line stderr policy refusal,
    zero network calls.
    """
    if not _probe_gate_open():
        # Manual one-line stderr policy refusal. Click's ``UsageError``
        # default rendering prints multi-line usage + help text, which
        # is unfriendly for CI / monitoring. The brief mandates a
        # **strict one-line** stderr so observability tooling can grep
        # for the policy refusal pattern; we exit 2 explicitly via
        # ``ctx.exit`` so the Click machinery does not append usage.
        click.echo(
            "provider probe is opt-in only. Set PLANNER_PROBE=1 in the "
            "environment AND invoke `planner provider-probe` explicitly. "
            "Refusing to issue any network call without explicit consent.",
            err=True,
        )
        ctx.exit(2)
    ...
```

```python
# planner/cli.py (or planner/probe_gate.py)
import os

_PROBE_ENV_VAR = "PLANNER_PROBE"

def _probe_gate_open() -> bool:
    """Single-point gate: env var must equal exactly "1".

    Returning False makes the subcommand exit 2 with a one-line stderr
    policy refusal, **before** any provider is instantiated or any
    network call attempted. We deliberately do NOT check the trigger
    (subcommand invocation) here: the function only sees env state;
    the subcommand body decides whether to call this.
    """
    return os.environ.get(_PROBE_ENV_VAR) == "1"
```

**为什么用顶级子命令而不是 `--probe` flag**：

1. **Alias 不污染**：`planner run --probe` 可以被 shell alias 误触
   （`alias prun='planner run --probe'`），与 production run 混入难排查；`planner provider-probe` 隔离子命令，子命令触发即显式，无歧义。
2. **退出码语义清晰**：子命令未触发 → 不进 `_probe_gate_open` 调用，
   触发后 gate open/false 对应不同 exit code，互不干扰。
3. **与现有 CI 守卫对齐**：`planner/agent/cli.py` 已有 `_check_and_write_report`
   policy 模式；probe 是只读诊断，不写 report，子命令隔离可继承同样风格。

**AND gate 行为**：

| 触发子命令 `planner provider-probe` | env `PLANNER_PROBE=1` | 行为 |
|---|---|---|
| ✗ | 任意 | 子命令不被执行（用户根本没敲） |
| ✓ | unset / != "1" | 调用 `_probe_gate_open()` 返回 False → `click.UsageError` → **exit 2**，**零**网络 |
| ✓ | == "1" | 调用 `provider.probe()`；按 §2.3 退出码分发 |

### 2.3 失败语义与退出码统一表

| 场景 | 调用路径 | 退出码 | stderr | 网络调用 |
|---|---|---|---|---|
| 子命令触发 + env 未设 / != "1" | `click.UsageError` 在 `_probe_gate_open()` 后 | **2** | 一行 policy refusal | 0 |
| env == "1" + adapter 未实现 `probe()` | CLI 顶层 `try/except PlannerError` 包住 `NotImplementedError` → 抛 `ProviderProbeError(reason="not_implemented")` | **1** | 一行 + exit code 1 marker | 0 |
| env == "1" + `probe()` raise `URLError` / `Timeout` / `HTTPError` 包装到 `ProviderProbeError(reason="unreachable", cause=...)` | CLI 顶层捕获 | **2** | 一行 cause + status_code | 已发，未达 200 |
| env == "1" + `probe()` 返回 `healthy=False`（已发请求，对端拒绝） | 直接 print + exit | **2** | 一行 reason | 已发 |
| env == "1" + `probe()` 返回 `healthy=True` | 直接 print + exit | **0** | (空) | 已发 |
| env == "1" + `probe()` 抛任何非 `PlannerError` | CLI 顶层 `try/except PlannerError` 不捕获；既有 traceback 入 stderr | (Click 默认) 1 | traceback | 已发 / 已抛 |
| probe 输出含 secret（任何字段） | `redact_secrets_text` 后再 print | (按上表) | `<redacted>` | 已发 |

**统一原则**：exit code 是**给 CI / 监控系统**的；`healthy` 字段是**给人**的。两套信号都看得到，不会静默放宽。

### 2.4 输出契约 & Redaction

CLI 默认输出（one-line JSON 进 stdout）：

```json
{"provider":"openai_compatible","healthy":true,"reason":"...","latency_ms":312}
```

`--verbose` 时附 `details` dict，但仍 redact 4 种 secret
pattern（沿用 `planner/agent/redact.py` 4 条 regex：
Bearer / sk- / sk-ant- / gho_）：

```json
{"provider":"openai_compatible","healthy":true,
 "reason":"GET /models returned 200","latency_ms":312,
 "details":{"http_status":"200","model":"...","api_key_env":"<redacted>"}}
```

**不**进 run_summary.json：probe 是独立 CLI 子命令，没有 run 上下文。
若未来想让 probe 结果落盘（v1.x 不做），必须新增 `probe_history.jsonl`
到 OS app-data（与 `[project]` 配置同目录），不能污染
`run_summary.json`。

### 2.5 Endpoint 拼接契约（**round-2 fix**）

`openai_compatible.probe()` 必须复用 `openai_compatible_adapter.py:431`
已建立的 URL 拼接模式：

```python
# planner/providers/openai_compatible_adapter.py
def probe(self) -> ProviderProbeResult:
    settings = self._require_settings()
    # Mirror the runtime URL join at openai_compatible_adapter.py:431
    # (settings.base_url.rstrip("/") + "/chat/completions"); the same
    # rstrip handles a base_url that already ends in /v1 (default OpenAI)
    # or /v1 (Ollama/vLLM convention). We add the documented OpenAI-
    # style models-listing endpoint, NOT a /v1 prefix.
    url = settings.base_url.rstrip("/") + "/models"
    ...
```

URL 展开示例（默认 `model_config.OpenAICompatibleConfig.base_url`
路径与 model-listing endpoint 拼接）：

| 配置 `base_url` | 拼接结果 | 用途 |
|---|---|---|
| `https://api.openai.com/v1`（**默认**） | `https://api.openai.com/v1/models` | OpenAI 官方 model list |
| `http://localhost:11434/v1`（Ollama） | `http://localhost:11434/v1/models` | Ollama OpenAI-compatible |
| `http://host:8000/v1`（vLLM） | `http://host:8000/v1/models` | vLLM OpenAI-compatible |
| `https://gateway.example.com/openai/v1` | `https://gateway.example.com/openai/v1/models` | 自定义 gateway |
| `https://api.openai.com/v1/`（末尾 `/`） | `https://api.openai.com/v1/models` | trailing slash 被 rstrip 吃掉 |

**`rstrip("/")` 是 contract 的核心**：与 `model_config._http_only` validator
行为一致（`planner/model_config.py:99-105`），以及 runtime
`openai_compatible_adapter.py:431` 一致——三处共用同一种 sanitize。

### 2.6 红线守门（与 CLAUDE.md §红线 严格对齐）

| 红线 | 本 brief 的兑现 |
|---|---|
| 不接真实 LLM API | G2 + §2.2 AND gate；§2.3 默认 NOT 实施；`openai_compatible.probe()` 仅做 HTTPS GET `/models` model-listing endpoint，不调 inference |
| 不新增模型 SDK 必需依赖 | G8；probe 实现只用 stdlib `urllib` / `socket`，与 `openai_compatible_adapter.py` 一致；可选 SDK 仅 Python 内置 |
| production fail-closed | `planner provider-probe` 子命令**不**走生产 env 路径；无 out_dir / run_dir 影响；probe 失败时 exit 2 |
| production 不写入 `runs/production/` | probe 子命令不创建任何 run 产物；零磁盘足迹 |
| API key 永不写盘 | G7 + §2.4 redact；`PLANNER_PROBE=1` 启用下 stderr 仍走 redact |
| 不静默放宽 | §2.2 双重 gate，任一未开即不工作（exit 2 policy refusal），与 `_production_locked_keys` "rejected loudly" 同精神 |

### 2.7 与 `health_check` 的严格分离

| 维度 | `health_check()` | `probe()` |
|---|---|---|
| 调用频率 | 每次 `_select_provider` 都调（自动） | 仅 `planner provider-probe` 子命令显式调 |
| 副作用面 | 0 网络 / 0 付费 | 1 次 HTTPS round-trip（opt-in） |
| 输出类型 | `ProviderHealth` | `ProviderProbeResult` |
| 写盘 | 进 `run_summary.json::provider_health.*` | 绝不写盘 |
| 调用方 | `_select_provider` 自动 | 人类运营 / debug |
| 失败语义 | fail-closed（production）/ fallback（dev） | exit 0 / 1 / 2 按 §2.3 表分发，**不影响** pipeline |
| 默认实现 | 抽象方法，each adapter 实现 | 默认 raise `NotImplementedError` |
| Gate | 永远开 | 顶级子命令 + env `PLANNER_PROBE=1` 双开 |

代码层——`health_check` **绝不**调用 `probe`，反之亦然：

```python
# planner/providers/base.py — both ABC
@abstractmethod
def health_check(self) -> ProviderHealth:
    """..."""
    # NOT calling self.probe() here. Ever.

@abstractmethod
def probe(self) -> ProviderProbeResult:
    """..."""
    # NOT calling self.health_check() here. Ever.
```

## 3. Adapter 范围

| Adapter | probe 默认实现 | 备注 |
|---|---|---|
| `deterministic` | raise `NotImplementedError` | 无网络，离开就空；CLI exit 1 |
| `openai_compatible` | `GET {settings.base_url.rstrip("/")}/models`（最多 5s），HTTPS / 标准 HTTP | §2.5 endpoint 契约；redact 任何 header / body 中的 secret |
| `openai` skeleton | raise `NotImplementedError` | Phase-1 implementation gate；reason="skeleton: phase-1 health-only, no probe"；CLI exit 1 |
| `anthropic` skeleton | raise `NotImplementedError` | 同 openai skeleton |

`deterministic` 在 probe 时 raise = "undetermined — operator didn't ask
for capability"，与 `healthy=False` 严格区分：

```python
# deterministic.py
def probe(self) -> ProviderProbeResult:
    raise NotImplementedError(
        "deterministic provider has no remote endpoint; "
        "probe() is not applicable."
    )
```

CLI 顶层 `try/except PlannerError` 包住 → `ProviderProbeError(reason="not_implemented")` → **exit 1**（!= 2 区别可观测）。

## 4. Test 范围

### 4.1 单元测试 (`tests/test_provider_probe.py`，预计 +18 unit tests)

| 测试 | 覆盖 |
|---|---|
| `test_probe_gate_closed_when_env_unset` | env var 不设 → False；exit 2 path（CLI 测试覆盖） |
| `test_probe_gate_closed_when_env_not_one` | env var "0" / "true" / "yes" / "" → 全部 False（除"1"以外都不开） |
| `test_probe_gate_open_when_env_one` | env var == "1" → True |
| `test_probe_endpoint_default_openai_url_no_double_v1` | **`base_url="https://api.openai.com/v1"` → 拼 `...com/v1/models`，不是 `...com/v1/v1/models`** |
| `test_probe_endpoint_ollama_no_double_v1` | **`base_url="http://localhost:11434/v1"` → 拼 `:11434/v1/models`，不是 `:11434/v1/v1/models`** |
| `test_probe_endpoint_vllm_no_double_v1` | **`base_url="http://host:8000/v1"` → 拼 `:8000/v1/models`** |
| `test_probe_endpoint_trailing_slash_normalized` | **`base_url="https://api.openai.com/v1/"` → rstrip 后还是 `/v1/models`**，不是 `/v1//models` |
| `test_probe_openai_compatible_succeeds_with_fake_endpoint` | `monkeypatch` `urllib.request.urlopen` 返回 200 + sentinel JSON，assert ProviderProbeResult(healthy=True, latency_ms=...) |
| `test_probe_openai_compatible_unhealthy_on_404` | urlopen 抛 HTTPError 404 → ProviderProbeResult(healthy=False, reason 含 "404") |
| `test_probe_openai_compatible_timeout_returns_not_healthy` | urlopen 抛 URLError(timeout) → ProviderProbeResult(healthy=False, reason 含 "timeout") |
| `test_probe_deterministic_raises_not_implemented` | deterministic.probe() → NotImplementedError |
| `test_probe_skeleton_openai_raises_not_implemented` | 同上；reason 与 skeleton gate 对齐 |
| `test_probe_skeleton_anthropic_raises_not_implemented` | 同上 |
| `test_probe_redacts_api_key_in_response_body` | urlopen 返回 body 含 `sk-...`，assert ProviderProbeResult.details 不含 raw secret |
| `test_probe_redacts_bearer_token_in_response_headers` | header 含 `Bearer <token>`，assert details + stderr 都 redact |
| `test_probe_does_not_write_run_summary` | monkeypatch file write count；probe 跑过 N 次 = 0 writes 到 `<cwd>/runs/...` |
| `test_probe_does_not_modify_provider_health` | probe 跑过 → `provider.health` dict 与之前 byte-identical |
| `test_probe_does_not_depend_on_health_check_call_path` | assert `health_check` 内部不调 `probe`，反之亦然（静态 + runtime） |

### 4.2 CLI 测试 (`tests/test_cli_provider_probe.py`，预计 +10 subprocess)

| 测试 | 覆盖 |
|---|---|
| `test_provider_probe_subcommand_only_no_env_exits_two` | 子命令触发但 env 未设 → exit **2** + stderr 一行 policy refusal，stderr 不含 traceback |
| `test_provider_probe_env_only_no_subcommand_exits_zero` | env 设但**没**调子命令 → 不应触发任何 probe（这条由 CLI 自身保证：subprocess 没跑就不跑） |
| `test_provider_probe_no_subcommand_no_env` | sanity baseline |
| `test_provider_probe_unhealthy_returns_exit_two` | mock urlopen 抛 → exit **2** + 结构化 stderr JSON，`healthy=False` 字段 |
| `test_provider_probe_healthy_returns_exit_zero` | mock urlopen 200 → exit **0** + stdout JSON，`healthy=True` 字段 |
| `test_provider_probe_not_implemented_returns_exit_one` | mock provider.probe 抛 NotImplementedError → exit **1** + stderr `ProviderProbeError(reason="not_implemented")` |
| `test_provider_probe_redacts_stderr_secret` | mock 返回 body 含 secret → stderr / stdout 都不含 raw |
| `test_provider_probe_does_not_create_run_dir` | env 严格隔离 cwd；`runs/`、`<tmp>/runs` 都不出现新文件 |
| `test_provider_probe_traceback_absent_on_failure` | mock probe raise ProviderProbeError → stderr 一行 message，**无** Python traceback |
| `test_provider_probe_subprocess_subcommand_required_no_alias` | assert `planner run --probe` 等旧 alias 不会触发 probe（不存在 `--probe` flag） |

### 4.3 Harness 新增 scenarios (`harness/agent_scenarios/`)

| 文件 | 类别 | 风险 | 期望工具 | 禁止工具 |
|---|---|---|---|---|
| `provider_probe_opt_in.json` | `probe` | `opt_in_network` | 4 (probe + read_run_summary?N/A) | read artifact / write / execute_arbitrary_shell |
| `provider_probe_gate_closed.json` | `probe` | `read_only` | 1 (CLI probe) | 同上 |

`provider_probe_opt_in.json` 的 `validate_live_agent_replay` 必须：

1. 顶级子命令 `planner provider-probe` + `PLANNER_PROBE=1` 双开 → 真实
   urlopen 调用，mock 200 endpoint；assert stderr 含 sentinel + 不含 raw secret。
2. `PLANNER_PROBE=0`（unset） → exit 2 + stderr 一行 policy refusal。

`provider_probe_gate_closed.json` 的 `validate_live_cross_check` 必须
通过现有的 `_find_free_port` 风格 in-process monkeypatch 验证 0 网络
被发起（patch `urllib.request.urlopen` 抛 `AssertionError("network
must not be called")`，跑 CLI，确认 exit 2 + patch 失败从未触发）。

## 5. Open Questions (留给 user / Codex review 拍板)

- [ ] **Q1**：`provider-probe` 子命令**名**——候选：a) `planner provider-probe`（顶级，跟 `planner run` / `validate` / `batch` 同级），b) `planner agent probe`（统一到 agent 子包），c) `planner probe`（顶级短名）。倾向 a（最 self-documenting）。
- [ ] **Q2**：`probe()` 在 strict no-network 默认下，要不要支持"local-only probe"（如检测 ENV / SDK / config 健全但**不**真发请求）？我倾向不要——这正是 `health_check` 的职责；混淆就会让两条路径又不互斥。
- [ ] **Q3**：`details` dict 是否纳入 `run_summary.json::provider_health[*].details` 的 redact-on-copy 链路？我的回答：纳入（与 `fallback_reason` 一致）；即便 probe 不写 run_summary，未来如果某个 operator 让 probe 输出写日志，**也**走同一套 redact。
- [ ] **Q4**：probe 失败重试 N 次？我倾向不重试——诊断工具不该有副作用；N>1 会让 stderr 多行、有歧义。

## 6. Out of Scope（v1.x 不做）

- probe 结果持久化（不入 SQLite / 不入日志文件 / 不入 OS app-data）
- probe 并发（顺序即可）
- 自动调度（不是定时任务对象；只有 CLI 显式触发）
- 与 GUI 集成（GUI 面板是单独 round；probe CLI 子命令先落地）
- batch probe（多 provider 一起探；先单 provider）
- probe 历史 / 趋势 / 报警（高级特性，留 v2.x）

## 7. References

- `planner/providers/base.py:14-17` docstring 已预告："Providers that
  need network reachability should expose an opt-in probe separately."
- `docs/PROMA_EXECUTION_BRIEF.md` Next Step 历史："不做 opt-in `probe`
  网络探活；probe 是下一轮单独设计。"
- `HANDOFF.md` 2026-07-10 沉淀的 5 条设计原则（与本 brief §2.2 §2.6
  一一对齐）。
- `planner/providers/openai_compatible_adapter.py:431` URL 拼接模板：
  `settings.base_url.rstrip("/") + "/chat/completions"`，本 brief §2.5
  probe endpoint 复用此模式。
- `planner/model_config.py:68` 默认 `base_url="https://api.openai.com/v1"`；
  `planner/model_config.py:99-105` `_http_only` validator `rstrip("/")`。
- `planner/agent/redact.py` 4 regex（Bearer / sk- / sk-ant- / gho_）。
- `planner/exceptions.py` 现有 PlannerError 子类层级。
- `planner/agent/diagnose.py::ProviderHealth` 现有 dataclass 形状。
- 测试夹具风格参考 `tests/test_openai_compatible_adapter.py`。
- harness 模板参考 `harness/agent_scenarios/diagnose_secret_redaction.json`。
- 仓库红线来源：`CLAUDE.md` (工作区级) + `.claude/memory/hard-boundaries.md`。

## 8. Implementation Plan（过审后启动，约 2-3 round）

> ⚠ 本节仅作 schedule skeleton；过审前不动。

1. **Round 1 (实现)**：
   - `planner/providers/base.py`：`ProviderProbeResult` dataclass +
     `BaseProvider.probe()` abstract（默认 raise `NotImplementedError`）
   - `planner/exceptions.py`：`ProviderProbeError(PlannerError)`
   - `planner/cli.py`：`provider-probe` 顶级子命令 + `_probe_gate_open()`
   - 4 adapter override：`openai_compatible` 用 §2.5 endpoint 拼接模式真实现，其余 3 raise
   - `planner/providers/__init__.py`：re-export `ProviderProbeResult` /
     `ProviderProbeError`
2. **Round 2 (测试 + harness)**：
   - `tests/test_provider_probe.py` +18 unit（含 4 endpoint 守卫 + 3 env gate + 11 其它覆盖）
   - `tests/test_cli_provider_probe.py` +10 cli（含 4 distinct exit code 表 + 1 no-`--probe`-flag 守卫 + 5 其它覆盖）
   - `harness/agent_scenarios/provider_probe_opt_in.json` + `..._gate_closed.json`
   - `harness/agent_scenarios/run_all.py` 加 2 个新 scenario 入口（总计 7 → 9）
3. **Round 3 (三件套 + Codex 复审 + push)**：
   - CHANGELOG / HANDOFF / PROJECT_STATUS 三件套同步
   - 重新跑 pytest / harness / smoke，确认 0 回归
   - 走完整 Codex 三轮复审闭环（P1 必修 + P2 顺手补 + P3 status 单独 commit）
   - push origin

预计代码量：~280 LOC 生产代码 + ~450 LOC 测试 + ~120 LOC harness。

---

**End of brief, round 2.** 等 user / Codex 拍板后启动 Round 1。本轮
design brief **不**改任何代码；本 commit 把 brief 入库 + 三件套同步
+ stale next_action 清理 + class name 拼写统一。
