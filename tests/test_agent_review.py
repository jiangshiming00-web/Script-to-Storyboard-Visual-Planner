"""Engine-level tests for ``planner.agent.review`` (Phase 3 P2).

Covers the 4 rules (rv1 header-bible bidirectional match / rv2 video
fields / rv3 placeholder / rv4 shot_id alignment), graceful degradation
(missing / corrupted run_summary + artifacts), status derivation,
tool_invocation recording, and the redact exit contract.

Fixtures use minimal valid JSON (review-run reads dicts, not Pydantic
models), so only the fields review-run actually accesses are populated.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from planner.agent.review import ReviewBatchReport, ReviewRunReport, review_batch_dir, review_run_dir

# ---------- minimal valid fixtures (internally consistent) ----------

DEFAULT_CHAR_BIBLE = {
    "characters": [
        {"id": "lin_xia", "name": "林夏", "appearance": "x", "positive_prompt": "x", "negative_prompt": "x"},
        {"id": "zhang_nan", "name": "张楠", "appearance": "x", "positive_prompt": "x", "negative_prompt": "x"},
    ]
}
DEFAULT_LOC_BIBLE = {
    "locations": [
        {"id": "office", "name": "办公室", "space_layout": "x", "positive_prompt": "x", "negative_prompt": "x"},
    ]
}
DEFAULT_PROP_BIBLE = {
    "props": [
        {"id": "folder", "name": "文件夹", "visual": "x", "positive_prompt": "x", "negative_prompt": "x"},
    ]
}
DEFAULT_SHOT_LIST = {
    "shots": [
        {
            "id": "shot-001", "scene_id": "scene-1", "location_id": "office",
            "character_ids": ["lin_xia"], "prop_ids": ["folder"],
            "shot_size": "medium", "camera_angle": "eye", "composition": "x",
            "action": "x", "emotion": "x",
        }
    ]
}
DEFAULT_IMAGE_PROMPTS = {
    "image_prompts": [
        {"shot_id": "shot-001", "prompt": "场景：办公室。人物：林夏。道具：文件夹。medium 镜头，eye，构图：x，情绪：x", "negative_prompt": "neg", "aspect_ratio": "16:9", "style_tags": []},
    ]
}
DEFAULT_VIDEO_PROMPTS = {
    "video_prompts": [
        {"shot_id": "shot-001", "prompt": "林夏翻文件夹", "motion": "push-in", "duration_sec": 4, "camera": "eye", "avoid": "不要换脸"},
    ]
}


def _write_run_summary(run_dir: Path, **kwargs: Any) -> None:
    summary = {"run_id": "test-run", "env": "development", "script": "x", "counts": {"shots": 1}}
    summary.update(kwargs)
    (run_dir / "run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False), encoding="utf-8"
    )


def _write_artifact(run_dir: Path, name: str, data: Any) -> None:
    (run_dir / f"{name}.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


def _make_reviewable_run(
    tmp_path: Path,
    *,
    run_summary: Optional[Dict[str, Any]] = None,
    char_bible: Any = ...,
    loc_bible: Any = ...,
    prop_bible: Any = ...,
    shot_list: Any = ...,
    image_prompts: Any = ...,
    video_prompts: Any = ...,
) -> Path:
    """Build a run dir with the requested artifacts.

    ``...`` (Ellipsis) means "use the DEFAULT fixture"; ``None`` means
    "omit this artifact" (to test graceful degradation).
    """
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_run_summary(run_dir, **(run_summary or {}))
    if char_bible is not ...:
        if char_bible is None:
            pass
        else:
            _write_artifact(run_dir, "character_bible", char_bible)
    else:
        _write_artifact(run_dir, "character_bible", DEFAULT_CHAR_BIBLE)
    if loc_bible is not ...:
        _write_artifact(run_dir, "location_bible", loc_bible if loc_bible else DEFAULT_LOC_BIBLE)
    else:
        _write_artifact(run_dir, "location_bible", DEFAULT_LOC_BIBLE)
    if prop_bible is not ...:
        _write_artifact(run_dir, "prop_bible", prop_bible if prop_bible else DEFAULT_PROP_BIBLE)
    else:
        _write_artifact(run_dir, "prop_bible", DEFAULT_PROP_BIBLE)
    if shot_list is not ...:
        _write_artifact(run_dir, "shot_list", shot_list if shot_list else DEFAULT_SHOT_LIST)
    else:
        _write_artifact(run_dir, "shot_list", DEFAULT_SHOT_LIST)
    if image_prompts is not ...:
        _write_artifact(run_dir, "image_prompts", image_prompts if image_prompts else DEFAULT_IMAGE_PROMPTS)
    else:
        _write_artifact(run_dir, "image_prompts", DEFAULT_IMAGE_PROMPTS)
    if video_prompts is not ...:
        _write_artifact(run_dir, "video_prompts", video_prompts if video_prompts else DEFAULT_VIDEO_PROMPTS)
    else:
        _write_artifact(run_dir, "video_prompts", DEFAULT_VIDEO_PROMPTS)
    return run_dir


def _codes(report: ReviewRunReport) -> list:
    return [f.code for f in report.findings]


# ---------- graceful: run_summary ----------


def test_missing_run_summary_emits_error(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    report = review_run_dir(run_dir)
    assert report.status == "errors"
    assert "missing_run_summary" in _codes(report)
    assert report.implementation_status == "full"


def test_corrupted_run_summary_emits_error(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run_summary.json").write_text("{ not valid json", encoding="utf-8")
    report = review_run_dir(run_dir)
    assert report.status == "errors"
    assert "corrupted_run_summary" in _codes(report)


# ---------- graceful: artifacts ----------


def test_missing_bible_emits_artifact_unreadable_and_skips_rv1(tmp_path: Path) -> None:
    run_dir = _make_reviewable_run(tmp_path, char_bible=None)
    report = review_run_dir(run_dir)
    codes = _codes(report)
    assert "artifact_unreadable" in codes
    # rv1 depends on all bibles; with character_bible missing it is skipped.
    assert "rv1_image_prompt_bible_ref_mismatch" not in codes


def test_corrupted_artifact_emits_artifact_corrupted(tmp_path: Path) -> None:
    run_dir = _make_reviewable_run(tmp_path)
    (run_dir / "image_prompts.json").write_text("not json", encoding="utf-8")
    report = review_run_dir(run_dir)
    assert "artifact_corrupted" in _codes(report)


# ---------- rv1: image prompt header vs bible refs ----------


def test_rv1_exact_match_no_finding(tmp_path: Path) -> None:
    run_dir = _make_reviewable_run(tmp_path)
    report = review_run_dir(run_dir)
    assert "rv1_image_prompt_bible_ref_mismatch" not in _codes(report)


def test_rv1_missing_character(tmp_path: Path) -> None:
    # header omits the character name the shot references
    bad = {"image_prompts": [{"shot_id": "shot-001", "prompt": "场景：办公室。道具：文件夹。body", "negative_prompt": "neg"}]}
    run_dir = _make_reviewable_run(tmp_path, image_prompts=bad)
    report = review_run_dir(run_dir)
    codes = _codes(report)
    assert "rv1_image_prompt_bible_ref_mismatch" in codes
    msg = next(f.message for f in report.findings if f.code == "rv1_image_prompt_bible_ref_mismatch")
    assert "人物" in msg


def test_rv1_missing_location(tmp_path: Path) -> None:
    bad = {"image_prompts": [{"shot_id": "shot-001", "prompt": "人物：林夏。道具：文件夹。body", "negative_prompt": "neg"}]}
    run_dir = _make_reviewable_run(tmp_path, image_prompts=bad)
    report = review_run_dir(run_dir)
    codes = _codes(report)
    assert "rv1_image_prompt_bible_ref_mismatch" in codes
    assert any("场景" in f.message for f in report.findings if f.code == "rv1_image_prompt_bible_ref_mismatch")


def test_rv1_character_name_mismatch_is_phantom(tmp_path: Path) -> None:
    # header 人物段 names 张楠 but shot references 林夏 (lin_xia);
    # rv1 flags both missing (林夏) and phantom (张楠). Under the
    # count-based header consumer (direction 2), a header segment
    # whose name disagrees with the shot's bible ref is the phantom
    # case; extra same-label segments past the expected count are
    # body, not phantom.
    shot = {"shots": [{"id": "shot-001", "scene_id": "scene-1", "location_id": "office",
        "character_ids": ["lin_xia"], "prop_ids": ["folder"],
        "shot_size": "medium", "camera_angle": "eye", "composition": "x",
        "action": "x", "emotion": "x"}]}
    bad = {"image_prompts": [{"shot_id": "shot-001", "prompt": "场景：办公室。人物：张楠。道具：文件夹。body", "negative_prompt": "neg"}]}
    run_dir = _make_reviewable_run(tmp_path, shot_list=shot, image_prompts=bad)
    report = review_run_dir(run_dir)
    codes = _codes(report)
    assert "rv1_image_prompt_bible_ref_mismatch" in codes
    assert any("张楠" in f.message and "未引用" in f.message for f in report.findings)


def test_rv1_no_reference_at_all(tmp_path: Path) -> None:
    shot = {"shots": [{"id": "shot-001", "scene_id": "s", "location_id": "", "character_ids": [], "prop_ids": [], "shot_size": "medium", "camera_angle": "eye", "composition": "x", "action": "x", "emotion": "x"}]}
    bad = {"image_prompts": [{"shot_id": "shot-001", "prompt": "body only no header", "negative_prompt": "neg"}]}
    run_dir = _make_reviewable_run(tmp_path, shot_list=shot, image_prompts=bad)
    report = review_run_dir(run_dir)
    assert "rv1_image_prompt_bible_ref_mismatch" in _codes(report)


def test_rv1_multi_character_prompt(tmp_path: Path) -> None:
    shot = {"shots": [{"id": "shot-001", "scene_id": "s", "location_id": "office", "character_ids": ["lin_xia", "zhang_nan"], "prop_ids": [], "shot_size": "medium", "camera_angle": "eye", "composition": "x", "action": "x", "emotion": "x"}]}
    prompts = {"image_prompts": [{"shot_id": "shot-001", "prompt": "场景：办公室。人物：林夏。人物：张楠。body", "negative_prompt": "neg"}]}
    run_dir = _make_reviewable_run(tmp_path, shot_list=shot, image_prompts=prompts)
    report = review_run_dir(run_dir)
    assert "rv1_image_prompt_bible_ref_mismatch" not in _codes(report)


# ---------- rv2: video prompt fields ----------


def test_rv2_all_fields_present_no_finding(tmp_path: Path) -> None:
    run_dir = _make_reviewable_run(tmp_path)
    report = review_run_dir(run_dir)
    assert "rv2_video_prompt_missing_field" not in _codes(report)


@pytest.mark.parametrize("field", ["motion", "camera", "avoid"])
def test_rv2_missing_field(tmp_path: Path, field: str) -> None:
    vp = {"shot_id": "shot-001", "prompt": "x", "motion": "push-in", "duration_sec": 4, "camera": "eye", "avoid": "不要换脸"}
    vp[field] = ""
    run_dir = _make_reviewable_run(tmp_path, video_prompts={"video_prompts": [vp]})
    report = review_run_dir(run_dir)
    codes = _codes(report)
    assert "rv2_video_prompt_missing_field" in codes
    assert any(field in f.message for f in report.findings if f.code == "rv2_video_prompt_missing_field")


# ---------- rv3: unresolved placeholders ----------


def test_rv3_placeholder_in_image_prompt_is_error(tmp_path: Path) -> None:
    bad = {"image_prompts": [{"shot_id": "shot-001", "prompt": "场景：办公室。人物：林夏。道具：文件夹。{location_name}", "negative_prompt": "neg"}]}
    run_dir = _make_reviewable_run(tmp_path, image_prompts=bad)
    report = review_run_dir(run_dir)
    assert "rv3_unresolved_placeholder" in _codes(report)
    assert report.status == "errors"


def test_rv3_placeholder_in_video_avoid(tmp_path: Path) -> None:
    bad = {"video_prompts": [{"shot_id": "shot-001", "prompt": "x", "motion": "p", "duration_sec": 4, "camera": "c", "avoid": "<TBD>"}]}
    run_dir = _make_reviewable_run(tmp_path, video_prompts=bad)
    report = review_run_dir(run_dir)
    assert "rv3_unresolved_placeholder" in _codes(report)


def test_rv3_no_false_positive_for_legitimate_braces(tmp_path: Path) -> None:
    # prose with braces that are NOT identifier-style placeholders
    prompts = {"image_prompts": [{"shot_id": "shot-001", "prompt": "场景：办公室。人物：林夏。道具：文件夹。情绪 {惊喜} 与 {愤怒}", "negative_prompt": "neg"}]}
    run_dir = _make_reviewable_run(tmp_path, image_prompts=prompts)
    report = review_run_dir(run_dir)
    # {惊喜} / {愤怒} are CJK, do not match [a-zA-Z_] identifier pattern
    assert "rv3_unresolved_placeholder" not in _codes(report)


# ---------- rv4: shot_id alignment ----------


def test_rv4_aligned_no_finding(tmp_path: Path) -> None:
    run_dir = _make_reviewable_run(tmp_path)
    report = review_run_dir(run_dir)
    assert "rv4_shot_id_misaligned" not in _codes(report)


def test_rv4_image_prompts_extra_shot(tmp_path: Path) -> None:
    prompts = {"image_prompts": [
        {"shot_id": "shot-001", "prompt": "场景：办公室。人物：林夏。道具：文件夹。body", "negative_prompt": "neg"},
        {"shot_id": "shot-999", "prompt": "extra", "negative_prompt": "neg"},
    ]}
    run_dir = _make_reviewable_run(tmp_path, image_prompts=prompts)
    report = review_run_dir(run_dir)
    assert "rv4_shot_id_misaligned" in _codes(report)


def test_rv4_video_prompts_missing_shot(tmp_path: Path) -> None:
    run_dir = _make_reviewable_run(tmp_path, video_prompts={"video_prompts": []})
    report = review_run_dir(run_dir)
    assert "rv4_shot_id_misaligned" in _codes(report)


# ---------- status derivation ----------


def test_status_errors_when_rv3_fires(tmp_path: Path) -> None:
    bad = {"image_prompts": [{"shot_id": "shot-001", "prompt": "场景：办公室。人物：林夏。道具：文件夹。{name}", "negative_prompt": "neg"}]}
    run_dir = _make_reviewable_run(tmp_path, image_prompts=bad)
    report = review_run_dir(run_dir)
    assert report.status == "errors"


def test_status_warnings_when_only_rv1(tmp_path: Path) -> None:
    bad = {"image_prompts": [{"shot_id": "shot-001", "prompt": "场景：办公室。道具：文件夹。body", "negative_prompt": "neg"}]}
    run_dir = _make_reviewable_run(tmp_path, image_prompts=bad)
    report = review_run_dir(run_dir)
    assert report.status == "warnings"


def test_status_ok_when_clean(tmp_path: Path) -> None:
    run_dir = _make_reviewable_run(tmp_path)
    report = review_run_dir(run_dir)
    assert report.status == "ok"


# ---------- tool_invocations ----------


def test_tool_invocations_recorded(tmp_path: Path) -> None:
    run_dir = _make_reviewable_run(tmp_path)
    report = review_run_dir(run_dir)
    # read_run_summary + list_artifacts + read_artifact x6 = 8 calls
    tools = [t.tool for t in report.tool_invocations]
    assert tools.count("read_run_summary") == 1
    assert tools.count("list_artifacts") == 1
    assert tools.count("read_artifact") == 6
    assert all(t.ok for t in report.tool_invocations)
    # bytes_read non-zero for present artifacts
    read_artifact_calls = [t for t in report.tool_invocations if t.tool == "read_artifact"]
    assert all(t.bytes_read > 0 for t in read_artifact_calls)


# ---------- redact exit ----------


def test_redact_secret_in_finding_message(tmp_path: Path) -> None:
    # character name embeds a token; rv1 missing-character finding
    # message must redact it.
    secret = "sk-leak-test-redact-12345678"
    char_bible = {"characters": [{"id": "leak", "name": secret, "appearance": "x", "positive_prompt": "x", "negative_prompt": "x"}]}
    shot = {"shots": [{"id": "shot-001", "scene_id": "s", "location_id": "", "character_ids": ["leak"], "prop_ids": [], "shot_size": "medium", "camera_angle": "eye", "composition": "x", "action": "x", "emotion": "x"}]}
    prompts = {"image_prompts": [{"shot_id": "shot-001", "prompt": "body without character name", "negative_prompt": "neg"}]}
    run_dir = _make_reviewable_run(tmp_path, char_bible=char_bible, loc_bible={"locations": []}, prop_bible={"props": []}, shot_list=shot, image_prompts=prompts)
    report = review_run_dir(run_dir)
    blob = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
    assert secret not in blob
    assert "<redacted>" in blob


# ---------- expected_env mismatch ----------


def test_expected_env_mismatch_warning(tmp_path: Path) -> None:
    run_dir = _make_reviewable_run(tmp_path, run_summary={"env": "development"})
    report = review_run_dir(run_dir, expected_env="production")
    assert "env_mismatch" in _codes(report)
    assert report.status == "warnings"


# ---------- P1 regression: legitimate JSON, wrong top-level shape ----------
# Codex Phase 3 P2 review (P1): a non-dict top level (list / str / int)
# must NOT raise AttributeError through the CLI. The engine emits an
# artifact_corrupted / corrupted_run_summary finding and skips
# dependent rules instead.


def test_artifact_top_level_list_emits_corrupted_no_crash(tmp_path: Path) -> None:
    # image_prompts.json is valid JSON but a bare list, not a dict.
    run_dir = _make_reviewable_run(tmp_path)
    (run_dir / "image_prompts.json").write_text("[1, 2, 3]", encoding="utf-8")
    report = review_run_dir(run_dir)
    codes = _codes(report)
    assert "artifact_corrupted" in codes
    # rv1 / rv3 depend on image_prompts being a usable dict; skipped.
    assert "rv1_image_prompt_bible_ref_mismatch" not in codes
    assert "rv3_unresolved_placeholder" not in codes


def test_artifact_top_level_string_emits_corrupted(tmp_path: Path) -> None:
    run_dir = _make_reviewable_run(tmp_path, char_bible="not a dict")
    report = review_run_dir(run_dir)
    assert "artifact_corrupted" in _codes(report)


def test_run_summary_top_level_list_emits_corrupted_no_crash(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run_summary.json").write_text("[1, 2, 3]", encoding="utf-8")
    report = review_run_dir(run_dir)
    assert report.status == "errors"
    assert "corrupted_run_summary" in _codes(report)
    # No AttributeError leaked; run_id / env stay None.
    assert report.run_id is None
    assert report.env is None


# ---------- P2 regression: body labels not mistaken for header ----------
# Codex Phase 3 P2 review (P2): the header parser used to scan the
# whole prompt, so body prose containing 场景：/人物：/道具： was
# mistaken for header refs and emitted phantom findings. After the
# fix, only the leading header run is parsed.


def test_rv1_body_character_label_not_phantom(tmp_path: Path) -> None:
    prompts = {"image_prompts": [{"shot_id": "shot-001",
        "prompt": "场景：办公室。人物：林夏。道具：文件夹。镜头中的人物：路人甲，背景喧嚣",
        "negative_prompt": "neg"}]}
    run_dir = _make_reviewable_run(tmp_path, image_prompts=prompts)
    report = review_run_dir(run_dir)
    assert "rv1_image_prompt_bible_ref_mismatch" not in _codes(report)


def test_rv1_body_scene_label_not_phantom(tmp_path: Path) -> None:
    prompts = {"image_prompts": [{"shot_id": "shot-001",
        "prompt": "场景：办公室。人物：林夏。道具：文件夹。窗外场景：夜空，月光洒落",
        "negative_prompt": "neg"}]}
    run_dir = _make_reviewable_run(tmp_path, image_prompts=prompts)
    report = review_run_dir(run_dir)
    assert "rv1_image_prompt_bible_ref_mismatch" not in _codes(report)


def test_rv1_body_prop_label_not_phantom(tmp_path: Path) -> None:
    prompts = {"image_prompts": [{"shot_id": "shot-001",
        "prompt": "场景：办公室。人物：林夏。道具：文件夹。桌上道具：怀表一只",
        "negative_prompt": "neg"}]}
    run_dir = _make_reviewable_run(tmp_path, image_prompts=prompts)
    report = review_run_dir(run_dir)
    assert "rv1_image_prompt_bible_ref_mismatch" not in _codes(report)


def test_rv1_codex_repro_body_character_after_full_header(tmp_path: Path) -> None:
    # Codex Phase 3 P2 round-2 原始复现: 完整 header (场景+人物+道具) 后,
    # body 第一段以纯 "人物：" 开头 ("人物：背景群众只是画面描述，不是
    # header 绑定"). The count-based consumer drinks exactly 1 人物
    # segment (expected), so the 4th segment is body -> no phantom.
    prompts = {"image_prompts": [{"shot_id": "shot-001",
        "prompt": "场景：办公室。人物：林夏。道具：文件夹。人物：背景群众只是画面描述，不是 header 绑定",
        "negative_prompt": "neg"}]}
    run_dir = _make_reviewable_run(tmp_path, image_prompts=prompts)
    report = review_run_dir(run_dir)
    assert "rv1_image_prompt_bible_ref_mismatch" not in _codes(report)


def test_rv1_codex_repro_body_same_label_prop(tmp_path: Path) -> None:
    # Same-label variant: body starts with "道具：" after a full
    # header. n_prop=1 drinks only the first 道具 segment; the second
    # is body -> no phantom.
    prompts = {"image_prompts": [{"shot_id": "shot-001",
        "prompt": "场景：办公室。人物：林夏。道具：文件夹。道具：怀表只是背景点缀，不是 shot 绑定",
        "negative_prompt": "neg"}]}
    run_dir = _make_reviewable_run(tmp_path, image_prompts=prompts)
    report = review_run_dir(run_dir)
    assert "rv1_image_prompt_bible_ref_mismatch" not in _codes(report)


def test_rv1_extra_prop_phantom_when_shot_has_no_prop(tmp_path: Path) -> None:
    # Codex round-3 反例 1: shot has no prop_ids but header writes
    # 道具：文件夹 (a known bible prop). rv1 must flag the phantom.
    # Direction E: extra segment name hits prop_names_all -> phantom.
    shot = {"shots": [{"id": "shot-001", "scene_id": "scene-1", "location_id": "office",
        "character_ids": ["lin_xia"], "prop_ids": [],
        "shot_size": "medium", "camera_angle": "eye", "composition": "x",
        "action": "x", "emotion": "x"}]}
    prompts = {"image_prompts": [{"shot_id": "shot-001",
        "prompt": "场景：办公室。人物：林夏。道具：文件夹。body",
        "negative_prompt": "neg"}]}
    run_dir = _make_reviewable_run(tmp_path, shot_list=shot, image_prompts=prompts)
    report = review_run_dir(run_dir)
    codes = _codes(report)
    assert "rv1_image_prompt_bible_ref_mismatch" in codes
    assert any("文件夹" in f.message and "未引用" in f.message for f in report.findings)


def test_rv1_extra_character_phantom_when_shot_has_no_char(tmp_path: Path) -> None:
    # Codex round-3 反例 2: shot has no character_ids but header writes
    # 人物：张楠 (a known bible char). rv1 must flag the phantom.
    shot = {"shots": [{"id": "shot-001", "scene_id": "scene-1", "location_id": "office",
        "character_ids": [], "prop_ids": ["folder"],
        "shot_size": "medium", "camera_angle": "eye", "composition": "x",
        "action": "x", "emotion": "x"}]}
    prompts = {"image_prompts": [{"shot_id": "shot-001",
        "prompt": "场景：办公室。人物：张楠。道具：文件夹。body",
        "negative_prompt": "neg"}]}
    run_dir = _make_reviewable_run(tmp_path, shot_list=shot, image_prompts=prompts)
    report = review_run_dir(run_dir)
    codes = _codes(report)
    assert "rv1_image_prompt_bible_ref_mismatch" in codes
    assert any("张楠" in f.message and "未引用" in f.message for f in report.findings)


# ---------- Phase 3 P2: review-batch (cross-episode consistency) ----------


def _full_episode(
    char: Any = None,
    loc: Any = None,
    prop: Any = None,
    shots: Any = None,
) -> Dict[str, Any]:
    """Build a fully-reviewable episode payload (4 artifacts)."""
    return {
        "character_bible": char if char is not None else {"characters": [{"id": "lin_xia", "name": "林夏"}]},
        "location_bible": loc if loc is not None else {"locations": [{"id": "office", "name": "办公室"}]},
        "prop_bible": prop if prop is not None else {"props": [{"id": "folder", "name": "文件夹"}]},
        "shot_list": shots if shots is not None else {"shots": [
            {"id": "s1", "scene_id": "sc1", "location_id": "office",
             "character_ids": ["lin_xia"], "prop_ids": ["folder"],
             "shot_size": "medium", "camera_angle": "eye", "composition": "x",
             "action": "x", "emotion": "x"}
        ]},
    }


def _make_batch_dir(
    tmp_path: Path,
    *,
    episodes: Dict[str, Dict[str, Any]],
    batch_summary: Optional[Dict[str, Any]] = None,
) -> Path:
    """Build a batch dir with batch_summary.json + per-episode subdirs.

    Each episode value is a dict of artifact name -> payload (None to
    omit that artifact). A ``run_summary.json`` is always written so
    ``list_runs_in_batch`` picks the episode up.
    """
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    summary = batch_summary or {
        "batch_id": "test-batch",
        "env": "development",
        "episodes": [
            {"run_id": ep, "episode_id": ep, "run_dir": str(batch_dir / ep), "status": "done"}
            for ep in episodes
        ],
        "totals": {"episodes": len(episodes), "done": len(episodes)},
    }
    (batch_dir / "batch_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False), encoding="utf-8"
    )
    for ep_id, arts in episodes.items():
        ep_dir = batch_dir / ep_id
        ep_dir.mkdir()
        rs = arts.get("run_summary") or {"run_id": ep_id, "env": "development", "script": "x", "counts": {"shots": 1}}
        (ep_dir / "run_summary.json").write_text(json.dumps(rs, ensure_ascii=False), encoding="utf-8")
        for name in ("character_bible", "location_bible", "prop_bible", "shot_list"):
            if name in arts and arts[name] is not None:
                (ep_dir / f"{name}.json").write_text(json.dumps(arts[name], ensure_ascii=False), encoding="utf-8")
    return batch_dir


def _batch_codes(report: ReviewBatchReport) -> list:
    return [f.code for f in report.findings]


# ----- rb1 character id consistency -----
def test_rb1_character_name_drift_across_episodes(tmp_path: Path) -> None:
    ep1 = _full_episode(char={"characters": [{"id": "lin_xia", "name": "林夏"}]})
    ep2 = _full_episode(char={"characters": [{"id": "lin_xia", "name": "林夏2"}]})
    batch = _make_batch_dir(tmp_path, episodes={"EP01": ep1, "EP02": ep2})
    report = review_batch_dir(batch)
    assert "rb1_character_id_inconsistent_across_episodes" in _batch_codes(report)
    assert report.status == "warnings"
    finding = next(f for f in report.findings if f.code == "rb1_character_id_inconsistent_across_episodes")
    # evidence cites each episode's character_bible (per-episode EvidenceRefs)
    assert len(finding.evidence) == 2


def test_rb1_consistent_name_no_finding(tmp_path: Path) -> None:
    ep1 = _full_episode()
    ep2 = _full_episode()
    batch = _make_batch_dir(tmp_path, episodes={"EP01": ep1, "EP02": ep2})
    report = review_batch_dir(batch)
    assert not any(c.startswith("rb1_") for c in _batch_codes(report))


# ----- rb2 location id consistency -----
def test_rb2_location_name_drift_across_episodes(tmp_path: Path) -> None:
    ep1 = _full_episode(loc={"locations": [{"id": "office", "name": "办公室"}]})
    ep2 = _full_episode(loc={"locations": [{"id": "office", "name": "公司"}]})
    batch = _make_batch_dir(tmp_path, episodes={"EP01": ep1, "EP02": ep2})
    report = review_batch_dir(batch)
    assert "rb2_location_id_inconsistent_across_episodes" in _batch_codes(report)


# ----- rb3 prop id consistency -----
def test_rb3_prop_name_drift_across_episodes(tmp_path: Path) -> None:
    ep1 = _full_episode(prop={"props": [{"id": "folder", "name": "文件夹"}]})
    ep2 = _full_episode(prop={"props": [{"id": "folder", "name": "档案夹"}]})
    batch = _make_batch_dir(tmp_path, episodes={"EP01": ep1, "EP02": ep2})
    report = review_batch_dir(batch)
    assert "rb3_prop_id_inconsistent_across_episodes" in _batch_codes(report)


# ----- rb4 orphan shot reference -----
def test_rb4_orphan_character_ref(tmp_path: Path) -> None:
    ep1 = _full_episode(shots={"shots": [
        {"id": "s1", "location_id": "office", "character_ids": ["lin_xia", "ghost"], "prop_ids": []}
    ]})
    batch = _make_batch_dir(tmp_path, episodes={"EP01": ep1})
    report = review_batch_dir(batch)
    assert "rb4_orphan_shot_reference" in _batch_codes(report)
    assert any("ghost" in f.message for f in report.findings)


def test_rb4_orphan_location_ref(tmp_path: Path) -> None:
    ep1 = _full_episode(shots={"shots": [
        {"id": "s1", "location_id": "nowhere", "character_ids": [], "prop_ids": []}
    ]})
    batch = _make_batch_dir(tmp_path, episodes={"EP01": ep1})
    report = review_batch_dir(batch)
    assert "rb4_orphan_shot_reference" in _batch_codes(report)
    assert any("nowhere" in f.message for f in report.findings)


def test_rb4_no_orphan_when_clean(tmp_path: Path) -> None:
    ep1 = _full_episode()
    batch = _make_batch_dir(tmp_path, episodes={"EP01": ep1})
    report = review_batch_dir(batch)
    assert "rb4_orphan_shot_reference" not in _batch_codes(report)


# ----- single episode: rb1-rb3 skipped, rb4 still runs -----
def test_single_episode_skips_cross_episode_rules(tmp_path: Path) -> None:
    ep1 = _full_episode()
    batch = _make_batch_dir(tmp_path, episodes={"EP01": ep1})
    report = review_batch_dir(batch)
    codes = _batch_codes(report)
    assert not any(c.startswith(("rb1_", "rb2_", "rb3_")) for c in codes)


# ----- graceful: batch_summary -----
def test_missing_batch_summary_emits_error(tmp_path: Path) -> None:
    batch = tmp_path / "batch"
    batch.mkdir()
    report = review_batch_dir(batch)
    assert "missing_batch_summary" in _batch_codes(report)
    assert report.status == "errors"
    assert report.implementation_status == "full"


def test_corrupted_batch_summary_emits_error(tmp_path: Path) -> None:
    batch = tmp_path / "batch"
    batch.mkdir()
    (batch / "batch_summary.json").write_text("{bad json", encoding="utf-8")
    report = review_batch_dir(batch)
    assert "corrupted_batch_summary" in _batch_codes(report)
    assert report.status == "errors"


def test_nondict_batch_summary_no_traceback(tmp_path: Path) -> None:
    """P1 guard mirror: a non-dict top level must not leak AttributeError."""
    batch = tmp_path / "batch"
    batch.mkdir()
    (batch / "batch_summary.json").write_text("[1, 2, 3]", encoding="utf-8")
    report = review_batch_dir(batch)
    assert "corrupted_batch_summary" in _batch_codes(report)
    assert report.status == "errors"


def test_empty_batch_no_reviewable_episodes(tmp_path: Path) -> None:
    batch = _make_batch_dir(tmp_path, episodes={})
    report = review_batch_dir(batch)
    assert "batch_no_reviewable_episodes" in _batch_codes(report)
    assert report.counts["episodes"] == 0


# ----- graceful: per-episode artifact missing -----
def test_missing_episode_artifact_skips_cross_episode_rules(tmp_path: Path) -> None:
    # EP01 missing character_bible -> not fully reviewable; EP02 alone <2
    ep1 = _full_episode()
    ep1["character_bible"] = None
    ep2 = _full_episode()
    batch = _make_batch_dir(tmp_path, episodes={"EP01": ep1, "EP02": ep2})
    report = review_batch_dir(batch)
    codes = _batch_codes(report)
    assert not any(c.startswith("rb1_") for c in codes)
    assert "artifact_unreadable" in codes


# ----- env_mismatch -----
def test_batch_env_mismatch_warning(tmp_path: Path) -> None:
    ep1 = _full_episode()
    ep2 = _full_episode()
    batch = _make_batch_dir(tmp_path, episodes={"EP01": ep1, "EP02": ep2})
    report = review_batch_dir(batch, expected_env="production")
    assert "env_mismatch" in _batch_codes(report)


# ----- status + counts + tool_invocations -----
def test_batch_status_ok_when_clean(tmp_path: Path) -> None:
    ep1 = _full_episode()
    ep2 = _full_episode()
    batch = _make_batch_dir(tmp_path, episodes={"EP01": ep1, "EP02": ep2})
    report = review_batch_dir(batch)
    assert report.status == "ok"
    assert report.counts["episodes"] == 2
    assert report.counts["episodes_reviewed"] == 2


def test_batch_tool_invocations_recorded(tmp_path: Path) -> None:
    # Codex P2 fix: review-batch no longer calls list_runs_in_batch —
    # batch_summary.episodes[] is the authoritative membership source.
    # read_batch_summary + 2 episodes x 4 read_artifact = 1 + 8 = 9
    ep1 = _full_episode()
    ep2 = _full_episode()
    batch = _make_batch_dir(tmp_path, episodes={"EP01": ep1, "EP02": ep2})
    report = review_batch_dir(batch)
    assert len(report.tool_invocations) == 9
    tools = [t.tool for t in report.tool_invocations]
    assert tools[0] == "read_batch_summary"
    assert tools.count("read_artifact") == 8


# ----- redact -----
def test_batch_redact_secret_in_finding_message(tmp_path: Path) -> None:
    secret = "sk-leak-test-redact-12345678"
    ep1 = _full_episode(char={"characters": [{"id": "lin_xia", "name": secret}]})
    ep2 = _full_episode(char={"characters": [{"id": "lin_xia", "name": "林夏"}]})
    batch = _make_batch_dir(tmp_path, episodes={"EP01": ep1, "EP02": ep2})
    report = review_batch_dir(batch)
    blob = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
    assert secret not in blob
    assert "<redacted>" in blob


# ----- P2-1 regression: rb4 must not false-positive when a bible is missing -----
def test_rb4_no_false_positive_when_bible_missing(tmp_path: Path) -> None:
    """P2-1 regression: a missing bible must not cause rb4 to flag
    every ref of that class as orphan. The missing bible is already
    reported by _read_artifact_safe as artifact_unreadable."""
    ep1 = _full_episode()
    ep1["location_bible"] = None  # missing -> artifact_unreadable
    batch = _make_batch_dir(tmp_path, episodes={"EP01": ep1})
    report = review_batch_dir(batch)
    codes = _batch_codes(report)
    assert "artifact_unreadable" in codes
    # rb4 must NOT false-positive location_id='office' as orphan
    rb4_findings = [f for f in report.findings if f.code == "rb4_orphan_shot_reference"]
    assert not any("office" in f.message for f in rb4_findings)


# ----- P2-2 regression: secret in id -> evidence locator redacted -----
def test_rb4_redact_secret_in_id_evidence_locator(tmp_path: Path) -> None:
    """P2-2 regression: a secret embedded in a character id that flows
    into an EvidenceRef locator must be redacted (defense in depth)."""
    secret_id = "sk-leak-id-test-12345678"
    ep1 = _full_episode(char={"characters": [{"id": secret_id, "name": "林夏"}]})
    ep2 = _full_episode(char={"characters": [{"id": secret_id, "name": "林夏2"}]})
    batch = _make_batch_dir(tmp_path, episodes={"EP01": ep1, "EP02": ep2})
    report = review_batch_dir(batch)
    blob = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
    assert secret_id not in blob
    assert "<redacted>" in blob


# ----- Codex Phase 3 P2 review-batch round-2 fixes -----
# P2 (核心): review-batch 不再扫描 batch_dir 下所有含 run_summary.json
# 的子目录；以 batch_summary.json::episodes[] 为权威清单。
# P3 (顺手补): counts 区分 episodes_total / episodes_reviewed / episodes_skipped；
# failed / missing run_dir / malformed meta 给 batch_episode_not_reviewable warning
# 且 counts 不谎报；stale 子目录不参与 cross-episode / orphan 规则。


def test_review_batch_ignores_stale_subdir_not_in_batch_summary(tmp_path: Path) -> None:
    """Codex P2 修复回归：磁盘上残留的 OLD_EP99 子目录（含 run_summary.json
    + 与 EP01 同名的角色但 name 不同）必须**不**进入 cross-episode 检查。
    review 报告里 counts.episodes_total 必须等于 batch_summary.episodes 的
    长度（1），episodes_reviewed 等于 1；OLD_EP99 的 char id 漂移不应被报。"""
    ep1 = _full_episode(char={"characters": [{"id": "lin_xia", "name": "林夏"}]})
    # stale 旧集：含 run_summary.json + char name 故意不同；老逻辑会误报 rb1 漂移。
    old_ep = _full_episode(char={"characters": [{"id": "lin_xia", "name": "旧名_stale"}]})
    episodes = {"EP01": ep1, "OLD_EP99": old_ep}
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    # 显式 batch_summary：只声明 EP01 done，OLD_EP99 不在 list 内
    summary = {
        "batch_id": "stale-test",
        "env": "development",
        "episodes": [
            {"run_id": "EP01", "episode_id": "EP01", "run_dir": str(batch_dir / "EP01"), "status": "done"},
        ],
        "totals": {"episodes": 1, "done": 1},
    }
    for ep_id, arts in episodes.items():
        ep_dir = batch_dir / ep_id
        ep_dir.mkdir()
        rs = arts.get("run_summary") or {"run_id": ep_id, "env": "development", "script": "x", "counts": {"shots": 1}}
        (ep_dir / "run_summary.json").write_text(json.dumps(rs, ensure_ascii=False), encoding="utf-8")
        for name in ("character_bible", "location_bible", "prop_bible", "shot_list"):
            if name in arts and arts[name] is not None:
                (ep_dir / f"{name}.json").write_text(json.dumps(arts[name], ensure_ascii=False), encoding="utf-8")
    (batch_dir / "batch_summary.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")

    report = review_batch_dir(batch_dir)
    codes = _batch_codes(report)
    assert report.counts["episodes_total"] == 1
    assert report.counts["episodes"] == 1  # back-compat alias
    assert report.counts["episodes_reviewed"] == 1
    assert report.counts["episodes_skipped"] == 0
    # OLD_EP99 不应触发任何 finding
    assert "rb1_character_id_inconsistent_across_episodes" not in codes
    assert "rb1" not in " ".join(codes) or "rb1_character_id_inconsistent_across_episodes" not in codes
    # 确认 summary 里没出现 OLD_EP99 的 name
    blob = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
    assert "旧名_stale" not in blob
    assert report.status == "ok"


def test_review_batch_warns_and_skips_when_episode_status_failed(tmp_path: Path) -> None:
    """Codex P2 修复回归：batch_summary.episodes 含 1 done + 1 failed；
    failed 集必须**不**被纳入 review，且必须 emit batch_episode_not_reviewable
    warning，counts 真实反映 total=2 / reviewed=1 / skipped=1，不谎报。"""
    ep1 = _full_episode(char={"characters": [{"id": "lin_xia", "name": "林夏"}]})
    # EP02 failed，但其 run_dir 仍在盘上（且含完整 artifacts）
    ep2 = _full_episode(char={"characters": [{"id": "lin_xia", "name": "林夏2"}]})
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    summary = {
        "batch_id": "mixed-status-test",
        "env": "development",
        "episodes": [
            {"run_id": "EP01", "episode_id": "EP01", "run_dir": str(batch_dir / "EP01"), "status": "done"},
            {"run_id": "EP02", "episode_id": "EP02", "run_dir": str(batch_dir / "EP02"), "status": "failed"},
        ],
        "totals": {"episodes": 2, "done": 1, "failed": 1},
    }
    for ep_id, arts in [("EP01", ep1), ("EP02", ep2)]:
        ep_dir = batch_dir / ep_id
        ep_dir.mkdir()
        rs = arts.get("run_summary") or {"run_id": ep_id, "env": "development", "script": "x", "counts": {"shots": 1}}
        (ep_dir / "run_summary.json").write_text(json.dumps(rs, ensure_ascii=False), encoding="utf-8")
        for name in ("character_bible", "location_bible", "prop_bible", "shot_list"):
            if name in arts and arts[name] is not None:
                (ep_dir / f"{name}.json").write_text(json.dumps(arts[name], ensure_ascii=False), encoding="utf-8")
    (batch_dir / "batch_summary.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")

    report = review_batch_dir(batch_dir)
    codes = _batch_codes(report)
    # counts 必须真实：total=2, reviewed=1, skipped=1（不谎报 reviewed=2）
    assert report.counts["episodes_total"] == 2
    assert report.counts["episodes_reviewed"] == 1
    assert report.counts["episodes_skipped"] == 1
    # batch_episode_not_reviewable warning 必出，message 含 EP02 + status=failed
    assert "batch_episode_not_reviewable" in codes
    skip_msgs = [f.message for f in report.findings if f.code == "batch_episode_not_reviewable"]
    assert any("EP02" in m and "failed" in m for m in skip_msgs)
    # EP02 的林夏2 vs EP01 的林夏 不应触发 rb1（因为 EP02 没进 reviewable）
    assert "rb1_character_id_inconsistent_across_episodes" not in codes
    # summary 提示：报告 review 1 集、跳过 1 集，不说"共 2 集 review 通过"
    assert "跳过 1 集" in report.summary or "跳过 1" in report.summary


def test_review_batch_warns_and_skips_when_run_dir_missing(tmp_path: Path) -> None:
    """Codex P2 修复回归：batch_summary.episodes 含一个 run_dir 不存在的
    done episode；review 必须跳过该集 + emit batch_episode_not_reviewable
    warning（message 含 episode_id 和缺失的 path），counts 真实反映。"""
    ep1 = _full_episode()
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    summary = {
        "batch_id": "missing-rundir-test",
        "env": "development",
        "episodes": [
            {"run_id": "EP01", "episode_id": "EP01", "run_dir": str(batch_dir / "EP01"), "status": "done"},
            {"run_id": "EP02", "episode_id": "EP02", "run_dir": str(batch_dir / "EP02_GONE"), "status": "done"},
        ],
        "totals": {"episodes": 2, "done": 2},
    }
    ep_dir = batch_dir / "EP01"
    ep_dir.mkdir()
    rs = {"run_id": "EP01", "env": "development", "script": "x", "counts": {"shots": 1}}
    (ep_dir / "run_summary.json").write_text(json.dumps(rs, ensure_ascii=False), encoding="utf-8")
    for name in ("character_bible", "location_bible", "prop_bible", "shot_list"):
        (ep_dir / f"{name}.json").write_text(json.dumps(DEFAULT_CHAR_BIBLE if name == "character_bible" else DEFAULT_LOC_BIBLE if name == "location_bible" else DEFAULT_PROP_BIBLE if name == "prop_bible" else DEFAULT_SHOT_LIST, ensure_ascii=False), encoding="utf-8")
    # 故意不创建 EP02_GONE 目录
    (batch_dir / "batch_summary.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")

    report = review_batch_dir(batch_dir)
    codes = _batch_codes(report)
    assert report.counts["episodes_total"] == 2
    assert report.counts["episodes_reviewed"] == 1
    assert report.counts["episodes_skipped"] == 1
    assert "batch_episode_not_reviewable" in codes
    skip_msgs = [f.message for f in report.findings if f.code == "batch_episode_not_reviewable"]
    assert any("EP02" in m and ("不存在" in m or "不是目录" in m) for m in skip_msgs)
