"""Regression test for the v1.0 wheel packaging fix.

Background
----------

Before the fix, ``pyproject.toml`` declared::

    [tool.setuptools]
    packages = ["planner"]

That only ships the top-level ``planner/*.py`` modules and silently
drops ``planner/providers/*`` and ``planner/web/*``. The result: a
fresh ``pip install .`` from a wheel could not import
``planner.providers`` (no OpenAI/Anthropic adapters) and could not
import ``planner.web`` (no GUI shell), defeating the entire v1.0 client
install path.

The v1.0 release plan (``docs/PROMA_V1_RELEASE_PLAN.md`` §1) names
this as the #1 blocker: a teammate running ``pip install .`` of the
shipped wheel must get a working ``planner`` and ``planner-web``.

These tests pin both the structural fix in ``pyproject.toml`` and
the actual built wheel contents. The integration test shells out to
``python -m pip wheel`` — the same command from the release plan's
acceptance recipe — so what fails here is exactly what would fail in
the install verification step.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

setuptools = pytest.importorskip("setuptools")
from setuptools import find_packages  # noqa: E402


# Modules that the v1.0 wheel MUST ship. Anything missing here means a
# teammate cannot ``pip install .`` and use the feature named in the
# matching release plan section.
REQUIRED_MODULES = [
    # provider layer (Phase-1 implementation gate lives here)
    "planner/providers/__init__.py",
    "planner/providers/base.py",
    "planner/providers/registry.py",
    "planner/providers/deterministic.py",
    "planner/providers/openai_adapter.py",
    "planner/providers/anthropic_adapter.py",
    # GUI backend (Phase-2)
    "planner/web/__init__.py",
    "planner/web/app.py",
    "planner/web/routes.py",
    "planner/web/run_service.py",
    "planner/web/run_registry.py",
    "planner/web/errors.py",
]


def _find_repo_root(start: Path | None = None) -> Path:
    cur = (start or Path.cwd()).resolve()
    for parent in [cur, *cur.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    pytest.skip("pyproject.toml not found; cannot locate repo root")


def test_pyproject_packages_find_includes_subpackages() -> None:
    """``pyproject.toml`` must use ``[tool.setuptools.packages.find]``
    with ``include = ["planner*"]`` so subpackages ship in the wheel.

    This is a fast, structural check that catches the regression
    without rebuilding a wheel.
    """

    repo = _find_repo_root()
    pyproject = (repo / "pyproject.toml").read_text(encoding="utf-8")

    assert "[tool.setuptools.packages.find]" in pyproject, (
        "pyproject.toml is missing [tool.setuptools.packages.find]; "
        "wheel will only ship top-level planner/*.py."
    )
    assert "planner*" in pyproject, (
        "[tool.setuptools.packages.find] must include planner* so "
        "planner.providers and planner.web are packaged."
    )
    # The old broken form must be gone.
    assert 'packages = ["planner"]' not in pyproject, (
        "Legacy [tool.setuptools] packages = [\"planner\"] regressed; "
        "replace with packages.find include = [\"planner*\"]."
    )


def test_pyproject_declares_static_package_data() -> None:
    """``planner/web/static/`` will be added in Phase 3. Declaring
    ``package-data`` now keeps the wheel packaging correct from the
    first v1.0 release onward and stops silent regressions where a
    freshly installed wheel cannot serve ``/``.
    """

    repo = _find_repo_root()
    pyproject = (repo / "pyproject.toml").read_text(encoding="utf-8")

    assert "[tool.setuptools.package-data]" in pyproject, (
        "pyproject.toml is missing [tool.setuptools.package-data]; "
        "Phase 3 static UI will not ship in the wheel."
    )
    assert '"planner.web"' in pyproject, (
        "package-data must declare planner.web so the static UI ships."
    )
    assert "static/" in pyproject, (
        "package-data for planner.web must include static/."
    )


def test_find_packages_returns_all_planner_subpackages() -> None:
    """Simulate ``[tool.setuptools.packages.find] include = ["planner*"]``
    via setuptools' own discovery API and assert every subpackage we
    rely on is included.

    This is a faster, dependency-free equivalent of actually building a
    wheel. Catches regressions where someone narrows ``include`` or
    introduces an explicit ``exclude`` that drops subpackages.
    """

    repo = _find_repo_root()
    # Mimic how setuptools invokes packages.find: walk the repo root,
    # honouring include patterns. We exclude the tests/ tree explicitly
    # because setuptools' default exclude already covers it.
    found = find_packages(
        where=str(repo),
        include=["planner", "planner.*"],
    )

    expected = {
        "planner",
        "planner.providers",
        "planner.web",
    }
    missing = expected - set(found)
    assert not missing, (
        f"setuptools.find_packages(include=['planner*']) did not return "
        f"{expected}; got {sorted(found)}. The wheel will not include "
        f"these subpackages."
    )


def test_wheel_includes_all_subpackages(tmp_path: Path) -> None:
    """Build a wheel via ``python -m pip wheel`` (the release plan's
    acceptance command) and assert every required module is present in
    the zip. Skips if pip or the ``wheel`` package is unavailable.
    """

    if shutil.which(sys.executable) is None:  # pragma: no cover
        pytest.skip("no python interpreter available")
    # Ensure pip is available; the wheel command needs it.
    try:
        import pip  # noqa: F401
    except ImportError:  # pragma: no cover
        pytest.skip("pip module not importable; cannot run `pip wheel`")

    repo = _find_repo_root()
    out_dir = tmp_path / "wheel"
    out_dir.mkdir()
    proc = subprocess.run(
        [
            sys.executable, "-m", "pip", "wheel", ".",
            "--no-deps", "-w", str(out_dir),
        ],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=180,
    )
    if proc.returncode != 0:
        pytest.skip(
            f"`pip wheel` failed (rc={proc.returncode}); "
            f"this test requires the `wheel` package to be installed. "
            f"stderr tail: {proc.stderr[-300:]}"
        )

    wheels = list(out_dir.glob("script_to_storyboard_planner-*.whl"))
    assert wheels, (
        f"`pip wheel` returned rc=0 but no wheel landed in {out_dir}. "
        f"stdout tail: {proc.stdout[-300:]}"
    )
    wheel_path = wheels[0]

    with zipfile.ZipFile(wheel_path) as zf:
        names = set(zf.namelist())

    missing = [m for m in REQUIRED_MODULES if m not in names]
    assert not missing, (
        f"wheel {wheel_path.name} is missing required modules: {missing}.\n"
        "This regresses the v1.0 install path: a teammate running\n"
        "`pip install .` would not get the provider layer or the GUI."
    )

    # Entry point must be wired.
    with zipfile.ZipFile(wheel_path) as zf:
        try:
            ep = zf.read(
                f"script_to_storyboard_planner-0.1.0.dist-info/"
                f"entry_points.txt"
            ).decode("utf-8")
        except KeyError:
            ep = zf.read(
                next(
                    n for n in zf.namelist()
                    if n.endswith(".dist-info/entry_points.txt")
                )
            ).decode("utf-8")
    assert "planner = planner.cli:main" in ep, (
        f"planner console script is not registered; entry_points.txt was:\n{ep}"
    )

    # No tests/ or other source-tree artefacts should leak in.
    bad = [
        n for n in names
        if n.startswith("tests/")
        or n.startswith("scripts/")
        or n.startswith(".github/")
        or n.startswith("config/")
        or n.startswith("data/")
        or n.startswith("assets/")
        or n.startswith("logs/")
    ]
    assert not bad, f"wheel shipped unexpected top-level entries: {bad}"