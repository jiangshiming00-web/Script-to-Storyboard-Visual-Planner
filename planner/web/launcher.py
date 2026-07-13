"""Native + headless launcher for the planner web app.

Two modes:

- ``launch_desktop`` opens a pywebview native window pointing at the
  locally-served FastAPI app. The native window lifecycle is
  managed by pywebview; when the user closes the window the
  embedded uvicorn server is stopped cleanly (``should_exit = True``
  + thread join).

- ``launch_server_only`` boots uvicorn on the given host/port
  without a native window. Useful for headless servers, CI, and
  tests. Exit with Ctrl-C / SIGTERM.

The console_scripts entry ``planner-web`` (defined in
``pyproject.toml``) and ``python -m planner.web`` both dispatch
through :func:`planner.web.scripts_entry.main`, which routes to one
of the two functions based on the ``--no-window`` flag.

Hard rules:

- The GUI is a **thin shell**: no business logic lives here. All
  pipeline / provider / run rules stay in :mod:`planner` core.
- pywebview is an **optional** dependency. ``launch_server_only``
  must work even on systems where pywebview is not installed
  (CI, Linux server, the ``pip install -e .`` base install).
- When ``launch_desktop`` is invoked without pywebview installed we
  surface a clear actionable error rather than silently falling
  back to a browser tab.
- Desktop shutdown MUST be deterministic: closing the window sets
  ``server.should_exit = True`` and joins the server thread; if the
  join times out we log a warning instead of leaving an orphan.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)


def _check_port_available(host: str, port: int) -> None:
    """Raise a clear error if ``(host, port)`` is already bound.

    We do this up-front so the operator gets a friendly hint instead
    of an opaque uvicorn / OS error. The check is racy (a process
    could bind between this call and uvicorn's bind), but in practice
    the window between them is microseconds and the worst case is
    that uvicorn raises ``OSError: address in use`` which is still
    actionable.
    """

    import socket as _socket

    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        try:
            sock.connect((host, port))
        except OSError:
            return  # not bound; safe to start
    raise RuntimeError(
        f"Port {port} on {host} is already in use. Pass --port <other> "
        "or stop the existing process. Refusing to start uvicorn to "
        "avoid a silent failure."
    )


def _build_server(app, host: str, port: int):
    """Construct a :class:`uvicorn.Server` for the given app.

    Centralised so desktop and server-only modes share the same
    config (log level, lifespan). Returns the server instance so the
    desktop launcher can hold it for shutdown.
    """

    import uvicorn

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        # In a launcher we don't want uvicorn to install its own
        # signal handlers - the host process owns SIGINT.
        lifespan="on",
    )
    return uvicorn.Server(config)


def _signal_ready_when_started(server, ready: threading.Event) -> None:
    """Poll ``server.started`` and set ``ready`` once uvicorn is bound.

    Runs in a tiny daemon thread so the caller's main thread can
    ``ready.wait(timeout=...)`` without blocking on ``server.run()``
    (which blocks until the server exits).
    """

    import time as _time

    for _ in range(100):
        if server.started:
            ready.set()
            return
        _time.sleep(0.05)


def launch_server_only(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    repo_root: Optional[Path] = None,
) -> None:
    """Start the FastAPI app on ``host:port`` and block until SIGINT.

    Use this from headless contexts (``planner-web --no-window``,
    CI smoke harnesses). Returns only on graceful shutdown.
    """

    from .app import create_app

    app = create_app(repo_root=repo_root)
    _check_port_available(host, port)
    _log.info("planner-web: starting server at http://%s:%d", host, port)
    server = _build_server(app, host=host, port=port)
    server.run()


def launch_desktop(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    width: int = 1200,
    height: int = 800,
    title: str = "Script-to-Storyboard Visual Planner",
    repo_root: Optional[Path] = None,
    server_join_timeout: float = 5.0,
) -> None:
    """Start uvicorn in a background thread, then open a pywebview
    native window pointing at ``http://<host>:<port>/``.

    When the user closes the window, pywebview's ``start()`` returns
    and the ``finally`` block flips ``server.should_exit = True`` and
    joins the server thread. If the join times out
    (``server_join_timeout`` seconds) a warning is logged so the
    operator notices an orphan instead of silently leaving a daemon
    behind.

    Args:
        server_join_timeout: Seconds to wait for the uvicorn thread
            to exit after ``should_exit``. Defaults to 5.0.
    """

    try:
        import webview  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on optional dep
        raise RuntimeError(
            "planner-web (desktop mode) requires the optional 'pywebview' "
            "package. Install it with "
            "`pip install 'script-to-storyboard-planner[gui]'` and retry. "
            "Headless mode (`planner-web --no-window`) works without it."
        ) from exc

    from .app import create_app

    app = create_app(repo_root=repo_root)
    _check_port_available(host, port)

    # Build the server here so we own the instance and can stop it
    # deterministically on window close.
    server = _build_server(app, host=host, port=port)
    ready = threading.Event()

    def _run_server() -> None:
        # Poll server.started in a daemon thread so this thread can
        # proceed to server.run() (which blocks until exit).
        threading.Thread(
            target=_signal_ready_when_started,
            args=(server, ready),
            daemon=True,
            name="uvicorn-ready",
        ).start()
        server.run()

    server_thread = threading.Thread(
        target=_run_server,
        name="planner-web-uvicorn",
        daemon=False,  # non-daemon so the host process can wait for shutdown
    )
    server_thread.start()
    # Wait up to ~5 seconds for the server to bind.
    if not ready.wait(timeout=5.0):
        # Never bound - tear down the thread so we don't leak it.
        server.should_exit = True
        server_thread.join(timeout=server_join_timeout)
        raise RuntimeError(
            f"planner-web: uvicorn did not bind {host}:{port} within 5s."
        )

    url = f"http://{host}:{port}/"
    _log.info("planner-web: opening window at %s", url)
    window = webview.create_window(
        title=title,
        url=url,
        width=width,
        height=height,
        # resizable + min size so the layout still works on tiny windows.
        resizable=True,
    )
    try:
        webview.start()
    finally:
        # Deterministic shutdown: signal the server to exit and join
        # the thread. A timeout here means uvicorn is stuck (in-flight
        # request refusing to finish, etc.) - log loudly so the
        # operator notices the orphan instead of silently leaking.
        _log.info("planner-web: window closed, stopping server")
        server.should_exit = True
        server_thread.join(timeout=server_join_timeout)
        if server_thread.is_alive():
            _log.warning(
                "planner-web: server thread did not exit within %.1fs "
                "after window close; process may hang on exit.",
                server_join_timeout,
            )


__all__ = ["launch_desktop", "launch_server_only"]