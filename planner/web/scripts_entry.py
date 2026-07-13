"""Console-script entry point for the ``planner-web`` command.

Registered in ``pyproject.toml``::

    [project.scripts]
    planner-web = "planner.web.scripts_entry:main"

Also reachable as ``python -m planner.web`` via :mod:`planner.web.__main__`.

The function dispatches to either :func:`launch_desktop` (default) or
:func:`launch_server_only` (with ``--no-window``) based on the CLI
flags. CLI parsing is intentionally light: we don't pull in Click
because the launcher is a small surface area and we'd rather not
add a dependency to the ``gui`` extra.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Sequence


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="planner-web",
        description=(
            "Launch the planner web UI. Default opens a native "
            "pywebview window; --no-window starts a headless server "
            "(useful for CI and remote browsers)."
        ),
    )
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="Skip the native window; only start the local server.",
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

    try:
        if args.no_window:
            launch_server_only(
                host=args.host, port=args.port, repo_root=args.repo_root,
            )
        else:
            launch_desktop(
                host=args.host,
                port=args.port,
                width=args.width,
                height=args.height,
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