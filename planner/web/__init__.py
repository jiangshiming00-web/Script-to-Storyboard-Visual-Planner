"""Web/GUI layer for the planner.

Phase-2 ships the FastAPI backend (``app``, ``routes``, ``run_service``,
``run_registry``, ``errors``). Phase-3 adds the static UI bundle
(``planner/web/static/``), the pywebview launcher (``launcher``),
the ``planner-web`` console script (``scripts_entry``), and the
``python -m planner.web`` entry (``__main__``).

Importing this package pulls in :mod:`fastapi` via :mod:`.app`. That is
an optional dependency, so the base install (pydantic + click only)
must avoid importing this package at any module level — ``planner``
itself does not reference ``planner.web`` anywhere in its core code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = ["create_app", "launch_desktop"]


def __getattr__(name: str):  # PEP 562 lazy attribute access
    if name == "create_app":
        from .app import create_app

        return create_app
    if name == "launch_desktop":
        # Phase-3 will provide the real implementation. Until then,
        # raise a clear error so callers know what to install.
        try:
            from .launcher import launch_desktop

            return launch_desktop
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "launch_desktop is not available. Install the GUI "
                "extras with: pip install -e '.[gui]'"
            ) from exc
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if TYPE_CHECKING:
    from .app import create_app  # noqa: F401
    from .launcher import launch_desktop  # noqa: F401