"""Tool functions exposed by the planner agent.

Phase 3 P1: thin wrappers around :mod:`planner.agent.readers` and
:func:`planner.validate.validate_run`, plus :data:`TOOL_REGISTRY`
and :data:`TOOL_ARTIFACT_MAP`.

Why a registry + artifact map:

* Harness ``agent_scenarios/run_all.py`` runs ``live_cross_check``
  that asserts each declared tool actually touches the artifacts
  listed in ``_TOOL_ARTIFACT_MAP``. The agent-side mirror in this
  file MUST stay in sync — PRs that change either side must touch
  both (reviewer convention enforced by the harness scenario suite).
* The P2 review-run / review-batch will declare their tool usage
  via the registry; the diagnose path already consumes it.

Hard rule: every tool here is **read-only**. None of them opens a
file for writing, calls subprocess, or imports LLM SDKs. If you
find yourself wanting to add a write action here, you are breaking
the Phase-3-P1 read-only contract — move it to ``planner.agent``
P2 or later, or refuse the action at the CLI layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import planner.validate as _validate_mod
from planner.validate import ValidationReport

from .readers import (
    KNOWN_ARTIFACTS,
    list_artifacts as _readers_list_artifacts,
    list_runs_in_batch as _readers_list_runs_in_batch,
    load_artifact,
    load_batch_summary,
    load_run_summary,
)


def read_run_summary(run_dir: Path) -> Dict[str, Any]:
    """Return ``run_summary.json`` as dict.

    Raises ``KeyError`` if missing or corrupted — the diagnose
    engine catches ``KeyError`` and translates it to the
    ``missing_run_summary`` / ``corrupted_run_summary`` finding.
    Never raises ``FileNotFoundError``; surfaces as KeyError instead.
    """
    data, err = load_run_summary(run_dir)
    if data is None:
        raise KeyError(f"run_summary.json missing or corrupted: {err}")
    return data


def list_artifacts(run_dir: Path) -> List[str]:
    """Return list of artifact names that actually exist.

    Returns a subset of :data:`planner.agent.readers.KNOWN_ARTIFACTS`.
    Used by R12 ``partial_run_missing_artifact`` to enumerate which
    files are missing without loading them.
    """
    existence = _readers_list_artifacts(run_dir)
    return [name for name, present in existence.items() if present]


def read_artifact(run_dir: Path, name: str) -> Dict[str, Any]:
    """Return a single artifact by name.

    Thin delegate to :func:`planner.agent.readers.load_artifact`.
    ``name`` must be in ``KNOWN_ARTIFACTS`` (defense against
    whitelist bypass). Raises ``ValueError`` / ``FileNotFoundError``
    / ``json.JSONDecodeError``; callers translate these to findings.
    """
    return load_artifact(run_dir, name)


def validate_run_tool(
    run_dir: Path, expected_env: Optional[str] = None
) -> ValidationReport:
    """Delegate to :func:`planner.validate.validate_run`.

    Note: ``validate_run`` internally calls ``pipeline.load_run``
    which fails fast on missing core artifacts (script_parse,
    bibles, shot_list, image/video_prompts, asset_manifest) and
    raises ``ScriptReadError``. The diagnose engine catches this
    exception and emits a finding rather than crashing.

    Renamed from ``validate_run`` to avoid shadowing the
    imported function; the public tool name is ``validate_run``
    in the registry.

    Implementation note: we go through the module attribute
    (``planner.validate.validate_run``) rather than a top-level
    import so that tests can monkeypatch the underlying function
    and observe delegation.
    """
    return _validate_mod.validate_run(run_dir, expected_env=expected_env)


def read_batch_summary(batch_dir: Path) -> Dict[str, Any]:
    """Return ``batch_summary.json`` as dict.

    Same ``KeyError`` contract as :func:`read_run_summary`.
    """
    data, err = load_batch_summary(batch_dir)
    if data is None:
        raise KeyError(f"batch_summary.json missing or corrupted: {err}")
    return data


def list_runs_in_batch(batch_dir: Path) -> List[Path]:
    """Delegate to :func:`planner.agent.readers.list_runs_in_batch`.

    Returns episode subdirectories (sorted, filtered to those with
    ``run_summary.json``).
    """
    return _readers_list_runs_in_batch(batch_dir)


# Tool registry — flat dict mapping tool name -> callable.
# Phase 3 P1 keeps this read-only; P2 review-run/review-batch may
# add their own tool implementations here.
TOOL_REGISTRY: Dict[str, Callable[..., Any]] = {
    "read_run_summary": read_run_summary,
    "list_artifacts": list_artifacts,
    "read_artifact": read_artifact,
    "validate_run": validate_run_tool,
    "read_batch_summary": read_batch_summary,
    "list_runs_in_batch": list_runs_in_batch,
}


# MUST stay in sync with
# harness/agent_scenarios/run_all.py:_TOOL_ARTIFACT_MAP.
# Reviewer convention: any change to either map must touch both
# files in the same PR (and add a CHANGELOG entry mentioning the
# map synchronization).
TOOL_ARTIFACT_MAP: Dict[str, List[str]] = {
    "read_run_summary": ["run_summary.json"],
    "validate_run": ["run_summary.json", "script_parse.json"],
    "list_artifacts": ["run_summary.json"],  # existence only
    "read_artifact": [],  # dynamic; depends on name arg
    "read_batch_summary": ["batch_summary.json"],
    "list_runs_in_batch": ["batch_summary.json"],
}


__all__ = [
    "KNOWN_ARTIFACTS",
    "TOOL_REGISTRY",
    "TOOL_ARTIFACT_MAP",
    "read_run_summary",
    "list_artifacts",
    "read_artifact",
    "validate_run_tool",
    "read_batch_summary",
    "list_runs_in_batch",
]
