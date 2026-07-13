"""Service layer that bridges the FastAPI handlers to the core planner.

This module is intentionally thin: it owns **no business logic**. It
only:

1. Resolves where a run's output directory should live (the
   ``out_dir policy`` below), guarding the production boundary.
2. Calls :func:`planner.pipeline.run` in a background thread so the
   HTTP handler can return immediately.
3. Records the run's lifecycle in :class:`RunRegistry` and updates
   it when the pipeline finishes or fails.
4. On failure, cleans up the empty output directory so we preserve
   the project's no-residue invariant in GUI context too.

Hard rules (red lines):

- ``out_dir`` for ``production`` is NEVER allowed inside the project
  repository's ``runs/`` tree. The default is the OS app-data dir.
- For ``development`` the default is ``<repo_root>/runs/development/``,
  which is already gitignored at the repo root.
- The pipeline's own fail-closed guarantees (provider health check
  before ``mkdir``; production no-overwrite; provider fallback
  auditable in ``run_summary``) are reused verbatim — we do not
  reimplement any of them here.

The background thread is ``daemon=False`` so the server can wait for
in-flight runs to finish during shutdown. Status is reflected in the
:class:`RunRegistry`.
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from ..env import PlannerConfig, load_config
from ..exceptions import ConfigError, EnvironmentBoundaryError, PlannerError
from ..model_config import load_model_config
from ..pipeline import run as pipeline_run
from ..pipeline import RunResult
from ..validate import validate_run
from .errors import classify
from .run_registry import RunRegistry

_log = logging.getLogger(__name__)


# --- out_dir policy ----------------------------------------------------

APP_DIR_NAME = "ShortDramaPlanner"


def os_app_data_dir() -> Path:
    """Return the per-user, per-app data directory for the current OS.

    - macOS: ``~/Library/Application Support/ShortDramaPlanner``
    - Windows: ``%APPDATA%/ShortDramaPlanner`` (fallback to user home)
    - Linux / other: ``$XDG_DATA_HOME/ShortDramaPlanner`` or ``~/.local/share``

    Override for tests / CI: setting ``PLANNER_APP_DATA_ROOT`` to an
    absolute path redirects this function (and therefore
    :func:`default_out_dir`, the upload-script handler, and the model
    config storage when paired with ``PLANNER_MODEL_CONFIG_PATH``) to a
    scratch directory. The GUI smoke harness sets both env vars so it
    never touches the user's real OS app-data store.
    """

    override = os.environ.get("PLANNER_APP_DATA_ROOT")
    if override:
        return Path(override).expanduser()

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_DIR_NAME
        return Path.home() / "AppData" / "Roaming" / APP_DIR_NAME
    # Linux / other Unix
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base) / APP_DIR_NAME
    return Path.home() / ".local" / "share" / APP_DIR_NAME


def is_inside_repo(path: Path, repo_root: Path) -> bool:
    """Return True if ``path`` is anywhere under ``repo_root``."""

    try:
        path.resolve().relative_to(repo_root.resolve())
        return True
    except ValueError:
        return False


def generate_run_id() -> str:
    """Return a filesystem-friendly run id with second + microsecond
    + 3-byte random suffix: ``20260710-103045-123456-zx9k``.

    The trailing random hex avoids collisions when two concurrent
    dev POSTs land in the same second (previously possible because
    the default out_dir uses the run_id as its name).
    """

    now = datetime.now(timezone.utc)
    return f"{now.strftime('%Y%m%d-%H%M%S')}-{now.microsecond:06d}-{secrets.token_hex(2)}"


def default_out_dir(env: str, repo_root: Optional[Path]) -> Path:
    """Return the GUI default ``out_dir`` for the given environment.

    ``development`` → ``<repo_root>/runs/development/<run_id>/`` (already
    gitignored at the repo root, so colleagues can browse artifacts
    without leaving their project).

    ``production`` → ``<os_app_data>/ShortDramaPlanner/runs/<run_id>/``
    (NEVER inside the repo, per red line #3).
    """

    run_id = generate_run_id()
    if env == "production":
        return os_app_data_dir() / "runs" / run_id
    if repo_root is None:
        raise EnvironmentBoundaryError(
            "Cannot resolve default dev out_dir: project root is unknown. "
            "Pass an explicit out_dir."
        )
    return repo_root / "runs" / "development" / run_id


def resolve_out_dir(
    env: str,
    user_specified: Optional[Path],
    repo_root: Optional[Path],
) -> Path:
    """Apply the out_dir policy. Raise on policy violations.

    - If the user gave an explicit path, it is accepted as-is in
      ``development`` and rejected if inside the repo in
      ``production``.
    - If the user did not specify a path, the env-specific default is
      used.
    """

    if user_specified is None:
        return default_out_dir(env, repo_root)

    p = Path(user_specified).expanduser().resolve()
    if env == "production" and repo_root is not None and is_inside_repo(p, repo_root):
        raise EnvironmentBoundaryError(
            f"Production runs cannot write inside the project repository "
            f"({p} is inside {repo_root}). Use the default app-data dir "
            f"({os_app_data_dir() / 'runs'}) or an explicit path outside "
            f"the repo."
        )
    return p


def detect_repo_root(start: Optional[Path] = None) -> Optional[Path]:
    """Walk up from ``start`` (or CWD) until we find ``pyproject.toml``.

    Returns ``None`` if not found; callers should then default the
    dev out_dir to a sibling of the executable (used inside the
    PyInstaller bundle where no repo is present).
    """

    cur = (start or Path.cwd()).resolve()
    for parent in [cur, *cur.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
        if (parent / "config" / "development.json").exists():
            return parent
    return None


# --- service ----------------------------------------------------------


class RunService:
    """Owns the lifecycle of pipeline runs launched by the web UI."""

    def __init__(self, registry: RunRegistry, repo_root: Optional[Path] = None) -> None:
        self._registry = registry
        self._repo_root = repo_root

    # --- facade methods (P3 polish: routes should not reach into _registry) ---

    @property
    def repo_root(self) -> Optional[Path]:
        """Return the project root used by this service, or ``None``
        when running from a PyInstaller bundle (no repo is present).

        Routes and other callers must use this public accessor instead
        of touching the underlying private attribute. The HTTP layer
        relies on this to resolve ``<repo>/config/<env>.json`` without
        falling back to :func:`pathlib.Path.cwd`, which would mis-read
        the current working directory when the app is launched from a
        different location.
        """

        return self._repo_root

    def get_repo_root(self) -> Optional[Path]:
        """Facade accessor mirroring :attr:`repo_root`. Use this from
        HTTP routes to keep the layer free of private attribute reach.
        """

        return self._repo_root

    # --- facade methods (P3 polish: routes should not reach into _registry) ---

    def get_run(self, run_id: str) -> Optional[RunRecord]:
        """Return the run record for ``run_id`` or ``None``."""

        return self._registry.get(run_id)

    def list_runs(self, env: Optional[str] = None) -> List[RunRecord]:
        """Return run records, optionally filtered by environment."""

        return self._registry.list(env=env)

    def start_run(
        self,
        *,
        env: str,
        script_path: Path,
        user_out_dir: Optional[Path],
        config_path: Optional[Path],
        force: bool,
        repo_root: Optional[Path],
        model_config_path: Optional[Path] = None,
    ) -> tuple[str, Path]:
        """Validate inputs, register the run, kick off the background
        thread, and return ``(run_id, resolved_out_dir)``.

        The pipeline runs asynchronously so the HTTP handler can
        respond immediately with 202. Clients poll
        ``GET /api/runs/{run_id}/summary`` to track progress.

        ``model_config_path`` (v1.0 P1-1) loads a
        :class:`ModelProviderConfig` whose ``planner_provider`` (when
        non-deterministic) overrides the env config's provider choice,
        and whose per-provider section injects runtime settings into
        the provider instance. The env config still owns production
        fail-closed boundaries.
        """

        if not script_path.exists():
            raise EnvironmentBoundaryError(  # closest semantic match
                f"Script not found: {script_path}"
            )

        config = load_config(env=env, project_root=repo_root, config_path=config_path)

        # v1.0 P1-1: load model config and let it steer the provider.
        model_config = None
        if model_config_path is not None:
            try:
                model_config = load_model_config(model_config_path)
            except ValueError as exc:
                raise ConfigError(f"model config error: {exc}") from exc
        if (
            model_config is not None
            and model_config.planner_provider != "deterministic"
        ):
            object.__setattr__(
                config, "planner_provider", model_config.planner_provider
            )

        out_dir = resolve_out_dir(env, user_out_dir, repo_root)
        run_id = out_dir.name  # e.g. "20260710-103045"

        if force:
            if config.is_production:
                raise EnvironmentBoundaryError(
                    "Refusing --force in production. Remove the run "
                    "directory manually instead."
                )
            object.__setattr__(config, "allow_overwrite_runs", True)

        # Register BEFORE spawning the thread so the UI can see the
        # row immediately.
        self._registry.register(run_id=run_id, out_dir=out_dir, env=env)

        thread = threading.Thread(
            target=self._run_pipeline,
            args=(run_id, script_path, out_dir, config, model_config),
            name=f"planner-run-{run_id}",
            daemon=False,
        )
        thread.start()
        return run_id, out_dir

    def _run_pipeline(
        self,
        run_id: str,
        script_path: Path,
        out_dir: Path,
        config: PlannerConfig,
        model_config=None,
    ) -> None:
        """Background thread body: call the pipeline, update registry,
        clean up on failure."""

        try:
            result: RunResult = pipeline_run(
                script_path=script_path,
                out_dir=out_dir,
                config=config,
                model_config=model_config,
            )
        except PlannerError as exc:
            status, err_type, err_message = classify(exc)
            _log.warning(
                "Pipeline run %s failed: %s: %s",
                run_id, err_type, err_message,
            )
            self._registry.mark_failed(run_id, err_type, err_message)
            self._cleanup_partial_run(out_dir)
            return
        except Exception as exc:  # pragma: no cover - defensive
            _log.exception("Pipeline run %s crashed unexpectedly", run_id)
            self._registry.mark_failed(run_id, "UnhandledError", str(exc))
            self._cleanup_partial_run(out_dir)
            return

        counts = {
            "characters": result.character_count,
            "locations": result.location_count,
            "props": result.prop_count,
            "shots": result.shot_count,
        }
        self._registry.mark_done(run_id, counts)

    @staticmethod
    def _cleanup_partial_run(out_dir: Path) -> None:
        """If the pipeline failed and only the empty run directory
        exists, remove it so we don't leak residue.

        If the pipeline wrote at least one artifact, keep the
        directory (the user may want to inspect what was produced
        before the failure).
        """

        if not out_dir.exists():
            return
        try:
            contents = list(out_dir.iterdir())
        except OSError:
            return
        if not contents:
            try:
                out_dir.rmdir()
            except OSError:
                pass

    # --- read-side helpers (used by routes) ---------------------------

    def validate_run(
        self,
        *,
        run_dir: Path,
        expected_env: Optional[str],
    ):
        """Delegate to :func:`planner.validate.validate_run`. No
        additional logic here."""

        return validate_run(run_dir, expected_env=expected_env)

    def remove_partial_on_startup(self) -> None:
        """No-op for now; reserved for future cleanup of orphaned
        partial runs discovered at server start."""