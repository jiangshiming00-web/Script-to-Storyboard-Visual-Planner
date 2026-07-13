"""Tests for production boundaries, env-var downgrade attempts, and
executor-tool neutrality. These tests exist specifically because the
Phase-1 review flagged the following risks:

1. ``PLANNER_EXECUTOR_DEFAULT_STATUS=pending`` must NOT downgrade
   production.
2. ``PLANNER_SUBMIT_PAID_JOBS=1`` must NOT enable paid jobs in production.
3. ``PLANNER_ALLOW_OVERWRITE_RUNS=true`` must NOT enable overwrites in
   production.
4. The planner must never hard-code ``flowith`` into executor tasks.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from planner.env import load_config
from planner.exceptions import ConfigError
from planner.manifest import build_executor_tasks
from planner.pipeline import run as run_pipeline
from planner.schema import AssetStatus, Shot, ShotList, ShotSize


@pytest.fixture(autouse=True)
def _scrub_planner_env(monkeypatch):
    """Make sure no PLANNER_* env from the host leaks into each test."""

    for key in list(os.environ):
        if key.startswith("PLANNER_"):
            monkeypatch.delenv(key, raising=False)
    yield


def test_production_rejects_env_var_downgrade_of_executor_status(
    monkeypatch, project_root: Path, tmp_path: Path
) -> None:
    monkeypatch.setenv("PLANNER_EXECUTOR_DEFAULT_STATUS", "pending")
    prod_cfg_path = project_root / "config" / "production.example.json"
    # The downgrade attempt must raise — production never silently
    # honours a PLANNER_* override for a locked key.
    with pytest.raises(ConfigError, match="PLANNER_EXECUTOR_DEFAULT_STATUS"):
        load_config(
            "production", project_root=project_root, config_path=prod_cfg_path
        )


def test_production_rejects_env_var_paid_jobs(monkeypatch, project_root: Path) -> None:
    monkeypatch.setenv("PLANNER_SUBMIT_PAID_JOBS", "1")
    prod_cfg_path = project_root / "config" / "production.example.json"
    with pytest.raises(ConfigError, match="submit_paid_jobs"):
        load_config(
            "production", project_root=project_root, config_path=prod_cfg_path
        )


def test_production_rejects_env_var_allow_overwrite(
    monkeypatch, project_root: Path
) -> None:
    monkeypatch.setenv("PLANNER_ALLOW_OVERWRITE_RUNS", "true")
    prod_cfg_path = project_root / "config" / "production.example.json"
    with pytest.raises(ConfigError, match="allow_overwrite_runs"):
        load_config(
            "production", project_root=project_root, config_path=prod_cfg_path
        )


def test_development_still_accepts_env_var_executor_status(
    monkeypatch, project_root: Path
) -> None:
    monkeypatch.setenv("PLANNER_EXECUTOR_DEFAULT_STATUS", "pending")
    cfg = load_config("development", project_root=project_root)
    assert cfg.executor_default_status == "pending"


def test_executor_tasks_default_tool_is_none() -> None:
    """The planner must not hard-code any specific executor."""

    shots = ShotList(
        shots=[
            Shot(
                id="EP01_SH001",
                scene_id="EP01_S01",
                location_id="office",
                shot_size=ShotSize.WIDE,
                camera_angle="eye",
                composition="est",
                action="x",
                emotion="x",
                duration_sec=4,
            )
        ]
    )
    tasks = build_executor_tasks(
        shots, "image_prompts.json", "asset_manifest.json"
    )
    for task in tasks.tasks:
        assert task.tool is None, f"task {task.id} has hard-coded tool"


def test_pipeline_does_not_emit_flowith_by_default(
    project_root: Path, sample_script_path: Path, tmp_path: Path
) -> None:
    out_dir = tmp_path / "dev_run"
    cfg = load_config("development", project_root=project_root)
    run_pipeline(script_path=sample_script_path, out_dir=out_dir, config=cfg)
    tasks = json.loads((out_dir / "executor_tasks.json").read_text("utf-8"))
    assert tasks["tasks"], "expected at least one executor task"
    for task in tasks["tasks"]:
        assert task.get("tool") is None, f"task {task['id']} hard-codes tool"


def test_cli_friendly_error_when_production_config_missing(
    project_root: Path, monkeypatch
) -> None:
    """CLI must not leak a Python traceback for ConfigError."""

    # Make sure config/production.json does not exist for this test.
    prod_path = project_root / "config" / "production.json"
    if prod_path.exists():
        prod_path.unlink()

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "planner",
            "run",
            "--env",
            "production",
            "--script",
            str(project_root / "data" / "development" / "input_scripts" / "sample_ep01.txt"),
            "--out",
            str(project_root / "runs" / "production" / "should_not_be_created"),
        ],
        capture_output=True,
        text=True,
        cwd=str(project_root),
        env={k: v for k, v in os.environ.items() if not k.startswith("PLANNER_")},
    )
    assert result.returncode != 0, "expected non-zero exit"
    assert "config error" in result.stderr.lower()
    # No traceback should reach the user.
    assert "Traceback" not in result.stderr


def test_cli_run_production_refuses_repo_internal_out(
    project_root: Path, tmp_path: Path
) -> None:
    """``planner run --env production --out <repo>/...`` must refuse.

    Mirrors the GUI's ``resolve_out_dir`` policy at the CLI boundary
    so a tampered shell script cannot sneak a production run into the
    project repository. The check runs at the CLI layer (NOT in
    pipeline.run) so the rejection happens before any directory I/O.
    """

    prod_cfg = project_root / "config" / "production.example.json"
    repo_out = project_root / "runs" / "production_repo_internal"
    repo_out.parent.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [
            sys.executable, "-m", "planner",
            "run",
            "--env", "production",
            "--script",
            str(project_root / "data" / "development" / "input_scripts" / "sample_ep01.txt"),
            "--out", str(repo_out),
            "--config", str(prod_cfg),
        ],
        capture_output=True,
        text=True,
        cwd=str(project_root),
        env={k: v for k, v in os.environ.items() if not k.startswith("PLANNER_")},
    )
    # rc=2 = CLI policy refusal (matches --force behaviour).
    assert result.returncode == 2, (
        f"expected rc=2 refusal, got {result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "refuses to write inside the project repository" in result.stderr
    assert "Traceback" not in result.stderr
    assert not repo_out.exists() or not any(repo_out.iterdir()), (
        f"production run leaked artifacts into {repo_out}"
    )


def test_is_inside_repo_helper(project_root: Path, tmp_path: Path) -> None:
    """``planner.env.is_inside_repo`` returns the right answer for the
    obvious repo-internal / repo-external / escape-via-symlink cases."""

    from planner.env import is_inside_repo

    # Inside repo
    inside = project_root / "runs" / "tmp"
    assert is_inside_repo(inside, project_root) is True
    # Outside repo
    outside = tmp_path / "external"
    outside.mkdir(parents=True, exist_ok=True)
    assert is_inside_repo(outside, project_root) is False
    # Symlink inside repo: ``resolve()`` follows the link, so this
    # still counts as inside.
    link_dir = tmp_path / "external" / "link"
    link_dir.mkdir(parents=True, exist_ok=True)
    link_target = project_root / "runs" / "linked_target"
    link_target.mkdir(parents=True, exist_ok=True)
    symlink = tmp_path / "external" / "back_to_repo"
    if not symlink.exists():
        symlink.symlink_to(link_target)
    assert is_inside_repo(symlink, project_root) is True


def test_validate_reports_env_mismatch(
    project_root: Path, sample_script_path: Path, tmp_path: Path
) -> None:
    """validate --env production on a development run must warn."""

    dev_dir = tmp_path / "dev_run"
    cfg = load_config("development", project_root=project_root)
    run_pipeline(script_path=sample_script_path, out_dir=dev_dir, config=cfg)

    # Validate with the WRONG --env flag.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "planner",
            "validate",
            "--env",
            "production",
            "--run",
            str(dev_dir),
        ],
        capture_output=True,
        text=True,
        cwd=str(project_root),
        env={k: v for k, v in os.environ.items() if not k.startswith("PLANNER_")},
    )
    # exit 0 because validation itself passed (artifacts are intact).
    assert result.returncode == 0
    assert "env mismatch" in result.stderr.lower()


def test_agent_cli_does_not_leak_traceback(project_root: Path, tmp_path: Path) -> None:
    """Phase 3 P1: ``planner agent diagnose`` on a missing path must
    exit non-zero with a friendly Click Usage message — never a
    Python traceback.

    Mirrors :func:`test_cli_friendly_error_when_production_config_missing`
    for the new ``planner agent`` subcommand. The contract is the
    same: the operator sees the error, not the internals.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "planner",
            "agent",
            "diagnose",
            str(tmp_path / "no_such_run_dir"),
        ],
        capture_output=True,
        text=True,
        cwd=str(project_root),
        env={k: v for k, v in os.environ.items() if not k.startswith("PLANNER_")},
    )
    assert result.returncode != 0
    assert "Traceback" not in result.stderr, (
        f"Traceback leaked to user: {result.stderr[:500]}"
    )