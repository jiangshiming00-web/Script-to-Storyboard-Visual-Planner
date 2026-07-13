# Harness Engineering

> **状态：v1.0 harness 脚本已落地。** 本目录现在提供 5 个可重复运行的
> 验收脚本（CLI / GUI / fake model / permission boundary / agent scenarios），
> 任何 release engineer 都可以在不依赖 AI 开发工具的前提下独立验证
> v1.0 的核心契约。

## 角色定位

Harness Engineering 是项目的**验证和适配层**，不是业务 agent。它
把 CLI、GUI、provider、agent、packaging 的验收变成可重复运行的
脚本、测试和场景，确保项目可以脱离 AI 开发工具独立运行。

详细职责与分工见 `docs/AI_COLLABORATION.md` 的 "Harness Engineering"
章节。

## 脚本清单（v1.0 已实现）

| 脚本 | 作用 |
| --- | --- |
| `smoke_install.py` | wheel 构建 + venv + ensurepip + `planner --help` + `planner-web --help` + base install probe 拒绝 optional deps |
| `smoke_cli.py` | 跑 `planner run / validate / batch / project / export` 全链路；显式 `--model-config` 隔离 OS app-data；含 secret-leak guard |
| `smoke_gui.py` | 启 `planner-web --no-window`，hit 全部 API endpoint + 静态资产；用 `PLANNER_APP_DATA_ROOT` + `PLANNER_MODEL_CONFIG_PATH` 隔离用户 OS app-data；POST runs/batches 显式 `/tmp` out_dir；`_find_free_port` fallback 到 `bind(0)` |
| `fake_model_e2e.py` | monkeypatch `openai_compatible_adapter.http_post` 接 fake server，跑完整 pipeline；sentinel 字符验证 artifacts 真来自 fake model；production + real_calls=off → fail-closed no-residue 验证 |
| `permission_boundaries.py` | 4 个 PLANNER_* env-var downgrade attempts + tampered production.json + production `--force` 拒绝 + production batch + CLI run 拒绝 repo-internal output_dir + app-data out_dir 成功 + API key hygiene + executor tool 中立性 + 7 条 agent 静态规则 |
| `agent_scenarios/run_all.py` | 验证每个 scenario JSON 的静态形状 + live cross-check（跑真 run / batch 验证 expected_tool_calls 适用） |

### Agent scenarios（`harness/agent_scenarios/`）

只读场景定义 + 验收脚本。**不实现产品内 agent**，仅固化未来 agent 的工具白名单与权限门禁：

- `diagnose_failed_run.json` — diagnose 类，read_only
- `review_prompt_refs.json` — review 类，read_only
- `batch_continuity.json` — review 类，跨集连续性，read_only
- `approval_required_write.json` — approval_gate 类，写动作必须停 approval request

每个 JSON 都声明：

- `expected_tool_calls`（白名单）
- `forbidden_tool_calls`（黑名单）
- `expected_approval_requests`（仅 approval_gate 类）
- 静态断言 + global_assertions

`run_all.py` 校验 JSON shape、expected ∩ forbidden = ∅、approval-gate
形状完整；并对 diagnose / review 类跑真 run / batch 验证其
expected_tool_calls 真的能 apply 到真实 artifacts 上。

## 验收命令（v1.0 目标）

```bash
python3 -m pytest                              # 单元测试（271 passed）
python3 harness/smoke_install.py               # wheel 构建 + venv + console_scripts
python3 harness/smoke_cli.py                   # CLI smoke
python3 harness/smoke_gui.py                   # GUI smoke（启 planner-web --no-window）
python3 harness/fake_model_e2e.py             # fake model 端到端
python3 harness/permission_boundaries.py       # 权限边界
python3 harness/agent_scenarios/run_all.py     # agent scenario 静态 + live 校验
```

`harness/permission_boundaries.py` 是 fast feedback 的入口——
它无需启动 server / 跑 batch，几秒内完成全部 contract 验证。
`harness/smoke_install.py` 是 release-blocking 的入口——任何 wheel
构建 / install 路径变更都要在这里跑过才算 v1.0 installable。

## 设计原则

1. **不修改业务边界**。Harness 只验证边界，不动边界。
2. **不依赖会话状态**。每个 harness step 从干净环境出发，不读
   AI agent 的 memory / Codex 报告 / Proma conversation。
3. **/tmp 全跑**。所有 smoke 产物落 `/tmp/smoke_*_<pid>`，仓库
   `runs/` 仍只含根 `.gitkeep`。
4. **友好错误，不静默**。失败时 stderr 出 friendly message + 上下文；
   traceback 一律不出现在用户可见的错误里。
5. **不发起付费请求**。fake server 在 in-process 模拟，不联网，
   不扣费。

## 不负责

- 创意方向判断
- 生产付费任务授权
- 修改核心业务边界（Harness 只验证边界，不改边界）
- 替代 Codex 复审
- 绕过 human approval
- 实现产品内 agent（v1.0 harness 只固化场景定义，不实现 agent runtime）

## 红线

- 不读取或输出 API key 明文
- 发现边界被绕过时必须 fail closed
- 不依赖 Codex/Proma/Zcode 的会话状态（只通过正式 CLI/API/测试入口验证）
- 每次新增 agent 能力时同步新增 permission / approval / trace replay 场景

## 已知局限

- `fake_model_e2e.py` 当前覆盖 `openai_compatible`；`openai` /
  `anthropic` 仍是 Phase-1 skeleton（`healthy=False`），不在 v1.0
  runtime 路径上。
- `permission_boundaries.py` 的 agent placeholder 步骤是静态清单，
  不是真实 agent 执行——产品内 agent 落地后再补 execution replay。
- GUI smoke 跑 `planner-web --no-window`，不验证 desktop launcher
  的 native window 生命周期；后者已在 `tests/test_web_launcher_import.py`
  覆盖。

## 仓库内 CLI 守卫（与 harness 配套）

Phase 2 harness 落地暴露了 CLI run 路径的真实产品 bug：production
`planner run --out <repo>/...` 没有 repo-internal 守卫。本轮
在仓库内补齐：

- **`planner.env.is_inside_repo(path, repo_root)`** — 共享 helper，
  `Path.resolve().is_relative_to`。
- **`planner.cli.run_cmd`** — `planner run --env production --out <repo>/...`
  rc=2 拒绝，友好文案复用 GUI `resolve_out_dir` 文案。
- **`planner.batch.BatchOptions.resolved_out_dir`** — 改用共享 helper。
- **GUI `resolve_out_dir`** — 不变（之前已有）。
- **测试** — `tests/test_boundaries.py` 加 3 个测试
  （CLI run repo guard + `is_inside_repo` symlink + helper base case）。

## Harness 隔离 env var

为了让 harness 完全不污染用户真实本机状态，仓库内增加两个
**可选** env var override（无 override 时行为不变）：

- **`PLANNER_APP_DATA_ROOT`** — `planner.web.run_service.os_app_data_dir()`
  返回此路径；GUI smoke + 测试用之把 uploads + production runs 重定向到 /tmp。
- **`PLANNER_MODEL_CONFIG_PATH`** — `planner.model_config.default_config_path()`
  返回此路径；GUI smoke PUT/GET `/api/model-config` 落到 /tmp 而非
  `~/Library/Application Support/ShortDramaPlanner/config.json`。

这两个 override 只在 harness + 测试中使用；正常用户运行时仍然
落到默认 OS app-data 路径。