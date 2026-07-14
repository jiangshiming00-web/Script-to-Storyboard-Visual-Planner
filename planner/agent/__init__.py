"""Planner product agent (Phase 3 P1).

The agent is a **read-only** diagnostic helper that surfaces
problems with completed runs / batches without ever writing
anything back (except via the explicit ``--write-report`` opt-in
on the CLI). It does **not** call any LLM, does not execute any
shell command, and does not save API keys.

Phase 3 P1 exposes ``planner agent diagnose`` (fully implemented).
Phase 3 P2 promotes ``planner agent review-run`` from stub to a
fully implemented prompt-bible consistency review.
``planner agent review-batch`` remains a stub. The contracts for
all three are encoded in :mod:`planner.agent.diagnose` /
:mod:`planner.agent.review` and the :mod:`planner.agent.tools`
registry.

Public surface (use these from the CLI; do not import internals):

* :func:`planner.agent.diagnose.diagnose_run_dir` — main entry
* :func:`planner.agent.diagnose.build_not_implemented_report` —
  stub factory
* :data:`planner.agent.tools.TOOL_REGISTRY` /
  :data:`planner.agent.tools.TOOL_ARTIFACT_MAP` — tool metadata
  consumed by harness scenarios

Hard rule: never write files from inside the agent package. The
only write path is the CLI's ``--write-report`` option, which
goes through :func:`planner.agent.cli._check_and_write_report`
and respects the production repo-internal refuse policy.
"""

from __future__ import annotations

from .diagnose import (
    DiagnoseFinding,
    DiagnoseReport,
    EvidenceRef,
    ImplementationStatus,
    ProviderRuntimeSummary,
    ProviderSummary,
    ReportStatus,
    Severity,
    ToolInvocation,
    ValidationSummary,
    build_not_implemented_report,
    diagnose_run_dir,
)
from .tools import (
    KNOWN_ARTIFACTS,
    TOOL_ARTIFACT_MAP,
    TOOL_REGISTRY,
)

__all__ = [
    # main public API
    "diagnose_run_dir",
    "build_not_implemented_report",
    # Pydantic surface (re-exported so callers don't need to reach
    # into the .diagnose submodule)
    "DiagnoseReport",
    "DiagnoseFinding",
    "EvidenceRef",
    "ProviderSummary",
    "ProviderRuntimeSummary",
    "ValidationSummary",
    "ToolInvocation",
    "Severity",
    "ReportStatus",
    "ImplementationStatus",
    # tool metadata
    "TOOL_REGISTRY",
    "TOOL_ARTIFACT_MAP",
    "KNOWN_ARTIFACTS",
]
