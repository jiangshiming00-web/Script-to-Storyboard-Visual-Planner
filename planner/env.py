"""Environment configuration loading.

The planner uses a single config object per process. The active
environment is selected with ``--env development|production`` on the
CLI. The configuration is loaded from ``config/<env>.json`` and may be
overridden via ``PLANNER_*`` environment variables.

Production boundary rules are enforced here:

- ``allow_overwrite_runs`` defaults to ``False`` in production.
- ``executor_default_status`` defaults to ``pending_manual_approval``
  in production.
- ``submit_paid_jobs`` must remain ``False`` in this phase.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from .exceptions import ConfigError

VALID_ENVS = ("development", "production")

#: Default provider name used when config has no ``planner_provider``
#: entry and no ``PLANNER_PLANNER_PROVIDER`` env var. This must always
#: be the deterministic stub until real LLM adapters are introduced.
DEFAULT_PLANNER_PROVIDER = "deterministic"


def is_inside_repo(path: "Path", repo_root: "Path") -> bool:
    """Return ``True`` when ``path`` resolves inside ``repo_root``.

    Used by the production out-dir guards (CLI run, CLI batch, GUI
    service) to refuse writes into the project repository. Symlinks
    are resolved via ``Path.resolve()`` so the check survives ``..``
    and other escape attempts.

    Raises:
        OSError: if ``path`` does not exist (we resolve before the
            ``is_relative_to`` check). Callers that need to test a
            not-yet-existing path should pre-create it.
    """

    return path.resolve().is_relative_to(repo_root.resolve())


@dataclass
class PlannerConfig:
    """Resolved planner configuration for a single run."""

    env: str
    config_path: Path
    allow_overwrite_runs: bool
    executor_default_status: str
    submit_paid_jobs: bool
    log_level: str
    executor_dry_run: bool
    data_root: Path
    assets_root: Path
    runs_root: Path
    logs_root: Path
    schema_strict: bool
    planner_provider: str = DEFAULT_PLANNER_PROVIDER
    allow_provider_fallback: bool = False
    overrides: Dict[str, str] = field(default_factory=dict)

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "env": self.env,
            "allow_overwrite_runs": self.allow_overwrite_runs,
            "executor_default_status": self.executor_default_status,
            "submit_paid_jobs": self.submit_paid_jobs,
            "log_level": self.log_level,
            "executor_dry_run": self.executor_dry_run,
            "data_root": str(self.data_root),
            "assets_root": str(self.assets_root),
            "runs_root": str(self.runs_root),
            "logs_root": str(self.logs_root),
            "schema_strict": self.schema_strict,
            "planner_provider": self.planner_provider,
            "allow_provider_fallback": self.allow_provider_fallback,
        }


def _coerce_path(value: Any, base: Path) -> Path:
    if isinstance(value, Path):
        return value
    p = Path(str(value))
    if not p.is_absolute():
        p = base / p
    return p


def load_config(
    env: str,
    project_root: Optional[Path] = None,
    config_path: Optional[Path] = None,
) -> PlannerConfig:
    """Load planner config for the requested environment.

    Args:
        env: ``development`` or ``production``.
        project_root: Used to resolve relative paths. Defaults to CWD.
        config_path: Optional explicit config file override. If omitted,
            ``config/<env>.json`` is used.

    Raises:
        ConfigError: if the environment is unknown, the config file is
            missing, or any production boundary rule is violated (these
            checks run **after** env-var overrides so they cannot be
            silently downgraded).
    """

    if env not in VALID_ENVS:
        raise ConfigError(
            f"Unknown environment: {env!r}. Valid: {', '.join(VALID_ENVS)}"
        )

    root = (project_root or Path.cwd()).resolve()
    cfg_file = config_path or (root / "config" / f"{env}.json")
    if not cfg_file.exists():
        if env == "production":
            hint = (
                "Copy config/production.example.json to "
                "config/production.json before running in production."
            )
        else:
            hint = "Ensure config/development.json exists."
        raise ConfigError(f"Config file not found: {cfg_file}. {hint}")

    with cfg_file.open("r", encoding="utf-8") as f:
        raw: Dict[str, Any] = json.load(f)

    # Apply env-var overrides. Names follow PLANNER_<UPPER> convention.
    # Production hard-pins a few keys (see _PRODUCTION_LOCKED_KEYS): any
    # PLANNER_* attempt to override them is **rejected loudly** so the
    # operator notices. Silent drop would hide attacks.
    if env == "production":
        for locked in _production_locked_keys():
            if f"PLANNER_{locked.upper()}" in os.environ:
                raise ConfigError(
                    f"PLANNER_{locked.upper()} is not honoured in "
                    f"production. {locked} is locked by policy; edit "
                    f"the config file or disable the override to proceed."
                )

    overrides: Dict[str, str] = {}
    for key in (
        "data_root",
        "assets_root",
        "runs_root",
        "logs_root",
        "executor_default_status",
        "log_level",
        "schema_strict",
        "allow_overwrite_runs",
        "submit_paid_jobs",
        "executor_dry_run",
        "planner_provider",
        "allow_provider_fallback",
    ):
        env_key = f"PLANNER_{key.upper()}"
        if env_key in os.environ:
            overrides[key] = os.environ[env_key]
            raw[key] = os.environ[env_key]

    schema_strict_raw = raw.get("schema_strict", env == "production")
    allow_overwrite_raw = raw.get("allow_overwrite_runs", env != "production")
    submit_paid_raw = raw.get("submit_paid_jobs", False)
    dry_run_raw = raw.get("executor_dry_run", True)
    log_level = str(raw.get("log_level", "INFO" if env == "production" else "DEBUG"))
    planner_provider = str(raw.get("planner_provider", DEFAULT_PLANNER_PROVIDER))
    allow_fallback_raw = raw.get("allow_provider_fallback", env != "production")

    cfg = PlannerConfig(
        env=env,
        config_path=cfg_file,
        allow_overwrite_runs=_coerce_bool(allow_overwrite_raw),
        executor_default_status=str(
            raw.get(
                "executor_default_status",
                "pending_manual_approval" if env == "production" else "pending",
            )
        ),
        submit_paid_jobs=_coerce_bool(submit_paid_raw),
        log_level=log_level,
        executor_dry_run=_coerce_bool(dry_run_raw),
        data_root=_coerce_path(raw.get("data_root", f"data/{env}"), root),
        assets_root=_coerce_path(raw.get("assets_root", f"assets/{env}"), root),
        runs_root=_coerce_path(raw.get("runs_root", f"runs/{env}"), root),
        logs_root=_coerce_path(raw.get("logs_root", f"logs/{env}"), root),
        schema_strict=_coerce_bool(schema_strict_raw),
        planner_provider=planner_provider,
        allow_provider_fallback=_coerce_bool(allow_fallback_raw),
        overrides=overrides,
    )

    _validate_provider(cfg)
    _enforce_boundaries(cfg)
    return cfg


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _validate_provider(cfg: PlannerConfig) -> None:
    """Ensure ``planner_provider`` resolves to a registered provider.

    Unknown providers raise :class:`ConfigError` so the operator sees
    the failure at config-load time, not later when the pipeline tries
    to call a method on a missing provider.

    Import is local to avoid a circular import at module load:
    ``providers/__init__`` imports ``deterministic``, which imports
    ``bible``, which imports ``schema``. We just need a lookup, so we
    do not import ``planner.providers`` itself.
    """

    from .providers.registry import available_providers, get_provider

    name = cfg.planner_provider
    if name not in available_providers():
        avail = ", ".join(available_providers()) or "(none registered)"
        raise ConfigError(
            f"Unknown planner_provider: {name!r}. Available: {avail}."
        )
    # Force instantiation so constructor-time configuration errors
    # surface at config load.
    get_provider(name)


def _production_locked_keys() -> set:
    """Keys that the production environment must never override.

    Returning a frozenset-like list keeps the policy auditable in one
    place. Any attempt to set one of these via a ``PLANNER_*`` env var
    in production is **rejected loudly** by :func:`load_config`
    (raising :class:`ConfigError`). The final value is then re-asserted
    by :func:`_enforce_boundaries` so even an attacker who finds a way
    around the env-var guard cannot downgrade these guarantees.
    """

    return {
        "executor_default_status",
        "submit_paid_jobs",
        "allow_overwrite_runs",
        "allow_provider_fallback",
    }


def _enforce_boundaries(cfg: PlannerConfig) -> None:
    """Hard guardrails that must never be lifted by env overrides.

    These checks run AFTER env-var overrides so they cannot be quietly
    downgraded. For production they re-assert locked values rather than
    trusting the config file or the environment.
    """

    if cfg.env == "production":
        # Production must always require manual approval in this phase.
        if cfg.executor_default_status != "pending_manual_approval":
            raise ConfigError(
                "Production executor_default_status must be exactly "
                "'pending_manual_approval'. Config files and the "
                "PLANNER_EXECUTOR_DEFAULT_STATUS env var cannot override "
                "this during Phase 1."
            )
        if cfg.submit_paid_jobs:
            raise ConfigError(
                "submit_paid_jobs must remain False in production during "
                "Phase 1. Disable it in config; the "
                "PLANNER_SUBMIT_PAID_JOBS env var is ignored in production."
            )
        if cfg.allow_overwrite_runs:
            raise ConfigError(
                "allow_overwrite_runs must remain False in production. "
                "Production runs never overwrite existing directories."
            )
        if cfg.allow_provider_fallback:
            # Production must fail closed: if the requested provider is
            # unhealthy we want a loud error, not a silent fall back to
            # deterministic that would make operators believe they are
            # running on a real model. Operators who explicitly want
            # production fallback must edit this check in code, not via
            # a config flag — the intent is to require a code review.
            raise ConfigError(
                "allow_provider_fallback must remain False in production. "
                "Production never falls back from a requested provider "
                "to deterministic; a failure must surface as an error so "
                "operators notice the issue instead of silently "
                "swapping providers."
            )