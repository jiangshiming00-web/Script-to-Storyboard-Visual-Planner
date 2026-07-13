"""Tests for planner.agent.diagnose (Phase 3 P1).

One test per rule (R10/R11 entry; R1/R5/R6 via validate_run
delegation; R2/R3/R4/R7/R8/R9/R12/R13 independent). Each test
builds a minimal ``run_dir`` so the suite runs in milliseconds
without spawning the real pipeline.

Why minimal fixtures: full pipeline runs take 1-2s each. 13 rules
* 3 dev/prod matrix = ~40 slow tests. The minimal fixture pins
each rule's contract independently of pipeline behavior.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from planner.agent.diagnose import (
    DiagnoseReport,
    build_not_implemented_report,
    diagnose_run_dir,
)


# ---------- Helpers ----------


def _write_run_summary(
    run_dir: Path,
    *,
    env: str = "development",
    requested_provider: str = "deterministic",
    effective_provider: str = "deterministic",
    fallback_used: bool = False,
    fallback_reason: Optional[str] = None,
    executor_status: Optional[str] = "pending",
    counts: Optional[Dict[str, int]] = None,
    provider_health: Optional[Dict[str, Any]] = None,
    provider_runtime: Optional[Dict[str, Any]] = None,
    script: str = "data/development/input_scripts/sample_ep01.txt",
) -> None:
    summary: Dict[str, Any] = {
        "run_id": "test-run-001",
        "env": env,
        "script": script,
        "episode_id": "EP01",
        "planner_provider": requested_provider,
        "requested_provider": requested_provider,
        "effective_provider": effective_provider,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "provider_health": provider_health
        or {
            "deterministic": {
                "name": "deterministic",
                "healthy": True,
                "reason": "deterministic provider has no external dependencies",
                "details": {"external_calls": "none", "phase": "1"},
            }
        },
        "executor_status": executor_status,
        "counts": counts
        or {
            "characters": 2,
            "locations": 2,
            "props": 1,
            "beats": 3,
            "shots": 6,
            "image_prompts": 6,
            "video_prompts": 6,
        },
        "artifacts": {},
    }
    if provider_runtime is not None:
        summary["provider_runtime"] = provider_runtime
    (run_dir / "run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _write_executor_tasks(
    run_dir: Path, *, tool: Optional[str] = None
) -> None:
    payload = {
        "tasks": [
            {
                "id": "task-001",
                "shot_id": "shot-001",
                "kind": "image",
                "tool": tool,
                "status": "pending_manual_approval",
                "input_prompt_ref": "image_prompts.json#shot-001",
            }
        ]
    }
    (run_dir / "executor_tasks.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _make_minimal_run(tmp_path: Path, **kwargs: Any) -> Path:
    """Build a minimal valid run dir under tmp_path. Returns the dir."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_run_summary(run_dir, **kwargs)
    return run_dir


# ---------- Entry / R10 / R11 ----------


def test_r10_missing_run_summary(tmp_path: Path) -> None:
    """R10: no run_summary.json -> status=errors, finding emitted."""
    run_dir = tmp_path / "missing"
    run_dir.mkdir()
    report = diagnose_run_dir(run_dir)
    assert report.status == "errors"
    codes = {f.code for f in report.findings}
    assert "missing_run_summary" in codes
    assert all(f.severity == "error" for f in report.findings)


def test_r11_corrupted_run_summary(tmp_path: Path) -> None:
    """R11: bad JSON -> status=errors, finding emitted (no raise)."""
    run_dir = tmp_path / "corrupt"
    run_dir.mkdir()
    (run_dir / "run_summary.json").write_text("{not valid json", encoding="utf-8")
    report = diagnose_run_dir(run_dir)
    assert report.status == "errors"
    codes = {f.code for f in report.findings}
    assert "corrupted_run_summary" in codes


# ---------- R2: dev fallback warning ----------


def test_r2_dev_fallback_used_emits_warning(tmp_path: Path) -> None:
    run_dir = _make_minimal_run(
        tmp_path,
        env="development",
        requested_provider="openai_compatible",
        effective_provider="deterministic",
        fallback_used=True,
        fallback_reason="openai_compatible is not configured",
    )
    report = diagnose_run_dir(run_dir)
    codes = [f.code for f in report.findings]
    assert "dev_fallback_used" in codes
    matching = [f for f in report.findings if f.code == "dev_fallback_used"]
    assert matching[0].severity == "warning"


def test_r2_dev_no_fallback_no_finding(tmp_path: Path) -> None:
    run_dir = _make_minimal_run(tmp_path, env="development", fallback_used=False)
    report = diagnose_run_dir(run_dir)
    assert "dev_fallback_used" not in [f.code for f in report.findings]


# ---------- R3: all providers unhealthy ----------


def test_r3_all_providers_unhealthy_emits_warning(tmp_path: Path) -> None:
    run_dir = _make_minimal_run(
        tmp_path,
        env="development",
        provider_health={
            "openai": {
                "name": "openai",
                "healthy": False,
                "reason": "missing key",
                "details": {"implemented": "false"},
            },
            "anthropic": {
                "name": "anthropic",
                "healthy": False,
                "reason": "missing key",
                "details": {"implemented": "false"},
            },
        },
    )
    report = diagnose_run_dir(run_dir)
    codes = [f.code for f in report.findings]
    assert "all_providers_unhealthy" in codes


def test_r3_partial_unhealthy_no_finding(tmp_path: Path) -> None:
    run_dir = _make_minimal_run(
        tmp_path,
        env="development",
        provider_health={
            "openai": {
                "name": "openai",
                "healthy": False,
                "reason": "missing key",
                "details": {},
            },
            "deterministic": {
                "name": "deterministic",
                "healthy": True,
                "reason": "ok",
                "details": {},
            },
        },
    )
    report = diagnose_run_dir(run_dir)
    assert "all_providers_unhealthy" not in [f.code for f in report.findings]


# ---------- R4: hardcoded executor tool ----------


def test_r4_hardcoded_tool_emits_error(tmp_path: Path) -> None:
    run_dir = _make_minimal_run(tmp_path)
    _write_executor_tasks(run_dir, tool="flowith")
    report = diagnose_run_dir(run_dir)
    codes = [f.code for f in report.findings]
    assert "executor_tool_hardcoded" in codes
    matching = [f for f in report.findings if f.code == "executor_tool_hardcoded"]
    assert matching[0].severity == "error"


def test_r4_none_tool_no_finding(tmp_path: Path) -> None:
    run_dir = _make_minimal_run(tmp_path)
    _write_executor_tasks(run_dir, tool=None)
    report = diagnose_run_dir(run_dir)
    assert "executor_tool_hardcoded" not in [f.code for f in report.findings]


# ---------- R7: production executor_status wrong ----------


def test_r7_production_status_wrong_emits_error(tmp_path: Path) -> None:
    run_dir = _make_minimal_run(
        tmp_path, env="production", executor_status="pending"
    )
    report = diagnose_run_dir(run_dir)
    codes = [f.code for f in report.findings]
    assert "production_executor_status_wrong" in codes
    matching = [f for f in report.findings if f.code == "production_executor_status_wrong"]
    assert matching[0].severity == "error"


def test_r7_production_status_correct_no_finding(tmp_path: Path) -> None:
    run_dir = _make_minimal_run(
        tmp_path, env="production", executor_status="pending_manual_approval"
    )
    report = diagnose_run_dir(run_dir)
    assert "production_executor_status_wrong" not in [f.code for f in report.findings]


# ---------- R8: api_key_env unset ----------


def test_r8_api_key_env_unset_dev_warning(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PLANNER_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    run_dir = _make_minimal_run(
        tmp_path,
        env="development",
        provider_runtime={
            "model": "gpt-4",
            "base_url": "https://api.openai.com/v1",
            "api_key_env": "PLANNER_OPENAI_API_KEY",
            "enable_real_model_calls": True,
        },
    )
    report = diagnose_run_dir(run_dir)
    codes = [f.code for f in report.findings]
    assert "api_key_env_unset" in codes
    matching = [f for f in report.findings if f.code == "api_key_env_unset"]
    # Dev: env var name should be echoed in the message.
    assert "PLANNER_OPENAI_API_KEY" in matching[0].message


def test_r8_api_key_env_unset_prod_message_sanitized(
    tmp_path: Path, monkeypatch
) -> None:
    """In production, agent must NOT echo the env var name."""
    monkeypatch.delenv("PLANNER_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    run_dir = _make_minimal_run(
        tmp_path,
        env="production",
        executor_status="pending_manual_approval",
        provider_runtime={
            "model": "gpt-4",
            "base_url": "https://api.openai.com/v1",
            "api_key_env": "PLANNER_OPENAI_API_KEY",
            "enable_real_model_calls": True,
        },
    )
    report = diagnose_run_dir(run_dir)
    codes = [f.code for f in report.findings]
    assert "api_key_env_unset" in codes
    matching = [f for f in report.findings if f.code == "api_key_env_unset"]
    # Production sanitization: env var name NOT echoed.
    assert "PLANNER_OPENAI_API_KEY" not in matching[0].message


def test_r8_api_key_env_present_no_finding(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PLANNER_OPENAI_API_KEY", "sk-fake-realistic-value-12345678")
    run_dir = _make_minimal_run(
        tmp_path,
        provider_runtime={
            "model": "gpt-4",
            "base_url": "https://api.openai.com/v1",
            "api_key_env": "PLANNER_OPENAI_API_KEY",
            "enable_real_model_calls": True,
        },
    )
    report = diagnose_run_dir(run_dir)
    assert "api_key_env_unset" not in [f.code for f in report.findings]
    # The actual key value must NEVER appear anywhere in the report.
    serialized = json.dumps(report.model_dump(mode="json"))
    assert "sk-fake-realistic-value-12345678" not in serialized


# ---------- R9: real_calls disabled but not deterministic ----------


def test_r9_real_calls_disabled_but_not_deterministic(tmp_path: Path) -> None:
    run_dir = _make_minimal_run(
        tmp_path,
        effective_provider="openai_compatible",
        provider_runtime={
            "model": "gpt-4",
            "base_url": "https://api.openai.com/v1",
            "api_key_env": "PLANNER_OPENAI_API_KEY",
            "enable_real_model_calls": False,
        },
    )
    report = diagnose_run_dir(run_dir)
    codes = [f.code for f in report.findings]
    assert "real_calls_disabled_but_not_deterministic" in codes


def test_r9_real_calls_disabled_and_deterministic_no_finding(tmp_path: Path) -> None:
    run_dir = _make_minimal_run(
        tmp_path,
        effective_provider="deterministic",
        provider_runtime={
            "model": "deterministic",
            "base_url": "",
            "api_key_env": "",
            "enable_real_model_calls": False,
        },
    )
    report = diagnose_run_dir(run_dir)
    assert "real_calls_disabled_but_not_deterministic" not in [
        f.code for f in report.findings
    ]


# ---------- R12: partial run ----------


def test_r12_partial_run_missing_artifact(tmp_path: Path) -> None:
    # Only run_summary.json exists; other 9 core artifacts absent.
    run_dir = _make_minimal_run(tmp_path, env="development", executor_status="pending")
    report = diagnose_run_dir(run_dir)
    codes = [f.code for f in report.findings]
    assert "partial_run_missing_artifact" in codes


def test_r12_no_finding_when_executor_status_failed(tmp_path: Path) -> None:
    # Failed runs are not "partial" by our definition — they may have
    # intentionally removed artifacts. Don't flag.
    run_dir = _make_minimal_run(
        tmp_path, env="development", executor_status="failed"
    )
    report = diagnose_run_dir(run_dir)
    assert "partial_run_missing_artifact" not in [f.code for f in report.findings]


# ---------- R13: counts mismatch ----------


def test_r13_image_prompts_count_mismatch(tmp_path: Path) -> None:
    run_dir = _make_minimal_run(
        tmp_path,
        counts={
            "characters": 2,
            "locations": 2,
            "props": 1,
            "beats": 3,
            "shots": 6,
            "image_prompts": 4,  # mismatch
            "video_prompts": 6,
        },
    )
    report = diagnose_run_dir(run_dir)
    codes = [f.code for f in report.findings]
    assert "image_prompts_count_mismatch" in codes


def test_r13_video_prompts_count_mismatch(tmp_path: Path) -> None:
    run_dir = _make_minimal_run(
        tmp_path,
        counts={
            "characters": 2,
            "locations": 2,
            "props": 1,
            "beats": 3,
            "shots": 6,
            "image_prompts": 6,
            "video_prompts": 2,  # mismatch
        },
    )
    report = diagnose_run_dir(run_dir)
    codes = [f.code for f in report.findings]
    assert "video_prompts_count_mismatch" in codes


# ---------- Status derivation ----------


def test_derive_status_promotes_to_errors_when_any_error_finding(tmp_path: Path) -> None:
    """End-to-end: a production+fallback run surfaces status=errors."""
    run_dir = _make_minimal_run(
        tmp_path,
        env="production",
        executor_status="pending_manual_approval",
        requested_provider="openai_compatible",
        effective_provider="deterministic",
        fallback_used=True,
        fallback_reason="openai_compatible skeleton",
    )
    # Need script_parse.json etc for validate_run to not crash.
    # Use a copy of a real dev run's artifacts.
    from tests.conftest import SAMPLE_SCRIPT

    _copy_real_pipeline_artifacts(run_dir, SAMPLE_SCRIPT.parent, tmp_path)

    report = diagnose_run_dir(run_dir)
    # The production+fallback rule fires via validate_run delegation.
    assert any(f.code == "production_fallback_used" for f in report.findings)
    assert report.status == "errors"


def test_derive_status_ok_when_no_findings(tmp_path: Path) -> None:
    run_dir = _make_minimal_run(tmp_path)
    report = diagnose_run_dir(run_dir)
    assert report.status in {"ok", "warnings"}
    assert all(f.severity != "error" for f in report.findings)


# ---------- Stub builder ----------


def test_build_not_implemented_report_has_empty_tool_invocations() -> None:
    """Phase 3 P1 contract: stub reports must NOT pretend to have
    read anything. ``tool_invocations=[]`` is the canonical signal.
    """
    rep = build_not_implemented_report(kind="review-run", target="/tmp/whatever")
    assert rep.implementation_status == "not_implemented"
    assert rep.tool_invocations == []
    assert rep.status == "ok"
    codes = [f.code for f in rep.findings]
    assert codes == ["not_implemented_in_p1"]


# ---------- Internal helpers ----------


def _copy_real_pipeline_artifacts(run_dir: Path, scripts_dir: Path, tmp_path: Path) -> None:
    """Run the real planner in dev mode to produce a valid run dir,
    then copy its script_parse.json + bibles + shot_list + image/video
    prompts + asset_manifest over to ``run_dir``. This is the
    fastest way to give validate_run what it needs.
    """
    import subprocess

    real_out = tmp_path / "_real_pipeline_out"
    real_out.mkdir()
    r = subprocess.run(
        [
            "python3",
            "-m",
            "planner",
            "run",
            "--env",
            "development",
            "--script",
            str(scripts_dir / "sample_ep01.txt"),
            "--out",
            str(real_out),
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, f"pipeline failed: {r.stderr[-500:]}"
    for name in (
        "script_parse.json",
        "character_bible.json",
        "location_bible.json",
        "prop_bible.json",
        "story_beats.json",
        "shot_list.json",
        "image_prompts.json",
        "video_prompts.json",
        "asset_manifest.json",
    ):
        src = real_out / name
        if src.exists():
            (run_dir / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
