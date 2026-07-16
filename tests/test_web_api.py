"""HTTP smoke tests for the planner web layer.

These tests use FastAPI's in-memory ``TestClient`` — no real uvicorn
process is spawned. Each test builds its own app via
:func:`planner.web.create_app` with an explicit ``repo_root`` so the
out_dir policy can be exercised against ``tmp_path`` without leaking
artifacts into the repository.

The tests are organized to mirror the eight documented endpoints, plus
a few safety-net checks (path traversal, error mapping).
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from planner.web.app import create_app  # noqa: E402


# --- fixtures ---------------------------------------------------------


@pytest.fixture
def app_with_repo(tmp_path: Path):
    """Build a TestClient + repo wired to a tmp-path repo with a
    minimal dev config. Returns ``(client, repo)``."""

    repo = tmp_path / "fake_repo"
    repo.mkdir()
    (repo / "config").mkdir()
    (repo / "config" / "development.json").write_text(
        json.dumps(
            {
                "env": "development",
                "allow_overwrite_runs": True,
                "executor_default_status": "pending",
                "submit_paid_jobs": False,
                "log_level": "DEBUG",
                "executor_dry_run": True,
                "data_root": "data/development",
                "assets_root": "assets/development",
                "runs_root": "runs/development",
                "logs_root": "logs/development",
                "schema_strict": False,
                "planner_provider": "deterministic",
                "allow_provider_fallback": True,
            }
        ),
        encoding="utf-8",
    )
    (repo / "data").mkdir()
    (repo / "data" / "development").mkdir()
    (repo / "data" / "development" / "input_scripts").mkdir()
    app = create_app(repo_root=repo)
    client = TestClient(app)
    return client, repo


@pytest.fixture
def sample_script(app_with_repo) -> Path:
    """Write a small valid script into the fake repo's input dir."""

    _, repo = app_with_repo
    script = repo / "data" / "development" / "input_scripts" / "EP01.txt"
    script.write_text(
        "EP01 — Test\n\n"
        "场 1 内景 咖啡馆 — 日\n"
        "林夏走进咖啡馆，点了一杯美式。\n"
        "苏晨（紧张）：你来了。\n",
        encoding="utf-8",
    )
    return script


# --- /api/health ------------------------------------------------------


def test_health_lists_deterministic(app_with_repo):
    client, _ = app_with_repo
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "version" in body
    assert "deterministic" in body["providers"]


# --- /api/config ------------------------------------------------------


def test_get_config_development(app_with_repo):
    client, _ = app_with_repo
    resp = client.get("/api/config?env=development")
    assert resp.status_code == 200
    body = resp.json()
    assert body["env"] == "development"
    assert body["planner_provider"] == "deterministic"
    assert body["allow_provider_fallback"] is True
    assert body["is_production"] is False


def test_get_config_production_missing_returns_404(app_with_repo):
    client, _ = app_with_repo
    # No config/production.json in the fake repo → 404 + hint.
    resp = client.get("/api/config?env=production")
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["error"] == "ConfigError"
    assert "production.example.json" in detail["message"]


def test_get_config_uses_explicit_repo_root_not_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    """P1.2 v1.0 invariant: ``/api/config`` MUST resolve
    ``<repo_root>/config/<env>.json`` from the explicit ``repo_root``
    passed to ``create_app``, not from the current working directory.

    Simulates the "launched from /tmp in a PyInstaller bundle"
    failure mode that motivated the v1.0 plan §1.2.
    """

    repo = tmp_path / "fake_repo"
    repo.mkdir()
    (repo / "config").mkdir()
    (repo / "config" / "development.json").write_text(
        json.dumps(
            {
                "env": "development",
                "allow_overwrite_runs": True,
                "executor_default_status": "pending",
                "submit_paid_jobs": False,
                "log_level": "DEBUG",
                "executor_dry_run": True,
                "data_root": "data/development",
                "assets_root": "assets/development",
                "runs_root": "runs/development",
                "logs_root": "logs/development",
                "schema_strict": False,
                "planner_provider": "deterministic",
                "allow_provider_fallback": True,
            }
        ),
        encoding="utf-8",
    )

    # Pretend the operator ran the binary from a completely unrelated
    # directory (e.g. ``cd /tmp && planner-web``).
    unrelated_cwd = tmp_path / "unrelated_workdir"
    unrelated_cwd.mkdir()
    monkeypatch.chdir(unrelated_cwd)

    # Create the app with the explicit repo_root. Note: no auto-detection.
    app = create_app(repo_root=repo)
    client = TestClient(app)

    resp = client.get("/api/config?env=development")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["env"] == "development"
    # Belt-and-braces: prove we read THIS repo's file, not the CWD's.
    assert body["runs_root"].endswith("runs/development")
    # No config/production.json in the fake repo → 404 (still works).
    resp_prod = client.get("/api/config?env=production")
    assert resp_prod.status_code == 404
    assert "production.example.json" in resp_prod.json()["detail"]["message"]


def test_get_config_invalid_existing_returns_400(app_with_repo) -> None:
    """P1.2 v1.0 invariant: ``/api/config`` MUST return 400 when the
    config file exists but cannot be parsed / fails ``load_config``
    validation. This keeps "missing → 404" and "broken → 400"
    distinguishable for the operator.
    """

    client, repo = app_with_repo
    # Overwrite development.json with content that fails
    # load_config (missing planner_provider → unknown provider error).
    (repo / "config" / "development.json").write_text(
        json.dumps(
            {
                "env": "development",
                "allow_overwrite_runs": True,
                "executor_default_status": "pending",
                "submit_paid_jobs": False,
                "log_level": "DEBUG",
                "executor_dry_run": True,
                "data_root": "data/development",
                "assets_root": "assets/development",
                "runs_root": "runs/development",
                "logs_root": "logs/development",
                "schema_strict": False,
                "planner_provider": "no_such_provider_xyz",
                "allow_provider_fallback": True,
            }
        ),
        encoding="utf-8",
    )

    resp = client.get("/api/config?env=development")
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "ConfigError"
    assert "no_such_provider_xyz" in detail["message"]


def test_get_config_packaged_mode_without_repo_root(
    tmp_path: Path, monkeypatch
) -> None:
    """P1.2 v1.0 invariant: when ``create_app`` is given no ``repo_root``
    (PyInstaller / no-repo mode), ``/api/config`` MUST NOT silently
    fall back to ``Path.cwd()``. It should fail with an actionable
    error telling the operator to pass ``?config_path=``.
    """

    # Simulate "no repo anywhere": chdir into a tmp dir with no
    # pyproject.toml ancestor and stub detect_repo_root to return None
    # so create_app() ends up with repo_root=None for the request.
    stray = tmp_path / "stray"
    stray.mkdir()
    monkeypatch.chdir(stray)

    from planner.web import app as app_mod

    monkeypatch.setattr(app_mod, "detect_repo_root", lambda *a, **kw: None)

    app = create_app(repo_root=None)
    client = TestClient(app)

    resp_dev = client.get("/api/config?env=development")
    assert resp_dev.status_code == 400, resp_dev.text
    assert "?config_path" in resp_dev.json()["detail"]["message"]

    resp_prod = client.get("/api/config?env=production")
    assert resp_prod.status_code == 404, resp_prod.text
    assert "?config_path" in resp_prod.json()["detail"]["message"]


def test_provider_output_error_maps_to_502(
    app_with_repo, sample_script, monkeypatch
) -> None:
    """P3-1 Codex review: ``ProviderOutputError`` (LLM response
    malformed / schema mismatch) MUST surface as HTTP 502 Bad
    Gateway — distinct from 503 ``ProviderUnavailableError`` so the
    GUI can tell "upstream rejected" from "upstream unreachable".
    """

    from planner.exceptions import ProviderOutputError
    from planner.web import run_service as rs

    client, _ = app_with_repo

    def _explode(*args, **kwargs):
        raise ProviderOutputError(
            "[openai_compatible/gpt-4o::extract_beats] response is not "
            "valid JSON: Expecting value at line 1 col 1."
        )

    monkeypatch.setattr(rs.RunService, "start_run", _explode)

    resp = client.post(
        "/api/runs",
        json={
            "env": "development",
            "script_path": str(sample_script),
        },
    )
    assert resp.status_code == 502, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "ProviderOutputError"
    assert "openai_compatible" in detail["message"]
    # No traceback leaked.
    assert "Traceback" not in detail["message"]


# --- /api/runs (list + start) ----------------------------------------


def test_post_runs_against_real_pipeline(app_with_repo, sample_script):
    client, repo = app_with_repo
    out_dir = repo / "runs" / "development" / "test-run-1"
    resp = client.post(
        "/api/runs",
        json={
            "env": "development",
            "script_path": str(sample_script),
            "out_dir": str(out_dir),
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    run_id = body["run_id"]
    assert body["status"] == "running"
    assert body["env"] == "development"

    # Wait for the background pipeline to finish.
    deadline = time.time() + 15
    while time.time() < deadline:
        s = client.get(f"/api/runs/{run_id}/summary").json()
        if s["status"] in ("done", "failed"):
            break
        time.sleep(0.2)
    else:
        pytest.fail("pipeline did not finish in 15s")

    s = client.get(f"/api/runs/{run_id}/summary").json()
    assert s["status"] == "done", s
    assert s["summary"] is not None
    assert "artifacts" in s["summary"]
    # 10 artifacts: script_parse + 9 core + executor_tasks + run_summary
    # (run_summary is in summary; artifacts dir has 10 JSON files.)
    assert len(s["summary"]["artifacts"]) >= 10

    # Download one artifact through the API.
    art = client.get(f"/api/runs/{run_id}/artifacts/script_parse")
    assert art.status_code == 200
    assert "blocks" in art.json()


def test_post_runs_default_out_dir_inside_repo_for_dev(app_with_repo, sample_script):
    """Red-line guard: dev default out_dir should be inside repo
    runs/development/ so colleagues can browse artifacts locally."""

    client, repo = app_with_repo
    resp = client.post(
        "/api/runs",
        json={
            "env": "development",
            "script_path": str(sample_script),
        },
    )
    assert resp.status_code == 200, resp.text
    out_dir = Path(resp.json()["out_dir"])
    assert out_dir.parent.parent.parent.resolve() == repo.resolve(), (
        f"dev default out_dir should be inside repo runs/development/, "
        f"got {out_dir}"
    )
    # Clean up: drop the artifact dir so the test leaves no residue.
    deadline = time.time() + 10
    while time.time() < deadline:
        s = client.get(f"/api/runs/{resp.json()['run_id']}/summary").json()
        if s["status"] in ("done", "failed"):
            break
        time.sleep(0.2)
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)


def test_post_runs_production_outside_repo_default(app_with_repo, sample_script):
    """Red-line guard: production default out_dir must NOT be inside
    the repo, even if the user does not specify one."""

    client, repo = app_with_repo
    # Create a production config so load_config succeeds.
    (repo / "config" / "production.json").write_text(
        json.dumps(
            {
                "env": "production",
                "allow_overwrite_runs": False,
                "executor_default_status": "pending_manual_approval",
                "submit_paid_jobs": False,
                "log_level": "INFO",
                "executor_dry_run": True,
                "data_root": "data/production",
                "assets_root": "assets/production",
                "runs_root": "runs/production",
                "logs_root": "logs/production",
                "schema_strict": True,
                "planner_provider": "deterministic",
                "allow_provider_fallback": False,
            }
        ),
        encoding="utf-8",
    )

    resp = client.post(
        "/api/runs",
        json={
            "env": "production",
            "script_path": str(sample_script),
        },
    )
    assert resp.status_code == 200, resp.text
    out_dir = Path(resp.json()["out_dir"])
    assert out_dir.is_absolute()
    # Must not be inside the repo (red line #3).
    try:
        out_dir.resolve().relative_to(repo.resolve())
        pytest.fail(f"production default out_dir leaked into repo: {out_dir}")
    except ValueError:
        pass  # Good: out_dir is outside repo.


def test_post_runs_production_inside_repo_is_rejected(app_with_repo, sample_script):
    """Red-line guard: production + explicit out_dir inside repo →
    403 EnvironmentBoundaryError, no directory created."""

    client, repo = app_with_repo
    (repo / "config" / "production.json").write_text(
        json.dumps(
            {
                "env": "production",
                "allow_overwrite_runs": False,
                "executor_default_status": "pending_manual_approval",
                "submit_paid_jobs": False,
                "log_level": "INFO",
                "executor_dry_run": True,
                "data_root": "data/production",
                "assets_root": "assets/production",
                "runs_root": "runs/production",
                "logs_root": "logs/production",
                "schema_strict": True,
                "planner_provider": "deterministic",
                "allow_provider_fallback": False,
            }
        ),
        encoding="utf-8",
    )

    forbidden_out = repo / "runs" / "production" / "leak"
    assert not forbidden_out.exists()

    resp = client.post(
        "/api/runs",
        json={
            "env": "production",
            "script_path": str(sample_script),
            "out_dir": str(forbidden_out),
        },
    )
    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "EnvironmentBoundaryError"
    assert "inside the project repository" in detail["message"]
    assert not forbidden_out.exists()


def test_post_runs_missing_script_returns_404(app_with_repo):
    client, _ = app_with_repo
    resp = client.post(
        "/api/runs",
        json={
            "env": "development",
            "script_path": "/nonexistent/script.txt",
        },
    )
    # 404 because ScriptReadError-style (we use EnvironmentBoundaryError
    # for "script not found" inside the service layer; classify maps it
    # to 404 because of the "not found" keyword). The exact status is
    # less important than: no traceback, friendly message.
    assert resp.status_code in (403, 404), resp.text
    detail = resp.json()["detail"]
    assert "Script not found" in detail["message"]


# --- /api/runs/{id}/artifacts/{name} ---------------------------------


def test_artifact_unknown_name_returns_404(app_with_repo):
    client, _ = app_with_repo
    resp = client.get("/api/runs/any-id/artifacts/etc_passwd")
    assert resp.status_code == 404
    assert "Unknown artifact" in resp.json()["detail"]["message"]


def test_artifact_unknown_run_returns_404(app_with_repo):
    client, _ = app_with_repo
    resp = client.get("/api/runs/no-such-run/artifacts/script_parse")
    assert resp.status_code == 404


# --- /api/runs/{id}/validate -----------------------------------------


def test_validate_endpoint_after_run(app_with_repo, sample_script):
    client, repo = app_with_repo
    out_dir = repo / "runs" / "development" / "validate-test"
    resp = client.post(
        "/api/runs",
        json={
            "env": "development",
            "script_path": str(sample_script),
            "out_dir": str(out_dir),
        },
    )
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]

    deadline = time.time() + 15
    while time.time() < deadline:
        s = client.get(f"/api/runs/{run_id}/summary").json()
        if s["status"] in ("done", "failed"):
            break
        time.sleep(0.2)
    assert s["status"] == "done"

    v = client.post(
        f"/api/runs/{run_id}/validate",
        json={"expected_env": "development"},
    )
    assert v.status_code == 200
    body = v.json()
    assert body["ok"] is True
    assert body["fallback_used"] is False
    # Audit fields present.
    for key in ("requested_provider", "effective_provider", "fallback_reason"):
        assert key in body


# --- /api/upload-script ----------------------------------------------


def test_upload_empty_file_rejected(app_with_repo):
    client, _ = app_with_repo
    resp = client.post(
        "/api/upload-script",
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert resp.status_code == 400


def test_upload_writes_to_app_data(app_with_repo):
    client, _ = app_with_repo
    payload = b"EP01\n\nscene 1\n"
    resp = client.post(
        "/api/upload-script",
        files={"file": ("EP01.txt", payload, "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["size_bytes"] == len(payload)
    saved = Path(body["saved_path"])
    assert saved.exists()
    assert saved.read_bytes() == payload


# --- list -------------------------------------------------------------


def test_list_runs_newest_first(app_with_repo, sample_script):
    client, repo = app_with_repo
    ids = []
    for i in range(2):
        out_dir = repo / "runs" / "development" / f"list-test-{i}"
        r = client.post(
            "/api/runs",
            json={
                "env": "development",
                "script_path": str(sample_script),
                "out_dir": str(out_dir),
            },
        )
        assert r.status_code == 200
        ids.append(r.json()["run_id"])

    r = client.get("/api/runs?env=development&limit=10")
    assert r.status_code == 200
    listed = r.json()["runs"]
    assert {r_["run_id"] for r_ in listed} >= set(ids)
    # Newest first: started_at should be monotonically non-increasing.
    times = [r_["started_at"] for r_ in listed]
    assert times == sorted(times, reverse=True)


# --- list does not block on background work ---------------------------


def test_list_does_not_block_on_background(app_with_repo, sample_script):
    """Calling /api/runs immediately after /api/runs POST should
    return quickly without waiting for the pipeline to finish."""

    client, repo = app_with_repo
    out_dir = repo / "runs" / "development" / "non-blocking"
    client.post(
        "/api/runs",
        json={
            "env": "development",
            "script_path": str(sample_script),
            "out_dir": str(out_dir),
        },
    )

    start = time.time()
    r = client.get("/api/runs")
    elapsed = time.time() - start
    assert r.status_code == 200
    assert elapsed < 1.0, f"/api/runs took {elapsed:.2f}s, should be <1s"


# --- P2-1: model-config API -------------------------------------------


def test_get_model_config_returns_defaults(tmp_path: Path, monkeypatch) -> None:
    """GET /api/model-config returns the persisted config + its path.
    When no file exists, returns defaults (planner_provider=deterministic)."""

    from planner.web import routes as routes_mod

    target = tmp_path / "model_config.json"
    monkeypatch.setattr(routes_mod, "default_config_path", lambda: target)

    client = TestClient(create_app(repo_root=tmp_path))
    resp = client.get("/api/model-config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["config"]["planner_provider"] == "deterministic"
    assert body["config"]["enable_real_model_calls"] is False
    assert body["path"] == str(target)
    # No API key value anywhere.
    assert "sk-" not in json.dumps(body)


def test_put_model_config_round_trips(tmp_path: Path, monkeypatch) -> None:
    """PUT /api/model-config persists the config; a subsequent GET
    returns the saved values. Never accepts a literal API key."""

    from planner.web import routes as routes_mod

    target = tmp_path / "model_config.json"
    monkeypatch.setattr(routes_mod, "default_config_path", lambda: target)

    client = TestClient(create_app(repo_root=tmp_path))
    cfg = {
        "planner_provider": "openai_compatible",
        "enable_real_model_calls": True,
        "allow_provider_fallback": False,
        "openai_compatible": {
            "base_url": "http://localhost:11434/v1",
            "model": "llama3.1",
            "api_key_env": "OLLAMA_KEY",
            "timeout_seconds": 45.0,
            "temperature": 0.2,
            "max_tokens": 1024,
        },
    }
    resp = client.put("/api/model-config", json={"config": cfg})
    assert resp.status_code == 200, resp.text
    assert resp.json()["path"] == str(target)
    assert target.exists()

    # GET returns the saved values.
    got = client.get("/api/model-config").json()
    assert got["config"]["planner_provider"] == "openai_compatible"
    assert got["config"]["openai_compatible"]["model"] == "llama3.1"
    assert got["config"]["openai_compatible"]["api_key_env"] == "OLLAMA_KEY"


def test_put_model_config_rejects_literal_key(
    tmp_path: Path, monkeypatch
) -> None:
    """The save path refuses to write any field that looks like a
    literal API key value (defense-in-depth on top of the schema)."""

    from planner.web import routes as routes_mod

    target = tmp_path / "model_config.json"
    monkeypatch.setattr(routes_mod, "default_config_path", lambda: target)

    client = TestClient(create_app(repo_root=tmp_path))
    # Stuff a literal key into api_key_env (schema would reject, but
    # test the redact guard directly via a field that accepts strings).
    cfg = {
        "planner_provider": "deterministic",
        "openai_compatible": {
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "api_key_env": "sk-abcdefghij1234567890ABCDEF",
        },
    }
    resp = client.put("/api/model-config", json={"config": cfg})
    assert resp.status_code == 400, resp.text
    assert not target.exists()


# --- P2-2: batch endpoint ---------------------------------------------


def test_post_batches_deterministic(app_with_repo, sample_script) -> None:
    """POST /api/batches runs every .txt under scripts_dir and
    returns the full BatchSummary."""

    client, repo = app_with_repo
    scripts_dir = repo / "data" / "development" / "input_scripts"
    # sample_script fixture already wrote EP01.txt; add EP02.
    (scripts_dir / "EP02.txt").write_text(
        "EP02 - Test\n\n场 1 内景 办公室 - 日\n苏晨走进办公室。\n",
        encoding="utf-8",
    )
    out_dir = repo / "runs" / "development" / "batch-test"

    resp = client.post(
        "/api/batches",
        json={
            "env": "development",
            "scripts_dir": str(scripts_dir),
            "out_dir": str(out_dir),
            "fail_fast": True,
            "skip_validation": True,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["totals"]["episodes_total"] == 2
    assert body["totals"]["episodes_done"] == 2
    assert body["totals"]["episodes_failed"] == 0
    assert "summary_path" in body
    assert (out_dir / "batch_summary.json").exists()


def test_post_batches_production_rejects_repo_out_dir(
    app_with_repo, sample_script
) -> None:
    """Production batch MUST refuse an out_dir inside the repo (red
    line #3), mirroring the CLI batch path."""

    client, repo = app_with_repo
    (repo / "config" / "production.json").write_text(
        json.dumps(
            {
                "env": "production",
                "allow_overwrite_runs": False,
                "executor_default_status": "pending_manual_approval",
                "submit_paid_jobs": False,
                "log_level": "INFO",
                "executor_dry_run": True,
                "data_root": "data/production",
                "assets_root": "assets/production",
                "runs_root": "runs/production",
                "logs_root": "logs/production",
                "schema_strict": True,
                "planner_provider": "deterministic",
                "allow_provider_fallback": False,
            }
        ),
        encoding="utf-8",
    )
    scripts_dir = repo / "data" / "development" / "input_scripts"
    forbidden_out = repo / "runs" / "production" / "batch-leak"

    resp = client.post(
        "/api/batches",
        json={
            "env": "production",
            "scripts_dir": str(scripts_dir),
            "out_dir": str(forbidden_out),
        },
    )
    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "EnvironmentBoundaryError"
    assert not forbidden_out.exists()


def test_post_batches_missing_scripts_dir_returns_error(app_with_repo) -> None:
    """A non-existent scripts_dir surfaces as a friendly error, not a
    traceback."""

    client, _ = app_with_repo
    resp = client.post(
        "/api/batches",
        json={
            "env": "development",
            "scripts_dir": "/nonexistent/zzz",
        },
    )
    assert resp.status_code in (400, 403, 404), resp.text
    detail = resp.json()["detail"]
    assert "message" in detail


# --- P0A-3: backend stable error shape contract -------------------------


def test_web_api_returns_stable_error_shape_for_broken_reference(
    app_with_repo, monkeypatch, project_root
) -> None:
    """P0A-3 contract: the backend returns the stable JSON shape
    ``{error: <type>, message: <raw>}``; the frontend (app.js::
    formatUserError) is what translates the type into a user-friendly
    Chinese sentence.

    This test pins the backend's responsibility: it MUST keep the
    engineering-semantic type name and MUST NOT include any
    frontend-friendly keyword in the raw message. A future refactor
    that, say, returns ``{"error": "BadRequest", "message": "分镜..."}``
    from the backend would break this contract and get caught here.
    """

    from unittest.mock import patch

    from planner.exceptions import BrokenReferenceError

    client, _repo = app_with_repo
    sample = project_root / "data" / "development" / "input_scripts" / "sample_ep01.txt"
    # Patch the service that create_app() already wired into app.state.
    # No need to re-include the router (FastAPI would raise on double-mount).
    with patch.object(
        app_with_repo[0].app.state.run_service,
        "start_run",
        side_effect=BrokenReferenceError(
            "shot EP01_SH001 references unknown location_id 'scene_default'"
        ),
    ):
        resp = client.post(
            "/api/runs",
            json={"env": "development", "script_path": str(sample)},
        )
    assert resp.status_code == 500, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "BrokenReferenceError"
    # Raw engineering message is preserved verbatim
    assert "scene_default" in detail["message"]
    assert "EP01_SH001" in detail["message"]
    # Backend MUST NOT include frontend-friendly keywords.
    # (Those live in app.js::formatUserError — not in the API response.)
    for forbidden in ("分镜", "建议", "检查", "重试", "切换"):
        assert forbidden not in detail["message"], (
            f"backend must not include frontend-friendly keyword "
            f"{forbidden!r}; got detail.message={detail['message']!r}"
        )