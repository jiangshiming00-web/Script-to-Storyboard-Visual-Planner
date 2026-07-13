"""Read-only diagnostic engine for the planner agent.

Phase 3 P1: 13 diagnostic rules produce a :class:`DiagnoseReport`
without mutating any file, calling any LLM, or executing any
shell command. The rules are split into:

* **R10/R11** (missing/corrupted run_summary): handled at the
  engine entry; subsequent rules depend on a readable summary.
* **R1/R5/R6** (production+fallback, env_mismatch,
  script_source_mismatch): delegated to
  :func:`planner.validate.validate_run` (which already implements
  these correctly) and translated into findings with stable
  ``code`` values.
* **R2/R3/R4/R7/R8/R9** (independent agent checks): pure
  rule-based, no LLM, deterministic.
* **R12/R13** (partial-run / counts mismatch): artifact-level
  inventory and shape checks.

The engine is **graceful degradation**: every error path returns
a partial report rather than raising. The CLI layer is responsible
for converting the resulting ``status`` into an exit code
(``errors`` → 1, ``warnings``/``ok`` → 0).

Hard rules:

* Never write files (read-only contract).
* Never call subprocess or shell.
* Never import LLM SDKs (no ``openai`` / ``anthropic``).
* Never echo ``api_key_value`` into any finding / summary /
  tool_invocations message. Run :func:`redact_secrets_text` on
  every string that flows into the report.
* Never inspect or mutate GUI's in-memory :class:`RunRegistry`
  (the agent CLI process is a separate address space).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from planner.exceptions import ScriptReadError
from planner.validate import validate_run as _validate_run

from .redact import redact_secrets_text
from .tools import list_artifacts as _tools_list_artifacts, read_run_summary

# ---------- Pydantic models ----------


Severity = Literal["info", "warning", "error"]
ImplementationStatus = Literal["full", "partial", "not_implemented"]
ReportStatus = Literal["ok", "warnings", "errors"]


class EvidenceRef(BaseModel):
    """Pointer to a specific artifact + location for a finding."""

    artifact: str  # e.g. "run_summary.json"
    path: str  # relative to run_dir
    locator: str  # JSONPath-ish ($.foo.bar) or "$" for whole file


class DiagnoseFinding(BaseModel):
    """A single rule output.

    ``code`` is a stable snake_case identifier (so third-party
    tooling can aggregate findings by code). ``message`` is a
    Chinese one-liner suitable for direct display.
    """

    severity: Severity
    code: str
    message: str
    evidence: List[EvidenceRef] = Field(default_factory=list)


class ToolInvocation(BaseModel):
    """Record of one tool call inside diagnose_run_dir.

    The P1 ``diagnose`` command always populates these so the
    harness ``live_cross_check`` can verify artifact access
    patterns match :data:`planner.agent.tools.TOOL_ARTIFACT_MAP`.
    """

    tool: str
    ok: bool
    artifact_refs: List[str] = Field(default_factory=list)
    bytes_read: int = 0


class HealthRecord(BaseModel):
    """One provider's health at run time (mirrors ProviderHealth)."""

    healthy: bool
    reason: Optional[str] = None
    details: Dict[str, str] = Field(default_factory=dict)


class ProviderRuntimeSummary(BaseModel):
    """Resolved runtime settings as recorded in run_summary.

    Note: ``api_key_env`` is the env var NAME only; never the
    value. ``enable_real_model_calls`` is a real bool (Pydantic
    bool field), distinct from ``provider_health.*.details``
    which use string sentinels by design.
    """

    model: str
    base_url: str
    api_key_env: str
    enable_real_model_calls: bool


class ProviderSummary(BaseModel):
    """Top-level provider audit fields + health + runtime."""

    requested: Optional[str] = None
    effective: Optional[str] = None
    fallback_used: Optional[bool] = None
    fallback_reason: Optional[str] = None
    health: Dict[str, HealthRecord] = Field(default_factory=dict)
    runtime: Optional[ProviderRuntimeSummary] = None
    audit_notes: List[str] = Field(default_factory=list)


class ValidationSummary(BaseModel):
    """Subset of :class:`planner.validate.ValidationReport` exposed
    in the diagnose report. Errors / warnings are strings (not
    objects) — the agent translates them into findings, keeping
    the originals here for transparency."""

    ok: bool = False
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    stats: Dict[str, int] = Field(default_factory=dict)


class DiagnoseReport(BaseModel):
    """Top-level diagnostic report for one run / batch.

    ``status`` is derived from findings (errors → "errors",
    any warning → "warnings", else "ok"). ``implementation_status``
    is independent — a stub command returns ``"not_implemented"``
    with ``status="ok"`` so JSON consumers can branch on it.
    """

    run_dir: str
    run_id: Optional[str] = None
    env: Optional[str] = None
    expected_env: Optional[str] = None
    status: ReportStatus = "ok"
    implementation_status: ImplementationStatus = "full"
    diagnose_version: Literal["1.0"] = "1.0"
    summary: str = ""
    counts: Dict[str, int] = Field(default_factory=dict)
    provider: ProviderSummary = Field(default_factory=ProviderSummary)
    validation: ValidationSummary = Field(default_factory=ValidationSummary)
    findings: List[DiagnoseFinding] = Field(default_factory=list)
    tool_invocations: List[ToolInvocation] = Field(default_factory=list)
    generated_at: str = ""

    def derive_status(self) -> "DiagnoseReport":
        """Recompute ``status`` from current findings. Returns self
        so it can be used as ``report.derive_status()``."""
        if any(f.severity == "error" for f in self.findings):
            self.status = "errors"
        elif any(f.severity == "warning" for f in self.findings):
            self.status = "warnings"
        else:
            self.status = "ok"
        return self


# ---------- Rule helpers ----------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _evidence_run_summary(
    run_dir: Path, locator: str = "$"
) -> EvidenceRef:
    return EvidenceRef(
        artifact="run_summary.json",
        path=str(run_dir / "run_summary.json"),
        locator=locator,
    )


def _safe_text(value: Any) -> str:
    """Stringify + redact. ``None`` / empty -> empty string."""
    if value is None:
        return ""
    return redact_secrets_text(str(value))


# ---------- Validation report translation (R1/R5/R6) ----------


def _translate_validate_report(
    report_summary: ValidationSummary,
    findings: List[DiagnoseFinding],
) -> None:
    """Translate ``validate.validate_run`` errors / warnings into
    findings with stable ``code`` values.

    Mapping (Phase 3 P1 minimum stable codes):

    * ``production_fallback_used``  -- errors[] mentioning "fallback"
    * ``script_source_mismatch``    -- errors[] mentioning "source_path"
    * ``validate_ref_error``        -- any other errors[] entry
    * ``env_mismatch``               -- warnings[] mentioning "env="
      or "env mismatch"
    * ``validate_ref_warning``      -- any other warnings[] entry

    Note on substring fragility: this mapping is a Phase 3 P1 stub.
    A future ``validate_run`` error message containing the literal
    "fallback" in a non-production-fallback context (e.g. "missing
    fallback_used flag" — which already exists in
    ``planner/validate.py:92-98`` as a WARNING) would normally be
    mis-routed, but because the matcher only runs on the ``errors[]``
    list (production is the only context where ``fallback_used=True``
    becomes an error in ``planner/validate.py:73-79``), the actual
    runtime behavior is correct. Pinned by
    ``test_translate_validate_report_substring_routing`` in
    ``tests/test_agent_diagnose.py``. Phase 3 P2 may replace this
    with structured fields on ``ValidationReport`` if validate_run
    grows more error categories.
    """
    for err in report_summary.errors:
        err_lower = err.lower()
        if "fallback" in err_lower:
            findings.append(
                DiagnoseFinding(
                    severity="error",
                    code="production_fallback_used",
                    message=_safe_text(err),
                )
            )
        elif "source_path" in err_lower or "source path" in err_lower:
            findings.append(
                DiagnoseFinding(
                    severity="error",
                    code="script_source_mismatch",
                    message=_safe_text(err),
                )
            )
        else:
            findings.append(
                DiagnoseFinding(
                    severity="error",
                    code="validate_ref_error",
                    message=_safe_text(err),
                )
            )

    for warn in report_summary.warnings:
        warn_lower = warn.lower()
        if "env=" in warn_lower or "env mismatch" in warn_lower:
            findings.append(
                DiagnoseFinding(
                    severity="warning",
                    code="env_mismatch",
                    message=_safe_text(warn),
                )
            )
        else:
            findings.append(
                DiagnoseFinding(
                    severity="warning",
                    code="validate_ref_warning",
                    message=_safe_text(warn),
                )
            )


# ---------- 13 diagnostic rules ----------


def _rule_r2_dev_fallback_used(
    summary: Dict[str, Any],
    findings: List[DiagnoseFinding],
    run_dir: Path,
) -> None:
    """R2: dev run used provider fallback → warning (not error)."""
    if (
        summary.get("env") == "development"
        and summary.get("fallback_used") is True
    ):
        findings.append(
            DiagnoseFinding(
                severity="warning",
                code="dev_fallback_used",
                message=(
                    "开发环境使用了 provider fallback "
                    f"({summary.get('requested_provider')!r} → "
                    f"{summary.get('effective_provider')!r})，"
                    f"原因：{_safe_text(summary.get('fallback_reason'))}"
                ),
                evidence=[_evidence_run_summary(run_dir, "$.fallback_used")],
            )
        )


def _rule_r3_all_providers_unhealthy(
    health: Dict[str, Any],
    findings: List[DiagnoseFinding],
    run_dir: Path,
) -> None:
    """R3: every provider in provider_health is unhealthy → warning."""
    if not health:
        return
    all_unhealthy = all(
        isinstance(h, dict) and h.get("healthy") is False for h in health.values()
    )
    if not all_unhealthy:
        return
    names = ", ".join(sorted(health.keys()))
    findings.append(
        DiagnoseFinding(
            severity="warning",
            code="all_providers_unhealthy",
            message=(
                f"所有已配置 provider 均不健康 ({names})；"
                "本次 run 实际跑到 effective_provider 是 fallback / deterministic 的结果。"
            ),
            evidence=[_evidence_run_summary(run_dir, "$.provider_health")],
        )
    )


def _rule_r4_executor_tool_hardcoded(
    executor_tasks: Optional[Dict[str, Any]],
    findings: List[DiagnoseFinding],
    run_dir: Path,
) -> None:
    """R4: any executor task has tool != None → error (red line)."""
    if not isinstance(executor_tasks, dict):
        return
    tasks = executor_tasks.get("tasks") or []
    bad: List[str] = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        tool = t.get("tool")
        if tool is not None and tool != "":
            bad.append(f"{t.get('id') or '?'} -> {tool!r}")
    if bad:
        findings.append(
            DiagnoseFinding(
                severity="error",
                code="executor_tool_hardcoded",
                message=(
                    "executor_tasks.json 中检测到硬编码 tool 字段"
                    "（红线 #3：核心 planner 不写死 executor 工具）："
                    + "; ".join(bad)
                ),
                evidence=[
                    EvidenceRef(
                        artifact="executor_tasks.json",
                        path=str(run_dir / "executor_tasks.json"),
                        locator="$.tasks[*].tool",
                    )
                ],
            )
        )


def _rule_r7_production_executor_status_wrong(
    summary: Dict[str, Any],
    findings: List[DiagnoseFinding],
    run_dir: Path,
) -> None:
    """R7: production run but executor_status != pending_manual_approval → error."""
    if summary.get("env") != "production":
        return
    status = summary.get("executor_status")
    if status != "pending_manual_approval":
        findings.append(
            DiagnoseFinding(
                severity="error",
                code="production_executor_status_wrong",
                message=(
                    "production run 的 executor_status 不是 "
                    f"pending_manual_approval（实际={status!r}）；"
                    "这违反红线 #1，请人工复核。"
                ),
                evidence=[_evidence_run_summary(run_dir, "$.executor_status")],
            )
        )


def _rule_r8_api_key_env_unset(
    summary: Dict[str, Any],
    findings: List[DiagnoseFinding],
    run_dir: Path,
) -> None:
    """R8: provider_runtime.api_key_env is set but env var is empty → warning.

    In production we sanitize the message (avoid echoing the env
    var name into stderr / JSON) to reduce information leakage in
    shared environments.
    """
    runtime = summary.get("provider_runtime") or {}
    api_key_env = runtime.get("api_key_env")
    if not api_key_env:
        return
    env_value = os.environ.get(api_key_env)
    if env_value:
        return
    is_prod = summary.get("env") == "production"
    if is_prod:
        message = (
            "运行时声明的 api_key_env 在当前进程未设置；"
            "production 下已 sanitized 环境变量名，避免日志泄露。"
        )
    else:
        message = (
            f"provider_runtime.api_key_env={api_key_env!r} "
            "在当前进程未设置；如需真实调用，请设置该环境变量。"
        )
    findings.append(
        DiagnoseFinding(
            severity="warning",
            code="api_key_env_unset",
            message=message,
            evidence=[_evidence_run_summary(run_dir, "$.provider_runtime.api_key_env")],
        )
    )


def _rule_r9_real_calls_disabled_but_not_deterministic(
    summary: Dict[str, Any],
    findings: List[DiagnoseFinding],
    run_dir: Path,
) -> None:
    """R9: enable_real_model_calls=False but effective_provider != deterministic → warning."""
    runtime = summary.get("provider_runtime") or {}
    effective = summary.get("effective_provider")
    if not runtime:
        return
    if runtime.get("enable_real_model_calls") is False and effective != "deterministic":
        findings.append(
            DiagnoseFinding(
                severity="warning",
                code="real_calls_disabled_but_not_deterministic",
                message=(
                    "provider_runtime.enable_real_model_calls=False，"
                    f"但 effective_provider={effective!r}（非 deterministic）；"
                    "请确认这是预期配置。"
                ),
                evidence=[_evidence_run_summary(run_dir, "$.provider_runtime")],
            )
        )


def _rule_r12_partial_run_missing_artifact(
    summary: Dict[str, Any],
    run_dir: Path,
    findings: List[DiagnoseFinding],
) -> None:
    """R12: run_summary.json says run is 'done' but at least one
    of the 9 core artifacts is missing → warning (not error: we
    don't presume the user's intent).

    Agents must NOT delete missing artifacts, NOT infer them, NOT
    rewrite the run. The diagnosis only enumerates the gap.
    """
    executor_status = summary.get("executor_status") or ""
    # The pipeline emits "pending" in dev and "pending_manual_approval"
    # in production. Anything else ("failed" / "" / missing) does NOT
    # trigger R12 because we can't claim a half-run is broken.
    if executor_status not in {"pending", "pending_manual_approval"}:
        return
    existing = set(_tools_list_artifacts(run_dir))
    # 9 core artifacts (excludes executor_tasks + run_summary which
    # are pipeline-emitted late; the run_summary is by definition
    # present here).
    core = {
        "script_parse.json",
        "character_bible.json",
        "location_bible.json",
        "prop_bible.json",
        "story_beats.json",
        "shot_list.json",
        "image_prompts.json",
        "video_prompts.json",
        "asset_manifest.json",
    }
    missing = sorted(core - existing)
    if missing:
        # Single aggregate evidence pointing at run_summary.json; the
        # actual missing artifacts are listed in ``message``. Avoid
        # emitting one evidence per missing file because the diagnostic
        # surface is meant to be 1 finding == 1 actionable issue.
        findings.append(
            DiagnoseFinding(
                severity="warning",
                code="partial_run_missing_artifact",
                message=(
                    "run_summary.json 标记 run 已完成，但下列核心 artifact 缺失："
                    + ", ".join(missing)
                    + "。Agent 不下结论，请人工复核是否需要重跑。"
                ),
                evidence=[
                    EvidenceRef(
                        artifact="run_summary.json",
                        path=str(run_dir / "run_summary.json"),
                        locator="$.counts",
                    )
                ],
            )
        )


def _rule_r13_counts_mismatch(
    summary: Dict[str, Any],
    findings: List[DiagnoseFinding],
    run_dir: Path,
) -> None:
    """R13: shots / image_prompts / video_prompts counts don't match.

    We check 2 invariants:

    * image_prompts_count == shot_list.shots count (when both present)
    * video_prompts_count == shot_list.shots count (when both present)
    """
    counts = summary.get("counts") or {}
    # Note: counts come from run_summary, which is filled by pipeline.
    # We do NOT load shot_list / image_prompts here — the validate_run
    # path already covers shot-level ref errors via validate_ref_error.
    shots = counts.get("shots")
    image_count = counts.get("image_prompts")
    video_count = counts.get("video_prompts")
    if (
        isinstance(shots, int)
        and isinstance(image_count, int)
        and shots != image_count
    ):
        findings.append(
            DiagnoseFinding(
                severity="warning",
                code="image_prompts_count_mismatch",
                message=(
                    f"counts.shots={shots} 与 counts.image_prompts={image_count} 不一致；"
                    "validate_run 已逐 shot 校验，但聚合视角仍发现差异，请人工复核。"
                ),
                evidence=[_evidence_run_summary(run_dir, "$.counts")],
            )
        )
    if (
        isinstance(shots, int)
        and isinstance(video_count, int)
        and shots != video_count
    ):
        findings.append(
            DiagnoseFinding(
                severity="warning",
                code="video_prompts_count_mismatch",
                message=(
                    f"counts.shots={shots} 与 counts.video_prompts={video_count} 不一致。"
                ),
                evidence=[_evidence_run_summary(run_dir, "$.counts")],
            )
        )


# ---------- Entry point ----------


def diagnose_run_dir(
    run_dir: Path,
    *,
    expected_env: Optional[str] = None,
) -> DiagnoseReport:
    """Produce a diagnostic report for ``run_dir``.

    Returns a partial report even on catastrophic failure (missing
    / corrupted run_summary). Never raises for predictable data
    problems. Only ``OSError`` from ``Path`` operations may
    propagate.
    """
    run_dir = Path(run_dir)
    report = DiagnoseReport(
        run_dir=str(run_dir),
        expected_env=expected_env,
        generated_at=_now_iso(),
        tool_invocations=[],
    )

    # ----- Step 0: load run_summary.json (R10 / R11) -----
    try:
        summary = read_run_summary(run_dir)
    except KeyError as exc:
        # R10 (missing) vs R11 (corrupted): the KeyError message has
        # the form ``run_summary.json missing or corrupted: <err>``
        # where ``<err>`` is the underlying cause. We branch on the
        # cause to distinguish:
        #   * "file not found" or "OS error" -> R10 (missing)
        #   * "invalid JSON: ..." -> R11 (corrupted)
        msg = str(exc)
        if "not found" in msg or "file not found" in msg:
            code = "missing_run_summary"
            severity: Severity = "error"
            message = (
                "run_summary.json 不存在；agent 无法审计本次 run。"
                "请确认 run_dir 路径正确，且 pipeline.run() 已成功完成。"
            )
        else:
            code = "corrupted_run_summary"
            severity = "error"
            message = (
                f"run_summary.json 损坏（{_safe_text(msg)}）；agent 将输出最小化报告。"
            )
        report.findings.append(
            DiagnoseFinding(
                severity=severity,
                code=code,
                message=message,
                evidence=[
                    EvidenceRef(
                        artifact="run_summary.json",
                        path=str(run_dir / "run_summary.json"),
                        locator="$",
                    )
                ],
            )
        )
        report.tool_invocations.append(
            ToolInvocation(
                tool="read_run_summary",
                ok=False,
                artifact_refs=["run_summary.json"],
                bytes_read=0,
            )
        )
        report.summary = _build_summary_zh(report)
        return report.derive_status()

    report.tool_invocations.append(
        ToolInvocation(
            tool="read_run_summary",
            ok=True,
            artifact_refs=["run_summary.json"],
            bytes_read=0,  # size of file not tracked at this granularity
        )
    )
    report.run_id = summary.get("run_id")
    report.env = summary.get("env")

    # ----- Step 1: fill provider summary -----
    provider_health_raw = summary.get("provider_health") or {}
    health_records: Dict[str, HealthRecord] = {}
    for name, h in provider_health_raw.items():
        if isinstance(h, dict):
            # P1 fix: redact every text field copied from the artifact
            # before it reaches the report. ``reason`` and every
            # ``details`` value can contain leaked tokens if a future
            # provider emits them (the existing skeleton adapters don't,
            # but defense in depth is mandatory for the agent output
            # surface — the same rationale as redact on findings).
            health_records[name] = HealthRecord(
                healthy=bool(h.get("healthy")),
                reason=_safe_text(h.get("reason")),
                details={
                    k: redact_secrets_text(str(v))
                    for k, v in (h.get("details") or {}).items()
                },
            )
    runtime_raw = summary.get("provider_runtime")
    runtime_summary: Optional[ProviderRuntimeSummary] = None
    if isinstance(runtime_raw, dict):
        runtime_summary = ProviderRuntimeSummary(
            model=str(runtime_raw.get("model") or ""),
            base_url=str(runtime_raw.get("base_url") or ""),
            api_key_env=str(runtime_raw.get("api_key_env") or ""),
            enable_real_model_calls=bool(runtime_raw.get("enable_real_model_calls")),
        )
    report.provider = ProviderSummary(
        requested=summary.get("requested_provider"),
        effective=summary.get("effective_provider"),
        fallback_used=summary.get("fallback_used"),
        fallback_reason=_safe_text(summary.get("fallback_reason")),
        health=health_records,
        runtime=runtime_summary,
    )

    # ----- Step 2: fill counts -----
    counts = summary.get("counts") or {}
    if isinstance(counts, dict):
        report.counts = {k: int(v) for k, v in counts.items() if isinstance(v, (int, float))}

    # ----- Step 3: load executor_tasks.json for R4 -----
    executor_tasks_payload: Optional[Dict[str, Any]] = None
    executor_path = run_dir / "executor_tasks.json"
    if executor_path.is_file():
        try:
            import json

            with executor_path.open("r", encoding="utf-8") as f:
                executor_tasks_payload = json.load(f)
            report.tool_invocations.append(
                ToolInvocation(
                    tool="read_artifact",
                    ok=True,
                    artifact_refs=["executor_tasks.json"],
                    bytes_read=executor_path.stat().st_size,
                )
            )
        except (OSError, ValueError) as exc:
            report.findings.append(
                DiagnoseFinding(
                    severity="warning",
                    code="executor_tasks_unreadable",
                    message=(
                        "executor_tasks.json 读取失败，"
                        f"R4 executor_tool_hardcoded 无法判断：{_safe_text(exc)}"
                    ),
                    evidence=[
                        EvidenceRef(
                            artifact="executor_tasks.json",
                            path=str(executor_path),
                            locator="$",
                        )
                    ],
                )
            )

    # ----- Step 4: independent rules (R2/R3/R4/R7/R8/R9/R12/R13) -----
    _rule_r2_dev_fallback_used(summary, report.findings, run_dir)
    _rule_r3_all_providers_unhealthy(provider_health_raw, report.findings, run_dir)
    _rule_r4_executor_tool_hardcoded(
        executor_tasks_payload, report.findings, run_dir
    )
    _rule_r7_production_executor_status_wrong(summary, report.findings, run_dir)
    _rule_r8_api_key_env_unset(summary, report.findings, run_dir)
    _rule_r9_real_calls_disabled_but_not_deterministic(summary, report.findings, run_dir)
    _rule_r12_partial_run_missing_artifact(summary, run_dir, report.findings)
    _rule_r13_counts_mismatch(summary, report.findings, run_dir)

    # ----- Step 5: delegate to validate_run (R1/R5/R6 + cross-ref) -----
    try:
        vrep = _validate_run(run_dir, expected_env=expected_env)
        report.validation = ValidationSummary(
            ok=bool(vrep.ok),
            errors=[_safe_text(e) for e in vrep.errors],
            warnings=[_safe_text(w) for w in vrep.warnings],
            stats=dict(vrep.stats),
        )
        report.tool_invocations.append(
            ToolInvocation(
                tool="validate_run",
                ok=True,
                artifact_refs=["run_summary.json", "script_parse.json"],
                bytes_read=0,
            )
        )
        _translate_validate_report(report.validation, report.findings)
    except ScriptReadError as exc:
        # validate_run internally calls load_run which fails fast
        # on missing core artifacts. Surface as warning, not error:
        # we already have run_summary.json so R12 should fire if
        # applicable; this is a deeper structural issue.
        report.findings.append(
            DiagnoseFinding(
                severity="warning",
                code="validate_run_crashed",
                message=(
                    "validate_run 因核心 artifact 缺失失败（"
                    f"{_safe_text(exc)}）；"
                    "R1/R5/R6/R12 可能不完整。"
                ),
                evidence=[
                    EvidenceRef(
                        artifact="(multiple)",
                        path=str(run_dir),
                        locator="$",
                    )
                ],
            )
        )
        report.tool_invocations.append(
            ToolInvocation(
                tool="validate_run",
                ok=False,
                artifact_refs=[],
                bytes_read=0,
            )
        )

    # ----- Step 6: derive status + Chinese summary -----
    report.summary = _build_summary_zh(report)
    return report.derive_status()


# ---------- Chinese summary ----------


def _build_summary_zh(report: DiagnoseReport) -> str:
    """Build a 1-3 sentence Chinese summary of the report.

    Tone: factual, no emojis, no exclamation marks. Suitable for
    both human reading and downstream tooling that parses it.
    """
    lines: List[str] = []
    if report.run_id is None and report.env is None:
        return (
            "未能读取 run_summary.json；"
            f"诊断只报告了 {len(report.findings)} 条 finding（见 findings 列表）。"
        )
    env_label = report.env or "未知"
    rid = report.run_id or "?"
    counts_str = ""
    if report.counts:
        shots = report.counts.get("shots")
        if shots is not None:
            counts_str = f"，共 {shots} 个镜头"
    lines.append(f"run {rid}（env={env_label}）{counts_str}。")

    findings_summary: List[str] = []
    n_err = sum(1 for f in report.findings if f.severity == "error")
    n_warn = sum(1 for f in report.findings if f.severity == "warning")
    if n_err:
        findings_summary.append(f"{n_err} 条 error")
    if n_warn:
        findings_summary.append(f"{n_warn} 条 warning")
    if findings_summary:
        lines.append("本次诊断发现 " + " 和 ".join(findings_summary) + "。")
    elif report.status == "ok":
        lines.append("未发现异常。")

    # Highlight production fallback if R1 fires
    for f in report.findings:
        if f.code == "production_fallback_used":
            lines.append(
                "[RED LINE] production run 触发了 provider fallback；"
                "这是红线违规，请立即人工复核。"
            )
            break
    # Highlight hardcoded tool
    for f in report.findings:
        if f.code == "executor_tool_hardcoded":
            lines.append(
                "[RED LINE] executor_tasks.json 中检测到硬编码 tool 字段；"
                "违反核心 planner 不写死 executor 工具的红线。"
            )
            break

    return "".join(lines)


# ---------- Stub builder (review-run / review-batch P1 placeholder) ----------


def build_not_implemented_report(
    *, kind: str, target: str
) -> DiagnoseReport:
    """Build a placeholder :class:`DiagnoseReport` for stub commands
    (``review-run`` / ``review-batch``).

    The stub:

    * has ``status="ok"`` (no actual work was done → no errors),
    * has ``implementation_status="not_implemented"``,
    * records a single info-level finding so JSON consumers can
      detect "this command did nothing",
    * records an EMPTY ``tool_invocations`` list — the stub
      must NOT pretend to have read anything.
    * sets ``env="production"`` by default so the
      ``--write-report`` policy's run_env fallback (``cli.py``)
      doesn't surprise the operator with a silent rc=2 refusal
      on a stub that never read anything. The CLI emits an
      explicit stderr INFO documenting the default.

    Returned object is suitable for both stdout JSON and
    ``--write-report`` paths.
    """
    return DiagnoseReport(
        run_dir=target,
        env="production",  # explicit so write-report policy is transparent
        implementation_status="not_implemented",
        summary=(
            f"agent {kind} 尚未在 Phase 3 P1 实现；"
            "请改用 `planner agent diagnose` 或 `planner validate`。"
        ),
        findings=[
            DiagnoseFinding(
                severity="info",
                code="not_implemented_in_p1",
                message=f"agent {kind} 在 Phase 3 P1 仅为占位；实际诊断请用 diagnose。",
            )
        ],
        tool_invocations=[],  # critical: stub does no real reads
        generated_at=_now_iso(),
    )
