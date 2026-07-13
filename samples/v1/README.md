# v1.0 验收样例

> ⚠️ **v1.0 状态：注释骨架 + 跨集种子** —— 每集文件含完整的
> `[meta:character ...]` / `[meta:location ...]` / `[meta:prop ...]`
> 种子 + 1 段场景 + 2 段对白 + 1 个 `[BEAT: ...]` 标记，足以让
> `planner batch` 走通确定性抽取并写齐 11 个 JSON 产物。
>
> **v1.1 计划：补齐完整对白** —— 当 Phase Core-3 落地跨集连续性
> / bible merge 时，需要每集 ≥10 行对白 + ≥3 `[BEAT: ...]` 标记
> 让连续性审计有真实 fixture 可对比。v1.0 release 阶段保持当前
> 骨架，先 ship 二进制。

三集短剧样例，跨集共享人物 / 场景 / 道具，用来验证 v1.0 pipeline 的
单集 + 多集行为，并为 Phase Core-3（跨集连续性 / bible merge）
打底。

## 人物（跨集共享）

| ID         | 名字   | 角色           | 出现于                |
| ---------- | ------ | -------------- | --------------------- |
| lin_xia    | 林夏   | 女主           | EP01, EP02, EP03     |
| chen_mo    | 陈默   | 男主 / 前男友  | EP01, EP03           |
| zhou_jie   | 周姐   | 配角 / 上司    | EP02                 |

## 场景（跨集共享）

| ID            | 名字         | 出现于                |
| ------------- | ------------ | --------------------- |
| office_night  | 夜晚办公室   | EP01, EP02           |
| street_rain   | 雨夜街道     | EP01, EP03           |

## 道具（跨集共享）

| ID                     | 名字             | 出现于           |
| ---------------------- | ---------------- | ---------------- |
| blue_contract_folder   | 蓝色合同文件夹   | EP01, EP03       |
| paper_cup_coffee       | 一次性纸杯咖啡   | EP02             |

## 跑法

```bash
# 1. 用 CLI 跑 batch
python3 -m planner batch --env development \
    --scripts samples/v1 \
    --out /tmp/smoke/runs

# 2. 导出 HTML 报告让人审
python3 -m planner export --batch /tmp/smoke/runs --format html \
    --output /tmp/smoke/report.html

# 3. 或：用 project.json 包起来
python3 -m planner project init --dir /tmp/smoke/proj --name "Demo"
cp samples/v1/*.txt /tmp/smoke/proj/scripts/
python3 -m planner batch --env development \
    --scripts /tmp/smoke/proj/scripts \
    --out /tmp/smoke/proj/runs
```

## 期望输出

每集确定性跑出：

- 2-3 角色 (lin_xia, chen_mo, zhou_jie)
- 1-2 场景 (office_night, street_rain)
- 1 道具 (blue_contract_folder 或 paper_cup_coffee)
- 2 节拍 ([BEAT: ...] 标记)
- 6 镜头 (2 场景 × 3 镜头)
- 6 image_prompts + 6 video_prompts
- 6 executor_tasks (tool=None, status=pending)
- 完整 run_summary.json（fallback_used=false）

batch 模式：

- 3 / 3 episodes_done
- totals.counts.characters: 7 (跨集去重)
- totals.counts.locations: 4 (跨集去重)
- totals.counts.props: 4 (跨集去重)

## 给 Phase Core-3 留的 hook

这 3 集共享 lin_xia / office_night / blue_contract_folder / street_rain
——为 "bible merge" 提供真实 fixture。v1.0 之后 Phase Core-3 的连续性
审计工具应该能：

1. 跨 `runs/development/<run>/` 抽出所有 character_bible / location_bible / prop_bible。
2. 把 `lin_xia` 在 EP01 / EP02 / EP03 的 appearance / wardrobe 拼起来。
3. 对比单集中的描述，发现漂移（例如 EP01 的 lin_xia 穿了"浅灰外套"，
   EP02 没说，EP03 没出现，但下游 shot_list 引用了 lin_xia）。

当 Phase Core-3 落地后，跑：

```bash
python3 -m planner validate --batch /tmp/smoke/runs --continuity-audit
```

预期会看到 7 个 character 跨集一致性报告。