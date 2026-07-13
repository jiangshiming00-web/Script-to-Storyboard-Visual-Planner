# Proma v1.0 Review Fix Plan + Product Agent / Harness Engineering Design

## 读取方式

这是给 Proma 继续开发用的执行文档。请先读本文件，再读：

- `PROJECT_STATUS.json`
- `HANDOFF.md`
- `CHANGELOG.md`
- `docs/AI_COLLABORATION.md`
- `docs/PROMA_V1_RELEASE_PLAN.md`
- `specs/DATA_CONTRACTS.md`

本轮目标分两块：

1. 修复 v1.0 复审发现的真实使用缺口，让版本回到可给同事安装试用的状态。
2. 设计并落地项目内置 agent 的第一阶段骨架，同时把 Harness Engineering 加入协作和验收体系。

## 总结结论

当前仓库已经完成了 v1.0 的大部分基础工作：wheel 漏包已修复，`planner-web`、静态 UI、batch、project.json、export、`openai_compatible` adapter、247 个测试都已存在。

但从“同事安装后实际使用”的角度，仍不能直接宣布正式 v1.0。原因不是底层能力完全缺失，而是若干关键链路没有真正接通：

- 自定义模型配置存在，但 CLI / GUI / pipeline 没有把配置注入 provider 实例。
- GUI run detail 的 artifact 渲染和 `run_summary.json` 实际结构不匹配。
- desktop launcher 关闭窗口后没有真正停止 uvicorn server。
- GUI 的模型配置、probe、batch 仍是占位，没有可保存和可运行闭环。
- `planner project` 和 `planner batch` 之间还没有项目级一键运行入口。

因此当前状态建议标记为：

```text
v1.0-rc: core package mostly ready, product-use blockers remain.
```

修完本文件的 P1/P2 后，再把 `PROJECT_STATUS.json` 改回正式 v1.0 ready。

## 第一部分：v1.0 复审结果

### P1-1 自定义模型没有真正接入运行链路

现象：

- `planner/model_config.py` 已有 `ModelProviderConfig`、`ProviderRuntimeSettings`、`load_model_config()`、`resolve_runtime_settings()`。
- `planner/providers/openai_compatible_adapter.py` 支持传入 `ProviderRuntimeSettings`。
- 但 `planner/pipeline.py` 仍然只调用 `get_provider(requested_name)`。
- `planner/providers/registry.py:get_provider()` 仍然直接 `return cls()`，没有参数。
- CLI 的 `planner run` / `planner batch` 只加载 `config/development.json` 或 `config/production.json`，没有加载 OS app data 或 `--model-config`。
- GUI 的模型表单只存在 DOM，不会保存，也不会影响 `/api/runs`。

影响：

用户在 UI 或本机配置里设置 `openai_compatible` 后，实际 pipeline 仍会得到一个没有 settings 的 provider。`health_check()` 会落到默认配置，并因为 `enable_real_model_calls=False` 报 unhealthy。结果是自定义模型能力停在单元测试层，尚未成为产品路径。

修复要求：

1. 增加 provider factory 或 provider context。
   - 不要破坏现有 deterministic provider。
   - 推荐新增：

```python
def get_provider(name: str, settings: ProviderRuntimeSettings | None = None) -> BaseProvider:
    ...
```

   - 只有 `openai_compatible` / 后续真实模型 provider 使用 settings。
   - deterministic、openai skeleton、anthropic skeleton 可以忽略 settings。

2. 在 CLI 增加模型配置入口。
   - `planner run` 增加 `--model-config /path/to/config.json`。
   - `planner batch` 增加 `--model-config /path/to/config.json`。
   - 没传时读取 `model_config.default_config_path()`。
   - `config/development.json` 仍负责环境边界；model config 负责 provider 运行参数。

3. 在 pipeline 选择 provider 时注入 runtime settings。
   - 若 `config.planner_provider == "openai_compatible"`，必须从 model config 解析 settings。
   - 真实调用前仍必须经过 `health_check()`。
   - production 仍然 fail closed。
   - development fallback 仍然必须写入 audit 字段。

4. `run_summary.json` 增加模型审计字段。
   - 允许写入：`provider_runtime.model`、`provider_runtime.base_url`、`provider_runtime.api_key_env`、`enable_real_model_calls`。
   - 禁止写入：API key 明文、Authorization header、完整上游错误体中疑似 key。

5. 增加端到端测试。
   - fake HTTP server 或 monkeypatch `openai_compatible_adapter.http_post`。
   - 使用 `--model-config` 跑 `planner run`。
   - 验证 provider 真的收到 configured `base_url/model/api_key_env`。
   - 验证 LLM 输出 JSON 被写入既有 artifacts。
   - 验证生产模式缺 key / real calls off 时 fail closed 且不创建空 run dir。

验收命令：

```bash
python3 -m pytest tests/test_model_config.py tests/test_openai_compatible_adapter.py tests/test_pipeline.py tests/test_batch.py
python3 -m pytest
```

### P1-2 GUI artifact 列表渲染会因结构不匹配出错

现象：

- `run_summary.json` 里的 `artifacts` 是 dict：`{artifact_name: path}`。
- `planner/web/static/app.js` 的 `renderDrawer()` 使用 `summary.artifacts.map(...)`。
- dict 没有 `.map()`，用户点开已完成 run detail 时会触发前端错误。

修复要求：

1. `app.js` 同时兼容 dict 和 array。
2. 推荐把 artifact 名称规整成 `Object.keys(summary.artifacts)`。
3. 链接仍然使用 `/api/runs/{run_id}/artifacts/{artifact_name}`。
4. 增加前端静态测试或 Playwright/DOM 测试，覆盖 dict 形态。

验收：

```bash
python3 -m pytest tests/test_web_static_ui.py
```

### P1-3 desktop launcher 关闭窗口后没有真正关闭 server

现象：

- `planner/web/launcher.py:launch_desktop()` 使用 non-daemon thread 启动 uvicorn。
- `finally` 里只记录日志：`window closed, stopping server`。
- 没有持有 `uvicorn.Server` 实例，也没有设置 `server.should_exit = True`，没有 join。

影响：

用户关闭窗口后，进程可能不退出；端口也可能继续占用。这对客户端安装使用是 P1。

修复要求：

1. 重构 launcher，让后台线程能拿到可停止的 `uvicorn.Server` 实例。
2. `finally` 必须设置 `server.should_exit = True`。
3. `server_thread.join(timeout=...)`。
4. 超时未退出时给出日志 warning，不要静默。
5. 增加测试：模拟 webview start 返回后，确认 server stop 被调用。

验收：

```bash
python3 -m pytest tests/test_web_launcher_import.py
```

如本地 socket 测试被 sandbox 拦截，需要在真实 shell 里复跑完整测试。

### P2-1 GUI 模型配置表单没有保存和读取闭环

现象：

- `index.html` 有 provider/model/base_url/api_key_env/enable_real_calls/allow_fallback 字段。
- `app.js` 没有读取 `/api/model-config` 或保存到本机 config。
- `/api/runs` 请求也没有携带 model_config_path 或 inline model config id。

修复要求：

1. 后端新增模型配置 API：
   - `GET /api/model-config`
   - `PUT /api/model-config`
   - 返回时不得包含 API key 明文。
2. 前端启动时加载 model config 并填充表单。
3. 点击保存后写入 OS app data 的 config.json。
4. 运行时 `/api/runs` 使用该模型配置。
5. real model calls toggle 必须有显式用户动作，不允许默认打开。
6. production 不允许 silent fallback，即使 UI 勾选也必须后端拒绝。

验收：

```bash
python3 -m pytest tests/test_web_api.py tests/test_model_config.py
```

### P2-2 GUI batch 和 probe 不能长期停留为“v1.1 占位”

当前 v1.0 如果定义为“同事能安装试用”，可以接受 probe 暂不开放，但 batch 最好至少有一个可用路径。

修复选项：

- 最小方案：UI 清楚提示 batch 请用 CLI，并在 `docs/GUI.md` 写明命令；不把它列为 v1.0 GUI 能力。
- 推荐方案：实现 `POST /api/batches`，调用现有 `planner.batch.run_batch()`。

如果选择推荐方案，要求：

1. 请求字段：`env`、`scripts_dir`、`out_dir`、`force`、`fail_fast`、`skip_validation`。
2. production 仍拒绝 repo 内 out_dir。
3. 返回 batch id、状态、summary path。
4. UI 显示 batch history 或至少显示完成 toast + summary link。

probe 的要求：

- v1.0 可以继续不做。
- 但如果实现，必须是 opt-in，必须独立于 `health_check()`，不得默认联网。

### P2-3 project.json 没有进入 batch/run 主流程

现象：

- `planner project init` 和 `planner project validate` 已存在。
- `planner batch` 仍只接受 `--scripts` 和 `--out`。
- v1.0 release plan 里希望有项目式使用体验。

修复要求：

1. 增加：

```bash
planner batch --project /path/to/project
```

2. 从 `project.json` 读取：
   - scripts_dir
   - runs_dir / output policy
   - default_env
   - default_provider
3. CLI 明确优先级：
   - 显式 CLI 参数 > project.json > config defaults。
4. `planner project validate` 继续作为 preflight。

验收：

```bash
planner project init --dir /tmp/storyboard-demo --name Demo
planner project validate --dir /tmp/storyboard-demo
planner batch --project /tmp/storyboard-demo
```

### P2-4 状态文档需要从“PASS”改为“RC 待修复”

修复要求：

- 更新 `PROJECT_STATUS.json`：
  - `phase` 改为 `v10_release_rc_codex_followup_required`
  - `status` 改为 `v10_release_rc_pending_p1_p2_product_use_fixes`
  - `next_actions` 加入本文件的 P1/P2 修复项。
- 修完后再改为正式 v1.0 ready。
- `CHANGELOG.md` 记录本轮复审和修复。
- `HANDOFF.md` 写明当前阻断项和 Proma 下一步。

## 第二部分：建议执行顺序

Proma 请按以下顺序修复，避免互相影响：

1. `P1-1`：打通 model config -> provider settings -> pipeline/CLI/GUI run。
2. `P1-2`：修复 GUI artifacts dict 渲染。
3. `P1-3`：修复 desktop launcher shutdown。
4. `P2-1`：补 GUI model config 保存/读取 API。
5. `P2-3`：补 `planner batch --project`。
6. `P2-2`：决定并实现 GUI batch；probe 可保持 opt-in 后续。
7. 更新 `PROJECT_STATUS.json`、`HANDOFF.md`、`CHANGELOG.md`。
8. 运行完整测试、wheel 检查、客户端 smoke。

最终验收命令：

```bash
python3 -m pytest
python3 -m pip wheel . --no-deps -w /tmp/storyboard-wheel
python3 - <<'PY'
from pathlib import Path
from zipfile import ZipFile
wheel = next(Path('/tmp/storyboard-wheel').glob('script_to_storyboard_planner-*.whl'))
with ZipFile(wheel) as z:
    names = set(z.namelist())
required = [
    'planner/providers/openai_compatible_adapter.py',
    'planner/web/static/index.html',
    'planner/web/static/app.js',
    'planner/web/launcher.py',
]
missing = [x for x in required if x not in names]
raise SystemExit(f'missing from wheel: {missing}' if missing else 'wheel ok')
PY
```

## 第三部分：项目内置 Agent 方案

### 设计结论

可以使用开源 agent 框架降低开发成本，但不要把通用 coding agent 整体内嵌到客户端产品。

推荐策略：

- 产品内 agent：使用 LangGraph 这类可控编排框架作为底座。
- 开发侧 agent：OpenHands / Codex / Proma 继续用于改代码、跑测试、复审。
- Harness Engineering：负责验证 agent 行为、权限、trace、安装包和回归，不负责替代业务逻辑。

### 产品内 agent 的定位

项目内置 agent 不应该是“会改代码的开发助手”，而应该是“短剧分镜规划助手”。

第一阶段名称建议：

```text
Storyboard Director Agent
```

职责：

- 读取项目状态。
- 读取 `project.json`。
- 读取 run / batch summary。
- 检查剧本、角色 bible、场景、道具、分镜、prompt 的一致性。
- 解释失败原因。
- 给用户建议下一步操作。
- 在用户确认后调用已有 pipeline / batch / export。

不负责：

- 任意执行 shell。
- 任意写文件。
- 读取 API key 明文。
- 自动提交付费生成任务。
- 修改 production config。
- 绕过 production fail-closed。
- 代替人工做最终视觉质量判断。

### 推荐目录结构

```text
planner/agent/
  __init__.py
  graph.py
  state.py
  policy.py
  tools.py
  memory.py
  traces.py
  scenarios.py
  workers/
    __init__.py
    script_reader.py
    continuity_reviewer.py
    prompt_reviewer.py
    run_diagnoser.py
    export_assistant.py
```

### Agent 工具白名单

第一阶段只允许这些工具：

- `read_project_status()`
- `read_project(project_dir)`
- `validate_project(project_dir)`
- `list_runs(env)`
- `read_run_summary(run_dir)`
- `validate_run(run_dir)`
- `run_single_episode(script_path, env, model_config_path, out_dir)`
- `run_batch(project_dir_or_scripts_dir, env, out_dir)`
- `export_run(run_dir, format)`
- `export_batch(batch_dir, format)`
- `explain_error(error_payload)`
- `suggest_bible_fix(run_dir)`
- `suggest_prompt_fix(run_dir)`
- `compare_episode_continuity(batch_dir)`

每个工具必须：

- 有 Pydantic input / output schema。
- 不返回 API key 明文。
- 不返回原始 traceback 给 UI。
- 写入 trace。
- 标注是否需要 human approval。

### 人工确认门禁

以下动作必须先得到用户确认：

- 开启 `enable_real_model_calls=true`。
- 使用 production env。
- 覆盖已有 run。
- 执行 batch。
- 写 model config。
- 触发任何外部 executor。
- 提交任何可能产生费用的生成任务。
- 删除或移动用户 artifact。

### Agent 状态设计

建议 state 至少包含：

```python
class AgentState(BaseModel):
    user_goal: str
    env: Literal["development", "production"]
    project_dir: str | None
    run_dir: str | None
    batch_dir: str | None
    current_step: str
    evidence: list[EvidenceRef]
    proposed_actions: list[ProposedAction]
    approvals_required: list[ApprovalRequest]
    final_answer: str | None
```

`EvidenceRef` 必须指向本地 artifact 或结构化 API 结果，避免 agent 只凭自然语言幻觉判断。

### 第一阶段交付范围

v1.0 follow-up 不要求完整 autonomous agent。建议只做 read-only / guided agent：

1. `planner agent diagnose --run <run_dir>`
2. `planner agent review --run <run_dir>`
3. `planner agent review-batch --batch <batch_dir>`
4. GUI 增加 “Ask Agent” 面板，但只读、不自动执行危险动作。

第一阶段输出：

- run 诊断摘要。
- 分镜一致性问题。
- prompt 缺失或引用错误。
- 失败原因解释。
- 建议用户执行的下一步 CLI/API 动作。

### 第二阶段再做半自动执行

第二阶段可以允许 agent 在人审后调用：

- `run_single_episode`
- `run_batch`
- `export_run`
- `export_batch`

但仍不允许：

- 任意 shell。
- 任意网络浏览。
- 任意代码修改。
- 自动付费生成。

## 第四部分：Harness Engineering 角色

### 角色定义

Harness Engineering 是项目的验证和适配层，不是新的业务 agent。

它负责把 CLI、GUI、provider、agent、packaging 的验收变成可重复运行的脚本、测试和场景，确保项目可以脱离 AI 开发工具独立运行。

### 加入协作分工

请同步更新 `docs/AI_COLLABORATION.md`，加入：

```text
Harness Engineering

负责：
- release smoke harness
- fake model harness
- GUI smoke harness
- agent scenario harness
- permission/approval harness
- trace replay harness
- wheel/installability harness

不负责：
- 创意判断
- 生产付费任务授权
- 修改核心业务边界
- 替代 Codex 复审
```

### 推荐目录结构

```text
harness/
  README.md
  smoke_install.sh
  smoke_cli.py
  smoke_gui.py
  fake_openai_server.py
  agent_scenarios/
    diagnose_failed_run.json
    review_prompt_refs.json
    batch_continuity.json
  golden/
    sample_run_expected.json
    sample_agent_review_expected.json
```

如果不想新增顶层目录，也可以放在：

```text
tests/harness/
```

但推荐顶层 `harness/`，因为它是给开发者和 agent 都能直接运行的工程入口。

### Harness 必须覆盖的场景

1. 安装包验证：
   - 新 venv 安装 wheel。
   - `planner --help` 可用。
   - `planner-web --help` 可用。
   - wheel 内包含 `planner/providers` 和 `planner/web/static`。

2. CLI smoke：
   - deterministic 单集 run。
   - deterministic batch。
   - project init / validate / batch。
   - export markdown/html/csv。

3. fake model smoke：
   - 使用 fake OpenAI-compatible server。
   - `enable_real_model_calls=true`。
   - 跑完整 pipeline。
   - 验证 artifacts 是 fake model 输出，而不是 deterministic fallback。

4. GUI smoke：
   - `planner-web --no-window` 启动。
   - `/api/health` 返回 200。
   - `/`、`/app.js`、`/style.css` 可加载。
   - `/api/model-config` 可读写。
   - `/api/runs` 可启动 development run。

5. Agent scenario smoke：
   - read-only diagnose 不触发 run。
   - review run 必须引用 artifact 证据。
   - 需要 approval 的动作必须停在 approval request。
   - 拒绝 approval 后不得执行。

6. 权限边界：
   - production 不允许 fallback。
   - production 不允许 repo 内 runs。
   - agent 不允许读 API key value。
   - agent 不允许执行 arbitrary shell。
   - agent 不允许自动提交付费 executor。

### Harness 验收命令

最终建议提供一个总入口：

```bash
python3 -m pytest
python3 harness/smoke_cli.py
python3 harness/smoke_gui.py
python3 harness/fake_model_e2e.py
python3 harness/agent_scenarios/run_all.py
```

如果 Proma 暂时不实现全部 harness，至少先实现：

- `harness/fake_model_e2e.py`
- `harness/smoke_cli.py`
- `harness/agent_scenarios/run_all.py`

## 第五部分：完成定义

Proma 完成本轮后，必须满足：

1. `python3 -m pytest` 全绿。
2. wheel 构建和 wheel 内容检查通过。
3. `openai_compatible` 通过 CLI 和 GUI 都能真正使用 model config。
4. GUI 已完成 run 的 artifact drawer 不报错。
5. desktop launcher 能随窗口关闭退出。
6. `planner batch --project` 可用，或文档明确 v1.0 不支持并从状态中移除该能力承诺。
7. `docs/AI_COLLABORATION.md` 已加入 Harness Engineering。
8. `PROJECT_STATUS.json`、`HANDOFF.md`、`CHANGELOG.md` 已同步。
9. 如果实现了产品内 agent，第一阶段必须是 read-only / guided，不允许危险自动执行。

## 禁止事项

- 不要把 API key 明文写入 repo、run summary、trace、export、agent memory。
- 不要让 health_check 联网或扣费。
- 不要让 production silent fallback。
- 不要把 OpenHands 这类 coding agent 直接嵌进客户端产品运行时。
- 不要让产品内 agent 拥有 arbitrary shell。
- 不要把 Flowith / libTV / ComfyUI 写死进 core planner。
- 不要让 Harness Engineering 修改业务边界；Harness 只验证边界。
