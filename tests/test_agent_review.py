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

from planner.agent.review import ReviewRunReport, review_run_dir

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


def test_rv1_phantom_character(tmp_path: Path) -> None:
    # header names 张楠 but shot does not reference zhang_nan
    bad = {"image_prompts": [{"shot_id": "shot-001", "prompt": "场景：办公室。人物：林夏。人物：张楠。道具：文件夹。body", "negative_prompt": "neg"}]}
    run_dir = _make_reviewable_run(tmp_path, image_prompts=bad)
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
