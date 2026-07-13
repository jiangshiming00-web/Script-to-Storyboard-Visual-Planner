"""Click-based CLI.

Commands
--------

``planner run``
    Run the planner pipeline against a script.

``planner validate``
    Validate the artifacts of a previous run.

``planner batch``
    Run the planner against every .txt in a scripts directory.

``planner project init``
    Create a v1.0 project folder + project.json.

``planner project validate``
    Pre-flight check that a project folder is usable.

``planner export``
    Render a run (or batch) as Markdown / HTML / CSV for human review.

All commands take ``--env development|production`` as a required flag.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import click

from .env import PlannerConfig, is_inside_repo, load_config
from .exceptions import EnvironmentBoundaryError, PlannerError
from .pipeline import run as run_pipeline
from .validate import ValidationReport, validate_run

# Phase 3 P1: product agent (read-only diagnose + 2 stubs).
# Imported here so its @click.group registers before any @cli.command
# below; the add_command call happens just after @cli.group is
# defined below.
from .agent.cli import agent_group


def _resolve_project_root(ctx: click.Context) -> Path:
    root = ctx.obj.get("project_root") if ctx.obj else None
    return Path(root).resolve() if root else Path.cwd().resolve()


def _echo_json(data: dict) -> None:
    click.echo(json.dumps(data, ensure_ascii=False, indent=2))


def _load_model_config_for_cli(model_config_path: Optional[Path]):
    """Load the v1.0 model config for CLI commands.

    Returns ``None`` when no config is available (no ``--model-config``
    and no OS app-data file), so existing deterministic runs are
    untouched. Raises ``SystemExit`` on malformed JSON so the operator
    sees a friendly error instead of a Python traceback.
    """

    from .model_config import default_config_path, load_model_config

    if model_config_path is not None:
        path = model_config_path
    else:
        path = default_config_path()
        if not path.exists():
            return None
    try:
        return load_model_config(path)
    except ValueError as exc:
        click.echo(
            click.style(f"model config error: {exc}", fg="red"), err=True
        )
        sys.exit(1)


@click.group()
@click.option(
    "--project-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Project root directory. Defaults to CWD.",
)
@click.pass_context
def cli(ctx: click.Context, project_root: Optional[Path]) -> None:
    ctx.ensure_object(dict)
    ctx.obj["project_root"] = project_root


# Phase 3 P1: register the agent sub-group. The actual command
# implementations live in planner/agent/cli.py.
cli.add_command(agent_group)


@cli.command("run")
@click.option(
    "--env",
    "env",
    type=click.Choice(["development", "production"]),
    required=True,
    help="Target environment.",
)
@click.option(
    "--script",
    "script",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the input script.",
)
@click.option(
    "--out",
    "out",
    type=click.Path(path_type=Path),
    required=True,
    help="Output run directory.",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Optional config file override.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Allow overwriting an existing run directory (development only).",
)
@click.option(
    "--model-config",
    "model_config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Path to a v1.0 model config JSON (planner_provider + per-provider "
        "runtime settings). When omitted, the OS app-data config is used "
        "if present; otherwise the env config's planner_provider stands."
    ),
)
def run_cmd(
    env: str,
    script: Path,
    out: Path,
    config_path: Optional[Path],
    force: bool,
    model_config_path: Optional[Path],
) -> None:
    """Run the planner pipeline."""

    root = Path.cwd().resolve()
    try:
        config = load_config(
            env=env, project_root=root, config_path=config_path
        )
    except PlannerError as exc:
        click.echo(click.style(f"config error: {exc}", fg="red"), err=True)
        sys.exit(1)
    if force and config.is_production:
        click.echo(
            click.style(
                "Refusing --force in production. Remove the run directory "
                "manually instead.",
                fg="red",
            ),
            err=True,
        )
        sys.exit(2)
    if force:
        # Local override only — we rebuild the config to allow overwrite.
        object.__setattr__(config, "allow_overwrite_runs", True)

    # v1.0 Phase-2 hardening: production refuses to write a run
    # directory inside the project repository, mirroring the GUI's
    # ``resolve_out_dir`` policy. The check runs here (CLI boundary)
    # so a tampered shell script can't sneak the path past the GUI.
    if config.is_production and is_inside_repo(out, root):
        click.echo(
            click.style(
                f"Production run refuses to write inside the project "
                f"repository ({out} is inside {root}). Use an --out "
                f"directory outside the repo, e.g. "
                f"~/Library/Application Support/ShortDramaPlanner/runs/ "
                f"(macOS) or %APPDATA%/ShortDramaPlanner/runs/ (Windows).",
                fg="red",
            ),
            err=True,
        )
        sys.exit(2)

    # v1.0 P1-1: load model config and let it steer the provider choice
    # + inject runtime settings into the provider instance. The env
    # config still owns the production fail-closed boundaries.
    model_config = _load_model_config_for_cli(model_config_path)
    if model_config is not None and model_config.planner_provider != "deterministic":
        object.__setattr__(config, "planner_provider", model_config.planner_provider)

    try:
        result = run_pipeline(
            script_path=script,
            out_dir=out,
            config=config,
            model_config=model_config,
        )
    except PlannerError as exc:
        click.echo(click.style(str(exc), fg="red"), err=True)
        sys.exit(1)

    summary = {
        "env": config.env,
        "run_dir": str(result.run_dir),
        "counts": {
            "characters": result.character_count,
            "locations": result.location_count,
            "props": result.prop_count,
            "shots": result.shot_count,
        },
        "artifacts": {k: str(v) for k, v in result.artifacts.items()},
    }
    _echo_json(summary)
    click.echo(
        click.style(
            f"✔ Wrote {len(result.artifacts)} artifacts to {result.run_dir}",
            fg="green",
        )
    )


@cli.command("validate")
@click.option(
    "--env",
    "env",
    type=click.Choice(["development", "production"]),
    required=True,
    help="Target environment (informational; validation is environment-agnostic).",
)
@click.option(
    "--run",
    "run_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Run directory to validate.",
)
def validate_cmd(env: str, run_dir: Path) -> None:
    """Validate a run directory."""

    try:
        report = validate_run(run_dir, expected_env=env)
    except PlannerError as exc:
        click.echo(click.style(str(exc), fg="red"), err=True)
        sys.exit(1)

    # Surface env mismatches as validation warnings so callers notice
    # when they validate a production run with --env development or
    # vice versa.
    if report.env_mismatch:
        click.echo(
            click.style(
                f"⚠ env mismatch: --env {env!r} but run was produced "
                f"under {report.run_env!r}",
                fg="yellow",
            ),
            err=True,
        )

    _echo_json(
        {
            "ok": report.ok,
            "stats": report.stats,
            "errors": report.errors,
            "warnings": report.warnings,
        }
    )

    if report.ok:
        click.echo(click.style("✔ Validation passed.", fg="green"))
        sys.exit(0)
    click.echo(click.style("✖ Validation failed.", fg="red"), err=True)
    sys.exit(1)


def main() -> None:
    cli(obj={})


@cli.command("batch")
@click.option(
    "--env",
    "env",
    type=click.Choice(["development", "production"]),
    default=None,
    help="Target environment. Defaults to project.json default_env when --project is given.",
)
@click.option(
    "--scripts",
    "scripts",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Directory containing one .txt script per episode. Defaults to project.json script_dir.",
)
@click.option(
    "--out",
    "out_dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Output root. Defaults to project.json output_dir.",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Optional config file override.",
)
@click.option(
    "--force",
    "force",
    is_flag=True,
    default=False,
    help="Allow overwriting existing per-episode subdirs (development only).",
)
@click.option(
    "--no-fail-fast",
    "no_fail_fast",
    is_flag=True,
    default=False,
    help="Continue past per-episode failures (default: abort on first failure).",
)
@click.option(
    "--skip-validation",
    "skip_validation",
    is_flag=True,
    default=False,
    help="Skip per-episode validate_run (faster; validation also runs separately).",
)
@click.option(
    "--model-config",
    "model_config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Path to a v1.0 model config JSON. When omitted, the OS "
        "app-data config is used if present."
    ),
)
@click.option(
    "--project",
    "project_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help=(
        "Path to a v1.0 project folder (with project.json). When "
        "given, scripts_dir / output_dir / default_env / "
        "default_provider are read from project.json; explicit "
        "--scripts / --out / --env flags override the project values."
    ),
)
def batch_cmd(
    env: Optional[str],
    scripts: Optional[Path],
    out_dir: Optional[Path],
    config_path: Optional[Path],
    force: bool,
    no_fail_fast: bool,
    skip_validation: bool,
    model_config_path: Optional[Path],
    project_dir: Optional[Path],
) -> None:
    """Run the planner pipeline on every .txt script in SCRIPTS, writing
    per-episode subdirs under OUT_DIR plus batch_summary.json."""

    root = Path.cwd().resolve()
    from .batch import BatchOptions, run_batch  # local import keeps base install lean
    from .project import load_project

    # v1.0 P2-3: resolve scripts_dir / out_dir / env / provider from
    # project.json when --project is given. Explicit CLI flags override
    # the project values; project values override config defaults.
    project = None
    if project_dir is not None:
        try:
            project = load_project(project_dir)
        except PlannerError as exc:
            click.echo(click.style(f"config error: {exc}", fg="red"), err=True)
            sys.exit(1)

    resolved_env = env
    if resolved_env is None and project is not None:
        resolved_env = project.default_env
    if resolved_env is None:
        click.echo(
            click.style(
                "--env is required (or pass --project with a default_env).",
                fg="red",
            ),
            err=True,
        )
        sys.exit(2)

    def _resolve_under_project(value: Optional[Path], project_field: str) -> Path:
        """Resolve a path option: explicit CLI value > project.json
        field (relative to project_dir) > error."""
        if value is not None:
            return value
        if project is not None:
            raw = getattr(project, project_field)
            p = Path(raw)
            if not p.is_absolute():
                p = project_dir / p  # type: ignore[union-attr]
            return p
        click.echo(
            click.style(
                f"--{project_field.replace('_', '-')} is required (or pass "
                "--project).",
                fg="red",
            ),
            err=True,
        )
        sys.exit(2)

    resolved_scripts = _resolve_under_project(scripts, "script_dir")
    resolved_out = _resolve_under_project(out_dir, "output_dir")

    # Single load_config at the CLI boundary; run_batch reuses the
    # resolved config. Pre-flight `--force` policy here so we can
    # exit with a friendly message BEFORE scanning the scripts dir.
    try:
        config = load_config(
            env=resolved_env, project_root=root, config_path=config_path
        )
    except PlannerError as exc:
        click.echo(click.style(f"config error: {exc}", fg="red"), err=True)
        sys.exit(1)

    if force and config.is_production:
        click.echo(
            click.style(
                "Refusing --force in production. Remove episode subdirs "
                "manually instead.",
                fg="red",
            ),
            err=True,
        )
        sys.exit(2)
    if force:
        object.__setattr__(config, "allow_overwrite_runs", True)

    # project.json default_provider overrides the env config's
    # planner_provider (when non-deterministic). model_config still
    # wins over both (it's the most explicit operator signal).
    if (
        project is not None
        and project.default_provider != "deterministic"
    ):
        object.__setattr__(config, "planner_provider", project.default_provider)

    # v1.0 P1-1: load model config and let it steer the provider choice
    # + inject runtime settings into the provider instance.
    model_config = _load_model_config_for_cli(model_config_path)
    if model_config is not None and model_config.planner_provider != "deterministic":
        object.__setattr__(config, "planner_provider", model_config.planner_provider)

    options = BatchOptions(
        env=resolved_env,
        scripts_dir=resolved_scripts,
        out_dir=resolved_out,
        fail_fast=not no_fail_fast,
        config_path=config_path,
        repo_root=root,
        skip_validation=skip_validation,
    )

    try:
        summary = run_batch(options, config=config, model_config=model_config)
    except PlannerError as exc:
        click.echo(click.style(str(exc), fg="red"), err=True)
        sys.exit(1)

    _echo_json(summary.model_dump(mode="json"))
    click.echo(
        click.style(
            f"✔ Batch {summary.batch_id}: "
            f"{summary.totals['episodes_done']}/{summary.totals['episodes_total']} episodes done, "
            f"{summary.totals['episodes_failed']} failed. "
            f"Summary: {options.out_dir.resolve() / 'batch_summary.json'}",
            fg="green" if summary.totals["episodes_failed"] == 0 else "yellow",
        )
    )

    if summary.totals["episodes_failed"] > 0:
        sys.exit(2)
    sys.exit(0)


# --- project group -----------------------------------------------------


@cli.group("project")
def project_group() -> None:
    """Create and validate v1.0 project folders (``project.json``)."""


@project_group.command("init")
@click.option(
    "--dir",
    "project_dir",
    required=True,
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    help="Folder to initialize. Created if it does not exist.",
)
@click.option(
    "--name",
    "project_name",
    default=None,
    help="Human-readable project name. Defaults to the folder basename.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite an existing project.json in the folder.",
)
def project_init_cmd(project_dir: Path, project_name: Optional[str], force: bool) -> None:
    """Initialize a project folder with ``project.json`` + subdirs."""

    from .project import init_project

    try:
        project = init_project(project_dir, project_name=project_name, overwrite=force)
    except PlannerError as exc:
        click.echo(click.style(f"config error: {exc}", fg="red"), err=True)
        sys.exit(1)
    click.echo(
        click.style(
            f"✔ Initialized project '{project.project_name}' at "
            f"{project_dir.resolve()}",
            fg="green",
        )
    )
    _echo_json(project.model_dump(mode="json"))


@project_group.command("validate")
@click.option(
    "--dir",
    "project_dir",
    required=True,
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
)
def project_validate_cmd(project_dir: Path) -> None:
    """Pre-flight check: project.json is valid, scripts_dir exists,
    default_env / default_provider are sensible."""

    from .project import validate_project

    try:
        report = validate_project(project_dir)
    except PlannerError as exc:
        click.echo(click.style(f"config error: {exc}", fg="red"), err=True)
        sys.exit(1)
    _echo_json(report.model_dump(mode="json"))
    if not report.ok:
        click.echo(
            click.style(
                f"✖ Project {project_dir} failed validation: {len(report.errors)} error(s).",
                fg="red",
            ),
            err=True,
        )
        sys.exit(2)
    if report.warnings:
        click.echo(
            click.style(
                f"⚠ Project {project_dir} OK with {len(report.warnings)} warning(s).",
                fg="yellow",
            )
        )
    else:
        click.echo(
            click.style(
                f"✔ Project {project_dir} OK "
                f"({report.script_count} script(s) ready).",
                fg="green",
            )
        )


# --- export ------------------------------------------------------------


@cli.command("export")
@click.option(
    "--run",
    "run_dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Path to a single run directory (with run_summary.json).",
)
@click.option(
    "--batch",
    "batch_dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Path to a batch directory (with batch_summary.json).",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["markdown", "html", "csv"]),
    required=True,
    help="Render the report as Markdown, HTML, or CSV.",
)
@click.option(
    "--output",
    "output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Output path. Defaults to <run_id>.{md,html,csv} next to the run.",
)
def export_cmd(
    run_dir: Optional[Path],
    batch_dir: Optional[Path],
    fmt: str,
    output: Optional[Path],
) -> None:
    """Export a run or batch as Markdown / HTML / CSV for human review."""

    from .export import export_batch, export_run

    if (run_dir is None) == (batch_dir is None):
        click.echo(
            click.style(
                "Pass exactly one of --run or --batch.",
                fg="red",
            ),
            err=True,
        )
        sys.exit(2)
    try:
        if run_dir is not None:
            target = export_run(run_dir, fmt, output=output)
        else:
            target = export_batch(batch_dir, fmt, output=output)  # type: ignore[arg-type]
    except PlannerError as exc:
        click.echo(click.style(f"export error: {exc}", fg="red"), err=True)
        sys.exit(1)
    click.echo(
        click.style(
            f"✔ Wrote {fmt} report ({target.stat().st_size} bytes) → {target}",
            fg="green",
        )
    )


if __name__ == "__main__":  # pragma: no cover
    main()