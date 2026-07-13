"""Shot list generator.

Deterministic Phase-1 implementation: for each scene, produce one
"establishing" shot, one "medium" dialogue/action shot and, when props
or beats are present, one "close-up" reaction shot. Each shot references
bible ids — never inline visual copy.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .bible import parse_script_text
from .schema import (
    ScriptBlockKind,
    Shot,
    ShotList,
    ShotSize,
    StoryBeat,
)


_SHOT_PLAN = [
    (ShotSize.WIDE, "eye level", "establishing shot of the scene"),
    (ShotSize.MEDIUM, "eye level", "medium shot capturing dialogue or action"),
    (ShotSize.CLOSE_UP, "slightly low angle", "close-up reaction shot"),
]


def _slugify(value: str) -> str:
    import re

    value = value.strip()
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"[^0-9A-Za-z_一-鿿]", "", value)
    return value or "unknown"


def generate_shots(
    script_text: str,
    episode_id: str,
    location_ids: List[str],
    character_ids: List[str],
    prop_ids: List[str],
    beats: List[StoryBeat],
    *,
    scene_default: str = "scene_default",
    display_to_character_id: Optional[Dict[str, str]] = None,
) -> ShotList:
    parse = parse_script_text(script_text, script_id=episode_id)
    blocks = parse.blocks

    display_map = display_to_character_id or {}
    scenes: Dict[str, Dict] = {}
    current_scene: str = scene_default
    for block in blocks:
        if block.kind == ScriptBlockKind.SCENE:
            current_scene = _slugify(block.character or block.text.split(" ")[0])
            scenes.setdefault(
                current_scene,
                {
                    "heading": block.text,
                    "characters": [],
                    "props": [],
                    "beat_ids": [],
                    "action_count": 0,
                },
            )
            continue
        scene = scenes.setdefault(
            current_scene,
            {
                "heading": current_scene,
                "characters": [],
                "props": [],
                "beat_ids": [],
                "action_count": 0,
            },
        )
        if block.kind == ScriptBlockKind.DIALOGUE and block.character:
            display = block.character.strip()
            cid = display_map.get(display) or _slugify(display)
            if cid not in scene["characters"]:
                scene["characters"].append(cid)
        elif block.kind == ScriptBlockKind.ACTION:
            scene["action_count"] += 1
            for pid in prop_ids:
                keywords = _prop_keywords(pid)
                if any(kw in block.text for kw in keywords):
                    if pid not in scene["props"]:
                        scene["props"].append(pid)

    # Attach beat ids to the scene they appear in.
    for beat in beats:
        # We cannot perfectly map a beat span back to a scene without
        # the original source. Approximate by using the first scene
        # whose heading matches beat summary.
        matched = False
        for sid, info in scenes.items():
            if info["heading"] and info["heading"] in beat.summary:
                info["beat_ids"].append(beat.id)
                matched = True
                break
        if not matched and scenes:
            first_scene = next(iter(scenes))
            scenes[first_scene]["beat_ids"].append(beat.id)

    shots: List[Shot] = []
    shot_counter = 0
    scene_order = list(scenes.keys())

    for scene_index, (sid, info) in enumerate(scenes.items(), start=1):
        scene_label = scene_id_from_slug(episode_id, sid, scene_index)
        location_id = scene_index_to_location(scene_index - 1, location_ids)

        # Pick at most two characters present in the scene, fall back
        # to the first global character so every shot has a reference.
        chars = info["characters"] or character_ids[:1]
        props = info["props"] or prop_ids[:0]

        for shot_index, (size, angle, composition) in enumerate(_SHOT_PLAN, start=1):
            if shot_index == 1 and not chars and not props:
                continue  # skip empty establishing shots

            shot_counter += 1
            shot_id = f"{episode_id}_SH{shot_counter:03d}"
            char_ids = chars[:1] if shot_index == 1 else chars[:2]
            prop_ids_for_shot = props if shot_index == 3 and props else []
            beat_id = info["beat_ids"][0] if info["beat_ids"] and shot_index == 3 else None
            duration = 4 if size != ShotSize.CLOSE_UP else 3

            shots.append(
                Shot(
                    id=shot_id,
                    scene_id=scene_label,
                    location_id=location_id,
                    character_ids=char_ids,
                    prop_ids=prop_ids_for_shot,
                    beat_id=beat_id,
                    shot_size=size,
                    camera_angle=angle,
                    composition=composition,
                    action=info["heading"] or scene_label,
                    emotion="紧张" if shot_index == 3 else "克制",
                    duration_sec=duration,
                    continuity_notes=[
                        f"复用 location {location_id}",
                        f"复用 characters {','.join(char_ids)}" if char_ids else "无角色",
                    ],
                )
            )

    if not shots:
        # Always produce at least one shot so downstream validation has
        # something to inspect.
        shots.append(
            Shot(
                id=f"{episode_id}_SH001",
                scene_id=f"{episode_id}_S01",
                location_id=location_ids[0] if location_ids else scene_default,
                character_ids=character_ids[:1],
                shot_size=ShotSize.WIDE,
                camera_angle="eye level",
                composition="establishing shot",
                action="未在剧本中识别出场景，使用默认镜头",
                emotion="克制",
                duration_sec=4,
            )
        )

    return ShotList(shots=shots)


def scene_id_from_slug(episode_id: str, scene_slug: str, idx: int) -> str:
    if scene_slug.startswith(f"{episode_id}_S") or re_match_episode_scene(
        scene_slug, episode_id
    ):
        return scene_slug
    return f"{episode_id}_S{idx:02d}"


def re_match_episode_scene(value: str, episode_id: str) -> bool:
    import re

    return bool(re.match(rf"^{episode_id}_S\d+", value))


def _prop_keywords(pid: str) -> List[str]:
    table = {
        "blue_contract_folder": ["蓝色合同", "蓝色文件夹", "合同"],
        "red_lipstick": ["口红", "唇膏"],
        "phone": ["手机"],
        "car_key": ["车钥匙", "钥匙"],
    }
    return table.get(pid, [pid])


def scene_index_to_location(idx: int, location_ids: List[str]) -> str:
    if not location_ids:
        return "scene_default"
    return location_ids[idx % len(location_ids)]