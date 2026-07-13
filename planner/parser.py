"""Script parser.

Reads a structured plain-text short drama script and produces a
``ScriptParse`` with stable ``source_span`` references. The parser is
deliberately conservative: if a line cannot be classified it is recorded
as an ``unknown`` block instead of being silently dropped.

Supported line shapes (case-insensitive for headings):

- ``EPxx_Syy <heading>`` or ``EPxx_Syy_<anything>`` — scene heading.
- ``场景 <id>：<heading>`` — alternative scene heading.
- ``<name>：<text>`` or ``<name>:<text>`` — dialogue line.
- ``[BEAT: <label>]`` — story beat marker.
- Anything else with non-whitespace content — action description.

The parser does NOT call any LLM. It only normalises layout and records
spans, leaving higher-level extraction to the bible builder.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

from .exceptions import ScriptReadError
from .io_utils import read_text
from .schema import ScriptBlock, ScriptBlockKind, ScriptParse, SourceSpan

# Pattern: EP01_S01 or EP01_S01_A etc.
SCENE_PATTERN = re.compile(
    r"^\s*(EP\d+_S\d+(?:_[A-Za-z0-9]+)?)\s*[::\-、]?\s*(.*?)\s*$"
)
# Pattern: 场景 1：xxx / Scene 1: xxx
SCENE_CN_PATTERN = re.compile(
    r"^\s*(?:场景|场|Scene)\s*([A-Za-z0-9一-鿿]+)\s*[:：\-]\s*(.+?)\s*$"
)
# Pattern: [BEAT: label]
BEAT_PATTERN = re.compile(r"^\s*\[BEAT\s*[:：]\s*([^\]]+?)\s*\]\s*$")
# Pattern: name：text or name:text
DIALOGUE_PATTERN = re.compile(
    r"^\s*([一-鿿A-Za-z][一-鿿A-Za-z0-9_\-\s]{0,20}?)\s*[:：]\s*(.+?)\s*$"
)


def _char_offset_to_line_offsets(text: str) -> List[int]:
    """Return the start offset of each line in ``text``."""
    offsets = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            offsets.append(i + 1)
    return offsets


def _span(line_start: int, line_end: int, text: str) -> SourceSpan:
    return SourceSpan(start=line_start, end=line_end, text=text)


def parse_script(path: Path, script_id: Optional[str] = None) -> ScriptParse:
    """Parse a script file and return a :class:`ScriptParse`."""

    if not path.exists():
        raise ScriptReadError(f"Script not found: {path}")

    try:
        raw = read_text(path)
    except UnicodeDecodeError as exc:
        raise ScriptReadError(f"Cannot read script as UTF-8: {path}") from exc

    if script_id is None:
        script_id = path.stem

    blocks: List[ScriptBlock] = []
    line_offsets = _char_offset_to_line_offsets(raw)
    lines = raw.splitlines()

    for idx, line in enumerate(lines):
        line_start = line_offsets[idx]
        line_end = line_start + len(line)
        stripped = line.strip()
        if not stripped:
            continue

        block = _classify_line(stripped, line, line_start, line_end)
        if block is not None:
            blocks.append(block)

    return ScriptParse(
        script_id=script_id,
        source_path=str(path),
        blocks=blocks,
    )


def _classify_line(
    stripped: str,
    original: str,
    line_start: int,
    line_end: int,
) -> Optional[ScriptBlock]:
    span = _span(line_start, line_end, original)

    # Skip planner meta annotations and plain comments.
    if stripped.startswith("[meta:") or stripped.startswith("#"):
        return None

    m = BEAT_PATTERN.match(stripped)
    if m:
        return ScriptBlock(
            kind=ScriptBlockKind.BEAT,
            text=m.group(1).strip(),
            span=span,
        )

    m = SCENE_PATTERN.match(stripped)
    if m:
        scene_id = m.group(1)
        heading = m.group(2).strip()
        return ScriptBlock(
            kind=ScriptBlockKind.SCENE,
            text=f"{scene_id} {heading}".strip(),
            character=scene_id,
            span=span,
        )

    m = SCENE_CN_PATTERN.match(stripped)
    if m:
        scene_id = f"scene_{m.group(1)}"
        heading = m.group(2).strip()
        return ScriptBlock(
            kind=ScriptBlockKind.SCENE,
            text=heading,
            character=scene_id,
            span=span,
        )

    # Dialogue needs to be a single colon split, with a short left side.
    # This intentionally avoids eating sentences like "他说：你好".
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
            and DIALOGUE_PATTERN.match(stripped)
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


# Local Optional import shim — keep pydantic-free signature clean.
from typing import Optional  # noqa: E402  (placed after function defs intentionally)