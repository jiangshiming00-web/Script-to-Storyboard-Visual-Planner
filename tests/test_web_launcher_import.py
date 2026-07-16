"""Smoke tests for the ``planner-web`` launcher.

The launcher is the project's public face: a teammate runs
``pip install '.[gui]' && planner-web`` and a window opens. These
tests pin the v1.0 contract:

- the module imports cleanly under both ``gui`` and ``server``
  extras,
- ``launch_server_only`` boots a real uvicorn server on a free port
  and the FastAPI ``/api/health`` endpoint responds,
- ``scripts_entry.main`` parses ``--help`` without raising,
- the ``planner-web`` console script is registered in the wheel's
  ``entry_points.txt`` so a fresh install exposes the command.

The desktop / pywebview path is exercised indirectly via the
``launch_desktop`` import; we do NOT open a real window in unit
tests (CI is headless). The runtime contract that pywebview is
optional and that ``launch_server_only`` works without it is
covered by the import smoke.
"""

from __future__ import annotations

import importlib
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest


# ---- imports ------------------------------------------------------------


def test_launcher_module_imports() -> None:
    mod = importlib.import_module("planner.web.launcher")
    assert hasattr(mod, "launch_desktop")
    assert hasattr(mod, "launch_server_only")


def test_scripts_entry_module_imports() -> None:
    mod = importlib.import_module("planner.web.scripts_entry")
    assert callable(mod.main)


def test_web_package_main_module_imports() -> None:
    """``python -m planner.web`` dispatches through
    :mod:`planner.web.__main__`. Importing it must not raise even on
    systems where the GUI extras are not installed."""

    spec = importlib.util.find_spec("planner.web.__main__")
    assert spec is not None
    mod = importlib.import_module("planner.web.__main__")
    assert hasattr(mod, "main")


# ---- CLI surface --------------------------------------------------------


def test_scripts_entry_help_exits_zero() -> None:
    """``planner-web --help`` must succeed — operators use it as the
    first sanity check after install."""

    from planner.web.scripts_entry import main

    # argparse exits with SystemExit(0) on --help; the script_entry
    # wrapper's ``return 0`` is NOT used here because argparse calls
    # ``sys.exit`` directly. Either is acceptable; we just assert no
    # other failure.
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0


def test_scripts_entry_help_lists_no_window_flag() -> None:
    """The ``--no-window`` flag is the load-bearing flag for CI /
    headless servers; if a refactor drops it, this test fails loud."""

    proc = subprocess.run(
        [sys.executable, "-m", "planner.web", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--no-window" in proc.stdout


# ---- launch_server_only: headless smoke -------------------------------


def _free_port() -> int:
    """Return an unused TCP port on localhost."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_launch_server_only_serves_health_endpoint(tmp_path: Path) -> None:
    """Boot ``launch_server_only`` on a free port in a daemon thread
    and confirm ``GET /api/health`` returns 200 with
    ``ok=True``. This is the same call pattern CI uses."""

    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("uvicorn")

    from planner.web.launcher import launch_server_only

    # Build a fake repo so /api/config can resolve.
    repo = tmp_path / "fake_repo"
    repo.mkdir()
    (repo / "config").mkdir()
    (repo / "config" / "development.json").write_text(
        '{"env":"development","planner_provider":"deterministic"}',
        encoding="utf-8",
    )

    port = _free_port()
    error: list = []

    def _run() -> None:
        try:
            launch_server_only(host="127.0.0.1", port=port, repo_root=repo)
        except Exception as exc:  # pragma: no cover - forwarded below
            error.append(exc)

    thread = threading.Thread(target=_run, daemon=True, name="launcher-smoke")
    thread.start()

    # Poll /api/health until the server is ready (or 5s elapses).
    import httpx

    deadline = time.time() + 5.0
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            resp = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1.0)
            if resp.status_code == 200:
                body = resp.json()
                assert body["ok"] is True
                break
        except Exception as exc:  # pragma: no cover - timing dependent
            last_err = exc
        time.sleep(0.1)
    else:  # pragma: no cover - diagnostic only
        pytest.fail(
            f"launch_server_only did not respond on port {port}: {last_err}"
        )

    # Stop the server. ``launch_server_only`` blocks until SIGINT, so
    # we cannot cleanly exit the thread — but the test process is
    # about to exit anyway. The daemon=True flag means the thread is
    # killed on interpreter shutdown.
    # The injected ``error`` list lets us surface any exception that
    # the launcher thread saw (e.g. port binding race).
    assert not error, error


# ---- port-in-use error path -------------------------------------------


def test_launch_server_only_raises_on_port_in_use(tmp_path: Path) -> None:
    """When the requested port is bound by another process, the
    launcher must raise a friendly ``RuntimeError`` BEFORE attempting
    uvicorn — so the operator gets a clear hint, not an opaque
    ``OSError``."""

    pytest.importorskip("fastapi")
    pytest.importorskip("uvicorn")

    from planner.web.launcher import _check_port_available

    port = _free_port()
    # Bind it from a separate socket so the next call sees "in use".
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", port))
        s.listen(1)
        try:
            with pytest.raises(RuntimeError, match="already in use"):
                _check_port_available("127.0.0.1", port)
        finally:
            s.close()


# ---- console-script registration -------------------------------------


def test_planner_web_console_script_registered() -> None:
    """The wheel MUST register ``planner-web`` so a teammate can run
    ``planner-web`` straight after ``pip install``. We check the
    installed metadata if present; otherwise we check the
    ``pyproject.toml`` source so a build / packaging regression
    surfaces here instead of at the next smoke.
    """

    try:
        from importlib.metadata import entry_points
    except ImportError:  # pragma: no cover - py<3.8
        pytest.skip("importlib.metadata unavailable")

    all_eps = entry_points()
    # Python 3.10+ exposes a SelectableView with ``.select`` /
    # ``.get``; Python 3.9 returns a plain dict. Handle both.
    if hasattr(all_eps, "select"):
        try:
            console_eps = list(all_eps.select(group="console_scripts"))
        except AttributeError:
            console_eps = list(all_eps.get("console_scripts", []))
    else:
        console_eps = list(all_eps.get("console_scripts", []))
    planner_web = [ep for ep in console_eps if ep.name == "planner-web"]
    if planner_web:
        assert planner_web[0].value == "planner.web.scripts_entry:main"
        return

    # Fall back to source inspection: the wheel may not be installed
    # in the current environment (CI runs from a checkout). We assert
    # that pyproject.toml declares the script so a future build will
    # include it.
    repo_pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    )
    text = repo_pyproject.read_text(encoding="utf-8")
    assert (
        'planner-web = "planner.web.scripts_entry:main"' in text
    ), (
        "pyproject.toml is missing the planner-web console script "
        "registration. v1.0 install path is broken."
    )


# ---- P1-3: desktop launcher shutdown ----------------------------------


class _FakeServer:
    """Stand-in for :class:`uvicorn.Server` that records shutdown
    signals without binding a real socket."""

    def __init__(self) -> None:
        self.started = True  # immediately "bound" so ready fires
        self.should_exit = False
        self.run_started = False

    def run(self) -> None:
        import time as _time

        self.run_started = True
        while not self.should_exit:
            _time.sleep(0.02)


class _FakeWebview:
    """Stand-in for the ``pywebview`` module. ``start()`` returns
    immediately, simulating the user closing the window."""

    def __init__(self) -> None:
        self.start_called = False
        self.created_windows: list = []

    def create_window(self, **kwargs):  # noqa: ANN201
        self.created_windows.append(kwargs)
        return object()

    def start(self) -> None:
        self.start_called = True
        # Return immediately - simulates window close.


def test_launch_desktop_stops_server_on_window_close(
    tmp_path: Path, monkeypatch
) -> None:
    """P1-3: closing the desktop window MUST set ``server.should_exit
    = True`` and join the server thread. Before the fix the ``finally``
    block only logged, leaving the uvicorn thread running and the port
    occupied after the window closed."""

    pytest.importorskip("fastapi")

    import sys

    from planner.web import launcher as launcher_mod

    fake_webview = _FakeWebview()
    monkeypatch.setitem(sys.modules, "webview", fake_webview)

    fake_server = _FakeServer()
    monkeypatch.setattr(
        launcher_mod,
        "_build_server",
        lambda app, host, port: fake_server,
    )
    monkeypatch.setattr(
        launcher_mod, "_check_port_available", lambda host, port: None
    )

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config").mkdir()
    (repo / "config" / "development.json").write_text(
        '{"env":"development","planner_provider":"deterministic"}',
        encoding="utf-8",
    )

    # launch_desktop returns when the window closes. Before the fix
    # this would hang (server thread never stopped).
    launcher_mod.launch_desktop(
        host="127.0.0.1",
        port=18799,
        repo_root=repo,
        server_join_timeout=3.0,
    )

    assert fake_webview.start_called, "webview.start was not called"
    assert fake_server.run_started, "server.run was not called"
    assert fake_server.should_exit is True, (
        "server.should_exit must be set to True on window close so the "
        "uvicorn thread exits; otherwise the port stays bound and the "
        "process hangs."
    )


def test_launch_desktop_logs_warning_when_server_join_times_out(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    """P1-3: if the server thread does not exit within the join
    timeout, a warning MUST be logged so the operator notices the
    orphan instead of silently leaking a daemon."""

    pytest.importorskip("fastapi")
    import logging as _logging
    import sys

    from planner.web import launcher as launcher_mod

    fake_webview = _FakeWebview()
    monkeypatch.setitem(sys.modules, "webview", fake_webview)

    class _SlowServer(_FakeServer):
        def run(self) -> None:
            import time as _time

            self.run_started = True
            while not self.should_exit:
                _time.sleep(0.02)
            # Slow shutdown: hold the thread alive past the join
            # timeout so the warning path is exercised. The thread
            # still exits shortly after, so pytest does not hang.
            _time.sleep(0.4)

    slow_server = _SlowServer()
    monkeypatch.setattr(
        launcher_mod,
        "_build_server",
        lambda app, host, port: slow_server,
    )
    monkeypatch.setattr(
        launcher_mod, "_check_port_available", lambda host, port: None
    )

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config").mkdir()
    (repo / "config" / "development.json").write_text(
        '{"env":"development","planner_provider":"deterministic"}',
        encoding="utf-8",
    )

    with caplog.at_level(_logging.WARNING, logger="planner.web.launcher"):
        launcher_mod.launch_desktop(
            host="127.0.0.1",
            port=18799,
            repo_root=repo,
            server_join_timeout=0.1,  # deliberately short
        )

    assert slow_server.should_exit is True
    assert any(
        "did not exit" in r.message for r in caplog.records
    ), "expected a warning when the server thread outlives the join timeout"


# ---- P0A-1: default headless + --window explicit + --no-window compat ----


def test_default_mode_is_headless_not_window(monkeypatch) -> None:
    """P0A-1: ``planner-web`` (no flags) MUST call ``launch_server_only``,
    not ``launch_desktop``. The default was flipped in Phase 3 P3 to
    avoid macOS focus stealing by the pywebview native window.
    """

    from planner.web import launcher, scripts_entry

    called = {"server_only": 0, "desktop": 0}

    def fake_server_only(*args, **kwargs):
        called["server_only"] += 1
        # Raise to break out of main() without actually starting a server.
        raise SystemExit(0)

    def fake_desktop(*args, **kwargs):
        called["desktop"] += 1
        raise AssertionError(
            "launch_desktop must not be called in default (no-flag) mode"
        )

    # scripts_entry.main() does ``from .launcher import launch_*`` —
    # patch the source module so the local import sees the fakes.
    monkeypatch.setattr(launcher, "launch_server_only", fake_server_only)
    monkeypatch.setattr(launcher, "launch_desktop", fake_desktop)

    with pytest.raises(SystemExit):
        scripts_entry.main([])

    assert called["server_only"] == 1
    assert called["desktop"] == 0


def test_explicit_window_flag_opens_desktop(monkeypatch) -> None:
    """P0A-1: ``planner-web --window`` MUST call ``launch_desktop`` and
    NOT ``launch_server_only``. This is the explicit opt-in for the
    native window.
    """

    from planner.web import launcher, scripts_entry

    called = {"server_only": 0, "desktop": 0}

    def fake_server_only(*args, **kwargs):
        called["server_only"] += 1
        raise AssertionError(
            "launch_server_only must not be called when --window is passed"
        )

    def fake_desktop(*args, **kwargs):
        called["desktop"] += 1
        raise SystemExit(0)

    monkeypatch.setattr(launcher, "launch_server_only", fake_server_only)
    monkeypatch.setattr(launcher, "launch_desktop", fake_desktop)

    with pytest.raises(SystemExit):
        scripts_entry.main(["--window"])

    assert called["desktop"] == 1
    assert called["server_only"] == 0


def test_no_window_deprecation_warning(monkeypatch, capsys) -> None:
    """P0A-1: ``planner-web --no-window`` is a deprecation alias. It
    must still work (headless) and emit a one-line stderr warning.
    The warning text MUST NOT point to --window (v3 round-2 cleanup):
    pointing to --window would mislead the user into thinking the
    default opens a window, which it does not.
    """

    from planner.web import launcher, scripts_entry

    def fake_server_only(*args, **kwargs):
        raise SystemExit(0)

    monkeypatch.setattr(launcher, "launch_server_only", fake_server_only)

    with pytest.raises(SystemExit):
        scripts_entry.main(["--no-window"])

    captured = capsys.readouterr()
    # Warning text contains "已不需要" (per brief)
    assert "已不需要" in captured.err
    # Must NOT point to --window
    assert "--window" not in captured.err


def test_serve_then_stop_releases_port(tmp_path: Path) -> None:
    """P0A-1: ``planner-web`` (default headless) starts on a free port,
    prints the headless banner, and exits cleanly on SIGINT — releasing
    the port within a few seconds.

    Approach: spawn the actual ``python -m planner.web`` subprocess on
    a free port; poll for the headless banner in stdout; give uvicorn
    a moment to actually bind; send SIGINT; wait for the process to
    exit; then probe the port via ``socket.connect`` — it MUST raise
    ``ConnectionRefusedError`` / ``OSError`` (port free).
    """

    pytest.importorskip("fastapi")
    pytest.importorskip("uvicorn")

    import os as _os
    import signal as _signal
    import socket as _socket
    import subprocess as _subprocess
    import sys as _sys
    import time as _time

    # Pick a free port
    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    # Build a minimal repo so /api/config can resolve
    repo = tmp_path / "fake_repo"
    repo.mkdir()
    (repo / "config").mkdir()
    (repo / "config" / "development.json").write_text(
        '{"env":"development","planner_provider":"deterministic"}',
        encoding="utf-8",
    )

    env = {
        **_os.environ,
        # Strip PLANNER_* so we don't leak the host's config
        **{k: v for k, v in _os.environ.items() if not k.startswith("PLANNER_")},
    }

    proc = _subprocess.Popen(
        [_sys.executable, "-m", "planner.web", "--port", str(port), "--repo-root", str(repo)],
        stdout=_subprocess.PIPE,
        stderr=_subprocess.PIPE,
        env=env,
        text=True,
    )

    try:
        # Wait for the headless banner in stdout (5s budget). The
        # banner is printed before uvicorn binds (we trade strict
        # "banner == bound" for clean SIGINT delivery through
        # uvicorn's own signal handler), so we additionally sleep
        # a short moment to let uvicorn finish binding.
        banner_deadline = _time.time() + 5.0
        banner = ""
        while _time.time() < banner_deadline:
            line = proc.stdout.readline() if proc.stdout else ""
            if not line:
                if proc.poll() is not None:
                    break
                continue
            if "planner-web ready" in line:
                banner = line
                break
        assert banner, (
            f"headless banner not seen within 5s; "
            f"proc.poll={proc.poll()}; stderr={proc.stderr.read() if proc.stderr else ''}"
        )

        # Give uvicorn a moment to actually bind the port. The banner
        # is printed just before server.run() is invoked, so there's
        # a small window where the port isn't bound yet.
        bound = False
        bind_deadline = _time.time() + 5.0
        while _time.time() < bind_deadline:
            try:
                with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as probe:
                    probe.settimeout(0.2)
                    probe.connect(("127.0.0.1", port))
                bound = True
                break
            except OSError:
                if proc.poll() is not None:
                    break
                _time.sleep(0.05)
        assert bound, (
            f"server did not bind port {port} within 5s of banner; "
            f"proc.poll={proc.poll()}"
        )

        # Send SIGINT and wait for graceful exit
        proc.send_signal(_signal.SIGINT)
        try:
            proc.wait(timeout=5.0)
        except _subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)
            pytest.fail(
                f"planner-web did not exit within 5s of SIGINT; "
                f"stderr={proc.stderr.read() if proc.stderr else ''}"
            )

        assert proc.returncode == 0, (
            f"planner-web exited with {proc.returncode}; "
            f"stderr={proc.stderr.read() if proc.stderr else ''}"
        )

        # Probe port: must be free (raise OSError). Note: SO_REUSEADDR
        # semantics can briefly keep a port reserved; we retry briefly.
        released = False
        release_deadline = _time.time() + 3.0
        last_err: Exception | None = None
        while _time.time() < release_deadline:
            try:
                with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as probe:
                    probe.settimeout(0.2)
                    probe.connect(("127.0.0.1", port))
                # Port still bound — wait and retry
            except OSError as exc:
                released = True
                last_err = exc
                break
            _time.sleep(0.1)
        assert released, (
            f"port {port} still bound 3s after planner-web exit; last_err={last_err}"
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)