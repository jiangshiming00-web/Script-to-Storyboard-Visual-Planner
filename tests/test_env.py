"""Environment configuration tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from planner.env import PlannerConfig, load_config
from planner.exceptions import ConfigError


def test_load_development_config(project_root: Path) -> None:
    cfg = load_config("development", project_root=project_root)
    assert cfg.env == "development"
    assert cfg.allow_overwrite_runs is True
    assert cfg.executor_default_status == "pending"
    assert cfg.submit_paid_jobs is False
    assert cfg.executor_dry_run is True
    assert cfg.data_root.name == "development"
    assert cfg.runs_root.name == "development"
    assert cfg.assets_root.name == "development"
    assert cfg.logs_root.name == "development"


def test_load_production_config(project_root: Path, tmp_path: Path) -> None:
    prod_cfg = tmp_path / "production.json"
    prod_cfg.write_text(
        json.dumps(
            {
                "env": "production",
                "allow_overwrite_runs": False,
                "executor_default_status": "pending_manual_approval",
                "submit_paid_jobs": False,
                "log_level": "INFO",
                "executor_dry_run": True,
                "data_root": "data/production",
                "assets_root": "assets/production",
                "runs_root": "runs/production",
                "logs_root": "logs/production",
                "schema_strict": True,
            }
        )
    )
    cfg = load_config("production", project_root=project_root, config_path=prod_cfg)
    assert cfg.is_production
    assert cfg.allow_overwrite_runs is False
    assert cfg.executor_default_status == "pending_manual_approval"
    assert cfg.submit_paid_jobs is False
    assert cfg.schema_strict is True


def test_production_rejects_paid_jobs(project_root: Path, tmp_path: Path) -> None:
    bad_cfg = tmp_path / "bad.json"
    bad_cfg.write_text(
        json.dumps(
            {
                "env": "production",
                "allow_overwrite_runs": False,
                "executor_default_status": "pending_manual_approval",
                "submit_paid_jobs": True,
                "log_level": "INFO",
                "executor_dry_run": True,
                "data_root": "data/production",
                "assets_root": "assets/production",
                "runs_root": "runs/production",
                "logs_root": "logs/production",
                "schema_strict": True,
            }
        )
    )
    with pytest.raises(ConfigError, match="submit_paid_jobs"):
        load_config("production", project_root=project_root, config_path=bad_cfg)


def test_unknown_env_rejected(project_root: Path) -> None:
    with pytest.raises(ConfigError, match="Unknown environment"):
        load_config("staging", project_root=project_root)


def test_missing_config_file(project_root: Path, tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    with pytest.raises(ConfigError, match="Config file not found"):
        load_config("production", project_root=project_root, config_path=missing)