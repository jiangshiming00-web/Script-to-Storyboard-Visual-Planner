"""Subprocess-driven tests for ``planner agent`` CLI (Phase 3 P1).

These exercise the CLI surface end-to-end (Click + subprocess) so
that any future regression in error handling / exit codes / policy
refusal / stdout JSON shape is caught here. They mirror the
hand-driven smoke tests in the plan's verification section.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Tuple

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _run_cli(*args: str) -> Tuple[int, str, str]:
    """Run ``python3 -m planner ...args`` and return ``(rc, stdout, stderr)``."""
    proc = subprocess.run(
        [sys.executable, "-m", "planner", *args],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=120,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _make_dev_run(tmp_path: Path) -> Path:
    """Produce a dev run via the real planner CLI; return its path."""
    out = tmp_path / "dev_run"
    rc, _, err = _run_cli(
        "run",
        "--env",
        "development",
        "--script",
        "data/development/input_scripts/sample_ep01.txt",
        "--out",
        str(out),
    )
    assert rc == 0, f"pipeline failed: {err[-500:]}"
    return out


def _make_prod_simulated_run(tmp_path: Path, source_dev_run: Path) -> Path:
    """Copy a dev run, override env=production in run_summary.json."""
    target = tmp_path / "prod_sim_run"
    shutil.copytree(source_dev_run, target)
    summary_path = target / "run_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["env"] = "production"
    summary["executor_status"] = "pending_manual_approval"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target


# ---------- diagnose ----------


def test_cli_diagnose_dev_run_returns_json_exit_zero(tmp_path: Path) -> None:
    run_dir = _make_dev_run(tmp_path)
    rc, out, err = _run_cli("agent", "diagnose", str(run_dir))
    assert rc == 0, f"stderr: {err[-500:]}"
    data = json.loads(out)
    assert data["run_id"]
    assert data["env"] == "development"
    assert data["implementation_status"] == "full"
    assert data["status"] in {"ok", "warnings", "errors"}


def test_cli_diagnose_missing_dir_exits_2(tmp_path: Path) -> None:
    rc, out, err = _run_cli("agent", "diagnose", str(tmp_path / "no_such_dir"))
    assert rc == 2
    assert "Traceback" not in err
    # Click writes a friendly Usage message to stderr; no JSON.
    assert "does not exist" in err.lower() or "no such" in err.lower()


def test_cli_diagnose_write_report_to_tmp(tmp_path: Path) -> None:
    run_dir = _make_dev_run(tmp_path)
    report_path = tmp_path / "report.json"
    rc, _, err = _run_cli(
        "agent", "diagnose", str(run_dir), "--write-report", str(report_path)
    )
    assert rc == 0, f"stderr: {err[-500:]}"
    assert report_path.exists()
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["run_id"]


def test_cli_diagnose_production_repo_internal_refused(tmp_path: Path) -> None:
    """Production run + --write-report inside repo -> rc=2, no file."""
    dev_run = _make_dev_run(tmp_path)
    prod_run = _make_prod_simulated_run(tmp_path, dev_run)
    repo_internal = PROJECT_ROOT / "runs" / "test-prod-cli-refuse.json"
    # Best-effort cleanup before
    if repo_internal.exists():
        repo_internal.unlink()
    rc, out, err = _run_cli(
        "agent",
        "diagnose",
        str(prod_run),
        "--write-report",
        str(repo_internal),
    )
    assert rc == 2, f"expected rc=2, got rc={rc}; stderr: {err[-500:]}"
    assert not repo_internal.exists(), "policy refused but file was written"
    assert "refuses" in err.lower()


def test_cli_diagnose_dev_repo_internal_warns_and_allows(tmp_path: Path) -> None:
    """Dev run + --write-report inside repo -> rc=0 + stderr WARNING + file written."""
    run_dir = _make_dev_run(tmp_path)
    repo_internal = PROJECT_ROOT / "runs" / "test-dev-cli-warn.json"
    if repo_internal.exists():
        repo_internal.unlink()
    try:
        rc, _, err = _run_cli(
            "agent",
            "diagnose",
            str(run_dir),
            "--write-report",
            str(repo_internal),
        )
        assert rc == 0, f"stderr: {err[-500:]}"
        assert repo_internal.exists()
        assert "warning" in err.lower() or "dev-only" in err.lower()
    finally:
        if repo_internal.exists():
            repo_internal.unlink()


# ---------- stub commands ----------


def test_cli_review_run_dev_run_returns_full_exit_zero(tmp_path: Path) -> None:
    run_dir = _make_dev_run(tmp_path)
    rc, out, err = _run_cli("agent", "review-run", str(run_dir))
    assert rc == 0, f"stderr: {err[-500:]}"
    data = json.loads(out)
    assert data["implementation_status"] == "full"
    assert data["review_version"] == "1.0"
    assert len(data["tool_invocations"]) > 0
    assert data["status"] in {"ok", "warnings"}
    assert "Traceback" not in err


def test_cli_review_run_format_markdown(tmp_path: Path) -> None:
    run_dir = _make_dev_run(tmp_path)
    rc, out, err = _run_cli("agent", "review-run", str(run_dir), "--format", "markdown")
    assert rc == 0, f"stderr: {err[-500:]}"
    assert "# Review Report" in out
    assert "review_version" in out
    assert "Traceback" not in err


def test_cli_review_run_verbose_emits_summary(tmp_path: Path) -> None:
    run_dir = _make_dev_run(tmp_path)
    rc, _, err = _run_cli("agent", "review-run", str(run_dir), "-v")
    assert rc == 0, f"stderr: {err[-500:]}"
    assert "摘要" in err
    assert "Traceback" not in err


def test_cli_review_run_write_report_to_tmp(tmp_path: Path) -> None:
    run_dir = _make_dev_run(tmp_path)
    report_path = tmp_path / "review_report.json"
    rc, _, err = _run_cli(
        "agent", "review-run", str(run_dir), "--write-report", str(report_path)
    )
    assert rc == 0, f"stderr: {err[-500:]}"
    assert report_path.exists()
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["implementation_status"] == "full"
    assert data["review_version"] == "1.0"


def test_cli_review_run_production_repo_internal_refused(tmp_path: Path) -> None:
    dev_run = _make_dev_run(tmp_path)
    prod_run = _make_prod_simulated_run(tmp_path, dev_run)
    repo_internal = PROJECT_ROOT / "runs" / "test-review-prod-cli-refuse.json"
    if repo_internal.exists():
        repo_internal.unlink()
    try:
        rc, _, err = _run_cli(
            "agent", "review-run", str(prod_run), "--write-report", str(repo_internal)
        )
        assert rc == 2, f"expected rc=2, got rc={rc}; stderr: {err[-500:]}"
        assert not repo_internal.exists(), "policy refused but file was written"
        assert "refuses" in err.lower()
    finally:
        if repo_internal.exists():
            repo_internal.unlink()


def test_cli_review_run_errors_exit_one(tmp_path: Path) -> None:
    run_dir = _make_dev_run(tmp_path)
    ip_path = run_dir / "image_prompts.json"
    ip = json.loads(ip_path.read_text(encoding="utf-8"))
    ip["image_prompts"][0]["prompt"] = ip["image_prompts"][0]["prompt"] + " {location_name}"
    ip_path.write_text(json.dumps(ip, ensure_ascii=False, indent=2), encoding="utf-8")
    rc, out, err = _run_cli("agent", "review-run", str(run_dir))
    assert rc == 1, f"expected rc=1, got rc={rc}; stderr: {err[-500:]}"
    data = json.loads(out)
    assert data["status"] == "errors"
    assert "Traceback" not in err


def test_cli_review_run_missing_dir_exits_2(tmp_path: Path) -> None:
    rc, _, err = _run_cli("agent", "review-run", str(tmp_path / "no_such_dir"))
    assert rc == 2
    assert "Traceback" not in err
    assert "does not exist" in err.lower() or "no such" in err.lower()


def test_cli_review_batch_stub_returns_not_implemented_exit_zero(tmp_path: Path) -> None:
    run_dir = _make_dev_run(tmp_path)
    rc, out, err = _run_cli("agent", "review-batch", str(run_dir))
    assert rc == 0, f"stderr: {err[-500:]}"
    data = json.loads(out)
    assert data["implementation_status"] == "not_implemented"
    assert data["tool_invocations"] == []
    assert "Traceback" not in err


def test_cli_review_run_corrupted_artifact_no_traceback(tmp_path: Path) -> None:
    """P1 regression: a legitimate-JSON / wrong-shape artifact must not
    leak a traceback. image_prompts.json as a bare list -> the engine
    emits an artifact_corrupted finding and the CLI exits cleanly with
    valid JSON (no AttributeError on stderr).
    """
    run_dir = _make_dev_run(tmp_path)
    (run_dir / "image_prompts.json").write_text("[1, 2, 3]", encoding="utf-8")
    rc, out, err = _run_cli("agent", "review-run", str(run_dir))
    assert rc in (0, 1), f"rc={rc}; stderr: {err[-500:]}"
    assert "Traceback" not in err
    assert "AttributeError" not in err
    data = json.loads(out)
    assert data["implementation_status"] == "full"
    codes = [f["code"] for f in data["findings"]]
    assert "artifact_corrupted" in codes


def test_cli_review_run_corrupted_run_summary_no_traceback(tmp_path: Path) -> None:
    """P1 regression: run_summary.json as a bare list must not leak a
    traceback; the CLI emits corrupted_run_summary and exits 1.
    """
    run_dir = _make_dev_run(tmp_path)
    (run_dir / "run_summary.json").write_text("[1, 2, 3]", encoding="utf-8")
    rc, out, err = _run_cli("agent", "review-run", str(run_dir))
    assert rc == 1, f"rc={rc}; stderr: {err[-500:]}"
    assert "Traceback" not in err
    assert "AttributeError" not in err
    data = json.loads(out)
    assert data["status"] == "errors"
    codes = [f["code"] for f in data["findings"]]
    assert "corrupted_run_summary" in codes
