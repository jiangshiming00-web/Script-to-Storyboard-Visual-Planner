"""End-to-end pipeline tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from planner.env import load_config
from planner.pipeline import run as run_pipeline
from planner.validate import validate_run


EXPECTED_ARTIFACTS = {
    "script_parse.json",
    "character_bible.json",
    "location_bible.json",
    "prop_bible.json",
    "story_beats.json",
    "shot_list.json",
    "image_prompts.json",
    "video_prompts.json",
    "asset_manifest.json",
    "executor_tasks.json",
    "run_summary.json",
}


def _run_dev(project_root: Path, sample_script_path: Path, tmp_run_dir: Path):
    cfg = load_config("development", project_root=project_root)
    return run_pipeline(
        script_path=sample_script_path, out_dir=tmp_run_dir, config=cfg
    )


def test_pipeline_writes_all_artifacts(
    project_root: Path, sample_script_path: Path, tmp_run_dir: Path
) -> None:
    _run_dev(project_root, sample_script_path, tmp_run_dir)
    produced = {p.name for p in tmp_run_dir.iterdir()}
    assert EXPECTED_ARTIFACTS.issubset(produced)


def test_script_parse_artifact_records_source_and_blocks(
    project_root: Path, sample_script_path: Path, tmp_run_dir: Path
) -> None:
    """script_parse.json is a first-class artifact (referenced by
    docs/ARCHITECTURE.md and specs/DATA_CONTRACTS.md): it must carry
    the source path, the script_id and at least one parsed block.
    """

    _run_dev(project_root, sample_script_path, tmp_run_dir)
    parse = json.loads((tmp_run_dir / "script_parse.json").read_text("utf-8"))
    assert parse["source_path"] == str(sample_script_path)
    assert parse["script_id"], "script_id must be populated"
    assert isinstance(parse["blocks"], list) and parse["blocks"], (
        "script_parse.json must contain at least one block"
    )
    # Each block must be a valid ScriptBlock (kind + text + span).
    kinds = {b["kind"] for b in parse["blocks"]}
    assert "scene" in kinds or "dialogue" in kinds or "action" in kinds


def test_run_summary_records_planner_provider(
    project_root: Path, sample_script_path: Path, tmp_run_dir: Path
) -> None:
    """run_summary.json must record which provider produced the run so
    downstream tools / audits can trace deterministic vs. LLM runs.
    """

    _run_dev(project_root, sample_script_path, tmp_run_dir)
    summary = json.loads((tmp_run_dir / "run_summary.json").read_text("utf-8"))
    assert summary.get("planner_provider") == "deterministic", (
        f"planner_provider missing or wrong: {summary.get('planner_provider')!r}"
    )
    assert "script_parse.json" in summary["artifacts"].get("script_parse", "") or (
        "script_parse" in summary["artifacts"]
    ), "run_summary.artifacts must include script_parse entry"


def test_sample_run_passes_validation(
    project_root: Path, sample_script_path: Path, tmp_run_dir: Path
) -> None:
    _run_dev(project_root, sample_script_path, tmp_run_dir)
    report = validate_run(tmp_run_dir)
    assert report.ok, f"errors: {report.errors}"


def test_shot_references_resolve_to_bibles(
    project_root: Path, sample_script_path: Path, tmp_run_dir: Path
) -> None:
    _run_dev(project_root, sample_script_path, tmp_run_dir)
    data = json.loads((tmp_run_dir / "shot_list.json").read_text("utf-8"))
    char_ids = {
        c["id"] for c in json.loads((tmp_run_dir / "character_bible.json").read_text("utf-8"))["characters"]
    }
    loc_ids = {
        loc["id"] for loc in json.loads((tmp_run_dir / "location_bible.json").read_text("utf-8"))["locations"]
    }
    prop_ids = {
        p["id"] for p in json.loads((tmp_run_dir / "prop_bible.json").read_text("utf-8"))["props"]
    }
    for shot in data["shots"]:
        assert shot["location_id"] in loc_ids
        for cid in shot["character_ids"]:
            assert cid in char_ids
        for pid in shot["prop_ids"]:
            assert pid in prop_ids


def test_image_prompts_include_character_location_and_cinematography(
    project_root: Path, sample_script_path: Path, tmp_run_dir: Path
) -> None:
    _run_dev(project_root, sample_script_path, tmp_run_dir)
    data = json.loads((tmp_run_dir / "image_prompts.json").read_text("utf-8"))
    char_names = {
        c["name"] for c in json.loads((tmp_run_dir / "character_bible.json").read_text("utf-8"))["characters"]
    }
    loc_names = {
        loc["name"] for loc in json.loads((tmp_run_dir / "location_bible.json").read_text("utf-8"))["locations"]
    }
    shots = json.loads((tmp_run_dir / "shot_list.json").read_text("utf-8"))["shots"]
    shot_by_id = {s["id"]: s for s in shots}
    for entry in data["image_prompts"]:
        prompt = entry["prompt"]
        shot = shot_by_id[entry["shot_id"]]
        if shot["character_ids"]:
            assert any(name in prompt for name in char_names)
        if shot["location_id"]:
            assert any(name in prompt for name in loc_names)
        assert "镜头" in prompt


def test_executor_status_differs_by_env(
    project_root: Path, sample_script_path: Path, tmp_path: Path
) -> None:
    # Let the pipeline create the run dirs itself, so we exercise the
    # production overwrite guard correctly.
    dev_dir = tmp_path / "dev" / "sample_ep01"
    prod_dir = tmp_path / "prod" / "sample_ep01"

    dev_cfg = load_config("development", project_root=project_root)
    prod_cfg = load_config(
        "production",
        project_root=project_root,
        config_path=project_root / "config" / "production.example.json",
    )

    run_pipeline(
        script_path=sample_script_path, out_dir=dev_dir, config=dev_cfg
    )
    run_pipeline(
        script_path=sample_script_path, out_dir=prod_dir, config=prod_cfg
    )

    dev_tasks = json.loads((dev_dir / "executor_tasks.json").read_text("utf-8"))
    prod_tasks = json.loads((prod_dir / "executor_tasks.json").read_text("utf-8"))
    assert {t["status"] for t in dev_tasks["tasks"]} == {"pending"}
    assert {t["status"] for t in prod_tasks["tasks"]} == {"pending_manual_approval"}