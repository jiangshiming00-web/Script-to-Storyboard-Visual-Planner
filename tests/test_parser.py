"""Script parser tests."""

from __future__ import annotations

from pathlib import Path

from planner.parser import parse_script
from planner.schema import ScriptBlockKind


def test_parse_sample_script(sample_script_path: Path) -> None:
    parse = parse_script(sample_script_path)
    assert parse.script_id == "sample_ep01"
    kinds = [b.kind for b in parse.blocks]
    assert ScriptBlockKind.SCENE in kinds
    assert ScriptBlockKind.DIALOGUE in kinds
    assert ScriptBlockKind.ACTION in kinds
    # Meta annotation lines should not appear as narrative blocks.
    for block in parse.blocks:
        assert not block.text.startswith("[meta:")


def test_parse_inline_text() -> None:
    from planner.bible import parse_script_text

    text = (
        "# comment\n"
        "[meta:character x]\n"
        "appearance: xxx\n"
        "\n"
        "EP01_S01 街道\n"
        "林夏：（叹气）你好。\n"
        "[BEAT: 冲突揭示]\n"
    )
    parse = parse_script_text(text, script_id="EP01")
    kinds = [b.kind for b in parse.blocks]
    assert kinds.count(ScriptBlockKind.SCENE) == 1
    assert kinds.count(ScriptBlockKind.DIALOGUE) == 1
    assert kinds.count(ScriptBlockKind.BEAT) == 1


def test_source_span_references_resolve(sample_script_path: Path) -> None:
    parse = parse_script(sample_script_path)
    text = sample_script_path.read_text(encoding="utf-8")
    for block in parse.blocks:
        snippet = text[block.span.start : block.span.end]
        # The snippet may not equal block.text exactly because the
        # classifier strips leading whitespace, but it must be present
        # in the source.
        assert snippet.strip() == block.text or block.text in snippet