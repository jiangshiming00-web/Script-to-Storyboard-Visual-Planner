"""Schema validation failure tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from planner.exceptions import BrokenReferenceError
from planner.pipeline import run as run_pipeline
from planner.env import load_config
from planner.validate import validate_run


def _corrupt_reference(tmp_run_dir: Path) -> None:
    path = tmp_run_dir / "shot_list.json"
    data = json.loads(path.read_text("utf-8"))
    data["shots"][0]["character_ids"] = ["ghost_character"]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def test_validate_reports_broken_reference(
    project_root: Path, sample_script_path: Path, tmp_run_dir: Path
) -> None:
    cfg = load_config("development", project_root=project_root)
    run_pipeline(
        script_path=sample_script_path, out_dir=tmp_run_dir, config=cfg
    )
    _corrupt_reference(tmp_run_dir)
    report = validate_run(tmp_run_dir)
    assert not report.ok
    assert any("ghost_character" in err for err in report.errors)


def test_source_span_end_after_start() -> None:
    from planner.schema import SourceSpan

    with pytest.raises(ValueError, match="end must be"):
        SourceSpan(start=10, end=5, text="...")


def test_shot_duration_bounds() -> None:
    from planner.schema import Shot, ShotSize

    with pytest.raises(ValueError):
        Shot(
            id="X",
            scene_id="S",
            location_id="L",
            shot_size=ShotSize.WIDE,
            camera_angle="eye",
            composition="x",
            action="x",
            emotion="x",
            duration_sec=0,
        )