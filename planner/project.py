"""Project abstraction for v1.0.

A *project* is a folder on disk with:

::

    project_folder/
      project.json     # typed metadata; the only required file
      scripts/         # the .txt episodes the planner will process
      runs/            # per-episode output directories
      exports/         # exported Markdown / HTML / CSV reports

The :class:`Project` model pins the file shape. ``init_project``
creates the folder tree + a default ``project.json``. ``validate_project``
is a pre-flight check that the operator can run before kicking off
a batch (``planner batch --project ...`` picks up ``default_env`` /
``default_provider`` / ``output_dir`` from here).

The CLI group lives at :mod:`planner.cli`; this module owns the
data + filesystem helpers only, so the GUI layer (``planner.web``)
can reuse them without dragging Click into the GUI extras.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .exceptions import ConfigError


#: Subdirectories every project folder carries. Kept in one place
#: so init / validate / GUI agree on what counts as "complete".
PROJECT_SUBDIRS = ("scripts", "runs", "exports")


class Project(BaseModel):
    """Typed v1.0 project metadata.

    The fields here are the same ones the v1.0 release plan §10
    enumerates; future revisions add new fields without breaking
    old ``project.json`` files (Pydantic's default behavior is
    additive; ``extra="forbid"`` here only rejects typos).
    """

    model_config = ConfigDict(extra="forbid")

    project_name: str = Field(
        min_length=1,
        description="Human-readable project name shown in the GUI header.",
    )
    script_dir: str = Field(
        default="scripts",
        description=(
            "Path (relative to the project folder or absolute) where "
            ".txt episodes live. ``planner batch --project`` reads this."
        ),
    )
    default_env: Literal["development", "production"] = "development"
    default_provider: Literal[
        "deterministic", "openai", "anthropic", "openai_compatible"
    ] = "deterministic"
    output_dir: str = Field(
        default="runs",
        description=(
            "Path (relative to the project folder or absolute) where "
            "per-episode outputs land. ``planner batch --project`` reads this."
        ),
    )
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )


class ProjectValidationReport(BaseModel):
    """Result of :func:`validate_project`. Mirrors the GUI's
    validation panel so a single shape feeds both endpoints."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    project_path: str
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    script_count: int = 0


# --- file IO ------------------------------------------------------------


def load_project(project_dir: Path) -> Project:
    """Read and validate ``project_dir/project.json``.

    Raises :class:`ConfigError` on missing / unreadable / malformed
    files so the operator sees a friendly error rather than a
    Python traceback.
    """

    path = Path(project_dir)
    config_path = path / "project.json"
    if not config_path.exists():
        raise ConfigError(
            f"project.json not found at {config_path}. "
            "Run `planner project init --dir <path>` first."
        )
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"project.json at {config_path} is not valid JSON: {exc}"
        ) from exc
    try:
        return Project.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError or shape mismatch
        raise ConfigError(
            f"project.json at {config_path} failed validation: {exc}"
        ) from exc


def init_project(
    project_dir: Path,
    *,
    project_name: Optional[str] = None,
    overwrite: bool = False,
) -> Project:
    """Create the project folder structure + default ``project.json``.

    Returns the written :class:`Project` model so the caller can echo
    it back to the operator. Refuses to overwrite an existing
    ``project.json`` unless ``overwrite=True``.
    """

    path = Path(project_dir).resolve()
    path.mkdir(parents=True, exist_ok=True)

    config_path = path / "project.json"
    if config_path.exists() and not overwrite:
        raise ConfigError(
            f"project.json already exists at {config_path}. "
            "Pass overwrite=True (or use a fresh --dir) to replace it."
        )

    name = project_name or path.name
    now = datetime.now(timezone.utc).isoformat()
    project = Project(
        project_name=name,
        created_at=now,
        updated_at=now,
    )
    # Subdirs.
    for sub in PROJECT_SUBDIRS:
        (path / sub).mkdir(exist_ok=True)

    # Atomic write via tmp + rename so a half-written file never lands.
    tmp = config_path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(
            project.model_dump(mode="json"), ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    tmp.replace(config_path)
    return project


def validate_project(project_dir: Path) -> ProjectValidationReport:
    """Pre-flight check used by ``planner project validate`` and by
    ``planner batch --project`` before each invocation.

    Errors block the batch; warnings don't.
    """

    path = Path(project_dir).resolve()
    errors: List[str] = []
    warnings: List[str] = []

    if not path.exists():
        errors.append(f"Project directory does not exist: {path}")
        return ProjectValidationReport(
            ok=False, project_path=str(path), errors=errors,
        )

    try:
        project = load_project(path)
    except ConfigError as exc:
        errors.append(str(exc))
        return ProjectValidationReport(
            ok=False, project_path=str(path), errors=errors,
        )

    # script_dir: relative paths resolve against the project folder.
    script_dir = Path(project.script_dir)
    if not script_dir.is_absolute():
        script_dir = path / script_dir
    if not script_dir.exists():
        errors.append(f"script_dir does not exist: {script_dir}")
    elif not script_dir.is_dir():
        errors.append(f"script_dir is not a directory: {script_dir}")

    # Count .txt files (deterministic order matches ``batch.discover_scripts``).
    script_count = 0
    if script_dir.exists():
        for entry in sorted(script_dir.iterdir()):
            if entry.is_file() and entry.suffix.lower() == ".txt":
                script_count += 1
        if script_count == 0:
            warnings.append(
                f"script_dir {script_dir} contains no .txt files yet."
            )

    # output_dir: just warn if missing; init_project created it but a
    # hand-edited project.json may not have.
    output_dir = Path(project.output_dir)
    if not output_dir.is_absolute():
        output_dir = path / output_dir
    if not output_dir.exists():
        warnings.append(f"output_dir does not exist yet: {output_dir}")

    # default_env + default_provider sanity.
    if (
        project.default_provider in ("openai", "anthropic")
        and project.default_env == "production"
    ):
        warnings.append(
            f"default_provider={project.default_provider!r} is a Phase-1 "
            "skeleton and reports unhealthy even with full prerequisites. "
            "Use provider='openai_compatible' for v1.0 production runs."
        )

    return ProjectValidationReport(
        ok=not errors,
        project_path=str(path),
        errors=errors,
        warnings=warnings,
        script_count=script_count,
    )


__all__ = [
    "PROJECT_SUBDIRS",
    "Project",
    "ProjectValidationReport",
    "init_project",
    "load_project",
    "validate_project",
]