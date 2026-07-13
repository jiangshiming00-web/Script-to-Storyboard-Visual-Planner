# Script-to-Storyboard Visual Planner

把短剧剧本转成可执行的视觉生产计划：人物设定、场景设定、道具设定、剧情节奏、分镜表、图片 prompt、视频 prompt、资产清单，并为下游 Web 工具自动化预留接口。

> **当前阶段：** v1.0 收敛。详见 [`docs/PROMA_V1_RELEASE_PLAN.md`](docs/PROMA_V1_RELEASE_PLAN.md) 与 [`HANDOFF.md`](HANDOFF.md)。

## 5 分钟上手

```bash
# 1. 装（GUI 客户端 + pywebview 原生窗口）
pip install "script-to-storyboard-planner[gui]"

# 2. 启动 GUI（macOS / Windows 会弹一个原生窗口）
planner-web

# 只要本地服务不要窗口：planner-web --no-window --port 8765
# 只要 CLI：pip install script-to-storyboard-planner

# 3. 用样例剧本跑一次
python3 -m planner project init --dir ./my-drama --name "我的短剧"
cp samples/v1/EP*.txt ./my-drama/scripts/
python3 -m planner batch --env development \
    --scripts ./my-drama/scripts \
    --out ./my-drama/runs \
    --config ./config/development.json

# 4. 导出报告让人审
python3 -m planner export --batch ./my-drama/runs --format html \
    --output ./my-drama/exports/report.html
```

完整 GUI 手册见 [`docs/GUI.md`](docs/GUI.md)。

## v1.0 命令一览

| 命令                                  | 作用                                                              |
| ------------------------------------- | ----------------------------------------------------------------- |
| `planner run`                         | 跑单集                                                            |
| `planner batch`                       | 跑多集（按 `^(EP\d+)` 排序 `.txt`）                              |
| `planner validate`                     | 校验 run 目录的引用完整性 + 审计字段                              |
| `planner project init`                | 创建 v1.0 项目文件夹（project.json + scripts/runs/exports/）      |
| `planner project validate`            | 项目 pre-flight 校验                                              |
| `planner export`                      | 导出 Markdown / HTML / CSV 给人工审片                            |
| `planner-web`                         | 启动 GUI 客户端（pywebview 原生窗口）                            |
| `python -m planner.web`               | 等价于 `planner-web`，方便 CI / 服务器模式                       |

## v1.0 核心契约（红线条款）

**绝不动摇的部分**（任何修改都会被测试卡住）：

- `production` 默认 `executor_default_status=pending_manual_approval`。
- `production` 默认 `submit_paid_jobs=False`。
- `production` 默认 `allow_overwrite_runs=False`。
- `production` 默认 `allow_provider_fallback=False`。
- `production` **不写仓库内** runs；落到 OS app-data 目录。
- 任何 `PLANNER_*` env-var 想覆盖上面四项 → 显式抛 `ConfigError`（不静默）。
- API key **只读 env var**，**永远不写盘**；`run_summary.json` 只记 `api_key_env`。
- `health_check()` **不联网、不登录、不扣费**；真实探测走 opt-in `probe`。
- `executor_tasks.json.tool` 默认 `None`；v1.0 不接 Flowith/libTV/可灵/即梦/ComfyUI。

详细测试见 `tests/test_boundaries.py`。

## 项目结构

```text
project/
  README.md
  CHANGELOG.md
  HANDOFF.md
  PROJECT_STATUS.json
  docs/
    ARCHITECTURE.md
    ROADMAP.md
    AI_COLLABORATION.md
    PROMA_EXECUTION_BRIEF.md
    PROMA_V1_RELEASE_PLAN.md     # v1.0 收敛计划
    GUI.md                        # GUI 用户手册
  specs/
    DATA_CONTRACTS.md
  samples/
    v1/
      EP01.txt                    # v1.0 验收样例 · 跨集共享人物
      EP02.txt
      EP03.txt
  planner/
    cli.py                        # Click CLI
    pipeline.py                   # 流水线编排
    providers/                    # LLM provider 抽象层
      base.py
      registry.py
      deterministic.py            # 默认 provider（不调真实 LLM）
      openai_adapter.py           # Phase-1 skeleton + implementation gate
      anthropic_adapter.py        # Phase-1 skeleton + implementation gate
      openai_compatible_adapter.py  # v1.0 runtime（OpenAI / vLLM / Ollama / 第三方）
    web/                          # FastAPI + static UI
      static/                     # index.html / app.js / style.css
      launcher.py                 # pywebview 启动器
      scripts_entry.py            # `planner-web` console_script
    model_config.py               # v1.0 结构化模型配置层
    project.py                    # v1.0 项目抽象
    export.py                     # Markdown / HTML / CSV 导出
    batch.py                      # 多集 driver
  config/
    development.json
    production.example.json       # 不提交 production.json
  tests/
    test_boundaries.py            # production 硬边界
    test_providers.py
    test_provider_health.py
    test_openai_anthropic_adapter.py
    test_openai_compatible_adapter.py
    test_wheel_packaging.py       # v1.0 wheel 漏包回归
    test_model_config.py
    test_batch.py
    test_web_api.py
    test_web_run_service.py
    test_web_static_ui.py
    test_web_launcher_import.py
    test_project.py
    test_export.py
```

## AI 分工

- **Codex**：方案设计、数据合同审计、复审。
- **Proma**：实现 + 测试 + 修复 + 维护三件套（CHANGELOG/HANDOFF/PROJECT_STATUS）。
- **Zcode**：实现 + 测试 + 验证工具。
- **Human**（shiming jiang）：创意决策 / 工具账号访问 / 质量批准。

详细见 [`docs/AI_COLLABORATION.md`](docs/AI_COLLABORATION.md)。

## 安装选项

```bash
# 基础 CLI
pip install script-to-storyboard-planner

# + GUI（pywebview 原生窗口；最常用）
pip install "script-to-storyboard-planner[gui]"

# + GUI 服务（无 pywebview，CI / Linux 服务器用浏览器访问）
pip install "script-to-storyboard-planner[server]"

# + dev（pytest / httpx）
pip install "script-to-storyboard-planner[dev]"

# + PyInstaller（CI 打包 .app / .exe）
pip install "script-to-storyboard-planner[build]"
```

## 测试 & 验收

```bash
python3 -m pytest -q                        # 单元测试（243 passed）
python3 -m pip wheel . --no-deps -w /tmp/wheel  # 构建 wheel 验证
unzip -l /tmp/wheel/script_to_storyboard_planner-*.whl  # 确认包含 subpackage

# CLI 帮助（确认 console scripts 注册成功）
planner --help
planner run --help
planner batch --help
planner project --help
planner export --help
planner-web --no-window --help

# 端到端 smoke（用 samples/v1/）
bash scripts/v10-smoke.sh     # 写一个脚本：init → batch → export → wheel
```

## 文档导航

| 文档                                              | 谁该读                              |
| ------------------------------------------------- | ----------------------------------- |
| `docs/PROMA_V1_RELEASE_PLAN.md`                   | 工程 AI（Proma / Zcode）—— v1.0 计划 |
| `docs/GUI.md`                                     | 非工程同事 —— GUI 使用               |
| `docs/ARCHITECTURE.md`                            | 想了解整体架构的所有人              |
| `docs/ROADMAP.md`                                 | 想看未来 Phase 走向的所有人         |
| `docs/AI_COLLABORATION.md`                        | AI 工具 —— 角色 + 工作流             |
| `docs/PROMA_EXECUTION_BRIEF.md`                   | 工程 AI —— 当前任务指令             |
| `specs/DATA_CONTRACTS.md`                         | 任何写/读 11 个 JSON 的人           |
| `HANDOFF.md`                                      | 接手的工程 AI                       |
| `PROJECT_STATUS.json`                             | 同上（机器可读）                    |
| `CHANGELOG.md`                                    | 所有 AI / 人类                      |