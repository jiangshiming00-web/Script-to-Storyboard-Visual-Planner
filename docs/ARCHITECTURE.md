# Architecture

## 目标

构建一个把短剧剧本转成视觉生产计划的 agent。它要理解剧本中的人物、场景、道具和剧情节奏，并输出稳定的分镜与 prompt。下游生图、生视频、下载、上传、归档由 executor 负责。

## 总体结构

```text
Script Input
  -> Script Parser
  -> Story Extractor
  -> Visual Bible Builder
  -> Shot Planner
  -> Prompt Compiler
  -> Asset Manifest
  -> Executor Adapter
```

## 模块说明

### 1. Script Parser

职责：

- 读取 `.txt`、`.md`，后续可扩展 `.docx`。
- 保留原始段落、角色台词、动作描写、场景标题。
- 给剧本文本建立可追踪引用，例如 `source_span`。

输出：

- `script_parse.json`

### 2. Story Extractor

职责：

- 抽取人物、关系、场景、道具、事件和情绪变化。
- 标记剧情节点：铺垫、冲突、反转、高潮、钩子。
- 判断哪些信息是明确写出的，哪些是模型推断。

输出：

- `characters_raw.json`
- `locations_raw.json`
- `props_raw.json`
- `story_beats.json`

### 3. Visual Bible Builder

职责：

- 把 raw extraction 变成可复用视觉设定。
- 给每个人物、场景、道具分配稳定 id。
- 生成正向描述、负面描述、连续性规则。

输出：

- `character_bible.json`
- `location_bible.json`
- `prop_bible.json`
- `style_bible.json`

### 4. Shot Planner

职责：

- 把剧情拆成镜头。
- 为每个镜头规划景别、机位、构图、动作、情绪、道具、时长。
- 引用 bible id，而不是重新写一遍设定。

输出：

- `shot_list.json`

### 5. Prompt Compiler

职责：

- 把镜头卡、角色 bible、场景 bible、道具 bible、风格 bible 合成 prompt。
- 分别输出 image prompt 与 video prompt。
- 保留负面 prompt 和连续性约束。

输出：

- `image_prompts.json`
- `video_prompts.json`

### 6. Asset Manifest

职责：

- 记录每个镜头对应的图片、视频、参考图、生成任务 id、下载路径和质量状态。
- 支持失败重试、人工确认和工具切换。

输出：

- `asset_manifest.json`

### 7. Executor Adapter

职责：

- 对接具体工具，例如 Flowith、Liblib/libTV、ComfyUI。
- 接收 prompt task，提交任务，等待结果，下载资产，更新 manifest。
- 不参与剧本理解，不修改 bible 和 shot list。

## 核心边界

planner 负责“想清楚要生成什么”；executor 负责“去哪里生成、怎么下载”。

这条边界非常重要。否则一旦 Web 工具变化，项目核心也会被拖垮。

