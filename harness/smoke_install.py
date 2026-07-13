"""Harness: install smoke for the v1.0 release.

Builds a fresh wheel from the current source tree, creates a brand
new virtual environment, installs the wheel into it, and verifies:

1. The ``planner`` console_script is on PATH and ``planner --help``
   runs cleanly.
2. The ``planner-web`` console_script is on PATH and
   ``planner-web --help`` runs cleanly.
3. The wheel contains ``planner/providers/openai_compatible_adapter.py``
   + ``planner/web/static/{index.html,app.js,style.css}``
   + ``planner/web/launcher.py`` (the v1.0 surface area).
4. The base install (no ``[gui]`` extra) installs cleanly without
   pulling in fastapi / uvicorn / pywebview.

The harness never modifies the host environment: the venv is created
under ``/tmp/smoke_install_<pid>`` and torn down on success. On
failure the venv is kept for post-mortem.

Run as::

    python3 harness/smoke_install.py

Exit code 0 on full success, non-zero on first failed check.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path
from typing import List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable

REQUIRED_WHEEL_PATHS = (
    "planner/providers/openai_compatible_adapter.py",
    "planner/web/static/index.html",
    "planner/web/static/app.js",
    "planner/web/static/style.css",
    "planner/web/launcher.py",
)


def _log(msg: str) -> None:
    print(f"[smoke_install] {msg}", flush=True)


def _venv_python(venv_dir: Path) -> Path:
    """Return the absolute path to the venv's Python interpreter."""

    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _venv_bin(venv_dir: Path, name: str) -> Path:
    """Return the absolute path to a venv-installed binary."""

    if sys.platform == "win32":
        return venv_dir / "Scripts" / f"{name}.exe"
    return venv_dir / "bin" / name


def _run(
    cmd: List[str],
    cwd: Path,
    env: dict = None,
    timeout: float = 120.0,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env or {**os.environ},
        timeout=timeout,
    )


def step_build_wheel(work_root: Path) -> Path:
    """Build the wheel into ``work_root/wheels`` and return its path."""

    wheels_dir = work_root / "wheels"
    wheels_dir.mkdir(parents=True, exist_ok=True)
    proc = _run(
        [PYTHON, "-m", "pip", "wheel", ".", "--no-deps", "-w", str(wheels_dir)],
        cwd=PROJECT_ROOT,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"[smoke_install] pip wheel failed rc={proc.returncode}\n"
            f"--- stderr ---\n{proc.stderr}"
        )
    wheels = list(wheels_dir.glob("*.whl"))
    if not wheels:
        raise SystemExit(
            f"[smoke_install] no wheel produced under {wheels_dir}"
        )
    wheel = wheels[0]
    _log(f"built wheel: {wheel.name} ({wheel.stat().st_size} bytes)")
    return wheel


def step_wheel_contains_required_files(wheel: Path) -> None:
    """Step 3: the wheel bundles the v1.0 surface area."""

    from zipfile import ZipFile

    with ZipFile(wheel) as zf:
        names = set(zf.namelist())
    missing = [p for p in REQUIRED_WHEEL_PATHS if p not in names]
    if missing:
        raise SystemExit(
            f"[smoke_install] wheel missing required files: {missing}"
        )
    _log(
        f"wheel bundles all {len(REQUIRED_WHEEL_PATHS)} required files"
    )


def step_create_venv(work_root: Path) -> Path:
    """Create a fresh virtualenv under ``work_root/venv``.

    Boots pip via :mod:`ensurepip` so the venv has a working
    ``pip`` even when the host Python distribution strips it (some
    Debian / Ubuntu builds ship without ``venv`` having pip).
    """

    venv_dir = work_root / "venv"
    builder = venv.EnvBuilder(
        system_site_packages=False,
        clear=True,
        symlinks=(sys.platform != "win32"),
        with_pip=True,
    )
    builder.create(str(venv_dir))
    # Ensure pip is present even on stripped distributions.
    proc = _run(
        [_venv_python(venv_dir), "-m", "ensurepip", "--upgrade"],
        cwd=PROJECT_ROOT,
        timeout=60.0,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"[smoke_install] ensurepip failed rc={proc.returncode}\n"
            f"--- stderr ---\n{proc.stderr}"
        )
    _log(f"created venv at {venv_dir} (pip bootstrapped)")
    return venv_dir


def step_install_wheel(venv_dir: Path, wheel: Path) -> None:
    """Install the wheel + the project's required dependencies into the venv.

    We do NOT pass ``--no-deps`` here: the base install genuinely needs
    ``pydantic`` + ``click`` (per ``pyproject.toml [project]``). The
    optional ``[gui]`` / ``[server]`` / ``[build]`` extras are not
    requested — that is what ``step_base_install_no_optional_deps``
    verifies next.
    """

    proc = _run(
        [_venv_python(venv_dir), "-m", "pip", "install", str(wheel)],
        cwd=PROJECT_ROOT,
        timeout=180.0,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"[smoke_install] pip install wheel failed rc={proc.returncode}\n"
            f"--- stderr ---\n{proc.stderr}"
        )
    _log("installed wheel + required deps (pydantic + click) into venv")


def step_console_scripts_on_path(venv_dir: Path) -> None:
    """Step 1 + 2: planner --help + planner-web --help."""

    for name in ("planner", "planner-web"):
        bin_path = _venv_bin(venv_dir, name)
        if not bin_path.exists():
            raise SystemExit(
                f"[smoke_install] {name} console_script missing at {bin_path}"
            )
        proc = _run([str(bin_path), "--help"], cwd=PROJECT_ROOT, timeout=15.0)
        if proc.returncode != 0:
            raise SystemExit(
                f"[smoke_install] {name} --help failed rc={proc.returncode}\n"
                f"--- stderr ---\n{proc.stderr}"
            )
        if name not in proc.stdout and "planner" not in proc.stdout:
            raise SystemExit(
                f"[smoke_install] {name} --help output unexpected: "
                f"{proc.stdout[:200]!r}"
            )
    _log("planner --help + planner-web --help both run in fresh venv")


def step_base_install_no_optional_deps(venv_dir: Path) -> None:
    """Step 4: the base install must not pull in fastapi / uvicorn / pywebview.

    We import ``planner.cli`` and check that the ``planner.web`` import
    raises ``ImportError`` when the optional GUI deps are absent. The
    wheel was installed WITH required deps (pydantic + click) but
    WITHOUT the ``[gui]`` / ``[server]`` / ``[build]`` extras — if the
    project's setup accidentally promotes any of those to required
    dependencies, the import succeeds and this step fails.
    """

    probe = venv_dir / "_probe_base_install.py"
    probe.write_text(
        (
            "from planner import cli\n"
            "print('planner.cli import ok')\n"
            "try:\n"
            "    import planner.web.app  # noqa: F401\n"
            "    print('UNEXPECTED: planner.web imported in base install')\n"
            "except ImportError as exc:\n"
            "    print('planner.web import refused as expected:', exc)\n"
            "except Exception as exc:\n"
            "    print('UNEXPECTED: planner.web raised', type(exc).__name__, exc)\n"
        ),
        encoding="utf-8",
    )
    proc = _run(
        [_venv_python(venv_dir), str(probe)],
        cwd=PROJECT_ROOT,
        timeout=15.0,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"[smoke_install] base-install probe failed rc={proc.returncode}\n"
            f"--- stderr ---\n{proc.stderr}"
        )
    if "planner.cli import ok" not in proc.stdout:
        raise SystemExit(
            f"[smoke_install] base install could not import planner.cli: "
            f"{proc.stdout!r}"
        )
    if "UNEXPECTED: planner.web imported" in proc.stdout:
        raise SystemExit(
            "[smoke_install] base install pulled in fastapi / uvicorn / "
            "pywebview — required dependency promotion is a red-line bug"
        )
    if "planner.web import refused as expected" not in proc.stdout:
        raise SystemExit(
            f"[smoke_install] base install probe did not refuse planner.web: "
            f"{proc.stdout!r}"
        )
    _log(
        "base install: planner.cli imports; planner.web refuses "
        "(no optional deps)"
    )


# --- entrypoint ----------------------------------------------------------


def main() -> int:
    work_root = Path(tempfile.mkdtemp(prefix="smoke_install_"))
    try:
        wheel = step_build_wheel(work_root)
        step_wheel_contains_required_files(wheel)
        venv_dir = step_create_venv(work_root)
        step_install_wheel(venv_dir, wheel)
        step_console_scripts_on_path(venv_dir)
        step_base_install_no_optional_deps(venv_dir)
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[smoke_install] unexpected error: {exc}", file=sys.stderr)
        return 3
    finally:
        _log(f"work dir kept at {work_root} for inspection")
    _log("ALL INSTALL SMOKE STEPS PASSED ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())