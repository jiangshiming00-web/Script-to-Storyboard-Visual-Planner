"""FastAPI application factory.

The factory takes optional overrides so tests can spin up an
in-memory app without touching the filesystem or starting uvicorn.
The :func:`create_app` function is what ``planner-web`` and the
``tests/test_web_api.py`` import.

State stored on ``app.state``:

- ``repo_root`` (Path or None) — used by the run service to apply the
  out_dir policy.
- ``run_registry`` (RunRegistry) — process-local run tracking.
- ``run_service`` (RunService) — service-layer facade.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ..exceptions import PlannerError
from .errors import classify
from .run_registry import RunRegistry
from .run_service import RunService, detect_repo_root
from .routes import make_router

_log = logging.getLogger(__name__)

# Path to the bundled static UI, relative to this file.
_STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(
    *,
    repo_root: Optional[Path] = None,
    static_dir: Optional[Path] = None,
) -> FastAPI:
    """Build and return a fully wired FastAPI app.

    Args:
        repo_root: Project root for the out_dir policy. If omitted,
            :func:`detect_repo_root` walks up from CWD looking for
            ``pyproject.toml`` / ``config/development.json``. Pass an
            explicit value from tests to avoid relying on CWD.
        static_dir: Override path to the static UI directory. Useful
            for tests that want to mount a different folder. Defaults
            to the bundled ``planner/web/static/``.
    """

    if repo_root is None:
        repo_root = detect_repo_root()

    app = FastAPI(
        title="Script-to-Storyboard Visual Planner",
        version="0.1.0",
        # Disable docs at the root when the static UI is mounted at
        # ``/`` so /docs and /openapi.json still work but do not
        # shadow the SPA index.
    )

    app.state.repo_root = repo_root
    app.state.run_registry = RunRegistry()
    app.state.run_service = RunService(app.state.run_registry, repo_root=repo_root)

    # Mount API routes.
    app.include_router(make_router(app.state.run_service))

    # Global exception handler: PlannerError → friendly JSON, no
    # traceback in the body. This is the load-bearing invariant for
    # the project's "rejected loudly, never silently" error style.
    @app.exception_handler(PlannerError)
    async def _planner_error_handler(
        request: Request, exc: PlannerError
    ) -> JSONResponse:
        status, err_type, message = classify(exc)
        # Full traceback still flows to server logs (via FastAPI's
        # default logger) — we just don't echo it in the HTTP body.
        _log.info(
            "PlannerError on %s %s: %s: %s",
            request.method, request.url.path, err_type, message,
        )
        return JSONResponse(
            status_code=status,
            content={"error": err_type, "message": message},
        )

    # Catch-all for unexpected exceptions so the client gets a
    # stable JSON shape (never an HTML traceback page).
    @app.exception_handler(Exception)
    async def _unhandled_error_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        _log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": "UnhandledError", "message": "An internal error occurred."},
        )

    # Mount the static UI at "/" if the static dir exists.
    static_path = static_dir or _STATIC_DIR
    if static_path.exists():
        app.mount("/", StaticFiles(directory=str(static_path), html=True), name="ui")
    else:
        # Static UI not yet present (Phase 2 lands before Phase 3).
        # Provide a tiny placeholder so curl /api/health still works
        # and the operator knows where to look.
        @app.get("/")
        def _root_placeholder() -> dict:
            return {
                "name": "Script-to-Storyboard Visual Planner",
                "ui": "not built yet — Phase 3 will add planner/web/static/",
                "api_root": "/api",
            }

    return app