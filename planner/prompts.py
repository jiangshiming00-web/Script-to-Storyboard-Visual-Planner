"""Prompt compiler.

Each shot's image/video prompt is composed from the relevant bible
entries. The composer never inlines visual copy that should live in a
bible — it concatenates bible text plus the shot's cinematography.
"""

from __future__ import annotations

from typing import Dict, List

from .schema import (
    Character,
    CharacterBible,
    ImagePrompt,
    ImagePrompts,
    Location,
    LocationBible,
    Prop,
    PropBible,
    Shot,
    ShotList,
    VideoPrompt,
    VideoPrompts,
)


def _index(items, key: str = "id"):
    return {getattr(item, key): item for item in items}


def compile_image_prompts(
    shots: ShotList,
    characters: CharacterBible,
    locations: LocationBible,
    props: PropBible,
) -> ImagePrompts:
    char_index: Dict[str, Character] = _index(characters.characters)
    loc_index: Dict[str, Location] = _index(locations.locations)
    prop_index: Dict[str, Prop] = _index(props.props)

    out: List[ImagePrompt] = []
    for shot in shots.shots:
        loc = loc_index.get(shot.location_id)
        chars = [char_index[c] for c in shot.character_ids if c in char_index]
        shot_props = [prop_index[p] for p in shot.prop_ids if p in prop_index]

        # Header includes the explicit character/location/prop names so
        # downstream validators (and humans) can confirm the shot is
        # bound to the right bible entries.
        header_parts: List[str] = []
        if loc:
            header_parts.append(f"场景：{loc.name}")
        for ch in chars:
            header_parts.append(f"人物：{ch.name}")
        for pr in shot_props:
            header_parts.append(f"道具：{pr.name}")

        body_parts: List[str] = []
        if loc:
            body_parts.append(loc.positive_prompt)
        for ch in chars:
            body_parts.append(ch.positive_prompt)
        for pr in shot_props:
            body_parts.append(pr.positive_prompt)

        cinematog = (
            f"{shot.shot_size.value} 镜头，{shot.camera_angle}，"
            f"构图：{shot.composition}，情绪：{shot.emotion}"
        )

        prompt = "。".join(
            p for p in header_parts + body_parts + [cinematog] if p
        )

        negatives: List[str] = []
        if loc:
            negatives.append(loc.negative_prompt)
        for ch in chars:
            negatives.append(ch.negative_prompt)
        for pr in shot_props:
            negatives.append(pr.negative_prompt)
        negatives.append("不要文字水印，不要畸形手指")

        out.append(
            ImagePrompt(
                shot_id=shot.id,
                prompt=prompt,
                negative_prompt="，".join(n for n in negatives if n),
                aspect_ratio="16:9",
                style_tags=["realistic", "cinematic", "short_drama"],
            )
        )
    return ImagePrompts(image_prompts=out)


def compile_video_prompts(
    shots: ShotList,
    characters: CharacterBible,
    locations: LocationBible,
    props: PropBible,
) -> VideoPrompts:
    char_index = _index(characters.characters)
    loc_index = _index(locations.locations)
    prop_index = _index(props.props)

    out: List[VideoPrompt] = []
    for shot in shots.shots:
        loc = loc_index.get(shot.location_id)
        chars = [char_index[c] for c in shot.character_ids if c in char_index]
        shot_props = [prop_index[p] for p in shot.prop_ids if p in prop_index]

        action_parts: List[str] = []
        if chars:
            names = "、".join(c.name for c in chars)
            action_parts.append(f"{names} {shot.action}")
        else:
            action_parts.append(shot.action)
        for pr in shot_props:
            action_parts.append(pr.positive_prompt)

        cinematog = (
            f"{shot.shot_size.value} 镜头，{shot.camera_angle}，"
            f"{shot.composition}，情绪：{shot.emotion}"
        )

        prompt = "，".join(action_parts) + f"，{cinematog}。"

        avoid_parts: List[str] = []
        avoid_parts.append("不要换脸，不要换服装，不要突然出现其他人物")
        for ch in chars:
            avoid_parts.append(ch.negative_prompt)
        for pr in shot_props:
            avoid_parts.append(pr.negative_prompt)
        if loc:
            avoid_parts.append(loc.negative_prompt)

        out.append(
            VideoPrompt(
                shot_id=shot.id,
                prompt=prompt,
                motion="slow push-in" if shot.shot_size.value == "close-up" else "static",
                duration_sec=shot.duration_sec,
                camera=f"{shot.camera_angle} {shot.shot_size.value}",
                avoid="，".join(avoid_parts),
            )
        )
    return VideoPrompts(video_prompts=out)