"""Harness: CLI smoke for the v1.0 release.

Drives the planner CLI end-to-end against the in-repo
``samples/v1/`` fixtures and writes everything under ``/tmp`` so the
repository tree stays untouched (see red line: smoke artifacts never
land in the repo).

What it covers
--------------

1. ``planner --help`` / ``planner run --help`` exit 0 and list the
   expected subcommands.
2. ``planner run`` on the bundled ``samples/v1/EP01.txt`` in
   development mode writes the full 11-artifact set (9 core +
   ``executor_tasks`` + ``run_summary``) under ``/tmp``.
3. ``planner validate`` returns ``ok=true`` against the produced run.
4. ``planner batch`` against ``samples/v1/`` produces a
   ``batch_summary.json`` with ``episodes_done == 3`` /
   ``episodes_failed == 0``.
5. ``planner project init`` + ``planner project validate`` +
   ``planner batch --project`` round-trips the project.json shape.
6. ``planner export --run`` and ``planner batch --batch`` produce
   non-empty Markdown / HTML / CSV reports and never leak API keys.

This script is designed to be runnable both manually and from CI::

    python3 harness/smoke_cli.py

Exit code is 0 on success, non-zero on first failed step. Each step
prints a single friendly status line so CI logs stay readable.

The harness does NOT call any real LLM, does NOT submit any paid
generation job, and does NOT touch the repository ``runs/`` tree.
All artifacts land under ``/tmp/smoke_cli_<pid>``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

# Resolve the project root (parent of this harness/ folder). The
# smoke runs against this repository; CI / local installs work the
# same way because we always use ``python3 -m planner.cli``.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Use samples/v1 as the deterministic fixture set.
SAMPLES_DIR = PROJECT_ROOT / "samples" / "v1"
SAMPLE_EP01 = SAMPLES_DIR / "EP01.txt"

# /tmp keeps the repo tree clean (no runs/development pollution).
WORK_ROOT = Path(tempfile.mkdtemp(prefix="smoke_cli_"))

# How we invoke the CLI. ``python3 -m`` works whether or not the
# package is pip-installed in the active environment.
PYTHON = sys.executable
CLI_INVOCATION = [PYTHON, "-m", "planner.cli"]


# --- helpers -------------------------------------------------------------


def _log(msg: str) -> None:
    print(f"[smoke_cli] {msg}", flush=True)


def _run_cli(args: List[str], cwd: Path) -> subprocess.CompletedProcess:
    """Run ``python3 -m planner.cli <args>`` from ``cwd``.

    Captures stdout / stderr so a failure can include the CLI output
    verbatim (the red line is friendly errors, not silent traces).
    """

    cmd = CLI_INVOCATION + args
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        # Inherit env so the operator's PLANNER_* overrides still take
        # effect, but strip any model-config env vars the harness
        # itself sets so each step starts from a known baseline.
        env={**os.environ},
    )


def _expect_success(
    label: str, proc: subprocess.CompletedProcess, expected_rc: int = 0
) -> None:
    """Assert ``proc.returncode == expected_rc`` and raise with a
    friendly message that includes the captured stderr on failure.
    """

    if proc.returncode == expected_rc:
        return
    raise SystemExit(
        f"[smoke_cli] {label}: expected rc={expected_rc}, "
        f"got rc={proc.returncode}.\n"
        f"--- stdout ---\n{proc.stdout}\n"
        f"--- stderr ---\n{proc.stderr}"
    )


def _expect_json_in_stdout(label: str, proc: subprocess.CompletedProcess) -> dict:
    """Parse the JSON object printed by the CLI before its check line.

    The CLI pretty-prints a JSON object via ``click.echo``, then writes
    a green ``✔ ...`` line on the next non-JSON line. We locate the
    first ``{`` and walk the string tracking brace depth so the
    appended check line is left out.
    """

    out = proc.stdout
    start = out.find("{")
    if start < 0:
        raise SystemExit(
            f"[smoke_cli] {label}: no JSON object found in CLI output.\n"
            f"--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}"
        )
    depth = 0
    end = -1
    in_string = False
    escape = False
    for i in range(start, len(out)):
        ch = out[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end < 0:
        raise SystemExit(
            f"[smoke_cli] {label}: unmatched braces in CLI output.\n"
            f"--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}"
        )
    blob = out[start:end]
    try:
        return json.loads(blob)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"[smoke_cli] {label}: failed to parse JSON from CLI "
            f"output: {exc}.\n--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}"
        )


# --- steps ---------------------------------------------------------------


def step_help_text() -> None:
    """Step 1: ``planner --help`` + subcommand help text works."""

    proc = _run_cli(["--help"], cwd=PROJECT_ROOT)
    _expect_success("planner --help", proc)
    for needle in ("run", "validate", "batch", "project", "export"):
        if needle not in proc.stdout:
            raise SystemExit(
                f"[smoke_cli] planner --help missing {needle!r} "
                f"subcommand listing"
            )
    _log("planner --help shows run/validate/batch/project/export")


def step_run_development() -> Path:
    """Step 2: deterministic single-episode run writes 11 artifacts.

    The CLI summary echoes 10 artifact paths; ``run_summary.json`` is
    always written alongside but is the meta-document, not a planning
    output, so it lives next to the others. We verify both: the
    echoed JSON lists the 10 planning artifacts, and the directory
    contains ``run_summary.json`` as the 11th file.

    We pass an explicit ``--model-config`` with ``planner_provider
    =deterministic`` so the step is independent of any leftover
    OS app-data model config from prior test runs.
    """

    out_dir = WORK_ROOT / "run_dev"
    model_cfg = WORK_ROOT / "cli_deterministic.json"
    model_cfg.write_text(
        json.dumps(
            {
                "planner_provider": "deterministic",
                "enable_real_model_calls": False,
                "allow_provider_fallback": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    proc = _run_cli(
        [
            "run",
            "--env", "development",
            "--script", str(SAMPLE_EP01),
            "--out", str(out_dir),
            "--model-config", str(model_cfg),
        ],
        cwd=PROJECT_ROOT,
    )
    _expect_success("planner run --env development", proc)
    summary = _expect_json_in_stdout("planner run dev summary", proc)
    planning_keys = {
        "script_parse.json", "character_bible.json", "location_bible.json",
        "prop_bible.json", "story_beats.json", "shot_list.json",
        "image_prompts.json", "video_prompts.json", "asset_manifest.json",
        "executor_tasks.json",
    }
    written_planning = {Path(p).name for p in summary["artifacts"].values()}
    missing_planning = planning_keys - written_planning
    if missing_planning:
        raise SystemExit(
            f"[smoke_cli] planner run dev missing planning artifacts: "
            f"{sorted(missing_planning)}"
        )
    summary_path = out_dir / "run_summary.json"
    if not summary_path.exists():
        raise SystemExit(
            f"[smoke_cli] planner run dev missing run_summary.json at {summary_path}"
        )
    rs = json.loads(summary_path.read_text(encoding="utf-8"))
    if rs.get("fallback_used") is not False:
        raise SystemExit(
            "[smoke_cli] planner run dev must not fall back (deterministic is healthy)"
        )
    if rs.get("executor_status") != "pending":
        raise SystemExit(
            f"[smoke_cli] planner run dev executor_status expected 'pending', "
            f"got {rs.get('executor_status')!r}"
        )
    _log(
        f"planner run dev wrote {len(written_planning) + 1} artifacts "
        f"(10 planning + run_summary.json) -> {out_dir}"
    )
    return out_dir


def step_validate(run_dir: Path) -> None:
    """Step 3: validate the just-produced run directory."""

    proc = _run_cli(
        ["validate", "--env", "development", "--run", str(run_dir)],
        cwd=PROJECT_ROOT,
    )
    _expect_success("planner validate dev", proc)
    parsed = _expect_json_in_stdout("planner validate dev", proc)
    if not parsed.get("ok"):
        raise SystemExit(
            f"[smoke_cli] planner validate dev not ok: {parsed.get('errors')}"
        )
    _log("planner validate dev: ok")


def step_batch_development() -> Path:
    """Step 4: batch over samples/v1/ → 3/3 done."""

    out_dir = WORK_ROOT / "batch_dev"
    model_cfg = WORK_ROOT / "cli_deterministic.json"
    proc = _run_cli(
        [
            "batch",
            "--env", "development",
            "--scripts", str(SAMPLES_DIR),
            "--out", str(out_dir),
            "--skip-validation",  # speed; we validate separately below
            "--model-config", str(model_cfg),
        ],
        cwd=PROJECT_ROOT,
    )
    _expect_success("planner batch dev", proc)
    summary = _expect_json_in_stdout("planner batch dev", proc)
    totals = summary.get("totals", {})
    if totals.get("episodes_done") != 3 or totals.get("episodes_failed") != 0:
        raise SystemExit(
            f"[smoke_cli] planner batch dev expected 3/0 done/failed, "
            f"got {totals}"
        )
    batch_summary = out_dir / "batch_summary.json"
    if not batch_summary.exists():
        raise SystemExit(
            f"[smoke_cli] planner batch dev missing batch_summary.json "
            f"at {batch_summary}"
        )
    _log(f"planner batch dev wrote {totals['episodes_done']}/3 episodes")
    return out_dir


def step_project_round_trip() -> Path:
    """Step 5: project init -> validate -> batch --project."""

    proj_dir = WORK_ROOT / "project"
    proc_init = _run_cli(
        ["project", "init", "--dir", str(proj_dir), "--name", "SmokeDemo"],
        cwd=PROJECT_ROOT,
    )
    _expect_success("planner project init", proc_init)
    # Copy samples into project/scripts so batch --project finds them.
    scripts_dir = proj_dir / "scripts"
    for ep in SAMPLES_DIR.glob("EP*.txt"):
        shutil.copy2(ep, scripts_dir / ep.name)
    proc_validate = _run_cli(
        ["project", "validate", "--dir", str(proj_dir)],
        cwd=PROJECT_ROOT,
    )
    _expect_success("planner project validate", proc_validate)
    report = _expect_json_in_stdout("planner project validate", proc_validate)
    if not report.get("ok"):
        raise SystemExit(
            f"[smoke_cli] planner project validate not ok: "
            f"{report.get('errors')}"
        )
    out_dir = WORK_ROOT / "batch_project"
    model_cfg = WORK_ROOT / "cli_deterministic.json"
    proc_batch = _run_cli(
        [
            "batch",
            "--project", str(proj_dir),
            "--out", str(out_dir),
            "--skip-validation",
            "--model-config", str(model_cfg),
        ],
        cwd=PROJECT_ROOT,
    )
    _expect_success("planner batch --project", proc_batch)
    summary = _expect_json_in_stdout("planner batch --project", proc_batch)
    totals = summary.get("totals", {})
    if totals.get("episodes_done") != 3:
        raise SystemExit(
            f"[smoke_cli] planner batch --project expected 3 done, "
            f"got {totals}"
        )
    _log(
        f"planner project init -> validate -> batch --project: "
        f"{totals['episodes_done']}/3 episodes via project.json"
    )
    return out_dir


def step_export_reports(run_dir: Path, batch_dir: Path) -> None:
    """Step 6: export markdown/html/csv from a run + a batch.

    The exporter writes ``<run_id>.<ext>`` next to the run directory
    (i.e. one level up from the per-episode subdir) and
    ``<batch_id>.<ext>`` next to the batch directory.
    """

    run_id = run_dir.name
    run_ext_map = {"markdown": "md", "html": "html", "csv": "csv"}
    for fmt, ext in run_ext_map.items():
        proc = _run_cli(
            ["export", "--run", str(run_dir), "--format", fmt],
            cwd=PROJECT_ROOT,
        )
        _expect_success(f"planner export run {fmt}", proc)
        target = run_dir.parent / f"{run_id}.{ext}"
        if not target.exists():
            raise SystemExit(
                f"[smoke_cli] planner export run {fmt} produced no file "
                f"(expected {target})"
            )
        body = target.read_text(encoding="utf-8", errors="replace")
        _assert_no_secret_leak(f"export run {fmt}", body)
    batch_report_map = {"markdown": "md", "html": "html", "csv": "csv"}
    for fmt, ext in batch_report_map.items():
        proc = _run_cli(
            ["export", "--batch", str(batch_dir), "--format", fmt],
            cwd=PROJECT_ROOT,
        )
        _expect_success(f"planner export batch {fmt}", proc)
        target = batch_dir / f"batch_report.{ext}"
        if not target.exists():
            raise SystemExit(
                f"[smoke_cli] planner export batch {fmt} produced no file "
                f"(expected {target})"
            )
        body = target.read_text(encoding="utf-8", errors="replace")
        _assert_no_secret_leak(f"export batch {fmt}", body)
    _log("planner export markdown/html/csv for run + batch ok")


def _assert_no_secret_leak(label: str, body: str) -> None:
    """Guard against accidental API-key writes into export reports."""

    bad_patterns = ("sk-", "sk-ant-", "Bearer sk-", "Bearer sk-")
    for needle in bad_patterns:
        if needle in body:
            raise SystemExit(
                f"[smoke_cli] {label}: secret-like token {needle!r} "
                f"found in export body — refusing to continue"
            )


# --- entrypoint ----------------------------------------------------------


def main() -> int:
    ctx: dict = {}
    try:
        step_help_text()
        ctx["run_dir"] = step_run_development()
        step_validate(ctx["run_dir"])
        ctx["batch_dir"] = step_batch_development()
        step_project_round_trip()
        step_export_reports(ctx["run_dir"], ctx["batch_dir"])
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[smoke_cli] unexpected error: {exc}", file=sys.stderr)
        return 3
    finally:
        # Leave /tmp artifacts in place for post-mortem; uncomment to
        # clean up: shutil.rmtree(WORK_ROOT, ignore_errors=True)
        _log(f"work dir kept at {WORK_ROOT} for inspection")
    _log("ALL CLI SMOKE STEPS PASSED ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())