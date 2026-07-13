# Data Contracts

所有模块之间通过 JSON 文件交接。字段名保持稳定，新增字段必须向后兼容。

## 通用字段

建议每个核心对象包含：

```json
{
  "id": "stable_id",
  "source_span": {
    "start": 0,
    "end": 120,
    "text": "原始剧本文本片段"
  },
  "confidence": 0.8,
  "inference_level": "explicit"
}
```

`inference_level` 可选：

- `explicit`: 剧本明确写出。
- `inferred`: 模型根据上下文推断。
- `human_confirmed`: 人工确认。

## character_bible.json

```json
{
  "characters": [
    {
      "id": "lin_xia",
      "name": "林夏",
      "role": "女主",
      "age": "26",
      "identity": "职场新人",
      "appearance": "清瘦，鹅蛋脸，黑色锁骨发",
      "wardrobe": "白色衬衫，浅灰西装外套，细银项链",
      "temperament": "克制、敏感、倔强",
      "relationships": [
        {
          "target_character_id": "chen_mo",
          "relationship": "前男友"
        }
      ],
      "positive_prompt": "26岁中国女性，清瘦鹅蛋脸，黑色锁骨发...",
      "negative_prompt": "不要夸张妆容，不要网红脸，不要古装",
      "continuity_rules": [
        "同一集默认保持白色衬衫和浅灰西装外套",
        "发型保持黑色锁骨发"
      ],
      "reference_assets": []
    }
  ]
}
```

## location_bible.json

```json
{
  "locations": [
    {
      "id": "office_night",
      "name": "夜晚办公室",
      "type": "interior",
      "time_of_day": "night",
      "space_layout": "现代开放式办公室，玻璃会议室，窗外城市霓虹",
      "lighting": "冷白顶灯，局部屏幕光",
      "mood": "压抑、紧张、安静",
      "positive_prompt": "现代开放式办公室，夜晚，冷白顶灯...",
      "negative_prompt": "不要古装场景，不要豪宅客厅",
      "continuity_rules": [
        "办公桌、玻璃会议室和窗外霓虹保持一致"
      ]
    }
  ]
}
```

## prop_bible.json

```json
{
  "props": [
    {
      "id": "blue_contract_folder",
      "name": "蓝色合同文件夹",
      "visual": "深蓝色文件夹，里面是股权转让合同",
      "story_function": "揭示背叛的关键证据",
      "positive_prompt": "深蓝色文件夹，白色合同纸，签名页清晰",
      "negative_prompt": "不要牛皮纸袋，不要红色文件夹",
      "continuity_rules": [
        "出现时始终是深蓝色文件夹"
      ]
    }
  ]
}
```

## shot_list.json

```json
{
  "shots": [
    {
      "id": "EP01_S03_SH006",
      "scene_id": "S03",
      "location_id": "office_night",
      "character_ids": ["lin_xia"],
      "prop_ids": ["blue_contract_folder"],
      "beat_id": "beat_reveal_forged_signature",
      "shot_size": "close-up",
      "camera_angle": "slightly low angle",
      "composition": "合同在前景，林夏眼睛在背景轻微虚焦",
      "action": "林夏翻开合同，看到签名被伪造",
      "emotion": "震惊但强忍愤怒",
      "duration_sec": 4,
      "continuity_notes": [
        "林夏服装保持白衬衫和浅灰西装外套",
        "合同文件夹保持深蓝色"
      ]
    }
  ]
}
```

## image_prompts.json

```json
{
  "image_prompts": [
    {
      "shot_id": "EP01_S03_SH006",
      "prompt": "现代都市短剧剧照，夜晚开放式办公室...",
      "negative_prompt": "古装，夸张妆容，网红脸，卡通，文字水印，畸形手指，多余人物",
      "aspect_ratio": "16:9",
      "style_tags": ["realistic", "cinematic", "short_drama"]
    }
  ]
}
```

## video_prompts.json

```json
{
  "video_prompts": [
    {
      "shot_id": "EP01_S03_SH006",
      "prompt": "林夏缓慢翻开蓝色合同文件夹，视线停在伪造签名上，呼吸变浅，镜头轻微推进。",
      "motion": "slow push-in",
      "duration_sec": 4,
      "camera": "slightly low angle close-up",
      "avoid": "不要换脸，不要换服装，不要突然出现其他人物"
    }
  ]
}
```

## asset_manifest.json

```json
{
  "assets": [
    {
      "shot_id": "EP01_S03_SH006",
      "image": {
        "status": "pending",
        "local_path": null,
        "tool": null,
        "task_id": null,
        "approved": false
      },
      "video": {
        "status": "pending",
        "local_path": null,
        "tool": null,
        "task_id": null,
        "approved": false
      }
    }
  ]
}
```

## executor_tasks.json

executor task 是下游工具层输入，不属于 planner 核心输出。

```json
{
  "tasks": [
    {
      "id": "task_EP01_S03_SH006_image_v001",
      "shot_id": "EP01_S03_SH006",
      "kind": "image_generation",
      "tool": "flowith",
      "status": "pending_manual_approval",
      "input_prompt_ref": "image_prompts.json#EP01_S03_SH006",
      "output_asset_ref": null
    }
  ]
}
```

## 状态枚举

通用任务状态：

- `pending`
- `pending_manual_approval`
- `submitted`
- `running`
- `completed`
- `failed`
- `approved`
- `rejected`
- `skipped`

