"""Batch driver: plan multiple episodes in one invocation.

``planner batch`` walks a directory of script files, invokes the
single-script :func:`planner.pipeline.run` for each, optionally
validates the resulting run, and writes a ``batch_summary.json`` next
to the per-episode subdirs.

Design constraints (all align with red lines):

- **Zero business logic.** This module owns no provider selection,
  no health check, no prompt format. Everything delegates to
  ``planner.pipeline.run`` and ``planner.validate.validate_run``.
- **Production never writes to the repo.** ``resolve_batch_out_dir``
  mirrors the policy in ``planner/web/run_service.py`` so the CLI
  has the same guarantees as the GUI backend.
- **Errors never silently dropped.** Every per-episode failure is
  recorded in ``batch_summary.json`` with ``status="failed"``,
  ``error_type``, ``error_message``. ``--fail-fast`` (default) aborts
  on the first failure; ``--no-fail-fast`` records and continues.
- **Audit fields preserved.** Each ``EpisodeRunSummary`` carries the
  same five fields the GUI surfaces (``requested_provider`` /
  ``effective_provider`` / ``fallback_used`` / ``fallback_reason``
  / provider health via ``run_summary.json``).

Cross-platform notes:

- File enumeration uses ``sorted(Path.rglob("*.txt"))`` so the order
  is deterministic regardless of OS.
- All paths are resolved via ``pathlib.Path``; both forward and
  backward slashes work in ``--scripts DIR`` because pathlib
  normalizes.
"""

from __future__ import annotations

import logging
import re
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .env import PlannerConfig, is_inside_repo, load_config
from .exceptions import EnvironmentBoundaryError, PlannerError
from .io_utils import read_json, write_json
from .pipeline import run as pipeline_run
from .schema import BatchSummary, EpisodeRunSummary
from .validate import validate_run

_log = logging.getLogger(__name__)

# Filenames that look like ``EP01.txt``, ``EP02_abc.txt``, etc.
# Falls back to the file stem (uppercased) if the regex does not match.
_EPISODE_ID_RE = re.compile(r"^(EP\d+)", re.IGNORECASE)


def derive_episode_id(script_path: Path) -> str:
    """Return a filesystem-friendly episode id parsed from the
    script filename.

    Mirrors :func:`planner.pipeline._episode_id_from_path` but is
    duplicated here to avoid reaching into a private function.
    Future refactor: move both into a small ``episodes`` module.
    """

    m = _EPISODE_ID_RE.match(script_path.stem)
    if m:
        return m.group(1).upper()
    return script_path.stem.upper()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _health_to_dict(h) -> dict:
    """Serialize a ``ProviderHealth`` dataclass to a JSON-friendly dict.

    Mirrors :func:`planner.pipeline._health_to_dict`; duplicated here
    to avoid reaching into a private function. Future refactor: move
    both into a small ``providers.health`` module.
    """

    return {
        "name": h.name,
        "healthy": h.healthy,
        "reason": h.reason,
        "details": dict(h.details) if h.details else {},
    }


def _new_batch_id() -> str:
    """Batch id with second precision + 3-byte random suffix to avoid
    collisions when two batches start in the same second."""

    import secrets

    return (
        datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        + "-"
        + secrets.token_hex(2)
    )


@dataclass
class BatchOptions:
    env: str
    scripts_dir: Path
    out_dir: Path
    fail_fast: bool = True
    config_path: Optional[Path] = None
    repo_root: Optional[Path] = None
    skip_validation: bool = False

    def resolved_out_dir(self) -> Path:
        """Apply the production out_dir policy. Returns the resolved
        directory; raises ``EnvironmentBoundaryError`` for production
        writes inside the repo."""

        resolved = self.out_dir.resolve()
        if self.env == "production" and self.repo_root is not None:
            if is_inside_repo(resolved, self.repo_root):
                raise EnvironmentBoundaryError(
                    f"Production batch refuses to write inside the project "
                    f"repository ({resolved} is inside {self.repo_root}). "
                    f"Use an --out directory outside the repo, e.g. "
                    f"~/Library/Application Support/ShortDramaPlanner/runs/ "
                    f"(macOS) or %APPDATA%/ShortDramaPlanner/runs/ (Windows)."
                )
        return resolved


def discover_scripts(scripts_dir: Path) -> List[Path]:
    """Return sorted ``*.txt`` script paths under ``scripts_dir``.

    Non-recursive: the user passes a flat directory. Recursion would
    surprise users who keep e.g. ``README.md`` or notes in the same
    folder. We ``is_file()`` check so symlinks to dirs do not break.
    """

    if not scripts_dir.exists():
        raise EnvironmentBoundaryError(
            f"Scripts directory does not exist: {scripts_dir}"
        )
    if not scripts_dir.is_dir():
        raise EnvironmentBoundaryError(
            f"Scripts path is not a directory: {scripts_dir}"
        )

    scripts = sorted(
        p for p in scripts_dir.iterdir()
        if p.is_file() and p.suffix.lower() == ".txt"
    )
    if not scripts:
        raise EnvironmentBoundaryError(
            f"No .txt script files found in {scripts_dir}"
        )
    return scripts


def run_one_episode(
    script_path: Path,
    episode_id: str,
    out_root: Path,
    config: PlannerConfig,
    *,
    skip_validation: bool = False,
    model_config: "ModelProviderConfig | None" = None,
) -> EpisodeRunSummary:
    """Run the pipeline for a single script and return its summary.

    On success, ``status="done"``; on planner error, ``status="failed"``
    with ``error_type`` + ``error_message`` populated; on unexpected
    exception, same shape with type ``UnhandledError``.

    The function never raises a ``PlannerError`` to its caller —
    every error is captured into the returned summary so the batch
    loop can keep going (or stop, depending on ``fail_fast``).
    """

    out_dir = out_root / episode_id
    started_at = _now_iso()
    counts: dict = {}
    audit: dict = {}

    try:
        result = pipeline_run(
            script_path=script_path,
            out_dir=out_dir,
            config=config,
            model_config=model_config,
        )
    except PlannerError as exc:
        # Friendly log line only — no Python traceback to stderr
        # (red line #6). The ``error_message`` in the summary carries
        # the user-facing detail.
        _log.warning(
            "Batch episode %s failed: %s: %s",
            episode_id, type(exc).__name__, exc,
        )
        return EpisodeRunSummary(
            run_id=episode_id,
            episode_id=episode_id,
            run_dir=str(out_dir),
            status="failed",
            script_path=str(script_path),
            started_at=started_at,
            finished_at=_now_iso(),
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
    except Exception as exc:  # pragma: no cover - defensive
        # Unhandled (programming) bug. Capture a short label — never
        # the full traceback — to keep the red-line #6 invariant.
        # Operators needing full traceback can enable DEBUG logging
        # via config and reproduce; or look at the per-episode log
        # file path (Phase Core-1 follow-up).
        _log.warning(
            "Batch episode %s crashed: %s: %s",
            episode_id, type(exc).__name__, exc,
        )
        return EpisodeRunSummary(
            run_id=episode_id,
            episode_id=episode_id,
            run_dir=str(out_dir),
            status="failed",
            script_path=str(script_path),
            started_at=started_at,
            finished_at=_now_iso(),
            error_type="UnhandledError",
            error_message=f"{type(exc).__name__}: {exc}",
        )

    counts = {
        "characters": result.character_count,
        "locations": result.location_count,
        "props": result.prop_count,
        "shots": result.shot_count,
    }
    audit = {
        "requested_provider": result.requested_provider,
        "effective_provider": result.effective_provider,
        "fallback_used": result.fallback_used,
        "fallback_reason": result.fallback_reason,
        # ``RunResult.provider_health`` is already a JSON-serializable
        # ``Dict[str, dict]`` (``provider_name -> health record``).
        # Copy it through so the GUI can render the same audit card
        # the single-run endpoint exposes.
        "provider_health": (
            dict(result.provider_health) if result.provider_health else None
        ),
    }

    validation_ok: Optional[bool] = None
    validation_errors = 0
    validation_warnings = 0
    if not skip_validation:
        try:
            report = validate_run(out_dir, expected_env=config.env)
            validation_ok = report.ok
            validation_errors = len(report.errors)
            validation_warnings = len(report.warnings)
        except PlannerError as exc:
            # Validation shouldn't normally raise; if it does, the
            # episode is still considered done but validation is
            # recorded as failed.
            _log.warning(
                "Validation for episode %s raised: %s: %s",
                episode_id, type(exc).__name__, exc,
            )
            validation_ok = False
            validation_errors = 1

    return EpisodeRunSummary(
        run_id=episode_id,
        episode_id=episode_id,
        run_dir=str(out_dir),
        status="done",
        script_path=str(script_path),
        started_at=started_at,
        finished_at=_now_iso(),
        counts=counts,
        requested_provider=audit["requested_provider"],
        effective_provider=audit["effective_provider"],
        fallback_used=audit["fallback_used"],
        fallback_reason=audit["fallback_reason"],
        provider_health=audit["provider_health"],
        validation_ok=validation_ok,
        validation_errors=validation_errors,
        validation_warnings=validation_warnings,
    )


def run_batch(
    options: BatchOptions,
    *,
    config: Optional[PlannerConfig] = None,
    on_episode_done: Optional[callable] = None,  # type: ignore[valid-type]
    model_config: "ModelProviderConfig | None" = None,
) -> BatchSummary:
    """Execute the batch and return its summary. Writes
    ``batch_summary.json`` under ``options.out_dir`` before returning.

    If ``config`` is omitted, :func:`load_config` is invoked from
    scratch; CLI callers usually pass a pre-loaded config to avoid
    the double-load.
    """

    if config is None:
        config = load_config(
            env=options.env,
            project_root=options.repo_root,
            config_path=options.config_path,
        )
    out_root = options.resolved_out_dir()
    out_root.mkdir(parents=True, exist_ok=True)

    scripts = discover_scripts(options.scripts_dir)
    batch_id = _new_batch_id()
    started_at = _now_iso()

    episodes: List[EpisodeRunSummary] = []
    aborted = False
    for script_path in scripts:
        episode_id = derive_episode_id(script_path)
        # Skip if episode_id collides with an existing subdir and we're
        # NOT in fail-fast mode. (In fail-fast mode, overwrite is
        # permitted per PlannerConfig.allow_overwrite_runs.)
        episode_dir = out_root / episode_id
        if episode_dir.exists() and not config.allow_overwrite_runs:
            ep_summary = EpisodeRunSummary(
                run_id=episode_id,
                episode_id=episode_id,
                run_dir=str(episode_dir),
                status="failed",
                script_path=str(script_path),
                started_at=_now_iso(),
                finished_at=_now_iso(),
                error_type="EnvironmentBoundaryError",
                error_message=(
                    f"Episode dir already exists: {episode_dir}. "
                    f"Pass --force (development only) or remove it."
                ),
            )
            episodes.append(ep_summary)
            if on_episode_done:
                on_episode_done(ep_summary)
            if options.fail_fast:
                aborted = True
                break
            continue

        ep_summary = run_one_episode(
            script_path=script_path,
            episode_id=episode_id,
            out_root=out_root,
            config=config,
            skip_validation=options.skip_validation,
            model_config=model_config,
        )
        episodes.append(ep_summary)
        if on_episode_done:
            on_episode_done(ep_summary)

        if ep_summary.status == "failed" and options.fail_fast:
            aborted = True
            break

    totals = _compute_totals(episodes, aborted=aborted)

    summary = BatchSummary(
        batch_id=batch_id,
        started_at=started_at,
        finished_at=_now_iso(),
        env=options.env,
        scripts_dir=str(options.scripts_dir.resolve()),
        episodes=episodes,
        totals=totals,
    )

    write_json(out_root / "batch_summary.json", summary.model_dump(mode="json"))
    return summary


def _compute_totals(
    episodes: Iterable[EpisodeRunSummary], *, aborted: bool
) -> dict:
    """Aggregate counts and statuses for the batch summary."""

    done = sum(1 for e in episodes if e.status == "done")
    failed = sum(1 for e in episodes if e.status == "failed")
    counts: dict = {"characters": 0, "locations": 0, "props": 0, "shots": 0}
    for e in episodes:
        if e.status == "done":
            for k in counts:
                counts[k] += e.counts.get(k, 0)
    return {
        "episodes_total": len(list(episodes)),
        "episodes_done": done,
        "episodes_failed": failed,
        "aborted": aborted,
        "counts": counts,
    }