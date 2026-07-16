"""HTTP route handlers for the planner web layer.

All endpoints return JSON. Errors come from
:mod:`planner.web.errors.classify` — never a raw traceback.

The eight endpoints (full list in the project plan at
``docs/.context/sparkling-honking-sprout.md``):

- ``GET  /api/health``
- ``GET  /api/config?env=...``
- ``GET  /api/runs?env=...&limit=...``
- ``POST /api/runs``
- ``GET  /api/runs/{run_id}/summary``
- ``GET  /api/runs/{run_id}/artifacts/{name}``
- ``POST /api/runs/{run_id}/validate``
- ``POST /api/upload-script`` (multipart)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from .. import __version__
from ..batch import BatchOptions, run_batch
from ..env import load_config
from ..exceptions import ConfigError, PlannerError
from ..model_config import (
    ModelProviderConfig,
    default_config_path,
    load_model_config,
    save_model_config,
)
from ..providers import available_providers
from .errors import classify
from .run_service import RunService, os_app_data_dir, resolve_out_dir

_log = logging.getLogger(__name__)


VALID_ARTIFACTS = {
    "script_parse",
    "character_bible",
    "location_bible",
    "prop_bible",
    "story_beats",
    "shot_list",
    "image_prompts",
    "video_prompts",
    "asset_manifest",
    "executor_tasks",
    "run_summary",
}


# --- pydantic request/response models ---------------------------------


class RunRequest(BaseModel):
    env: str = Field(pattern="^(development|production)$")
    script_path: str
    out_dir: Optional[str] = None
    force: bool = False
    config_path: Optional[str] = None
    # v1.0 P2-1: optional path to a model config JSON saved via
    # PUT /api/model-config. When supplied, the run service loads it
    # and injects the matching ProviderRuntimeSettings into the
    # provider instance (see P1-1).
    model_config_path: Optional[str] = None

    # Pydantic v2 protects the ``model_`` namespace; our field is
    # named ``model_config_path`` which collides. Opt out so the field
    # serializes normally.
    model_config = ConfigDict(protected_namespaces=())


class ModelConfigRequest(BaseModel):
    """Body for ``PUT /api/model-config``.

    The ``config`` field is a :class:`ModelProviderConfig` dict. The
    API never accepts or returns the API key *value* - only the env
    var name (``api_key_env``).
    """

    config: dict


class BatchRequest(BaseModel):
    """Body for ``POST /api/batches`` (v1.0 P2-2).

    Mirrors the CLI ``planner batch`` flags. ``out_dir`` defaults to
    the env-specific batch root (dev: repo, prod: OS app-data) when
    omitted; production rejects repo-internal paths.
    """

    env: str = Field(pattern="^(development|production)$")
    scripts_dir: str
    out_dir: Optional[str] = None
    force: bool = False
    fail_fast: bool = True
    skip_validation: bool = False
    model_config_path: Optional[str] = None

    model_config = ConfigDict(protected_namespaces=())


class RunAcceptedResponse(BaseModel):
    run_id: str
    status: str
    out_dir: str
    started_at: str
    env: str


class ValidateRequest(BaseModel):
    expected_env: Optional[str] = Field(
        default=None, pattern="^(development|production)$"
    )


# --- router factory ---------------------------------------------------


def make_router(service: RunService) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health")
    def health() -> dict:
        return {
            "ok": True,
            "version": __version__,
            "providers": available_providers(),
        }

    @router.get("/config")
    def get_config(
        env: str = "development",
        config_path: Optional[str] = None,
    ) -> dict:
        cfg_path = Path(config_path).expanduser() if config_path else None
        repo_root = service.repo_root  # public facade (no SLF001)

        # Resolve the default config path explicitly from repo_root so
        # the endpoint NEVER falls back to ``Path.cwd()``. This is the
        # v1.0 invariant: launching the GUI from an unrelated working
        # directory (PyInstaller bundle, ``cd`` into /tmp, CI runner,
        # etc.) must still land on the project's ``config/<env>.json``.
        if cfg_path is None:
            if repo_root is None:
                # Packaged / no-repo mode: there is no project tree to
                # resolve from. Require the caller to pass an absolute
                # config path explicitly. Production missing config
                # gets 404 (matches the existing "copy from example"
                # hint); development gets 400 because we can't really
                # help without a path.
                status = 404 if env == "production" else 400
                raise HTTPException(
                    status_code=status,
                    detail={
                        "error": "ConfigError",
                        "message": (
                            f"No project repository detected. Pass "
                            f"?config_path=/abs/path/to/{env}.json "
                            f"explicitly."
                        ),
                    },
                )
            cfg_path = repo_root / "config" / f"{env}.json"

        # Production pre-flight: distinguish 404 (file missing → "copy
        # from example" hint) from 400 (file present but invalid).
        # Substring-matching on the ConfigError message is fragile,
        # so we check the file system instead. The check runs BEFORE
        # load_config so the operator gets the actionable hint instead
        # of a generic "config error" wall.
        if env == "production" and not cfg_path.exists():
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "ConfigError",
                    "message": (
                        f"Config file not found: {cfg_path}. "
                        f"Copy config/production.example.json to "
                        f"config/production.json before running in "
                        f"production."
                    ),
                },
            )

        try:
            cfg = load_config(
                env=env,
                project_root=repo_root,
                config_path=cfg_path,
            )
        except ConfigError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": "ConfigError", "message": str(exc)},
            )
        return {**cfg.as_dict(), "is_production": cfg.is_production}

    @router.get("/model-config")
    def get_model_config() -> dict:
        """Return the persisted v1.0 model config + its on-disk path.

        Reads :func:`default_config_path`. If the file does not exist,
        returns defaults. Never includes the API key value - only the
        env var name (``api_key_env``).
        """

        path = default_config_path()
        try:
            cfg = load_model_config(path)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": "ConfigError", "message": str(exc)},
            )
        return {"config": cfg.model_dump(mode="json"), "path": str(path)}

    @router.put("/model-config")
    def put_model_config(req: ModelConfigRequest) -> dict:
        """Persist the v1.0 model config to :func:`default_config_path`.

        Refuses to write if any field looks like a literal API key
        (defense-in-depth on top of the schema). Returns the path
        written so the GUI can pass it to subsequent ``POST /api/runs``.
        """

        path = default_config_path()
        try:
            cfg = ModelProviderConfig.model_validate(req.config)
            save_model_config(cfg, path=path)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": "ConfigError", "message": str(exc)},
            )
        except Exception as exc:
            # pydantic.ValidationError or shape mismatch.
            raise HTTPException(
                status_code=400,
                detail={"error": "ConfigError", "message": str(exc)},
            )
        return {"path": str(path)}

    @router.get("/runs")
    def list_runs(
        env: Optional[str] = None,
        limit: int = 50,
    ) -> dict:
        records = service.list_runs(env=env)
        return {
            "runs": [r.to_dict() for r in records[: max(0, limit)]]
        }

    @router.post("/runs", response_model=RunAcceptedResponse)
    def start_run(req: RunRequest, request: Request) -> RunAcceptedResponse:
        repo_root = getattr(request.app.state, "repo_root", None)
        try:
            run_id, out_dir = service.start_run(
                env=req.env,
                script_path=Path(req.script_path).expanduser(),
                user_out_dir=(
                    Path(req.out_dir).expanduser() if req.out_dir else None
                ),
                config_path=(
                    Path(req.config_path).expanduser()
                    if req.config_path
                    else None
                ),
                force=req.force,
                repo_root=repo_root,
                model_config_path=(
                    Path(req.model_config_path).expanduser()
                    if req.model_config_path
                    else None
                ),
            )
        except PlannerError as exc:
            status, err_type, msg = classify(exc)
            raise HTTPException(
                status_code=status,
                detail={"error": err_type, "message": msg},
            )

        rec = service.get_run(run_id)
        return RunAcceptedResponse(
            run_id=run_id,
            status=rec.status if rec else "running",
            out_dir=str(out_dir),
            started_at=rec.started_at if rec else "",
            env=req.env,
        )

    @router.post("/batches")
    def start_batch(req: BatchRequest, request: Request) -> dict:
        """Run ``planner batch`` synchronously and return the summary.

        v1.0 P2-2: the batch runs in the FastAPI threadpool (this is
        a ``def``, not ``async def``) so it doesn't block the event
        loop. Production still rejects repo-internal ``out_dir`` via
        :func:`resolve_out_dir`. The response is the full
        :class:`BatchSummary` so the GUI can show per-episode status
        + the summary path.
        """

        repo_root = getattr(request.app.state, "repo_root", None)
        try:
            config = load_config(env=req.env, project_root=repo_root)
        except PlannerError as exc:
            status, err_type, msg = classify(exc)
            raise HTTPException(
                status_code=status, detail={"error": err_type, "message": msg}
            )

        if req.force and config.is_production:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "EnvironmentBoundaryError",
                    "message": "Refusing --force in production.",
                },
            )
        if req.force:
            object.__setattr__(config, "allow_overwrite_runs", True)

        # v1.0 P1-1: load model config and let it steer the provider.
        model_config = None
        if req.model_config_path:
            try:
                model_config = load_model_config(
                    Path(req.model_config_path).expanduser()
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "ConfigError", "message": str(exc)},
                )
        if (
            model_config is not None
            and model_config.planner_provider != "deterministic"
        ):
            object.__setattr__(
                config, "planner_provider", model_config.planner_provider
            )

        user_out = (
            Path(req.out_dir).expanduser() if req.out_dir else None
        )
        try:
            out_dir = resolve_out_dir(req.env, user_out, repo_root)
            options = BatchOptions(
                env=req.env,
                scripts_dir=Path(req.scripts_dir).expanduser(),
                out_dir=out_dir,
                fail_fast=req.fail_fast,
                config_path=None,
                repo_root=repo_root,
                skip_validation=req.skip_validation,
            )
            summary = run_batch(options, config=config, model_config=model_config)
        except PlannerError as exc:
            status, err_type, msg = classify(exc)
            raise HTTPException(
                status_code=status, detail={"error": err_type, "message": msg}
            )

        result = summary.model_dump(mode="json")
        result["summary_path"] = str(out_dir / "batch_summary.json")
        return result

    @router.get("/runs/{run_id}/summary")
    def get_summary(run_id: str) -> dict:
        rec = service.get_run(run_id)
        if rec is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "NotFound", "message": f"Unknown run: {run_id}"},
            )
        summary_path = rec.out_dir / "run_summary.json"
        record_dict = rec.to_dict()
        if not summary_path.exists():
            # Run still in progress or failed before writing summary.
            return {**record_dict, "summary": None}
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning("Failed to read %s: %s", summary_path, exc)
            return {**record_dict, "summary": None}
        return {**record_dict, "summary": summary}

    @router.get("/runs/{run_id}/artifacts/{name}")
    def get_artifact(run_id: str, name: str) -> dict:
        if name not in VALID_ARTIFACTS:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "NotFound",
                    "message": (
                        f"Unknown artifact: {name!r}. Valid names: "
                        f"{sorted(VALID_ARTIFACTS)}"
                    ),
                },
            )
        rec = service.get_run(run_id)
        if rec is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "NotFound", "message": f"Unknown run: {run_id}"},
            )
        path = rec.out_dir / f"{name}.json"
        if not path.exists():
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "NotFound",
                    "message": f"Artifact not yet written: {name!r}",
                },
            )
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "ArtifactReadError",
                    "message": f"Failed to read {name}: {exc}",
                },
            )

    @router.post("/runs/{run_id}/validate")
    def validate_run_endpoint(run_id: str, req: ValidateRequest) -> dict:
        rec = service.get_run(run_id)
        if rec is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "NotFound", "message": f"Unknown run: {run_id}"},
            )
        try:
            report = service.validate_run(
                run_dir=rec.out_dir,
                expected_env=req.expected_env,
            )
        except PlannerError as exc:
            status, err_type, msg = classify(exc)
            raise HTTPException(
                status_code=status,
                detail={"error": err_type, "message": msg},
            )
        return asdict(report)

    @router.post("/upload-script")
    async def upload_script(file: UploadFile = File(...)) -> dict:
        # P0A-5: validate filename / extension / size BEFORE write.
        # No new exception class: HTTPException(detail={error, message})
        # matches the existing web-layer convention (see classify() and
        # other routes in this file). The frontend formatUserError()
        # matches "UploadValidationError" to its upload-failure prefix.

        # 1. filename path-traversal (reject / \\ .. null byte)
        name = file.filename or ""
        if not name:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "UploadValidationError",
                    "message": "文件名为空。",
                },
            )
        if any(c in name for c in ("/", "\\", "\x00")) or ".." in name:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "UploadValidationError",
                    "message": (
                        f"文件名包含非法字符（/ \\\\ .. 或 null byte）：{name!r}"
                    ),
                },
            )

        # 2. extension whitelist (.txt only in P0A; .docx deferred to P1)
        ext = Path(name).suffix.lower()
        if ext != ".txt":
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "UploadValidationError",
                    "message": (
                        f"仅支持 .txt 文件（.docx 在 v1.0 P1 支持；.doc 不支持）。"
                        f"收到：{ext!r}"
                    ),
                },
            )

        # 3. size cap (env var overridable for tests; default 10MB)
        max_bytes = int(
            os.environ.get("PLANNER_UPLOAD_MAX_BYTES", str(10 * 1024 * 1024))
        )

        contents = await file.read()
        if len(contents) > max_bytes:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "UploadValidationError",
                    "message": (
                        f"文件过大：{len(contents)} 字节 > 上限 {max_bytes} 字节。"
                    ),
                },
            )
        if not contents:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "UploadValidationError",
                    "message": "空文件。",
                },
            )

        # 4. write (unchanged from v3.0)
        sha = hashlib.sha256(contents).hexdigest()
        upload_dir = os_app_data_dir() / "uploaded_scripts"
        upload_dir.mkdir(parents=True, exist_ok=True)
        target = upload_dir / f"{sha}.txt"  # extension forced; sha name is safe
        if not target.exists():
            # Atomic-ish: write to .tmp then rename so a half-written
            # file never lands in the upload dir.
            tmp = target.with_suffix(".tmp")
            tmp.write_bytes(contents)
            tmp.replace(target)
        return {
            "saved_path": str(target),
            "size_bytes": len(contents),
            "sha256": sha,
            "filename": name,
        }

    return router