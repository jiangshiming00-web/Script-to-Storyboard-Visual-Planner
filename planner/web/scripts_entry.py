"""Console-script entry point for the ``planner-web`` command.

Registered in ``pyproject.toml``::

    [project.scripts]
    planner-web = "planner.web.scripts_entry:main"

Also reachable as ``python -m planner.web`` via :mod:`planner.web.__main__`.

The function dispatches to either :func:`launch_server_only` (default,
Phase 3 P3 P0A-1) or :func:`launch_desktop` (with ``--window``) based
on the CLI flags. CLI parsing is intentionally light: we don't pull in
Click because the launcher is a small surface area and we'd rather not
add a dependency to the ``gui`` extra.

Phase 3 P3 product usability reset: the default mode flipped from
``launch_desktop`` (pywebview native window) to ``launch_server_only``
(headless service). Reasons:

1. macOS focus: a freshly-spawned pywebview window steals focus from
   other apps and can interfere with cross-project workflows.
2. CI / smoke parity: ``harness/smoke_gui.py`` and friends already use
   ``--no-window``; defaulting to that mode unifies the on-disk and
   CI paths.
3. Single-user local install: opening a browser tab to
   ``http://127.0.0.1:8765/`` is the safer default for the v1.0
   product usability reset.

``--no-window`` is kept as a deprecation alias for backward
compatibility. ``--window`` is the new explicit opt-in for the native
window. SIGTERM handler / background mode / PID-file management are
deliberately deferred to P2 (P0A scope is "default headless + --window
explicit + Ctrl-C exit").
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Sequence


_EPILOG = """\
Safe Start:
  planner-web                 # 起 headless 服务；访问 http://127.0.0.1:8765/
  planner-web --window        # 显式打开 pywebview 原生窗口
  planner-web --port 9000     # 换端口（默认 8765）
  planner-web --host 0.0.0.0  # 改绑定 host（暴露到局域网——注意安全）

Safe Stop:
  Ctrl-C                      # 干净退出；端口 5s 内释放

故障恢复:
  端口被占：planner-web --port <other>
  残留进程：lsof -nP -iTCP:<port> -sTCP:LISTEN  # macOS / Linux
             netstat -ano | findstr :<port>     # Windows
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="planner-web",
        description=(
            "Launch the planner web UI. Default mode is headless "
            "(local server only; visit http://127.0.0.1:8765/ in a "
            "browser). Pass --window to open a native pywebview window."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG,
    )
    parser.add_argument(
        "--window",
        action="store_true",
        help=(
            "显式打开 pywebview 原生窗口。默认不开，仅起 headless 服务。"
        ),
    )
    parser.add_argument(
        "--no-window",
        action="store_true",
        help=(
            "兼容 alias：已不需要（默认就是 headless），保留兼容。"
            "v2.x 会移除；如有需要请用 --window 显式开窗。"
        ),
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Bind port (default: 8765).",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=1200,
        help="Native window width in pixels (default: 1200).",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=800,
        help="Native window height in pixels (default: 800).",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help=(
            "Override the project root detection. Defaults to walking "
            "up from CWD looking for pyproject.toml."
        ),
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point. Returns the process exit code (0 on graceful
    shutdown, non-zero on a fatal startup error)."""

    parser = _build_parser()
    args = parser.parse_args(argv)

    from .launcher import launch_desktop, launch_server_only

    # P0A-1: deprecation handling. If the user passes --no-window (but
    # not --window), emit a one-line stderr warning and keep headless
    # mode. The warning deliberately does NOT point to --window because
    # the default is already headless; pointing to --window would
    # mislead the user into thinking the default opens a window.
    if args.no_window and not args.window:
        print(
            "planner-web: --no-window 已不需要（默认就是 headless），保留兼容。",
            file=sys.stderr,
        )
        args.window = False  # explicit for clarity; default is False anyway

    try:
        if args.window:
            launch_desktop(
                host=args.host,
                port=args.port,
                width=args.width,
                height=args.height,
                repo_root=args.repo_root,
            )
        else:
            launch_server_only(
                host=args.host,
                port=args.port,
                repo_root=args.repo_root,
            )
    except KeyboardInterrupt:
        # Operator hit Ctrl-C; treat as graceful shutdown.
        return 0
    except RuntimeError as exc:
        # Launcher-level errors (port in use, missing optional dep).
        # Print the friendly message and exit non-zero so CI / shells
        # see the failure.
        print(f"planner-web: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via __main__.py
    raise SystemExit(main())