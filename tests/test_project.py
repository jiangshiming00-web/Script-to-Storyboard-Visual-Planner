"""Tests for the v1.0 project abstraction (``planner/project.py``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from planner.exceptions import ConfigError
from planner.project import (
    PROJECT_SUBDIRS,
    Project,
    ProjectValidationReport,
    init_project,
    load_project,
    validate_project,
)


# --- model --------------------------------------------------------------


def test_project_defaults_are_sane() -> None:
    p = Project(project_name="demo")
    assert p.script_dir == "scripts"
    assert p.default_env == "development"
    assert p.default_provider == "deterministic"
    assert p.output_dir == "runs"
    # ISO timestamp set by factory.
    assert "T" in p.created_at


def test_project_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Project.model_validate(
            {"project_name": "x", "secret_admin_token": "y"}
        )


def test_project_provider_literal_locked() -> None:
    with pytest.raises(ValidationError):
        Project(project_name="x", default_provider="flowith")  # type: ignore[arg-type]


def test_project_env_literal_locked() -> None:
    with pytest.raises(ValidationError):
        Project(project_name="x", default_env="staging")  # type: ignore[arg-type]


# --- init_project -------------------------------------------------------


def test_init_project_creates_tree(tmp_path: Path) -> None:
    proj_dir = tmp_path / "demo_proj"
    project = init_project(proj_dir, project_name="Demo")
    assert project.project_name == "Demo"
    # Subdirs created.
    for sub in PROJECT_SUBDIRS:
        assert (proj_dir / sub).is_dir()
    # project.json exists + parses back to an equal Project.
    config_path = proj_dir / "project.json"
    assert config_path.exists()
    reloaded = load_project(proj_dir)
    assert reloaded.project_name == "Demo"
    # Updated_at is recent.
    assert reloaded.updated_at == project.updated_at


def test_init_project_refuses_overwrite_without_flag(tmp_path: Path) -> None:
    proj_dir = tmp_path / "demo"
    init_project(proj_dir)
    with pytest.raises(ConfigError, match="already exists"):
        init_project(proj_dir)


def test_init_project_overwrite_with_flag(tmp_path: Path) -> None:
    proj_dir = tmp_path / "demo"
    init_project(proj_dir, project_name="First")
    project = init_project(proj_dir, project_name="Second", overwrite=True)
    assert project.project_name == "Second"
    assert load_project(proj_dir).project_name == "Second"


def test_init_project_default_name_uses_dir_basename(tmp_path: Path) -> None:
    proj_dir = tmp_path / "my_short_drama"
    project = init_project(proj_dir)
    assert project.project_name == "my_short_drama"


def test_init_project_atomic_write(tmp_path: Path) -> None:
    """A half-written ``project.json`` must never land. After
    init_project, the only ``project.json*`` artifact in the folder
    is the canonical file (no orphan ``.tmp`` siblings)."""

    proj_dir = tmp_path / "atomic_demo"
    init_project(proj_dir)
    siblings = sorted(p.name for p in proj_dir.iterdir() if p.name.startswith("project"))
    assert siblings == ["project.json"]


# --- load_project -------------------------------------------------------


def test_load_project_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_project(tmp_path)


def test_load_project_invalid_json(tmp_path: Path) -> None:
    proj_dir = tmp_path / "bad"
    proj_dir.mkdir()
    (proj_dir / "project.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_project(proj_dir)


def test_load_project_invalid_shape(tmp_path: Path) -> None:
    proj_dir = tmp_path / "shape"
    proj_dir.mkdir()
    (proj_dir / "project.json").write_text(
        json.dumps({"default_provider": "flowith"}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="failed validation"):
        load_project(proj_dir)


# --- validate_project ---------------------------------------------------


def test_validate_project_missing_dir(tmp_path: Path) -> None:
    report = validate_project(tmp_path / "nope")
    assert report.ok is False
    assert any("does not exist" in e for e in report.errors)


def test_validate_project_missing_project_json(tmp_path: Path) -> None:
    proj_dir = tmp_path / "nojson"
    proj_dir.mkdir()
    report = validate_project(proj_dir)
    assert report.ok is False
    assert any("not found" in e for e in report.errors)


def test_validate_project_happy_path(tmp_path: Path) -> None:
    proj_dir = tmp_path / "ok"
    init_project(proj_dir, project_name="OK")
    # Drop in three .txt files matching batch.discover_scripts order.
    (proj_dir / "scripts").mkdir(exist_ok=True)
    for name in ("EP01.txt", "EP02.txt", "EP03.txt"):
        (proj_dir / "scripts" / name).write_text(
            "EP" + name[2:3] + " — Test\n\nscene 1\n", encoding="utf-8"
        )
    report = validate_project(proj_dir)
    assert report.ok is True
    assert report.script_count == 3
    assert report.errors == []


def test_validate_project_warns_on_empty_scripts_dir(tmp_path: Path) -> None:
    proj_dir = tmp_path / "empty"
    init_project(proj_dir)
    report = validate_project(proj_dir)
    assert report.ok is True
    assert any("no .txt files" in w for w in report.warnings)


def test_validate_project_warns_on_default_env_prod_with_skeleton_provider(
    tmp_path: Path,
) -> None:
    proj_dir = tmp_path / "warn"
    init_project(proj_dir)
    # Rewrite the project.json to flip provider + env to a known-bad combo.
    cfg = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    cfg["default_env"] = "production"
    cfg["default_provider"] = "openai"
    (proj_dir / "project.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report = validate_project(proj_dir)
    assert report.ok is True
    assert any("skeleton" in w and "openai_compatible" in w for w in report.warnings)


def test_validate_project_missing_script_dir(tmp_path: Path) -> None:
    proj_dir = tmp_path / "noscripts"
    init_project(proj_dir)
    # Drop the scripts dir.
    import shutil
    shutil.rmtree(proj_dir / "scripts")
    report = validate_project(proj_dir)
    assert report.ok is False
    assert any("script_dir" in e for e in report.errors)


# --- CLI surface -------------------------------------------------------


def test_project_group_help_lists_subcommands() -> None:
    """``planner project --help`` must surface both init and validate
    subcommands so the help text is the canonical reference for the
    v1.0 install."""

    from click.testing import CliRunner

    from planner.cli import project_group

    runner = CliRunner()
    result = runner.invoke(project_group, ["--help"])
    assert result.exit_code == 0
    assert "init" in result.output
    assert "validate" in result.output


def test_cli_project_init_then_validate(tmp_path: Path) -> None:
    """End-to-end CLI smoke: ``project init`` + ``project validate``."""

    from click.testing import CliRunner

    from planner.cli import project_group

    runner = CliRunner()
    proj_dir = tmp_path / "demo"
    init_result = runner.invoke(
        project_group,
        ["init", "--dir", str(proj_dir), "--name", "Demo"],
    )
    assert init_result.exit_code == 0, init_result.output
    assert "Initialized" in init_result.output

    # Drop a script so validate counts it.
    (proj_dir / "scripts").mkdir(exist_ok=True)
    (proj_dir / "scripts" / "EP01.txt").write_text(
        "EP01\n\nscene\n", encoding="utf-8"
    )

    validate_result = runner.invoke(
        project_group,
        ["validate", "--dir", str(proj_dir)],
    )
    assert validate_result.exit_code == 0, validate_result.output
    assert "1 script" in validate_result.output


# --- P2-3: planner batch --project ------------------------------------


def _write_dev_config(repo_root: Path) -> None:
    """Write a minimal config/development.json under repo_root."""
    import json as _json

    cfg_dir = repo_root / "config"
    cfg_dir.mkdir(exist_ok=True)
    (cfg_dir / "development.json").write_text(
        _json.dumps(
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


def test_cli_batch_with_project_reads_defaults(
    tmp_path: Path, monkeypatch
) -> None:
    """P2-3: ``planner batch --project DIR`` reads scripts_dir /
    output_dir / default_env from project.json. No --env / --scripts /
    --out flags needed."""

    from click.testing import CliRunner

    from planner.cli import batch_cmd, project_group

    # config/development.json under tmp_path (the CLI's CWD).
    _write_dev_config(tmp_path)
    monkeypatch.chdir(tmp_path)

    proj_dir = tmp_path / "demo"
    runner = CliRunner()
    init_result = runner.invoke(
        project_group, ["init", "--dir", str(proj_dir), "--name", "Demo"]
    )
    assert init_result.exit_code == 0, init_result.output

    # Drop two scripts into the project's scripts/ dir.
    for name in ("EP01", "EP02"):
        (proj_dir / "scripts" / f"{name}.txt").write_text(
            f"{name} - Test\n\n场 1 内景 咖啡馆 - 日\n测试对白。\n",
            encoding="utf-8",
        )

    # batch --project alone (no --env / --scripts / --out).
    result = runner.invoke(batch_cmd, ["--project", str(proj_dir)])
    assert result.exit_code == 0, result.output
    assert "2/2 episodes done" in result.output
    # batch_summary.json landed under the project's output_dir (runs/).
    assert (proj_dir / "runs" / "batch_summary.json").exists()


def test_cli_batch_explicit_flags_override_project(
    tmp_path: Path, monkeypatch
) -> None:
    """Explicit --env / --scripts / --out override project.json values."""

    from click.testing import CliRunner

    from planner.cli import batch_cmd, project_group

    _write_dev_config(tmp_path)
    monkeypatch.chdir(tmp_path)

    proj_dir = tmp_path / "demo"
    runner = CliRunner()
    runner.invoke(
        project_group, ["init", "--dir", str(proj_dir), "--name", "Demo"]
    )
    (proj_dir / "scripts" / "EP01.txt").write_text(
        "EP01 - Test\n\n场 1 内景 咖啡馆 - 日\n测试对白。\n",
        encoding="utf-8",
    )

    # Override --out to a different dir.
    alt_out = tmp_path / "alt_runs"
    result = runner.invoke(
        batch_cmd,
        [
            "--project", str(proj_dir),
            "--env", "development",
            "--out", str(alt_out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (alt_out / "batch_summary.json").exists()
    # Project's default runs/ was NOT used.
    assert not (proj_dir / "runs" / "batch_summary.json").exists()


def test_cli_batch_without_env_or_project_errors(tmp_path: Path) -> None:
    """Without --env and without --project, batch MUST exit non-zero
    with a friendly message (not a traceback)."""

    from click.testing import CliRunner

    from planner.cli import batch_cmd

    runner = CliRunner()
    result = runner.invoke(batch_cmd, ["--scripts", str(tmp_path)])
    assert result.exit_code != 0
    assert "--env" in result.output or "project" in result.output.lower()


def test_cli_project_validate_fails_on_missing_dir() -> None:
    from click.testing import CliRunner

    from planner.cli import project_group

    runner = CliRunner()
    # Click validates ``exists=True`` before invoking; expect exit 2.
    result = runner.invoke(
        project_group, ["validate", "--dir", "/nonexistent/zzz"]
    )
    assert result.exit_code != 0