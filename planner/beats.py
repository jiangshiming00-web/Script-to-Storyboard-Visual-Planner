"""Story beat extractor.

In Phase-1 the beat list is derived deterministically from explicit
``[BEAT: label]`` markers plus one inferred "setup" beat per episode.
"""

from __future__ import annotations

from typing import List

from .parser import parse_script
from .schema import InferenceLevel, ScriptBlockKind, StoryBeat, SourceSpan
from pathlib import Path


def extract_beats(script_path: Path, *, episode_id: str = "EP01") -> List[StoryBeat]:
    parse = parse_script(script_path)
    beats: List[StoryBeat] = []
    beat_idx = 0
    current_scene: str = "scene_default"

    for idx, block in enumerate(parse.blocks):
        if block.kind == ScriptBlockKind.SCENE:
            current_scene = block.character or f"scene_{idx}"
            if beat_idx == 0:
                beat_idx += 1
                beats.append(
                    StoryBeat(
                        id=f"beat_{episode_id}_setup",
                        label=f"{episode_id} 场景建立",
                        summary=block.text,
                        span=block.span,
                        inference_level=InferenceLevel.EXPLICIT,
                    )
                )
            continue
        if block.kind == ScriptBlockKind.BEAT:
            beat_idx += 1
            beats.append(
                StoryBeat(
                    id=f"beat_{episode_id}_{beat_idx:02d}",
                    label=block.text,
                    summary=f"{current_scene} 中出现剧情节点：{block.text}",
                    span=block.span,
                    inference_level=InferenceLevel.EXPLICIT,
                )
            )

    if not beats:
        beats.append(
            StoryBeat(
                id=f"beat_{episode_id}_setup",
                label=f"{episode_id} 场景建立",
                summary="未发现 BEAT 标记，使用默认开场节拍",
                span=SourceSpan(start=0, end=0, text=""),
                inference_level=InferenceLevel.INFERRED,
                confidence=0.3,
            )
        )

    return beats