# Proma v1.0 Release Plan

## 目标

把 Script-to-Storyboard Visual Planner 收敛成第一个可安装、可启动、可配置模型、可跑剧本的 v1.0 客户端版本。

v1.0 的重点不是继续扩展实验性能力，而是让用户和同事能真实使用：

- 安装后可运行 CLI。
- 安装后可启动本地客户端。
- 可以上传或选择剧本。
- 可以跑单集和多集。
- 可以查看运行状态、错误、审计字段和产物。
- 可以配置默认 deterministic、OpenAI、Anthropic、OpenAI-compatible 自定义模型。
- 可以导出 Markdown / HTML / CSV 报告，方便人工审片和判断结果质量。

## v1.0 验收定义

v1.0 必须满足以下条件。

1. 同事可以安装使用：
   - 支持 `pip install .`。
   - 支持 wheel 安装。
   - 安装后 `planner` 可用。
   - 安装后 `planner-web` 可用。
   - 为后续 PyInstaller `.app` / `.exe` 打包做好结构准备。

2. 用户可以实际跑剧本：
   - 支持单集运行。
   - 支持多集 batch 运行。
   - 支持上传或选择 `.txt` 剧本。
   - 支持查看 run 历史、运行状态、错误、产物和校验结果。
   - 支持导出 Markdown / HTML / CSV。

3. 用户可以配置模型：
   - 默认仍是 `deterministic`，保证没有 API key 也能跑。
   - 支持 `openai`。
   - 支持 `anthropic`。
   - 支持 `openai_compatible` 自定义模型。
   - 自定义模型至少支持 `base_url`、`model`、`api_key_env`、`timeout_seconds`、`temperature`、`max_tokens`。
   - API key 只从环境变量或用户本机配置读取，不写入 repo，不写入 `run_summary.json`。
   - `health_check()` 仍只能做本地检查，不能联网、不能登录、不能产生费用。
   - 真实联网探活必须通过 opt-in `probe`，默认关闭。
   - 真实模型调用必须显式开启，不能误触产生费用。

4. 现有边界不能被破坏：
   - 核心 planner 不写死 Flowith / libTV / 可灵 / 即梦 / ComfyUI。
   - `executor_tasks.json` 默认 `tool=None`。
   - production 默认 `pending_manual_approval`。
   - production 不允许 silent fallback。
   - production 不写 repo 内 runs。
   - GUI 只是薄壳，业务规则仍在 `planner/` 核心包。

## 第一阶段：修复 v1.0 阻断问题

### 1. 修复 wheel 漏包

当前 `pyproject.toml` 只声明：

```toml
[tool.setuptools]
packages = ["planner"]
```

这会导致 wheel 只包含顶层 `planner/*.py`，漏掉：

- `planner/providers/*`
- `planner/web/*`
- 未来的 `planner/web/static/*`

这是 v1.0 客户端安装的阻断问题。请改成 setuptools package discovery。

建议方向：

```toml
[tool.setuptools.packages.find]
include = ["planner*"]
```

如果 Phase UI 已经加入静态文件，还必须补 package data：

```toml
[tool.setuptools.package-data]
"planner.web" = ["static/*", "static/**/*"]
```

验收要求：

```bash
python3 -m pip wheel . --no-deps -w /tmp/storyboard-wheel
unzip -l /tmp/storyboard-wheel/script_to_storyboard_planner-*.whl
```

wheel 文件列表必须包含：

- `planner/providers/base.py`
- `planner/providers/registry.py`
- `planner/providers/openai_adapter.py`
- `planner/providers/anthropic_adapter.py`
- `planner/web/app.py`
- `planner/web/routes.py`
- `planner/web/run_service.py`
- Phase UI 完成后包含 `planner/web/static/index.html`

新增测试或构建验证脚本，确保以后不会回归。

### 2. 修复 GUI config endpoint 的 repo root

`planner/web/routes.py` 的 `/api/config` 当前必须显式使用 app/service 的 repo root 调用 `load_config()`。

风险：

- 从源码目录启动时可能暂时正常。
- 打包后或从其他目录启动时，可能错误读取当前工作目录下的 config。

修复要求：

- `/api/config` 调 `load_config(env=..., project_root=repo_root, config_path=...)`。
- 不要直接依赖当前工作目录。
- production 缺 `config/production.json` 时仍返回 404 + copy example 提示。
- config 文件存在但内容非法时仍返回 400。

新增测试：

- 从非 repo CWD 创建 app。
- 传入明确 `repo_root`。
- `GET /api/config?env=development` 仍读取该 repo 的 `config/development.json`。
- `GET /api/config?env=production` 缺 production config 时仍是 404。

### 3. 修正文档和测试注释旧语义

`tests/test_openai_anthropic_adapter.py` 顶部注释仍有旧语义：key + SDK 满足时 healthy。

当前正确语义：

- skeleton 阶段即使 key + SDK 齐全也必须 `healthy=False`。
- 只有真实规划方法实现后，provider 才能返回 `healthy=True`。
- `health_check()` 不联网、不登录、不扣费。

同步修复：

- `tests/test_openai_anthropic_adapter.py` 注释。
- `HANDOFF.md`。
- `PROJECT_STATUS.json`。
- `CHANGELOG.md`。

## 第二阶段：模型配置与自定义模型

### 4. 增加结构化模型配置

新增结构化配置层，避免把模型参数散落在 `env.py` 或 GUI 表单里。

建议新增：

```text
planner/model_config.py
```

建议模型：

- `ProviderRuntimeSettings`
- `ModelProviderConfig`
- `OpenAICompatibleConfig`

字段建议：

- `planner_provider`
- `model`
- `base_url`
- `api_key_env`
- `timeout_seconds`
- `temperature`
- `max_tokens`
- `enable_real_model_calls`
- `allow_provider_fallback`

安全要求：

- 不保存 API key 明文。
- `run_summary.json` 只能记录 `api_key_env`，不能记录 key 值。
- 用户本机配置可以放在 OS app data 下，不要求同事改 repo 文件。
- production 仍不能 silent fallback。

建议配置存储位置：

- macOS: `~/Library/Application Support/ShortDramaPlanner/config.json`
- Windows: `%APPDATA%/ShortDramaPlanner/config.json`
- Linux: `$XDG_DATA_HOME/ShortDramaPlanner/config.json` 或 `~/.local/share/ShortDramaPlanner/config.json`

### 5. 实现 `openai_compatible` provider

新增 provider：

```text
planner/providers/openai_compatible_adapter.py
```

用途：

- OpenAI 官方 API。
- 兼容 OpenAI Chat Completions 或 Responses 形态的第三方模型。
- 公司内部模型网关。
- 本地 vLLM / Ollama 兼容网关。

配置字段：

- `base_url`
- `model`
- `api_key_env`
- `timeout_seconds`
- `temperature`
- `max_tokens`
- `enable_real_model_calls`

硬规则：

- SDK 或 HTTP client 仍是 optional dependency。
- 没有 `enable_real_model_calls=true` 时，不能真实调用模型。
- `health_check()` 只检查本地配置、key env 是否存在、依赖是否可用。
- `probe` 必须独立，默认关闭，不写 `run_summary.json`。
- 真实 run 时必须把 LLM 输出解析成既有 Pydantic schema。
- JSON 解析失败要返回结构化错误，不要吞掉后 fallback。
- production 不允许 fallback 到 deterministic。

建议先实现最小稳定链路：

- `build_bibles`
- `extract_beats`
- `generate_shots`
- `compile_image_prompts`
- `compile_video_prompts`

每一步要求模型返回 JSON，并用既有 Pydantic schema 校验。

失败处理：

- 返回 `ProviderOutputError` 或类似 `PlannerError` 子类。
- 错误信息包含 provider、model、step、可读原因。
- 可以记录截断后的原始响应片段，但不能记录 API key。
- CLI 和 GUI 都不能显示 traceback。

### 6. 保留 OpenAI / Anthropic provider

`openai` 和 `anthropic` 的处理建议：

- `openai` 可以作为 `openai_compatible` 的 thin wrapper，使用官方默认 base URL 和 `PLANNER_OPENAI_API_KEY`。
- `anthropic` 如果接口差异较大，v1.0 可以先保持 configured-but-not-implemented，或实现独立 adapter。
- 不要为了 Anthropic 硬改核心 schema。
- 不要把 OpenAI / Anthropic SDK 加进基础依赖。

## 第三阶段：客户端 UI

### 7. 增加静态前端

新增：

```text
planner/web/static/index.html
planner/web/static/app.js
planner/web/static/style.css
```

打开 `planner-web` 后第一屏就是工具界面，不要做营销页。

v1.0 UI 必须包含：

- 顶部环境切换：development / production。
- 模型设置页或面板：
  - provider。
  - model。
  - base_url。
  - api_key_env。
  - enable real model calls toggle。
  - probe 按钮。
- 剧本上传 / 选择区。
- 单集运行按钮。
- 多集 batch 运行按钮。
- run 历史列表。
- run 详情抽屉：
  - counts。
  - provider audit。
  - fallback banner。
  - validation result。
  - artifact 查看。
  - artifact 下载。
- 错误展示：
  - 不显示 traceback。
  - 显示用户能处理的人话错误。

UI 设计要求：

- 不要把说明文字堆成教程页。
- 主要工作流要一眼能操作。
- 运行状态要清楚：queued / running / done / failed。
- fallback 必须醒目。
- production 相关危险操作必须明确提示。

### 8. 增加 `planner-web` 启动器

新增：

```text
planner/web/launcher.py
planner/web/scripts_entry.py
planner/web/__main__.py
```

`pyproject.toml` 注册：

```toml
[project.scripts]
planner = "planner.cli:main"
planner-web = "planner.web.scripts_entry:main"
```

必须支持：

```bash
planner-web
planner-web --no-window --host 127.0.0.1 --port 8765
python3 -m planner.web --no-window
```

行为要求：

- `planner-web` 默认打开 pywebview 原生窗口。
- `--no-window` 只启动本地服务，方便测试和服务器模式。
- 如果端口占用，给清楚错误或自动换端口并输出最终 URL。
- 关闭窗口时优雅停止 server。
- 不要让后台线程成为不可控孤儿进程。

新增测试：

- `tests/test_web_launcher_import.py`
- `planner-web --no-window` smoke。
- `python3 -m planner.web --no-window` smoke。

### 9. 增加 GUI 使用文档

新增：

```text
docs/GUI.md
```

面向非工程同事写清楚：

- 如何安装。
- 如何启动。
- 如何配置模型。
- API key 应该放在哪里。
- 如何跑单集。
- 如何跑多集。
- 如何查看结果。
- 如何导出报告。
- 常见错误怎么处理。

不要只写开发者说明。

## 第四阶段：v1.0 项目工作流

### 10. 增加 `project.json`

新增最小项目结构：

```text
project_folder/
  project.json
  scripts/
    EP01.txt
    EP02.txt
  runs/
  exports/
```

新增命令：

```bash
planner project init --dir ...
planner project validate --dir ...
planner batch --project ...
```

`project.json` 至少包含：

- `project_name`
- `script_dir`
- `default_env`
- `default_provider`
- `output_dir`
- `created_at`
- `updated_at`

GUI 也要支持打开 project folder。

验收：

- 初始化项目。
- 放入 3 集样例。
- GUI 能打开该项目。
- CLI 能 batch 跑该项目。

### 11. 增加导出

新增命令：

```bash
planner export --run ... --format markdown
planner export --run ... --format html
planner export --run ... --format csv
planner export --batch ... --format markdown
planner export --batch ... --format html
planner export --batch ... --format csv
```

导出内容至少包含：

- 项目名 / episode。
- provider audit。
- fallback 状态。
- validation 结果。
- character bible。
- location bible。
- prop bible。
- story beats。
- shot list。
- image prompts。
- video prompts。
- executor tasks。

v1.0 最重要的是让人审结果，所以导出优先级高于花哨 UI。

## 第五阶段：测试与验收

### 12. 必跑测试

完成后必须跑：

```bash
python3 -m pytest -q
python3 -m pip wheel . --no-deps -w /tmp/storyboard-wheel
```

创建临时 venv 安装 wheel 后验证：

```bash
planner --help
planner run --help
planner batch --help
planner project --help
planner export --help
planner-web --no-window --host 127.0.0.1 --port 8765
```

### 13. 必须新增测试

至少新增以下测试：

- wheel 包含 `planner.providers`。
- wheel 包含 `planner.web`。
- wheel 包含 `planner.web.static`。
- `/api/config` 使用 repo_root，不依赖 CWD。
- `planner-web --no-window` import/start smoke。
- static UI 被打包。
- `openai_compatible` provider 配置缺失时 fail。
- `openai_compatible` provider 禁止未授权真实调用。
- fake LLM server 返回合法 JSON 时 pipeline 成功。
- fake LLM server 返回坏 JSON 时用户看到结构化错误。
- production 不 fallback。
- production 不写 repo runs。
- GUI 模型配置不保存明文 key。
- 导出 Markdown / HTML / CSV 成功。

### 14. v1.0 验收样例

准备：

```text
samples/v1/
  EP01.txt
  EP02.txt
  EP03.txt
```

验收必须证明：

- deterministic 单集成功。
- deterministic batch 成功。
- fake OpenAI-compatible provider 单集成功。
- fake OpenAI-compatible provider batch 成功。
- GUI 能启动。
- GUI 能上传并运行。
- GUI 能查看 artifacts。
- GUI 能导出报告。
- wheel 安装后仍可用。

## 禁止范围

v1.0 不做以下事情：

- 不自动提交 Flowith / libTV / 可灵 / 即梦任务。
- 不做无人值守成片。
- 不保存真实 API key。
- 不提交真实 `config/production.json`。
- 不提交真实密钥、cookie、账号。
- 不把 executor 默认状态改成 `pending`。
- 不让 production silent fallback。
- 不把 `health_check()` 做成联网探活。
- 不让 GUI 绕过核心 planner 规则。
- 不把模型调用失败吞掉后假装成功。

## 交付要求

完成后必须更新：

- `PROJECT_STATUS.json`
- `HANDOFF.md`
- `CHANGELOG.md`
- `README.md`
- `docs/GUI.md`

交付说明必须包含：

1. 变更摘要。
2. v1.0 功能清单。
3. 安装方式。
4. 启动方式。
5. 模型配置方式。
6. 测试命令和结果。
7. wheel 验证结果。
8. GUI smoke 结果。
9. 已知限制。
10. 下一版建议。

## 优先级

必须按以下顺序推进：

1. 修打包阻断。
2. 修 GUI repo_root。
3. 注册并实现 `planner-web` 启动。
4. 增加静态 UI。
5. 增加 OpenAI-compatible 自定义模型。
6. 增加 `project.json` 工作流。
7. 增加 export。
8. 增加 PyInstaller spec。

不要跳过前两项直接做 UI。当前最危险的问题是安装包漏子包，必须先修。
