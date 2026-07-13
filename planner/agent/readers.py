"""Read-only loaders for planner run / batch directories.

Phase 3 P1: agent must gracefully degrade when artifacts are missing
or corrupted (vs. pipeline.load_run which fails fast with
ScriptReadError). These readers return ``Optional[dict]`` with
diagnostic info so ``diagnose_run_dir`` can emit findings instead of
crashing the CLI.

Why a separate module from pipeline.load_run:

* ``pipeline.load_run`` raises ``ScriptReadError`` if any of the 9
  core artifacts is missing. That contract is correct for the
  pipeline (the run is broken), but the agent's job is to *report*
  what is broken — including the case where ``run_summary.json``
  itself is missing or corrupted.
* The agent must enumerate 11 artifacts individually so the
  ``partial_run_missing_artifact`` rule (R12) can list exactly
  which ones are gone.
* A 50 MB size cap on individual artifact reads defends against
  accidental consumption of runaway outputs; the cap is per-call
  so callers in P2 review-run can opt-out by passing
  ``max_bytes=...``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# 11 known artifact names (matches planner/pipeline.py:load_run
# EXPECTED_FILES + planner/web/routes.py artifact whitelist).
# Order is preserved from the pipeline's emission order.
KNOWN_ARTIFACTS: Tuple[str, ...] = (
    "script_parse.json",
    "character_bible.json",
    "location_bible.json",
    "prop_bible.json",
    "story_beats.json",
    "shot_list.json",
    "image_prompts.json",
    "video_prompts.json",
    "asset_manifest.json",
    "executor_tasks.json",
    "run_summary.json",
)

# Per-artifact cap (50 MB). Sufficient for ~10K shots at ~5 KB/shot.
# Above this we treat the artifact as corrupt / runaway and surface
# it via finding rather than loading the whole file into memory.
MAX_ARTIFACT_BYTES: int = 50 * 1024 * 1024


def _safe_read_json(path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Read JSON safely; return ``(data, error_message)``.

    ``data is None`` and ``error_message`` set means the file could
    not be loaded. ``(data, None)`` means success.
    """
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f), None
    except FileNotFoundError:
        return None, "file not found"
    except json.JSONDecodeError as exc:
        return None, (
            f"invalid JSON: {exc.msg} "
            f"(line {exc.lineno}, column {exc.colno})"
        )
    except OSError as exc:
        return None, f"OS error: {exc}"


def load_run_summary(
    run_dir: Path,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Load ``run_summary.json`` from a run directory.

    Returns ``(data, error)``. Never raises. ``data is None`` +
    ``error`` set is the contract for "could not load".
    """
    summary_path = run_dir / "run_summary.json"
    return _safe_read_json(summary_path)


def load_artifact(
    run_dir: Path,
    name: str,
    *,
    max_bytes: int = MAX_ARTIFACT_BYTES,
) -> Dict[str, Any]:
    """Load a single artifact by name (must be in KNOWN_ARTIFACTS).

    Raises ``ValueError`` for unknown names (defense against path
    traversal / whitelist bypass — the CLI should not pass
    ``../../etc/passwd`` here). Raises ``FileNotFoundError`` if the
    artifact is absent. Raises ``ValueError`` if the file exceeds
    ``max_bytes``. Raises ``json.JSONDecodeError`` on invalid JSON
    (caller / diagnose translates this to a finding).
    """
    if name not in KNOWN_ARTIFACTS:
        raise ValueError(
            f"unknown artifact name: {name!r}; "
            f"must be one of {KNOWN_ARTIFACTS}"
        )
    path = run_dir / name
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist")
    if not path.is_file():
        raise ValueError(f"{path} is not a regular file")
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(
            f"artifact {name!r} exceeds size cap "
            f"({size} bytes > {max_bytes})"
        )
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def list_artifacts(run_dir: Path) -> Dict[str, bool]:
    """Return existence map for all known artifacts.

    e.g. ``{"run_summary.json": True, "shot_list.json": False, ...}``

    Used by R12 ``partial_run_missing_artifact`` and by the
    ``list_artifacts`` tool which returns only the names that exist.
    """
    if not run_dir.is_dir():
        return {name: False for name in KNOWN_ARTIFACTS}
    return {name: (run_dir / name).is_file() for name in KNOWN_ARTIFACTS}


def load_batch_summary(
    batch_dir: Path,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Load ``batch_summary.json`` from a batch root directory.

    Same ``(data, error)`` contract as ``load_run_summary``.
    """
    summary_path = batch_dir / "batch_summary.json"
    return _safe_read_json(summary_path)


def list_runs_in_batch(batch_dir: Path) -> List[Path]:
    """List episode subdirectories inside a batch root.

    Returns subdirectories that contain a ``run_summary.json`` so
    half-written episodes (cleaned up by run_one_episode on failure)
    are excluded. Sorted alphabetically for deterministic output.
    """
    if not batch_dir.is_dir():
        return []
    result: List[Path] = []
    for child in sorted(batch_dir.iterdir()):
        if not child.is_dir():
            continue
        if (child / "run_summary.json").is_file():
            result.append(child)
    return result
