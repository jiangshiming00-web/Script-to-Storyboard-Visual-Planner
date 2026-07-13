"""Tests for the v1.0 export command (``planner/export.py``).

Pins the v1.0 contract from the release plan §11:

- ``planner export --run DIR --format {markdown,html,csv}`` writes a
  human-readable report.
- ``planner export --batch DIR --format {markdown,html,csv}`` writes
  a combined report across all per-episode runs.
- The report always carries: provider audit, fallback status,
  validation result (when present), all bibles, beats, shots,
  prompts, executor tasks.
- The report never carries literal API keys (only env var names).
- Production runs surface their audit fields the same way dev runs
  do (no special-casing).
"""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
from pathlib import Path

import pytest

from planner.batch import BatchOptions, run_batch
from planner.exceptions import PlannerError
from planner.export import (
    VALID_FORMATS,
    export_batch,
    export_run,
    load_batch,
    load_run,
)
from planner.pipeline import run as run_pipeline
from planner.cli import export_cmd


# ---- fixtures --------------------------------------------------------


@pytest.fixture
def single_run(tmp_path: Path) -> Path:
    """Run the pipeline once deterministically into ``tmp_path/run``.

    Returns the run directory (containing all 11 artifacts).
    """

    repo = tmp_path / "fake_repo"
    repo.mkdir()
    (repo / "config").mkdir()
    (repo / "config" / "development.json").write_text(
        json.dumps(
            {
                "env": "development",
                "allow_overwrite_runs": True,
                "executor_default_status": "pending",
                "submit_paid_jobs": False,
                "log_level": "DEBUG",
                "executor_dry_run": True,
                "data_root": "data/development",
                "assets_root": "assets/development",
                "runs_root": "runs/development",
                "logs_root": "logs/development",
                "schema_strict": False,
                "planner_provider": "deterministic",
                "allow_provider_fallback": True,
            }
        ),
        encoding="utf-8",
    )
    (repo / "data" / "development" / "input_scripts").mkdir(parents=True)
    script = repo / "data" / "development" / "input_scripts" / "EP01.txt"
    script.write_text(
        "EP01 — Test\n\n"
        "场 1 内景 咖啡馆 — 日\n"
        "林夏走进咖啡馆，点了一杯美式。\n"
        "苏晨（紧张）：你来了。\n",
        encoding="utf-8",
    )
    from planner.env import load_config

    cfg = load_config(env="development", project_root=repo)
    out_dir = tmp_path / "run"
    run_pipeline(script_path=script, out_dir=out_dir, config=cfg)
    return out_dir


@pytest.fixture
def batch_runs(tmp_path: Path) -> Path:
    """Run a 2-episode batch and return the batch root directory."""

    repo = tmp_path / "fake_repo"
    repo.mkdir()
    (repo / "config").mkdir()
    (repo / "config" / "development.json").write_text(
        json.dumps(
            {
                "env": "development",
                "allow_overwrite_runs": True,
                "executor_default_status": "pending",
                "submit_paid_jobs": False,
                "log_level": "DEBUG",
                "executor_dry_run": True,
                "data_root": "data/development",
                "assets_root": "assets/development",
                "runs_root": "runs/development",
                "logs_root": "logs/development",
                "schema_strict": False,
                "planner_provider": "deterministic",
                "allow_provider_fallback": True,
            }
        ),
        encoding="utf-8",
    )
    scripts = repo / "scripts"
    scripts.mkdir()
    for n, name in enumerate(("EP01", "EP02"), start=1):
        (scripts / f"{name}.txt").write_text(
            f"{name} — Test\n\n场 1 内景 咖啡馆 — 日\n林夏走进咖啡馆。\n",
            encoding="utf-8",
        )
    options = BatchOptions(
        env="development",
        scripts_dir=scripts,
        out_dir=tmp_path,
        fail_fast=True,
        config_path=repo / "config" / "development.json",
        repo_root=repo,
        skip_validation=True,
    )
    from planner.env import load_config
    cfg = load_config(env="development", project_root=repo)
    summary = run_batch(options, config=cfg)
    assert summary.totals["episodes_failed"] == 0
    return tmp_path


# ---- load_run / load_batch -------------------------------------------


def test_load_run_reads_all_eleven_artifacts(single_run: Path) -> None:
    run = load_run(single_run)
    assert run.summary.get("env") == "development"
    assert run.script_parse.get("script_id") == "EP01"
    # bible shapes are list-of-records (Pydantic-driven).
    assert isinstance(run.character_bible.get("characters"), list)
    assert isinstance(run.location_bible.get("locations"), list)
    assert isinstance(run.prop_bible.get("props"), list)
    assert isinstance(run.story_beats.get("beats"), list)
    assert isinstance(run.shot_list.get("shots"), list)
    assert isinstance(run.image_prompts.get("image_prompts"), list)
    assert isinstance(run.video_prompts.get("video_prompts"), list)
    assert isinstance(run.asset_manifest.get("assets"), list)
    assert isinstance(run.executor_tasks.get("tasks"), list)


def test_load_run_rejects_non_run_directory(tmp_path: Path) -> None:
    with pytest.raises(PlannerError, match="No run_summary.json"):
        load_run(tmp_path)


def test_load_batch_reads_all_runs(batch_runs: Path) -> None:
    runs = load_batch(batch_runs)
    assert len(runs) == 2
    for run in runs:
        assert "EP" in run.run_id


def test_load_batch_rejects_non_batch_directory(tmp_path: Path) -> None:
    with pytest.raises(PlannerError, match="No batch_summary.json"):
        load_batch(tmp_path)


# ---- export_run ------------------------------------------------------


def test_export_run_markdown(single_run: Path, tmp_path: Path) -> None:
    target = export_run(single_run, "markdown")
    assert target.suffix == ".md"
    text = target.read_text(encoding="utf-8")
    # Required sections.
    for required in (
        "Provider audit",
        "Character bible",
        "Location bible",
        "Prop bible",
        "Story beats",
        "Shot list",
        "Image prompts",
        "Video prompts",
        "Executor tasks",
    ):
        assert required in text, f"Markdown export missing section: {required}"
    # Provider audit fields surfaced.
    assert "requested_provider" in text
    assert "effective_provider" in text
    # Belt-and-braces: never leak a literal sk-... key.
    assert not re.search(r"sk-[A-Za-z0-9_\-]{16,}", text)


def test_export_run_html(single_run: Path) -> None:
    target = export_run(single_run, "html")
    assert target.suffix == ".html"
    text = target.read_text(encoding="utf-8")
    assert text.lstrip().startswith("<!doctype html>")
    assert "<h1>" in text
    # No literal API keys.
    assert not re.search(r"sk-[A-Za-z0-9_\-]{16,}", text)


def test_export_run_csv(single_run: Path) -> None:
    target = export_run(single_run, "csv")
    assert target.suffix == ".csv"
    text = target.read_text(encoding="utf-8")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    # Find the section headers; CSV uses "### section".
    section_headers = [
        r[0] for r in rows if r and r[0].startswith("### ")
    ]
    for required in (
        "### run_summary",
        "### characters",
        "### locations",
        "### props",
        "### story_beats",
        "### shots",
        "### image_prompts",
        "### video_prompts",
        "### executor_tasks",
    ):
        assert required in section_headers, (
            f"CSV export missing section: {required}; got {section_headers}"
        )


def test_export_run_rejects_unknown_format(single_run: Path) -> None:
    with pytest.raises(PlannerError, match="Unknown export format"):
        export_run(single_run, "docx")


def test_export_run_custom_output_path(single_run: Path, tmp_path: Path) -> None:
    target = tmp_path / "out" / "report.md"
    written = export_run(single_run, "markdown", output=target)
    assert written == target
    assert target.exists()


def test_export_run_valid_formats_constant() -> None:
    assert VALID_FORMATS == ("markdown", "html", "csv")


# ---- export_batch -----------------------------------------------------


def test_export_batch_markdown(batch_runs: Path) -> None:
    target = export_batch(batch_runs, "markdown")
    assert target.suffix == ".md"
    text = target.read_text(encoding="utf-8")
    # Both runs should appear; we don't enforce order.
    assert text.count("Provider audit") >= 2


def test_export_batch_html(batch_runs: Path) -> None:
    target = export_batch(batch_runs, "html")
    text = target.read_text(encoding="utf-8")
    assert text.lstrip().startswith("<!doctype html>")


def test_export_batch_csv(batch_runs: Path) -> None:
    target = export_batch(batch_runs, "csv")
    text = target.read_text(encoding="utf-8")
    # Both runs each emit one set of section headers.
    assert text.count("### run_summary") >= 2


def test_export_batch_rejects_non_batch_directory(tmp_path: Path) -> None:
    with pytest.raises(PlannerError, match="batch_summary.json"):
        export_batch(tmp_path, "markdown")


def test_export_batch_rejects_when_summary_lists_no_runs(tmp_path: Path) -> None:
    """A directory that has ``batch_summary.json`` but no episode
    rows is treated as an empty batch (no runs to render)."""

    (tmp_path / "batch_summary.json").write_text(
        json.dumps({"episodes": []}), encoding="utf-8"
    )
    with pytest.raises(PlannerError, match="No runs found"):
        export_batch(tmp_path, "markdown")


def test_export_batch_rejects_unknown_format(batch_runs: Path) -> None:
    with pytest.raises(PlannerError, match="Unknown export format"):
        export_batch(batch_runs, "pdf")


# ---- CLI integration --------------------------------------------------


def test_cli_export_run(single_run: Path) -> None:
    from click.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(
        export_cmd,
        ["--run", str(single_run), "--format", "markdown"],
    )
    assert result.exit_code == 0, result.output
    assert "Wrote markdown report" in result.output
    # The default output path is the parent dir.
    expected = single_run.parent / f"{single_run.name}.md"
    assert expected.exists()


def test_cli_export_run_to_custom_path(single_run: Path, tmp_path: Path) -> None:
    from click.testing import CliRunner

    runner = CliRunner()
    target = tmp_path / "custom.md"
    result = runner.invoke(
        export_cmd,
        ["--run", str(single_run), "--format", "markdown", "--output", str(target)],
    )
    assert result.exit_code == 0, result.output
    assert target.exists()


def test_cli_export_batch(batch_runs: Path) -> None:
    from click.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(
        export_cmd,
        ["--batch", str(batch_runs), "--format", "html"],
    )
    assert result.exit_code == 0, result.output
    assert "Wrote html report" in result.output


def test_cli_export_requires_exactly_one_of_run_or_batch(tmp_path: Path) -> None:
    from click.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(
        export_cmd,
        ["--format", "markdown"],
    )
    assert result.exit_code != 0
    assert "Pass exactly one of --run or --batch" in result.output


def test_cli_export_rejects_both_run_and_batch(tmp_path: Path) -> None:
    from click.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(
        export_cmd,
        [
            "--run", str(tmp_path), "--batch", str(tmp_path),
            "--format", "markdown",
        ],
    )
    assert result.exit_code != 0


# ---- hard boundary preservation --------------------------------------


def test_executor_tasks_never_show_real_tool(single_run: Path) -> None:
    """The v1.0 contract says ``tool`` defaults to ``None`` in
    Phase 1 — exporting the run MUST NOT introduce a real tool name."""

    target = export_run(single_run, "markdown")
    text = target.read_text(encoding="utf-8")
    # No real executor tool name may appear in the export.
    for forbidden in ("flowith", "libtv", "comfyui", "keling", "jiemeng"):
        assert forbidden not in text.lower()


def test_export_reports_never_leak_secrets_across_formats(single_run: Path) -> None:
    """P3-5 Codex polish: the secret-leak guard must cover the same
    four patterns :func:`_redact_secrets` uses — ``Bearer XXX``,
    ``sk-...``, ``sk-ant-...``, ``gho_...``. Markdown, HTML, and CSV
    exports all run through this assertion.
    """

    secret_patterns = [
        # Bearer ...
        re.compile(r"Bearer\s+[A-Za-z0-9_\-]{8,}"),
        # OpenAI key
        re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
        # Anthropic key
        re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
        # GitHub OAuth token (defensive — never expected, but covered)
        re.compile(r"gho_[A-Za-z0-9_\-]{8,}"),
    ]
    for fmt in ("markdown", "html", "csv"):
        target = export_run(single_run, fmt)
        text = target.read_text(encoding="utf-8")
        for pat in secret_patterns:
            assert not pat.search(text), (
                f"{fmt} export leaks a secret matching {pat.pattern}: "
                f"{pat.search(text).group(0)!r}"
            )