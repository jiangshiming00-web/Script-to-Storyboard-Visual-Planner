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


def test_build_not_implemented_report_has_explicit_production_env() -> None:
    """P3-4 fix: stub reports set ``env="production"`` explicitly so
    the ``--write-report`` policy's run_env fallback is transparent
    (the operator sees why a stub report inside the repo is refused).
    """
    rep = build_not_implemented_report(kind="review-batch", target="/tmp/x")
    assert rep.env == "production"


# ---------- P1 fix: redact fallback_reason + provider_health fields ----------


def test_provider_fallback_reason_is_redacted_in_report(tmp_path: Path) -> None:
    """P1 fix (Codex manual review): ``provider.fallback_reason`` is
    a free-form string copied from run_summary.json. If the upstream
    run leaked a token into the reason (a real risk for any
    provider that interpolates the request body into the error),
    the agent MUST redact it before the value reaches the report
    JSON / --write-report file / Markdown renderer.
    """
    secret_bearer = "Bearer eyJhbGciOiJIUzI1NiJ9-real-secret-12345678"
    secret_sk = "sk-proj-abcdefghij-real-secret-12345678"
    run_dir = _make_minimal_run(
        tmp_path,
        env="development",
        requested_provider="openai_compatible",
        effective_provider="deterministic",
        fallback_used=True,
        fallback_reason=f"upstream returned {secret_bearer} "
        f"and api_key={secret_sk}",
    )
    report = diagnose_run_dir(run_dir)
    serialized = json.dumps(report.model_dump(mode="json"))
    assert secret_bearer not in serialized
    assert secret_sk not in serialized
    # And the redacted substring should appear in fallback_reason.
    assert "<redacted>" in (report.provider.fallback_reason or "")


def test_provider_health_reason_and_details_are_redacted_in_report(
    tmp_path: Path,
) -> None:
    """P1 fix: ``provider.health[*].reason`` and ``.details[*]`` are
    also free-form text copied from run_summary. Redact both.
    """
    secret = "sk-ant-api03-real-secret-12345678"
    run_dir = _make_minimal_run(
        tmp_path,
        provider_health={
            "anthropic": {
                "name": "anthropic",
                "healthy": False,
                "reason": f"missing key: {secret}",
                "details": {
                    "implemented": "false",
                    "api_key_env_value": secret,
                    "phase": "1-skeleton",
                },
            }
        },
    )
    report = diagnose_run_dir(run_dir)
    serialized = json.dumps(report.model_dump(mode="json"))
    assert secret not in serialized
    # Verify both reason and details are redacted (not just the
    # binary healthy flag).
    anthro_record = report.provider.health.get("anthropic")
    assert anthro_record is not None
    assert secret not in (anthro_record.reason or "")
    assert secret not in (anthro_record.details.get("api_key_env_value") or "")


def test_validation_errors_warnings_are_redacted_in_report(tmp_path: Path) -> None:
    """P1 fix: ``validation.errors`` / ``validation.warnings`` are
    copied from ``validate.validate_run`` output and may include
    the original ``fallback_reason`` in the error message
    (``planner/validate.py:73-79``). Redact these fields too.
    """
    secret = "Bearer token-real-secret-12345678"
    # Inject a secret into fallback_reason; validate_run will quote
    # it in the production-fallback error if env=production +
    # fallback_used=True. We don't run pipeline here; instead craft a
    # minimal run with the right shape and patch validate_run to
    # return an error containing the secret.
    run_dir = _make_minimal_run(
        tmp_path,
        env="production",
        executor_status="pending_manual_approval",
        requested_provider="openai_compatible",
        effective_provider="deterministic",
        fallback_used=True,
        fallback_reason=secret,
    )
    # Patch validate_run to return an error embedding the secret.
    from planner import validate as validate_mod

    def fake_validate(run_dir, *, expected_env=None):
        from planner.validate import ValidationReport

        return ValidationReport(
            ok=False,
            errors=[
                f"Production run used provider fallback ({secret}) — "
                "fail-closed contract violated."
            ],
        )

    original = validate_mod.validate_run
    validate_mod.validate_run = fake_validate
    try:
        report = diagnose_run_dir(run_dir)
    finally:
        validate_mod.validate_run = original

    serialized = json.dumps(report.model_dump(mode="json"))
    assert secret not in serialized
    # And the validation summary itself was redacted.
    for err in report.validation.errors:
        assert secret not in err
    for warn in report.validation.warnings:
        # warn list may be empty for this fixture; guard anyway.
        assert secret not in warn


# ---------- P1.6 fix: redact provider_runtime fields + R8 dev message ----------


def test_provider_runtime_fields_are_redacted_in_report(tmp_path: Path) -> None:
    """P1 fix (Codex manual review round 2): ``provider.runtime.model``,
    ``base_url``, ``api_key_env`` are copied verbatim from
    ``provider_runtime``. A future provider may interpolate tokens
    into any of them (e.g. ``base_url`` with query-string bearer
    tokens, or a model name that contains a leak). Redact all
    three before they reach the report.
    """
    secret_model = "sk-runtime-model-secret-redact-test-12345678"
    secret_url = (
        "https://api.example.com/v1?token=Bearer RUNTIMESECRET-"
        "redact-test-12345678"
    )
    secret_env = (
        "PLANNER_TEST_with_sk-runtime-env-secret-redact-test-12345678"
    )
    run_dir = _make_minimal_run(
        tmp_path,
        env="development",
        provider_runtime={
            "model": secret_model,
            "base_url": secret_url,
            "api_key_env": secret_env,
            "enable_real_model_calls": True,
        },
    )
    report = diagnose_run_dir(run_dir)
    runtime = report.provider.runtime
    assert runtime is not None
    serialized = json.dumps(report.model_dump(mode="json"))

    # Each injected token must NOT appear anywhere in the report.
    assert secret_model not in serialized
    assert "RUNTIMESECRET-redact-test-12345678" not in serialized
    assert "sk-runtime-env-secret-redact-test-12345678" not in serialized

    # And the redacted strings must appear in the runtime fields.
    assert runtime.model and "sk-runtime-model-secret-redact-test" not in runtime.model
    assert runtime.base_url and "RUNTIMESECRET" not in runtime.base_url
    assert runtime.api_key_env and "sk-runtime-env-secret-redact-test" not in runtime.api_key_env


def test_r8_dev_message_redacts_api_key_env_name(tmp_path: Path, monkeypatch) -> None:
    """P1 fix (Codex manual review round 2): R8 dev branch used to
    echo the raw ``api_key_env`` env-var name (e.g.
    ``'PLANNER_OPENAI_API_KEY'``) into the finding message. Env-var
    names are not secrets by convention, but a future provider may
    stuff a token into that field. Run the env-var name through
    the same redact path as everything else to close the exit.

    We use a synthetic env-var name that contains an OpenAI-style
    ``sk-...`` token suffix so the redact regex actually fires,
    demonstrating that the path is exercised end-to-end.
    """
    monkeypatch.delenv("PLANNER_TEST_API_KEY_RUNTIME_LEAK", raising=False)
    run_dir = _make_minimal_run(
        tmp_path,
        env="development",
        provider_runtime={
            "model": "gpt-4",
            "base_url": "https://api.openai.com/v1",
            "api_key_env": (
                "PLANNER_TEST_API_KEY_sk-leak-test-redact-12345678"
            ),
            "enable_real_model_calls": True,
        },
    )
    report = diagnose_run_dir(run_dir)
    matching = [
        f for f in report.findings if f.code == "api_key_env_unset"
    ]
    assert matching, "expected R8 to fire when api_key_env is unset"
    msg = matching[0].message
    # The leak token substring must be redacted.
    assert "sk-leak-test-redact-12345678" not in msg
    # And ``<redacted>`` should appear in the message.
    assert "<redacted>" in msg


# ---------- Internal helpers ----------


def test_translate_validate_report_substring_routing() -> None:
    """P3-1/2/3 fix: pin the current substring-based mapping so a
    future ``validate_run`` message change cannot silently flip a
    dev warning into a "production_fallback_used" error code.

    The contract:

    * errors[] containing "fallback" -> production_fallback_used
    * errors[] containing "source_path" -> script_source_mismatch
    * other errors[] -> validate_ref_error
    * warnings[] containing "env=" or "env mismatch" -> env_mismatch
    * other warnings[] (including the real "missing fallback_used
      flag" warning that contains the word "fallback") ->
      validate_ref_warning, NOT production_fallback_used.
    """
    from planner.agent.diagnose import _translate_validate_report
    from planner.agent.diagnose import DiagnoseFinding, ValidationSummary

    # Real-world warning from planner/validate.py:92-98 — contains
    # "fallback" but is in warnings[], not errors[].
    summary = ValidationSummary(
        ok=True,
        errors=[],
        warnings=[
            "run_summary.json missing fallback_used flag; the run "
            "predates the fallback design.",
        ],
    )
    findings: list[DiagnoseFinding] = []
    _translate_validate_report(summary, findings)
    assert len(findings) == 1
    assert findings[0].code == "validate_ref_warning"
    assert findings[0].severity == "warning"

    # Real-world error from planner/validate.py:73-79 (production+fallback)
    summary2 = ValidationSummary(
        ok=False,
        errors=[
            "Production run used provider fallback "
            "('openai_compatible' -> 'deterministic', reason=None); "
            "production must remain fail-closed and never silently "
            "swap providers."
        ],
        warnings=[],
    )
    findings2: list[DiagnoseFinding] = []
    _translate_validate_report(summary2, findings2)
    assert len(findings2) == 1
    assert findings2[0].code == "production_fallback_used"
    assert findings2[0].severity == "error"


# ---------- R14/R15/R16: bible self-consistency (Phase 3 P2 continuity-audit) ----------


def _write_clean_bibles(run_dir: Path) -> None:
    """Write minimally valid bibles: each entry has a unique id +
    unique Chinese name + non-empty critical visual field.

    Used as the baseline so tests only mutate the field they care
    about (id / name / visual field). Mirrors the shape the real
    pipeline emits.
    """
    (run_dir / "character_bible.json").write_text(
        json.dumps(
            {
                "characters": [
                    {"id": "lin_xia", "name": "林夏",
                     "appearance": "短发女性", "positive_prompt": "p", "negative_prompt": "n"},
                    {"id": "zhang_nan", "name": "张楠",
                     "appearance": "长裙女性", "positive_prompt": "p", "negative_prompt": "n"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (run_dir / "location_bible.json").write_text(
        json.dumps(
            {
                "locations": [
                    {"id": "office", "name": "办公室",
                     "space_layout": "开放工位", "positive_prompt": "p", "negative_prompt": "n"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (run_dir / "prop_bible.json").write_text(
        json.dumps(
            {
                "props": [
                    {"id": "folder", "name": "文件夹",
                     "visual": "蓝色塑料皮", "positive_prompt": "p", "negative_prompt": "n"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _codes(report: DiagnoseReport) -> list:
    return [f.code for f in report.findings]


# ----- R14 character bible -----
def test_r14_character_id_conflict(tmp_path: Path) -> None:
    """R14: same character id with different names -> warning."""
    run_dir = _make_minimal_run(tmp_path)
    (run_dir / "character_bible.json").write_text(
        json.dumps(
            {
                "characters": [
                    {"id": "lin_xia", "name": "林夏",
                     "appearance": "x", "positive_prompt": "p", "negative_prompt": "n"},
                    {"id": "lin_xia", "name": "林夏_别名",
                     "appearance": "x", "positive_prompt": "p", "negative_prompt": "n"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_clean_bibles_for_locations_and_props(run_dir)
    report = diagnose_run_dir(run_dir)
    codes = _codes(report)
    assert "character_bible_internal_id_conflict" in codes
    f = next(x for x in report.findings if x.code == "character_bible_internal_id_conflict")
    assert f.severity == "warning"
    assert "lin_xia" in f.message and "林夏" in f.message and "林夏_别名" in f.message


def test_r14_character_name_conflict(tmp_path: Path) -> None:
    """R14: same character name with different ids -> warning."""
    run_dir = _make_minimal_run(tmp_path)
    (run_dir / "character_bible.json").write_text(
        json.dumps(
            {
                "characters": [
                    {"id": "lin_xia", "name": "林夏",
                     "appearance": "x", "positive_prompt": "p", "negative_prompt": "n"},
                    {"id": "lin_xia_alt", "name": "林夏",
                     "appearance": "x", "positive_prompt": "p", "negative_prompt": "n"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_clean_bibles_for_locations_and_props(run_dir)
    report = diagnose_run_dir(run_dir)
    codes = _codes(report)
    assert "character_bible_internal_name_conflict" in codes
    f = next(x for x in report.findings if x.code == "character_bible_internal_name_conflict")
    assert f.severity == "warning"
    assert "林夏" in f.message and "lin_xia" in f.message and "lin_xia_alt" in f.message


def test_r14_character_missing_visual_field(tmp_path: Path) -> None:
    """R14: character entry with all critical visual fields empty -> warning."""
    run_dir = _make_minimal_run(tmp_path)
    (run_dir / "character_bible.json").write_text(
        json.dumps(
            {
                "characters": [
                    {"id": "lin_xia", "name": "林夏",
                     "appearance": "", "positive_prompt": "", "negative_prompt": ""},
                    {"id": "zhang_nan", "name": "张楠",
                     "appearance": "长裙", "positive_prompt": "p", "negative_prompt": "n"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_clean_bibles_for_locations_and_props(run_dir)
    report = diagnose_run_dir(run_dir)
    codes = _codes(report)
    assert "character_bible_missing_visual_field" in codes
    f = next(x for x in report.findings if x.code == "character_bible_missing_visual_field")
    assert f.severity == "warning"
    assert "lin_xia" in f.message
    assert "appearance" in f.message


def test_r14_skip_when_character_bible_missing(tmp_path: Path) -> None:
    """R14: no character_bible.json -> rule skipped, no R14 finding
    (mirror R12 partial_run_missing_artifact grace pattern)."""
    run_dir = _make_minimal_run(tmp_path)
    # No bibles at all. R12 will fire (partial_run_missing_artifact);
    # R14/R15/R16 must NOT fire because the bibles are absent.
    report = diagnose_run_dir(run_dir)
    codes = _codes(report)
    assert not any(c.startswith("character_bible_") for c in codes)
    assert not any(c.startswith("location_bible_") for c in codes)
    assert not any(c.startswith("prop_bible_") for c in codes)


# ----- R15 location bible -----
def test_r15_location_id_conflict(tmp_path: Path) -> None:
    run_dir = _make_minimal_run(tmp_path)
    (run_dir / "location_bible.json").write_text(
        json.dumps(
            {
                "locations": [
                    {"id": "office", "name": "办公室",
                     "space_layout": "x", "positive_prompt": "p", "negative_prompt": "n"},
                    {"id": "office", "name": "公司",
                     "space_layout": "x", "positive_prompt": "p", "negative_prompt": "n"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_clean_bibles_for_chars_and_props(run_dir)
    report = diagnose_run_dir(run_dir)
    codes = _codes(report)
    assert "location_bible_internal_id_conflict" in codes
    f = next(x for x in report.findings if x.code == "location_bible_internal_id_conflict")
    assert f.severity == "warning"
    assert "office" in f.message and "办公室" in f.message and "公司" in f.message


def test_r15_location_name_conflict(tmp_path: Path) -> None:
    run_dir = _make_minimal_run(tmp_path)
    (run_dir / "location_bible.json").write_text(
        json.dumps(
            {
                "locations": [
                    {"id": "office", "name": "办公室",
                     "space_layout": "x", "positive_prompt": "p", "negative_prompt": "n"},
                    {"id": "office_alt", "name": "办公室",
                     "space_layout": "x", "positive_prompt": "p", "negative_prompt": "n"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_clean_bibles_for_chars_and_props(run_dir)
    report = diagnose_run_dir(run_dir)
    codes = _codes(report)
    assert "location_bible_internal_name_conflict" in codes


def test_r15_location_missing_visual_field(tmp_path: Path) -> None:
    run_dir = _make_minimal_run(tmp_path)
    (run_dir / "location_bible.json").write_text(
        json.dumps(
            {
                "locations": [
                    {"id": "office", "name": "办公室",
                     "space_layout": "", "positive_prompt": "", "negative_prompt": ""},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_clean_bibles_for_chars_and_props(run_dir)
    report = diagnose_run_dir(run_dir)
    codes = _codes(report)
    assert "location_bible_missing_visual_field" in codes
    f = next(x for x in report.findings if x.code == "location_bible_missing_visual_field")
    assert "space_layout" in f.message


# ----- R16 prop bible -----
def test_r16_prop_id_conflict(tmp_path: Path) -> None:
    run_dir = _make_minimal_run(tmp_path)
    (run_dir / "prop_bible.json").write_text(
        json.dumps(
            {
                "props": [
                    {"id": "folder", "name": "文件夹",
                     "visual": "蓝色", "positive_prompt": "p", "negative_prompt": "n"},
                    {"id": "folder", "name": "档案夹",
                     "visual": "蓝色", "positive_prompt": "p", "negative_prompt": "n"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_clean_bibles_for_chars_and_locs(run_dir)
    report = diagnose_run_dir(run_dir)
    codes = _codes(report)
    assert "prop_bible_internal_id_conflict" in codes


def test_r16_prop_name_conflict(tmp_path: Path) -> None:
    run_dir = _make_minimal_run(tmp_path)
    (run_dir / "prop_bible.json").write_text(
        json.dumps(
            {
                "props": [
                    {"id": "folder", "name": "文件夹",
                     "visual": "蓝色", "positive_prompt": "p", "negative_prompt": "n"},
                    {"id": "folder_alt", "name": "文件夹",
                     "visual": "蓝色", "positive_prompt": "p", "negative_prompt": "n"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_clean_bibles_for_chars_and_locs(run_dir)
    report = diagnose_run_dir(run_dir)
    codes = _codes(report)
    assert "prop_bible_internal_name_conflict" in codes


def test_r16_prop_missing_visual_field(tmp_path: Path) -> None:
    run_dir = _make_minimal_run(tmp_path)
    (run_dir / "prop_bible.json").write_text(
        json.dumps(
            {
                "props": [
                    {"id": "folder", "name": "文件夹",
                     "visual": "", "positive_prompt": "", "negative_prompt": ""},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_clean_bibles_for_chars_and_locs(run_dir)
    report = diagnose_run_dir(run_dir)
    codes = _codes(report)
    assert "prop_bible_missing_visual_field" in codes
    f = next(x for x in report.findings if x.code == "prop_bible_missing_visual_field")
    assert "visual" in f.message


# ----- Happy path: all clean bibles -> no R14/R15/R16 finding -----
def test_clean_bibles_no_self_consistency_finding(tmp_path: Path) -> None:
    run_dir = _make_minimal_run(tmp_path)
    _write_clean_bibles(run_dir)
    report = diagnose_run_dir(run_dir)
    codes = _codes(report)
    assert not any(c.startswith(("character_bible_", "location_bible_", "prop_bible_")) for c in codes)


# ----- Helpers used by R14/R15/R16 tests -----
def _write_clean_bibles_for_locations_and_props(run_dir: Path) -> None:
    """Write only location_bible + prop_bible (clean)."""
    (run_dir / "location_bible.json").write_text(
        json.dumps({"locations": [{"id": "office", "name": "办公室",
            "space_layout": "x", "positive_prompt": "p", "negative_prompt": "n"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (run_dir / "prop_bible.json").write_text(
        json.dumps({"props": [{"id": "folder", "name": "文件夹",
            "visual": "x", "positive_prompt": "p", "negative_prompt": "n"}]}, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_clean_bibles_for_chars_and_props(run_dir: Path) -> None:
    (run_dir / "character_bible.json").write_text(
        json.dumps({"characters": [{"id": "lin_xia", "name": "林夏",
            "appearance": "x", "positive_prompt": "p", "negative_prompt": "n"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (run_dir / "prop_bible.json").write_text(
        json.dumps({"props": [{"id": "folder", "name": "文件夹",
            "visual": "x", "positive_prompt": "p", "negative_prompt": "n"}]}, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_clean_bibles_for_chars_and_locs(run_dir: Path) -> None:
    (run_dir / "character_bible.json").write_text(
        json.dumps({"characters": [{"id": "lin_xia", "name": "林夏",
            "appearance": "x", "positive_prompt": "p", "negative_prompt": "n"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (run_dir / "location_bible.json").write_text(
        json.dumps({"locations": [{"id": "office", "name": "办公室",
            "space_layout": "x", "positive_prompt": "p", "negative_prompt": "n"}]}, ensure_ascii=False),
        encoding="utf-8",
    )


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
