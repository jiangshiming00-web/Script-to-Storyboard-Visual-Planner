"""Tests for planner.agent.tools (Phase 3 P1).

Phase 3 P1 contract:

* ``TOOL_REGISTRY`` exposes exactly 6 read-only tools.
* ``TOOL_ARTIFACT_MAP`` mirrors
  ``harness/agent_scenarios/run_all.py:_TOOL_ARTIFACT_MAP`` —
  the keys must match.
* All tools are pure-Python (no subprocess, no LLM, no writes).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Set

import pytest

from planner.agent.tools import (
    KNOWN_ARTIFACTS,
    TOOL_ARTIFACT_MAP,
    TOOL_REGISTRY,
    list_artifacts,
    list_runs_in_batch,
    read_artifact,
    read_batch_summary,
    read_run_summary,
    validate_run_tool,
)


EXPECTED_TOOL_KEYS: Set[str] = {
    "read_run_summary",
    "list_artifacts",
    "read_artifact",
    "validate_run",
    "read_batch_summary",
    "list_runs_in_batch",
}


def test_tool_registry_has_six_keys() -> None:
    assert set(TOOL_REGISTRY.keys()) == EXPECTED_TOOL_KEYS
    assert len(TOOL_REGISTRY) == 6


def test_tool_artifact_map_has_same_keys_as_registry() -> None:
    # Critical invariant: harness scenarios rely on this alignment.
    assert set(TOOL_ARTIFACT_MAP.keys()) == set(TOOL_REGISTRY.keys())


def test_known_artifacts_count_is_eleven() -> None:
    # 11 emitted JSON artifacts; matches pipeline.run + harness /agent_scenarios.
    assert len(KNOWN_ARTIFACTS) == 11
    assert "run_summary.json" in KNOWN_ARTIFACTS
    assert "executor_tasks.json" in KNOWN_ARTIFACTS


def test_read_run_summary_raises_keyerror_when_missing(tmp_path: Path) -> None:
    with pytest.raises(KeyError, match="run_summary.json"):
        read_run_summary(tmp_path)


def test_read_run_summary_returns_dict_when_present(tmp_path: Path) -> None:
    payload = {"run_id": "abc", "env": "development"}
    (tmp_path / "run_summary.json").write_text(json.dumps(payload), encoding="utf-8")
    assert read_run_summary(tmp_path) == payload


def test_read_batch_summary_raises_keyerror_when_missing(tmp_path: Path) -> None:
    with pytest.raises(KeyError, match="batch_summary.json"):
        read_batch_summary(tmp_path)


def test_list_artifacts_returns_only_existing_names(tmp_path: Path) -> None:
    (tmp_path / "run_summary.json").write_text("{}", encoding="utf-8")
    assert list_artifacts(tmp_path) == ["run_summary.json"]


def test_validate_run_tool_delegates_to_validate_run(
    tmp_path: Path, monkeypatch
) -> None:
    """Verify validate_run_tool actually delegates to validate.validate_run.

    We mock the underlying validate_run so we don't need a full
    pipeline-run fixture here. The integration test is in
    test_agent_diagnose.py.
    """
    from planner import validate as validate_mod

    called = {"with": None}

    def fake_validate_run(run_dir, *, expected_env=None):
        called["with"] = (run_dir, expected_env)
        # Return a minimal ValidationReport-like object with .ok = True
        from planner.validate import ValidationReport

        return ValidationReport(ok=True)

    monkeypatch.setattr(validate_mod, "validate_run", fake_validate_run)
    result = validate_run_tool(tmp_path, expected_env="development")
    assert result.ok is True
    assert called["with"] == (tmp_path, "development")
