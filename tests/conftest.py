"""Shared pytest fixtures."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_SCRIPT = (
    PROJECT_ROOT / "data" / "development" / "input_scripts" / "sample_ep01.txt"
)


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def sample_script_path() -> Path:
    if not SAMPLE_SCRIPT.exists():
        pytest.skip(f"Sample script missing: {SAMPLE_SCRIPT}")
    return SAMPLE_SCRIPT


@pytest.fixture
def tmp_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "runs" / "sample_ep01"
    run_dir.mkdir(parents=True, exist_ok=True)
    yield run_dir
    if run_dir.exists():
        shutil.rmtree(run_dir)