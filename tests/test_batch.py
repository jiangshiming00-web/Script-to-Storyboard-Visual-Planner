"""Tests for ``planner.batch`` and the ``planner batch`` CLI command.

The batch driver is a thin orchestrator over ``planner.pipeline.run``
and ``planner.validate.validate_run``. Tests here focus on:

- Happy path: multiple scripts → per-episode subdirs + 11 JSON each + ``batch_summary.json``
- Episode-id parsing (filename regex)
- ``--fail-fast`` (default) vs ``--no-fail-fast``
- Production refuses to write inside the repo (red line #3)
- Missing scripts dir / no .txt files → ``EnvironmentBoundaryError``
- ``batch_summary.json`` carries audit fields + per-episode validation status
- CLI subprocess integration (``planner batch --help`` and a real run)
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from planner.batch import (
    BatchOptions,
    derive_episode_id,
    discover_scripts,
    run_batch,
)
from planner.exceptions import EnvironmentBoundaryError
from planner.schema import BatchSummary


def _write_script(path: Path, *, episode: str = "EP01") -> None:
    path.write_text(
        f"{episode} — Test\n\n"
        "场 1 内 咖啡馆 日\n"
        "林夏走进咖啡馆，点了一杯美式。\n"
        "苏晨（紧张）：你来了。\n",
        encoding="utf-8",
    )


def _make_repo_with_scripts(tmp_path: Path, n_episodes: int = 3) -> Path:
    """Create a tmp repo with config/development.json + n .txt scripts."""

    repo = tmp_path / "fake_repo"
    repo.mkdir()
    (repo / "config").mkdir()
    (repo / "config" / "development.json").write_text(
        json.dumps(
            {
                "env": "development",
                "allow_overwrite_runs": True,
                "executor_default_status": "pending",
                "submit_paid_jobs": False,
                "log_level": "DEBUG",
                "executor_dry_run": True,
                "data_root": "data/development",
                "assets_root": "assets/development",
                "runs_root": "runs/development",
                "logs_root": "logs/development",
                "schema_strict": False,
                "planner_provider": "deterministic",
                "allow_provider_fallback": True,
            }
        ),
        encoding="utf-8",
    )
    scripts_dir = repo / "scripts"
    scripts_dir.mkdir()
    for i in range(1, n_episodes + 1):
        _write_script(scripts_dir / f"EP{i:02d}.txt", episode=f"EP{i:02d}")
    return repo


# --- derive_episode_id -------------------------------------------------


def test_derive_episode_id_lowercase_filename():
    assert derive_episode_id(Path("ep01.txt")) == "EP01"


def test_derive_episode_id_with_suffix():
    assert derive_episode_id(Path("EP01_test.txt")) == "EP01"


def test_derive_episode_id_no_match_uses_stem():
    assert derive_episode_id(Path("pilot.txt")) == "PILOT"


# --- discover_scripts --------------------------------------------------


def test_discover_scripts_sorted(tmp_path: Path):
    sd = tmp_path / "sd"
    sd.mkdir()
    (sd / "EP03.txt").write_text("x", encoding="utf-8")
    (sd / "EP01.txt").write_text("x", encoding="utf-8")
    (sd / "EP02.txt").write_text("x", encoding="utf-8")
    (sd / "README.md").write_text("x", encoding="utf-8")
    found = discover_scripts(sd)
    assert [p.name for p in found] == ["EP01.txt", "EP02.txt", "EP03.txt"]


def test_discover_scripts_missing_dir(tmp_path: Path):
    with pytest.raises(EnvironmentBoundaryError) as exc_info:
        discover_scripts(tmp_path / "no_such")
    assert "does not exist" in str(exc_info.value)


def test_discover_scripts_no_txt(tmp_path: Path):
    sd = tmp_path / "sd"
    sd.mkdir()
    (sd / "README.md").write_text("x", encoding="utf-8")
    with pytest.raises(EnvironmentBoundaryError) as exc_info:
        discover_scripts(sd)
    assert "No .txt script files found" in str(exc_info.value)


# --- run_batch happy path ----------------------------------------------


def test_run_batch_three_episodes(tmp_path: Path):
    repo = _make_repo_with_scripts(tmp_path, n_episodes=3)
    out = tmp_path / "batch"
    summary = run_batch(
        BatchOptions(
            env="development",
            scripts_dir=repo / "scripts",
            out_dir=out,
            repo_root=repo,
        )
    )
    assert isinstance(summary, BatchSummary)
    assert summary.env == "development"
    assert summary.totals["episodes_total"] == 3
    assert summary.totals["episodes_done"] == 3
    assert summary.totals["episodes_failed"] == 0
    assert summary.totals["aborted"] is False

    # Per-episode subdirs each have all 10 artifacts (script_parse + 9 core).
    for ep in summary.episodes:
        ep_dir = Path(ep.run_dir)
        assert ep_dir.exists(), f"missing episode dir {ep_dir}"
        for name in (
            "script_parse",
            "character_bible",
            "location_bible",
            "prop_bible",
            "story_beats",
            "shot_list",
            "image_prompts",
            "video_prompts",
            "asset_manifest",
            "executor_tasks",
        ):
            assert (ep_dir / f"{name}.json").exists(), f"{ep_dir}/{name}.json missing"
        assert ep.status == "done"
        # Audit fields preserved per-episode.
        assert ep.requested_provider == "deterministic"
        assert ep.effective_provider == "deterministic"
        assert ep.fallback_used is False
        assert ep.fallback_reason is None
        # Validation succeeded (deterministic bibles pass).
        assert ep.validation_ok is True
        assert ep.validation_errors == 0

    # batch_summary.json written.
    written = out / "batch_summary.json"
    assert written.exists()
    roundtrip = json.loads(written.read_text(encoding="utf-8"))
    assert roundtrip["batch_id"] == summary.batch_id
    assert len(roundtrip["episodes"]) == 3
    assert roundtrip["totals"]["episodes_done"] == 3


def test_run_batch_single_episode(tmp_path: Path):
    repo = _make_repo_with_scripts(tmp_path, n_episodes=1)
    summary = run_batch(
        BatchOptions(
            env="development",
            scripts_dir=repo / "scripts",
            out_dir=tmp_path / "out",
            repo_root=repo,
        )
    )
    assert summary.totals["episodes_total"] == 1
    assert summary.totals["counts"]["shots"] >= 1


# --- fail-fast semantics -----------------------------------------------


def test_run_batch_fail_fast_aborts_on_missing_script(tmp_path: Path, monkeypatch):
    """A planning failure must abort the batch by default."""
    repo = _make_repo_with_scripts(tmp_path, n_episodes=2)
    # Make EP01 succeed; sabotage EP02 by making its script unreadable.
    (repo / "scripts" / "EP02.txt").chmod(0o000)
    try:
        summary = run_batch(
            BatchOptions(
                env="development",
                scripts_dir=repo / "scripts",
                out_dir=tmp_path / "out",
                repo_root=repo,
            )
        )
    finally:
        (repo / "scripts" / "EP02.txt").chmod(0o644)

    # First episode should have succeeded, then second failed,
    # batch should have aborted (fail-fast default).
    statuses = [e.status for e in summary.episodes]
    assert statuses[0] == "done"
    assert statuses[1] == "failed"
    assert summary.totals["aborted"] is True
    assert summary.totals["episodes_done"] == 1
    assert summary.totals["episodes_failed"] == 1


def test_run_batch_no_fail_fast_records_and_continues(tmp_path: Path, monkeypatch):
    """With ``--no-fail-fast``, every episode is attempted and
    failures are recorded inline rather than silently dropped."""

    repo = _make_repo_with_scripts(tmp_path, n_episodes=2)
    (repo / "scripts" / "EP02.txt").chmod(0o000)
    try:
        summary = run_batch(
            BatchOptions(
                env="development",
                scripts_dir=repo / "scripts",
                out_dir=tmp_path / "out",
                fail_fast=False,
                repo_root=repo,
            )
        )
    finally:
        (repo / "scripts" / "EP02.txt").chmod(0o644)

    statuses = [e.status for e in summary.episodes]
    assert statuses == ["done", "failed"]
    assert summary.totals["aborted"] is False
    assert summary.totals["episodes_done"] == 1
    assert summary.totals["episodes_failed"] == 1
    # The failed episode carries a friendly error.
    failed = summary.episodes[1]
    assert failed.error_type is not None
    assert failed.error_message is not None
    assert "traceback" not in (failed.error_message or "").lower()


# --- production refuses to write inside repo ---------------------------


def test_run_batch_production_refuses_repo_path(tmp_path: Path):
    repo = _make_repo_with_scripts(tmp_path, n_episodes=1)
    (repo / "config" / "production.json").write_text(
        json.dumps(
            {
                "env": "production",
                "allow_overwrite_runs": False,
                "executor_default_status": "pending_manual_approval",
                "submit_paid_jobs": False,
                "log_level": "INFO",
                "executor_dry_run": True,
                "data_root": "data/production",
                "assets_root": "assets/production",
                "runs_root": "runs/production",
                "logs_root": "logs/production",
                "schema_strict": True,
                "planner_provider": "deterministic",
                "allow_provider_fallback": False,
            }
        ),
        encoding="utf-8",
    )
    forbidden_out = repo / "runs" / "production" / "leak"

    with pytest.raises(EnvironmentBoundaryError) as exc_info:
        run_batch(
            BatchOptions(
                env="production",
                scripts_dir=repo / "scripts",
                out_dir=forbidden_out,
                repo_root=repo,
            )
        )
    assert "inside the project repository" in str(exc_info.value)
    assert not forbidden_out.exists()


def test_run_batch_production_outside_repo_succeeds(tmp_path: Path):
    repo = _make_repo_with_scripts(tmp_path, n_episodes=1)
    (repo / "config" / "production.json").write_text(
        json.dumps(
            {
                "env": "production",
                "allow_overwrite_runs": False,
                "executor_default_status": "pending_manual_approval",
                "submit_paid_jobs": False,
                "log_level": "INFO",
                "executor_dry_run": True,
                "data_root": "data/production",
                "assets_root": "assets/production",
                "runs_root": "runs/production",
                "logs_root": "logs/production",
                "schema_strict": True,
                "planner_provider": "deterministic",
                "allow_provider_fallback": False,
            }
        ),
        encoding="utf-8",
    )
    outside = tmp_path / "external_batch"
    summary = run_batch(
        BatchOptions(
            env="production",
            scripts_dir=repo / "scripts",
            out_dir=outside,
            repo_root=repo,
        )
    )
    assert summary.totals["episodes_done"] == 1
    # Each episode carries the production-shaped audit fields.
    ep = summary.episodes[0]
    assert ep.status == "done"
    assert ep.requested_provider == "deterministic"
    assert ep.fallback_used is False


# --- batch_summary shape -----------------------------------------------


def test_batch_summary_includes_audit_and_validation(tmp_path: Path):
    repo = _make_repo_with_scripts(tmp_path, n_episodes=1)
    summary = run_batch(
        BatchOptions(
            env="development",
            scripts_dir=repo / "scripts",
            out_dir=tmp_path / "out",
            repo_root=repo,
        )
    )
    ep = summary.episodes[0]
    # Required audit fields.
    for key in (
        "run_id",
        "episode_id",
        "run_dir",
        "status",
        "script_path",
        "started_at",
        "finished_at",
        "counts",
        "requested_provider",
        "effective_provider",
        "fallback_used",
        "fallback_reason",
        "provider_health",
        "validation_ok",
        "validation_errors",
        "validation_warnings",
    ):
        assert hasattr(ep, key), f"EpisodeRunSummary missing {key}"
    # provider_health must carry the deterministic snapshot.
    assert ep.provider_health is not None
    assert "deterministic" in ep.provider_health
    assert ep.provider_health["deterministic"]["healthy"] is True
    # Required batch-level fields.
    for key in (
        "batch_id",
        "started_at",
        "finished_at",
        "env",
        "scripts_dir",
        "episodes",
        "totals",
    ):
        assert hasattr(summary, key), f"BatchSummary missing {key}"
    # Totals structure.
    for key in ("episodes_total", "episodes_done", "episodes_failed", "aborted", "counts"):
        assert key in summary.totals


# --- CLI integration ---------------------------------------------------


def test_cli_batch_help_exits_zero():
    """``planner batch --help`` exits 0 and prints a description."""

    result = subprocess.run(
        [sys.executable, "-m", "planner", "batch", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "batch" in result.stdout.lower()


def test_cli_batch_runs_end_to_end(tmp_path: Path):
    """End-to-end: invoke the actual CLI binary on a real batch."""

    repo = _make_repo_with_scripts(tmp_path, n_episodes=2)
    out_dir = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable, "-m", "planner",
            "--project-root", str(repo),
            "batch",
            "--env", "development",
            "--scripts", str(repo / "scripts"),
            "--out", str(out_dir),
            "--no-fail-fast",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"CLI failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert (out_dir / "batch_summary.json").exists()
    summary = json.loads((out_dir / "batch_summary.json").read_text(encoding="utf-8"))
    assert summary["env"] == "development"
    assert summary["totals"]["episodes_done"] == 2
    assert summary["totals"]["episodes_failed"] == 0


def test_cli_batch_failure_exits_nonzero(tmp_path: Path):
    """CLI exit code 2 when at least one episode fails."""

    repo = _make_repo_with_scripts(tmp_path, n_episodes=1)
    out_dir = tmp_path / "out"
    # Delete the script after discovery so pipeline.run raises ScriptReadError.
    scripts_dir = repo / "scripts"
    (scripts_dir / "EP01.txt").chmod(0o000)
    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "planner",
                "--project-root", str(repo),
                "batch",
                "--env", "development",
                "--scripts", str(scripts_dir),
                "--out", str(out_dir),
                "--no-fail-fast",
            ],
            capture_output=True,
            text=True,
        )
    finally:
        (scripts_dir / "EP01.txt").chmod(0o644)
    # With chmod 0 the planner should still produce a failed episode,
    # and the CLI should exit non-zero with the friendly-error contract.
    assert result.returncode != 0
    assert "Traceback" not in result.stderr