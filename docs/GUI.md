# GUI 用户手册

> 面向**非工程同事**的 v1.0 客户端使用说明。读这份文档不需要 Python
> 经验；命令行只在你愿意用 CLI 跑批处理时才看。

## 0. 这个工具做什么

把一份短剧剧本（`.txt`）拆成视觉规划：角色 / 场景 / 道具 bible、
故事节拍、分镜列表、image / video prompt 草稿，并把它们和审计
字段一起写到 `runs/` 目录供人工审片。**v1.0 不自动提交给任何
生成工具**——所有产物等你看完再决定下一步。

## 1. 安装

需要 Python 3.9+。一条命令装好基础 CLI + GUI 客户端：

```bash
pip install "script-to-storyboard-planner[gui]"
```

`gui` 这组额外依赖会拉 `pywebview`（原生窗口）。如果你只想用浏览器
访问或跑服务器，可以装 `[server]` 组：

```bash
pip install "script-to-storyboard-planner[server]"
```

只想要 CLI（无 GUI）：

```bash
pip install script-to-storyboard-planner
```

装好后会有两个命令可用：

| 命令          | 作用                                  |
| ------------- | ------------------------------------- |
| `planner`     | CLI：`planner run` / `planner batch` / `planner validate` / `planner export` |
| `planner-web` | 启动 GUI 客户端（pywebview 原生窗口） |

## 2. 启动 GUI

### 2.1 默认（带原生窗口）

```bash
planner-web
```

会弹一个 1200×800 的窗口。关闭窗口即停服务。日志写到 stdout。

### 2.2 只跑服务（不开窗口）

CI、远程 Linux 服务器或只想在自己浏览器打开的人：

```bash
planner-web --no-window --host 127.0.0.1 --port 8765
```

然后浏览器打开 <http://127.0.0.1:8765/>。

可选参数：

- `--host 0.0.0.0` 允许局域网访问（注意防火墙）
- `--port 8766` 换端口（如果 8765 被占，`planner-web` 会拒绝启动并报清楚原因）
- `--width 1400 --height 900` 自定义窗口尺寸
- `--repo-root /path/to/repo` 显式指定项目根（默认从当前目录向上找）

### 2.3 用 Python 模块方式启动

等价于 `planner-web`：

```bash
python -m planner.web            # 默认窗口
python -m planner.web --no-window --port 8765
```

## 3. 环境切换（dev vs production）

窗口顶部的 `development` / `production` 切换按钮决定：

- `development`：run 写到仓库内 `runs/development/<run_id>/`，gitignored 本地可见；provider 不健康会**自动 fallback** 到 deterministic 并写审计字段。
- `production`：run 写到操作系统 app-data 目录（macOS `~/Library/Application Support/ShortDramaPlanner/runs/`），**绝不写进仓库**；provider 不健康直接 fail-closed（抛错），不会静默换。

**不要在生产模式不小心覆盖已有 run 目录**——planner 会拒绝。

## 4. 模型配置

### 4.1 默认（deterministic）

不配任何东西就能跑。deterministic provider 用本地规则生成 bibles / beats / shots / prompts，**不发任何网络请求**，不消耗 API 额度。适合：

- 上手试用
- 校验 pipeline
- CI smoke

### 4.2 用真实 OpenAI / 第三方兼容服务（v1.0 推荐路径）

v1.0 通过 `openai_compatible` provider 走任意 OpenAI Chat-Completions
兼容端点：OpenAI 官方、Azure OpenAI、本地 vLLM / Ollama、公司内部
网关都覆盖。配置在 GUI 顶部的 **Model settings** 面板：

| 字段                  | 示例                                          |
| --------------------- | --------------------------------------------- |
| Provider              | `openai_compatible`                            |
| Model                 | `gpt-4o-mini`                                  |
| Base URL              | `https://api.openai.com/v1`                    |
| API key env var       | `OPENAI_API_KEY`（或你自己的 env 名）           |
| Enable real model calls | ✅（**默认关闭，必须显式勾选**才发请求）       |
| Allow fallback        | dev 可勾选；production 必须关闭                 |

#### API key 放哪里

**只放在环境变量里**。planner 永远不会把 key 写到磁盘、也不会出现在
`run_summary.json`。GUI 也只存 env var 名字（如 `OPENAI_API_KEY`），
不存 key 本身。

```bash
# 临时（当前 shell）
export OPENAI_API_KEY="sk-..."

# 永久（macOS zsh）
echo 'export OPENAI_API_KEY="sk-..."' >> ~/.zshrc
source ~/.zshrc
```

#### 写一个本地配置文件（可选）

GUI 的 Model settings 是按需设置。如果你希望设置跨多次启动保留，
可以在 OS app-data 目录写一个 JSON：

- macOS: `~/Library/Application Support/ShortDramaPlanner/config.json`
- Windows: `%APPDATA%\ShortDramaPlanner\config.json`
- Linux: `$XDG_DATA_HOME/ShortDramaPlanner/config.json` 或
  `~/.local/share/ShortDramaPlanner/config.json`

文件长这样（**只放 env var 名，不放 key**）：

```json
{
  "planner_provider": "openai_compatible",
  "enable_real_model_calls": true,
  "allow_provider_fallback": false,
  "openai_compatible": {
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o-mini",
    "api_key_env": "OPENAI_API_KEY",
    "timeout_seconds": 30.0,
    "temperature": 0.7,
    "max_tokens": 2048
  }
}
```

### 4.3 `openai` / `anthropic` 名字

这两个 provider 在 v1.0 是**skeleton（占位）**：即使配好 key + SDK，
`health_check` 仍返回 `unhealthy` 并提示用 `openai_compatible`。这是
Phase-1 implementation gate 的硬约束，保证不会"配错就扣钱"。

> 历史背景：plan 见 `docs/PROMA_V1_RELEASE_PLAN.md` §6。

### 4.4 probe 按钮

GUI 的 **Probe provider** 按钮是 opt-in 的"看一眼当前 provider
配置"，不会发起真实网络请求。要真正探测网络可达性需要 CLI：
（v1.0 暂未提供独立 probe 命令；健康检查已足够本地检查。）

## 5. 跑单集

1. 切到 `development` 环境。
2. **Script** 区域：
   - 上传：点 "Upload (.txt)" 选剧本文件；上传后会出现在 "Or pick an existing script" 框里。
   - 或：在 "Or pick" 框里直接粘绝对路径（如 `/Users/me/scripts/EP01.txt`）。
3. （可选）填 Output directory；不填就走默认。
4. 点 **Run single episode**。

`/api/runs` 返回 `{run_id, status, out_dir, ...}`，列表里会出现一行
新 run，状态从 `running` → `done` / `failed`。点这一行打开抽屉看
详情。

## 6. 跑多集（batch）

GUI v1.0 单集按钮直接可用；批量入口通过 CLI（GUI 的 batch 按钮会
提示用 CLI）：

```bash
planner batch --env development --scripts ./scripts --out ./runs/development
```

- `--scripts DIR`：包含 `.txt` 的目录；按文件名排序处理（确定性顺序）。
- `--out ROOT`：每集会写到 `<ROOT>/<run_id>/`，再写一个 `batch_summary.json`。
- 默认 `--fail-fast`（遇到第一集失败就停）；想跑完所有集加 `--no-fail-fast`。
- 想跳过 `validate` 加 `--skip-validation`。

文件命名：planner 用脚本名前缀的 `^(EP\d+)` 解析 episode_id；
解析不到就回退到大写 stem。

## 7. 查看结果

GUI 的 **Run history** 列出所有 run；点行打开 **详情抽屉**：

- **Counts**：characters / locations / props / shots 数量
- **Audit**：requested_provider / effective_provider / fallback_used / fallback_reason / env
- **Fallback banner**（如果用了 fallback，黄色横条醒目）
- **Artifacts**：11 个 JSON 文件链接
  - `script_parse`
  - `character_bible` / `location_bible` / `prop_bible`
  - `story_beats` / `shot_list`
  - `image_prompts` / `video_prompts`
  - `asset_manifest` / `executor_tasks` / `run_summary`

点 artifact 名直接下载；点 **Validate** 调 `planner validate` 检查引用完整性 + 审计字段。

## 8. 导出报告

CLI 一条命令导出 Markdown / HTML / CSV（v1.0 GUI 暂未集成，按钮会
提示用 CLI）：

```bash
# 单集
planner export --run runs/development/20260711-103045-abc12 --format markdown
planner export --run runs/development/20260711-103045-abc12 --format html
planner export --run runs/development/20260711-103045-abc12 --format csv

# 整批
planner export --batch runs/development/20260711-103045-batch --format markdown
```

每个 report 都包含：项目名 / episode、provider audit、fallback 状态、
validation 结果、bibles / beats / shot list / prompts / executor tasks。
**v1.0 优先让人审结果**，所以 export 是核心功能。

## 9. 常见错误

| 错误信息（人话）                                  | 怎么修                                                       |
| ------------------------------------------------- | ------------------------------------------------------------ |
| `Config file not found: config/production.json`   | 复制 `config/production.example.json` → `config/production.json` |
| `Provider 'xxx' failed health check`              | 切回 `deterministic`，或修 provider 配置                     |
| `API key env var 'OPENAI_API_KEY' is unset`      | `export OPENAI_API_KEY=...`                                  |
| `Real model calls are disabled`                   | GUI 的 Model settings 勾上 Enable real model calls           |
| `Port 8765 is already in use`                     | `--port 8766` 或 `lsof -i :8765` 找占用者                     |
| `Production runs cannot write inside the project repository` | production 不能写仓库内；用默认 app-data 路径，或显式给外部路径 |
| `LLM response is not valid JSON`                  | 模型没按 schema 输出；换模型 / 降 temperature / 重跑         |
| `Empty file upload`                               | 上传文件是 0 字节；检查源文件                                |

GUI 永远只显示**人话错误**，不会显示 Python traceback。如果看到任何
traceback 是 bug，请抓屏发给工程同事。

## 10. 测试 & 升级

升级前先确认 wheel 能装：

```bash
pip install --upgrade "script-to-storyboard-planner[gui]"
planner-web --no-window --port 8765    # smoke：浏览器看 UI 出来
planner --help                          # smoke：CLI 帮助正常
```

更深入的验收见 `samples/v1/`（如果已存在）和 PR 模板里的 checklist。

## 11. 隐私与红线（不要踩）

planner v1.0 **永远不会**：

- 静默把 run 写到仓库内（除非 dev 环境 + 默认路径）
- 静默 fallback 到 deterministic（production）
- 把 API key 写到磁盘或日志
- 自动提交生成任务到 Flowith / libTV / 可灵 / 即梦 / ComfyUI
- 调用真实 LLM 而不显式 `enable_real_model_calls=true`

如果 GUI 出现以上任何一种行为，请立即报 bug。

---

更多细节：

- `docs/PROMA_V1_RELEASE_PLAN.md` —— v1.0 完整交付计划
- `docs/ARCHITECTURE.md` —— 项目架构
- `HANDOFF.md` —— 工程同事视角的状态交接
- `tests/test_boundaries.py` —— production 硬边界测试