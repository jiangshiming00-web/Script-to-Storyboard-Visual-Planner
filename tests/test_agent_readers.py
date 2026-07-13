"""Tests for planner.agent.readers (Phase 3 P1).

Phase 3 P1 contract: readers are graceful — missing or corrupted
JSON returns ``(None, error_message)`` rather than raising. Only
``load_artifact`` raises (because callers explicitly request a
single artifact and need to know precisely why it failed).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from planner.agent.readers import (
    KNOWN_ARTIFACTS,
    MAX_ARTIFACT_BYTES,
    list_artifacts,
    list_runs_in_batch,
    load_artifact,
    load_batch_summary,
    load_run_summary,
)


# ---------- load_run_summary ----------


def test_load_run_summary_missing_returns_none_with_error(
    tmp_path: Path,
) -> None:
    data, err = load_run_summary(tmp_path)
    assert data is None
    assert err is not None
    assert "not found" in err.lower()


def test_load_run_summary_corrupted_returns_none(tmp_path: Path) -> None:
    (tmp_path / "run_summary.json").write_text("{not valid json", encoding="utf-8")
    data, err = load_run_summary(tmp_path)
    assert data is None
    assert err is not None
    assert "invalid json" in err.lower()


def test_load_run_summary_valid_returns_dict(tmp_path: Path) -> None:
    payload = {"run_id": "abc", "env": "development"}
    (tmp_path / "run_summary.json").write_text(json.dumps(payload), encoding="utf-8")
    data, err = load_run_summary(tmp_path)
    assert data == payload
    assert err is None


# ---------- load_artifact ----------


def test_load_artifact_unknown_name_raises_value_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown artifact name"):
        load_artifact(tmp_path, "../../etc/passwd")


def test_load_artifact_missing_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_artifact(tmp_path, "run_summary.json")


def test_load_artifact_size_cap_raises(tmp_path: Path) -> None:
    big = tmp_path / "run_summary.json"
    big.write_text("{}", encoding="utf-8")
    # 2 bytes > 1 byte cap -> ValueError
    with pytest.raises(ValueError, match="exceeds size cap"):
        load_artifact(tmp_path, "run_summary.json", max_bytes=1)


def test_load_artifact_loads_each_known_name(tmp_path: Path) -> None:
    for name in KNOWN_ARTIFACTS:
        (tmp_path / name).write_text(json.dumps({"name": name}), encoding="utf-8")
    for name in KNOWN_ARTIFACTS:
        data = load_artifact(tmp_path, name)
        assert data["name"] == name


def test_max_artifact_bytes_default_is_50mb() -> None:
    # Pin the default; agents / P2 review-run can opt out by passing
    # max_bytes explicitly.
    assert MAX_ARTIFACT_BYTES == 50 * 1024 * 1024


# ---------- list_artifacts ----------


def test_list_artifacts_on_non_existent_dir(tmp_path: Path) -> None:
    nonexistent = tmp_path / "missing"
    result = list_artifacts(nonexistent)
    assert result == {name: False for name in KNOWN_ARTIFACTS}


def test_list_artifacts_partial(tmp_path: Path) -> None:
    (tmp_path / "run_summary.json").write_text("{}", encoding="utf-8")
    (tmp_path / "shot_list.json").write_text("{}", encoding="utf-8")
    result = list_artifacts(tmp_path)
    assert result["run_summary.json"] is True
    assert result["shot_list.json"] is True
    assert result["script_parse.json"] is False
    assert result["image_prompts.json"] is False


# ---------- list_runs_in_batch ----------


def test_list_runs_in_batch_filters_by_run_summary(tmp_path: Path) -> None:
    # Two episodes with run_summary.json
    (tmp_path / "ep01").mkdir()
    (tmp_path / "ep01" / "run_summary.json").write_text("{}", encoding="utf-8")
    (tmp_path / "ep02").mkdir()
    (tmp_path / "ep02" / "run_summary.json").write_text("{}", encoding="utf-8")
    # Half-written episode (no run_summary) must be excluded
    (tmp_path / "ep03_partial").mkdir()
    (tmp_path / "ep03_partial" / "shot_list.json").write_text("{}", encoding="utf-8")
    # A stray file (not a directory) must be ignored
    (tmp_path / "stray.txt").write_text("hello", encoding="utf-8")

    result = list_runs_in_batch(tmp_path)
    names = [p.name for p in result]
    assert names == ["ep01", "ep02"]


def test_list_runs_in_batch_non_existent_dir(tmp_path: Path) -> None:
    assert list_runs_in_batch(tmp_path / "missing") == []


# ---------- load_batch_summary ----------


def test_load_batch_summary_missing(tmp_path: Path) -> None:
    data, err = load_batch_summary(tmp_path)
    assert data is None
    assert err is not None
