"""Deterministic bible builder.

Phase-1 implementation: extracts characters, locations and props from the
parsed script and from inline ``[meta:*]`` annotations. All ids,
defaults and copy are deterministic — no LLM calls. This is enough to
prove the pipeline and to validate reference integrity.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

from .annotations import MetaAnnotation, parse_annotations
from .parser import parse_script
from .schema import (
    Character,
    CharacterBible,
    CharacterRelationship,
    InferenceLevel,
    Location,
    LocationBible,
    LocationType,
    Prop,
    PropBible,
    ScriptBlockKind,
    SourceSpan,
)

# Default visual / wardrobe / prompt copy when the script does not specify one.
DEFAULT_APPEARANCE = "未在剧本中明确描写"
DEFAULT_NEGATIVE = (
    "不要夸张妆容，不要网红脸，不要卡通脸，不要畸形手指，"
    "不要多余人物，不要文字水印"
)
DEFAULT_LOCATION_NEGATIVE = (
    "不要古装，不要豪门客厅，不要卡通画风"
)
DEFAULT_PROP_NEGATIVE = (
    "不要卡通画风，不要夸张颜色，不要文字水印"
)
DEFAULT_PROP_VISUAL = "未在剧本中明确描写"


def _slugify(value: str) -> str:
    """Stable ascii-ish slug for ids.

    Keeps Chinese characters so ids remain readable in JSON.
    """

    value = value.strip()
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"[^0-9A-Za-z_一-鿿]", "", value)
    return value or "unknown"


def build_bibles(
    script_text: str,
    *,
    script_id: str = "sample",
) -> Tuple[CharacterBible, LocationBible, PropBible]:
    """Build the three core bibles from a script."""

    annotations = parse_annotations(script_text)
    parse = parse_script_text(script_text, script_id=script_id)

    character_seeds = {
        a.id: a for a in annotations if a.kind == "character"
    }
    location_seeds = {
        a.id: a for a in annotations if a.kind == "location"
    }
    prop_seeds = {a.id: a for a in annotations if a.kind == "prop"}
    rel_seeds = [a for a in annotations if a.kind == "relationship"]

    characters = _build_characters(parse, character_seeds, rel_seeds)
    locations = _build_locations(parse, location_seeds)
    props = _build_props(parse, prop_seeds)

    return characters, locations, props


def parse_script_text(script_text: str, *, script_id: str) -> object:
    """Wrapper used so :func:`build_bibles` does not depend on filesystem.

    Returns the raw ``ScriptParse`` object — duck-typed because callers
    only need ``blocks`` and the underlying text.

    Meta annotation blocks (``[meta:*]``) and the key/value lines they
    contain are intentionally NOT emitted as narrative blocks. They are
    consumed by :mod:`annotations` separately.
    """

    # We import lazily to keep this module independent of the parser's
    # file-loading concerns.
    from .schema import ScriptParse

    blocks = []
    offset = 0
    in_meta_block = False
    for line in script_text.splitlines():
        stripped = line.strip()
        if not stripped:
            in_meta_block = False
            offset += len(line) + 1
            continue
        if stripped.startswith("#"):
            offset += len(line) + 1
            continue
        if stripped.startswith("[meta:"):
            in_meta_block = True
            offset += len(line) + 1
            continue
        if in_meta_block:
            # Lines inside a [meta:*] block are key/value pairs; they
            # belong to the annotation, not the narrative.
            offset += len(line) + 1
            continue
        # Also stop the meta block once we hit a new structural line.
        if (
            stripped.startswith("EP")
            or stripped.startswith("Scene ")
            or stripped.startswith("场景")
        ):
            in_meta_block = False
        block = _classify_inline(line, stripped, offset, offset + len(line))
        if block is not None:
            blocks.append(block)
        offset += len(line) + 1
    return ScriptParse(script_id=script_id, source_path="<inline>", blocks=blocks)


def _classify_inline(
    line: str, stripped: str, start: int, end: int
):  # type: ignore[no-untyped-def]
    """Lightweight classifier mirroring :mod:`parser` for inline text."""

    from .schema import ScriptBlock, ScriptBlockKind, SourceSpan

    span = SourceSpan(start=start, end=end, text=line)

    if stripped.startswith("[meta:") or stripped.startswith("#"):
        return None

    m = re.match(r"^\s*\[BEAT\s*[:：]\s*([^\]]+?)\s*\]\s*$", stripped)
    if m:
        return ScriptBlock(
            kind=ScriptBlockKind.BEAT,
            text=m.group(1).strip(),
            span=span,
        )

    m = re.match(
        r"^\s*(EP\d+_S\d+(?:_[A-Za-z0-9]+)?)\s*[::\-、]?\s*(.*?)\s*$", stripped
    )
    if m:
        return ScriptBlock(
            kind=ScriptBlockKind.SCENE,
            text=f"{m.group(1)} {m.group(2)}".strip(),
            character=m.group(1),
            span=span,
        )

    if re.match(r"^\s*(?:场景|场|Scene)\s*", stripped):
        m = re.match(
            r"^\s*(?:场景|场|Scene)\s*([^\s:：]+)\s*[:：\-]?\s*(.+?)\s*$", stripped
        )
        if m:
            return ScriptBlock(
                kind=ScriptBlockKind.SCENE,
                text=m.group(2).strip(),
                character=m.group(1).strip(),
                span=span,
            )

    if "：" in stripped or ":" in stripped:
        sep = "：" if "：" in stripped else ":"
        left, right = stripped.split(sep, 1)
        left_s = left.strip()
        right_s = right.strip()
        if (
            left_s
            and right_s
            and len(left_s) <= 12
            and "\n" not in left_s
            and re.match(
                r"^\s*[一-鿿A-Za-z][一-鿿A-Za-z0-9_\-\s]*\s*$", left_s
            )
        ):
            return ScriptBlock(
                kind=ScriptBlockKind.DIALOGUE,
                text=right_s,
                character=left_s,
                span=span,
            )

    return ScriptBlock(
        kind=ScriptBlockKind.ACTION,
        text=stripped,
        span=span,
    )


# ----- Characters -----------------------------------------------------------


def _build_characters(
    parse: object,
    seeds: Dict[str, MetaAnnotation],
    rel_seeds: List[MetaAnnotation],
) -> CharacterBible:
    seen: Dict[str, Character] = {}

    # 1) Seeds first.
    for cid, seed in seeds.items():
        seen[cid] = _character_from_seed(cid, seed)

    # Map display names from seeds back to their canonical id so
    # dialogue lines like "林夏：..." don't create a duplicate entry.
    display_to_id: Dict[str, str] = {}
    for cid, char in seen.items():
        display_to_id[char.name] = cid
        display_to_id[cid] = cid

    # 2) Walk dialogue blocks; auto-add characters not in seeds.
    blocks = getattr(parse, "blocks", [])
    character_scene_pairs: Dict[str, set] = {}
    current_scene: str = "scene_default"

    for block in blocks:
        if block.kind == ScriptBlockKind.SCENE:
            current_scene = block.character or _slugify(block.text.split(" ")[0])
            continue
        if block.kind == ScriptBlockKind.DIALOGUE and block.character:
            display = block.character.strip()
            cid = display_to_id.get(display) or _slugify(display)
            character_scene_pairs.setdefault(cid, set()).add(current_scene)
            if cid not in seen:
                seen[cid] = _character_default(cid, display)

    # 3) Relationships: explicit seeds + co-appearance heuristic.
    explicit_rels: List[Tuple[str, str, str]] = []
    for seed in rel_seeds:
        parts = seed.id.split("->")
        if len(parts) == 2:
            src_raw, dst_raw = parts[0].strip(), parts[1].strip()
            src = display_to_id.get(src_raw) or _slugify(src_raw)
            dst = display_to_id.get(dst_raw) or _slugify(dst_raw)
            label = seed.get("label") or seed.get("relationship") or "同剧角色"
            explicit_rels.append((src, dst, label))

    co_rels: List[Tuple[str, str, str]] = []
    for cid, scenes in character_scene_pairs.items():
        for other_cid in character_scene_pairs:
            if cid == other_cid:
                continue
            if scenes & character_scene_pairs[other_cid]:
                co_rels.append((cid, other_cid, "同场景出现"))

    all_rels = explicit_rels + co_rels
    for src, dst, label in all_rels:
        if src not in seen or dst not in seen:
            continue
        char = seen[src]
        if any(r.target_character_id == dst for r in char.relationships):
            continue
        char.relationships.append(
            CharacterRelationship(target_character_id=dst, relationship=label)
        )

    return CharacterBible(characters=list(seen.values()))


def _character_from_seed(cid: str, seed: MetaAnnotation) -> Character:
    name = seed.get("name") or cid
    appearance = seed.get("appearance") or DEFAULT_APPEARANCE
    wardrobe = seed.get("wardrobe")
    temperament = seed.get("temperament")
    role = seed.get("role")
    age = seed.get("age")
    identity = seed.get("identity")
    negative = seed.get("negative") or DEFAULT_NEGATIVE
    rules = _split_rules(seed.get("continuity"))
    positive = (
        seed.get("positive")
        or f"{age or ''} {identity or ''} {name}，{appearance}".strip()
    )
    return Character(
        id=cid,
        name=name,
        role=role,
        age=age,
        identity=identity,
        appearance=appearance,
        wardrobe=wardrobe,
        temperament=temperament,
        positive_prompt=positive,
        negative_prompt=negative,
        continuity_rules=rules,
    )


def _character_default(cid: str, display: str) -> Character:
    return Character(
        id=cid,
        name=display,
        appearance=DEFAULT_APPEARANCE,
        positive_prompt=f"{display}，{DEFAULT_APPEARANCE}",
        negative_prompt=DEFAULT_NEGATIVE,
        inference_level=InferenceLevel.INFERRED,
        confidence=0.4,
    )


def _split_rules(value):  # type: ignore[no-untyped-def]
    if not value:
        return []
    return [item.strip() for item in re.split(r"[|;；,，\n]", value) if item.strip()]


# ----- Locations ------------------------------------------------------------


def _build_locations(
    parse: object, seeds: Dict[str, MetaAnnotation]
) -> LocationBible:
    locations: Dict[str, Location] = {}

    # 1) Seeds.
    for lid, seed in seeds.items():
        locations[lid] = _location_from_seed(lid, seed)

    # Build a name→id map so a scene heading like "EP01_S01 夜晚办公室"
    # collapses onto the seeded "office_night" location.
    name_to_id: Dict[str, str] = {}
    for lid, loc in locations.items():
        name_to_id[loc.name] = lid
        name_to_id[lid] = lid

    # 2) Auto-add from SCENE blocks.
    for block in getattr(parse, "blocks", []):
        if block.kind != ScriptBlockKind.SCENE:
            continue
        raw_heading = block.text or block.character or ""
        heading = raw_heading.split(" ", 1)[1].strip() if " " in raw_heading else raw_heading
        # If the heading text matches a seeded location name, skip.
        if heading and heading in name_to_id:
            continue
        lid = _slugify(block.character or raw_heading.split(" ")[0])
        if lid in locations:
            continue
        interior = _guess_interior(heading or raw_heading)
        locations[lid] = Location(
            id=lid,
            name=heading or raw_heading or lid,
            type=LocationType.INTERIOR if interior else LocationType.OTHER,
            space_layout=heading or raw_heading or lid,
            positive_prompt=heading or raw_heading or lid,
            negative_prompt=DEFAULT_LOCATION_NEGATIVE,
            inference_level=InferenceLevel.INFERRED,
            confidence=0.5,
            source_span=block.span,
        )

    return LocationBible(locations=list(locations.values()))


def _location_from_seed(lid: str, seed: MetaAnnotation) -> Location:
    name = seed.get("name") or lid
    type_raw = (seed.get("type") or "other").lower()
    try:
        loc_type = LocationType(type_raw)
    except ValueError:
        loc_type = LocationType.OTHER
    lighting = seed.get("lighting")
    mood = seed.get("mood")
    layout = seed.get("layout") or name
    negative = seed.get("negative") or DEFAULT_LOCATION_NEGATIVE
    positive = seed.get("positive") or f"{name}，{layout}"
    return Location(
        id=lid,
        name=name,
        type=loc_type,
        time_of_day=seed.get("time"),
        space_layout=layout,
        lighting=lighting,
        mood=mood,
        positive_prompt=positive,
        negative_prompt=negative,
        continuity_rules=_split_rules(seed.get("continuity")),
    )


def _guess_interior(name: str) -> bool:
    keywords = ("办公室", "会议室", "家", "卧室", "客厅", "餐厅", "室内", "office", "room")
    return any(k in name.lower() for k in keywords)


# ----- Props ----------------------------------------------------------------


_PROP_KEYWORDS = {
    "blue_contract_folder": ["蓝色合同文件夹", "蓝色文件夹", "合同文件夹"],
    "red_lipstick": ["口红", "唇膏"],
    "phone": ["手机", "电话"],
    "car_key": ["车钥匙", "钥匙"],
}


def _build_props(parse: object, seeds: Dict[str, MetaAnnotation]) -> PropBible:
    props: Dict[str, Prop] = {}

    for pid, seed in seeds.items():
        props[pid] = _prop_from_seed(pid, seed)

    text = "\n".join(b.text for b in getattr(parse, "blocks", []))
    for pid, kws in _PROP_KEYWORDS.items():
        if pid in props:
            continue
        if any(kw in text for kw in kws):
            props[pid] = Prop(
                id=pid,
                name=pid,
                visual=DEFAULT_PROP_VISUAL,
                positive_prompt=pid,
                negative_prompt=DEFAULT_PROP_NEGATIVE,
                inference_level=InferenceLevel.INFERRED,
                confidence=0.5,
            )

    return PropBible(props=list(props.values()))


def _prop_from_seed(pid: str, seed: MetaAnnotation) -> Prop:
    name = seed.get("name") or pid
    visual = seed.get("visual") or DEFAULT_PROP_VISUAL
    function = seed.get("function")
    positive = seed.get("positive") or f"{name}，{visual}"
    negative = seed.get("negative") or DEFAULT_PROP_NEGATIVE
    return Prop(
        id=pid,
        name=name,
        visual=visual,
        story_function=function,
        positive_prompt=positive,
        negative_prompt=negative,
        continuity_rules=_split_rules(seed.get("continuity")),
    )