"""Click subcommands for the planner product agent.

Phase 3 P1 ships ``diagnose`` (fully implemented). Phase 3 P2
promotes ``review-run`` (single-run prompt-bible consistency) and
``review-batch`` (cross-episode character / location / prop id
consistency + orphan shot references) from stubs to fully
implemented read-only reviews.

Hard rules enforced here:

* No subprocess / shell - agent is a pure-Python CLI.
* No LLM SDK imports - diagnostic rules are pure-data.
* ``--write-report`` goes through
  :func:`_check_and_write_report` which respects the
  production repo-internal refuse policy (mirror of
  ``planner.cli.run_cmd`` for ``--out``).
* All errors flow through ``PlannerError``; never leak a Python
  traceback to the user (mirrors ``planner.cli.run_cmd``). The
  review-run and review-batch engines additionally guard against
  legitimate-JSON / wrong-shape artifacts so a non-dict top level
  degrades to a finding instead of an ``AttributeError`` traceback.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import click

from planner.env import is_inside_repo
from planner.exceptions import PlannerError


def _echo_json(data: Dict[str, Any]) -> None:
    click.echo(json.dumps(data, ensure_ascii=False, indent=2))


def _resolve_project_root(ctx: click.Context) -> Path:
    """Resolve project root from Click context, falling back to CWD."""
    root = ctx.obj.get("project_root") if ctx.obj else None
    return Path(root).resolve() if root else Path.cwd().resolve()


def _check_and_write_report(
    report_dict: Dict[str, Any],
    write_report: Path,
    project_root: Path,
) -> None:
    """Implement the ``--write-report`` policy.

    * ``dev`` run + path inside repo -> stderr yellow warning, allow
    * ``production`` run + path inside repo -> hard refuse ``rc=2``
    * ``run_summary`` missing (env == None) -> default to production
      policy (fail-closed)
    * Path outside repo -> write unconditionally (dev or production)

    The check happens **before** any directory creation so a refused
    write leaves no residue on disk.
    """
    report_path = Path(write_report).resolve()

    # Determine run env from the report; missing env defaults to
    # production policy (conservative).
    run_env = report_dict.get("env") or "production"

    if is_inside_repo(report_path, project_root):
        if run_env == "production":
            click.echo(
                click.style(
                    f"production diagnose refuses to write inside "
                    f"the project repository ({report_path} is inside "
                    f"{project_root}). Use a path outside the repo, "
                    f"e.g. ~/.planner/agent_reports/.",
                    fg="red",
                ),
                err=True,
            )
            sys.exit(2)
        # development — allow but warn loudly
        click.echo(
            click.style(
                f"WARNING: --write-report {report_path} is inside "
                f"the project repo; dev-only convenience. Production "
                f"runs will be hard-rejected.",
                fg="yellow",
            ),
            err=True,
        )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report_dict, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    click.echo(f"report written to {report_path}", err=True)


def _render_markdown(
    report_dict: Dict[str, Any], *, title: str = "Diagnose Report"
) -> None:
    """Render the report as Markdown to stdout.

    ``title`` lets review-run reuse this renderer; the version field
    label follows whichever version key the report carries
    (``diagnose_version`` or ``review_version``) so diagnose output is
    unchanged.
    """
    version_key = (
        "diagnose_version" if "diagnose_version" in report_dict else "review_version"
    )
    lines: list[str] = [
        f"# {title} - {report_dict.get('run_dir', '?')}",
        "",
        f"- **run_id**: `{report_dict.get('run_id')}`",
        f"- **env**: `{report_dict.get('env')}`",
        f"- **status**: `{report_dict.get('status')}`",
        f"- **implementation_status**: "
        f"`{report_dict.get('implementation_status')}`",
        f"- **{version_key}**: `{report_dict.get(version_key)}`",
        "",
        "## Summary",
        "",
        report_dict.get("summary", ""),
        "",
        f"## Findings ({len(report_dict.get('findings', []))})",
        "",
    ]
    for f in report_dict.get("findings", []):
        lines.append(
            f"- **[{f['severity']}]** `{f['code']}`: {f['message']}"
        )
    if report_dict.get("counts"):
        lines += ["", "## Counts", ""]
        for k, v in report_dict["counts"].items():
            lines.append(f"- {k}: {v}")
    if report_dict.get("provider", {}).get("runtime"):
        rt = report_dict["provider"]["runtime"]
        lines += [
            "",
            "## Provider Runtime",
            "",
            f"- model: `{rt.get('model')}`",
            f"- base_url: `{rt.get('base_url')}`",
            f"- api_key_env: `{rt.get('api_key_env')}`",
            f"- enable_real_model_calls: `{rt.get('enable_real_model_calls')}`",
        ]
    click.echo("\n".join(lines))


@click.group("agent")
@click.pass_context
def agent_group(ctx: click.Context) -> None:
    """Planner product agent (read-only diagnose + review-run + stub).

    The agent is read-only by default; it never writes files unless
    ``--write-report PATH`` is given explicitly. In production
    environments, ``--write-report PATH`` with PATH inside the project
    repository is hard-refused.
    """


@agent_group.command("diagnose")
@click.argument(
    "run_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--expected-env",
    type=click.Choice(["development", "production"]),
    default=None,
    help=(
        "If given, diagnose checks run_summary.env against this value "
        "and emits an env_mismatch finding when they differ."
    ),
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "markdown"]),
    default="json",
    help="Output format (default: json).",
)
@click.option(
    "--write-report",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Write the report to this path. Default: stdout. Production "
        "runs refuse to write inside the project repo."
    ),
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    default=False,
    help="Also emit the Chinese summary on stderr.",
)
@click.pass_context
def diagnose_cmd(
    ctx: click.Context,
    run_dir: Path,
    expected_env: Optional[str],
    fmt: str,
    write_report: Optional[Path],
    verbose: bool,
) -> None:
    """Diagnose a completed run directory.

    RUN_DIR is the path produced by ``planner run`` or
    ``planner batch`` (a directory containing ``run_summary.json``).
    Agent cannot access the GUI's in-memory run registry; always
    pass a filesystem path.
    """
    # Local imports keep the planner/agent package off the base
    # install's import path only if the user opts in (we currently
    # always include it, but the convention matches planner.batch).
    from planner.agent.diagnose import diagnose_run_dir

    project_root = _resolve_project_root(ctx)
    try:
        report = diagnose_run_dir(run_dir, expected_env=expected_env)
    except PlannerError as exc:
        click.echo(click.style(f"agent error: {exc}", fg="red"), err=True)
        sys.exit(1)
    except OSError as exc:
        click.echo(click.style(f"agent error: {exc}", fg="red"), err=True)
        sys.exit(1)

    report_dict = report.model_dump(mode="json")

    # --write-report may itself exit(2) on policy refusal; do this
    # BEFORE writing stdout so the operator sees the refusal message
    # in stderr.
    if write_report is not None:
        _check_and_write_report(report_dict, write_report, project_root)

    # Stdout: JSON or Markdown
    if fmt == "json":
        _echo_json(report_dict)
    else:
        _render_markdown(report_dict)

    # Stderr: Chinese summary (always for markdown, on -v for json)
    if verbose or fmt == "markdown":
        click.echo(click.style("\n摘要：", fg="cyan"), err=True)
        click.echo(report.summary, err=True)

    # Exit code per plan: errors -> 1, else 0. Policy refusal is
    # raised inside _check_and_write_report (rc=2) and never
    # reaches here.
    if report.status == "errors":
        sys.exit(1)
    sys.exit(0)


@agent_group.command("review-run")
@click.argument(
    "run_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--expected-env",
    type=click.Choice(["development", "production"]),
    default=None,
    help=(
        "If given, review checks run_summary.env against this value "
        "and emits an env_mismatch finding when they differ."
    ),
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "markdown"]),
    default="json",
    help="Output format (default: json).",
)
@click.option(
    "--write-report",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Write the report to this path. Default: stdout. Production "
        "runs refuse to write inside the project repo."
    ),
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    default=False,
    help="Also emit the Chinese summary on stderr.",
)
@click.pass_context
def review_run_cmd(
    ctx: click.Context,
    run_dir: Path,
    expected_env: Optional[str],
    fmt: str,
    write_report: Optional[Path],
    verbose: bool,
) -> None:
    """Review a single run's prompt-bible consistency (read-only).

    RUN_DIR is the path produced by ``planner run`` (a directory
    containing ``run_summary.json``). Checks image / video prompts
    against the bibles (character / location / prop) and emits a
    ReviewRunReport. Does NOT write run artifacts. Cross-episode
    consistency is ``review-batch``'s job.
    """
    from planner.agent.review import review_run_dir

    project_root = _resolve_project_root(ctx)
    try:
        report = review_run_dir(run_dir, expected_env=expected_env)
    except PlannerError as exc:
        click.echo(click.style(f"agent error: {exc}", fg="red"), err=True)
        sys.exit(1)
    except OSError as exc:
        click.echo(click.style(f"agent error: {exc}", fg="red"), err=True)
        sys.exit(1)

    report_dict = report.model_dump(mode="json")

    if write_report is not None:
        _check_and_write_report(report_dict, write_report, project_root)

    if fmt == "json":
        _echo_json(report_dict)
    else:
        _render_markdown(report_dict, title="Review Report")

    if verbose or fmt == "markdown":
        click.echo(click.style("\n摘要：", fg="cyan"), err=True)
        click.echo(report.summary, err=True)

    # errors -> 1, else 0. Policy refusal is rc=2 inside
    # _check_and_write_report and never reaches here.
    if report.status == "errors":
        sys.exit(1)
    sys.exit(0)


@agent_group.command("review-batch")
@click.argument(
    "batch_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--expected-env",
    type=click.Choice(["development", "production"]),
    default=None,
    help=(
        "If given, review checks batch_summary.env against this value "
        "and emits an env_mismatch finding when they differ."
    ),
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "markdown"]),
    default="json",
    help="Output format (default: json).",
)
@click.option(
    "--write-report",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Write the report to this path. Default: stdout. Production "
        "runs refuse to write inside the project repo."
    ),
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    default=False,
    help="Also emit the Chinese summary on stderr.",
)
@click.pass_context
def review_batch_cmd(
    ctx: click.Context,
    batch_dir: Path,
    expected_env: Optional[str],
    fmt: str,
    write_report: Optional[Path],
    verbose: bool,
) -> None:
    """Cross-episode continuity review of a batch (read-only).

    BATCH_DIR is the path produced by ``planner batch`` (a directory
    containing ``batch_summary.json`` and per-episode subdirectories).
    Checks character / location / prop id consistency across episodes
    + orphan shot references, and emits a ReviewBatchReport. Does NOT
    write batch artifacts, does NOT merge bibles, does NOT re-run
    per-run rv1-rv4 rules (use ``planner agent review-run`` per
    episode for prompt-bible checks).
    """
    from planner.agent.review import review_batch_dir

    project_root = _resolve_project_root(ctx)
    try:
        report = review_batch_dir(batch_dir, expected_env=expected_env)
    except PlannerError as exc:
        click.echo(click.style(f"agent error: {exc}", fg="red"), err=True)
        sys.exit(1)
    except OSError as exc:
        click.echo(click.style(f"agent error: {exc}", fg="red"), err=True)
        sys.exit(1)

    report_dict = report.model_dump(mode="json")

    if write_report is not None:
        _check_and_write_report(report_dict, write_report, project_root)

    if fmt == "json":
        _echo_json(report_dict)
    else:
        _render_markdown(report_dict, title="Review Batch Report")

    if verbose or fmt == "markdown":
        click.echo(click.style("\n摘要：", fg="cyan"), err=True)
        click.echo(report.summary, err=True)

    # errors -> 1, else 0. Policy refusal is rc=2 inside
    # _check_and_write_report and never reaches here.
    if report.status == "errors":
        sys.exit(1)
    sys.exit(0)


__all__ = ["agent_group"]
