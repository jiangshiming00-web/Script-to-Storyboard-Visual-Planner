"""Unit tests for ``planner.web.run_service``.

These exercise the out_dir policy, the no-residue cleanup, and the
production fail-closed invariant at the service layer — independent
of the HTTP layer.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

from planner.exceptions import EnvironmentBoundaryError
from planner.web.run_registry import RunRegistry
from planner.web.run_service import (
    RunService,
    default_out_dir,
    detect_repo_root,
    generate_run_id,
    is_inside_repo,
    os_app_data_dir,
    resolve_out_dir,
)


# --- out_dir policy ---------------------------------------------------


def test_os_app_data_dir_uses_platform_specific_path(tmp_path, monkeypatch):
    """The app-data dir resolves to a platform-appropriate location."""

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    if sys.platform == "darwin":
        expected = fake_home / "Library" / "Application Support" / "ShortDramaPlanner"
    elif sys.platform == "win32":
        # Fallback when APPDATA is unset.
        monkeypatch.delenv("APPDATA", raising=False)
        expected = fake_home / "AppData" / "Roaming" / "ShortDramaPlanner"
    else:
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        expected = fake_home / ".local" / "share" / "ShortDramaPlanner"
    assert os_app_data_dir() == expected


def test_is_inside_repo_true_and_false(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    inside = repo / "runs" / "x"
    outside = tmp_path / "elsewhere"
    assert is_inside_repo(inside, repo) is True
    assert is_inside_repo(outside, repo) is False


def test_default_out_dir_dev_inside_repo(tmp_path):
    repo = tmp_path / "repo"
    out = default_out_dir("development", repo)
    # out = <repo>/runs/development/<run_id>/
    assert out.parent == repo / "runs" / "development"
    assert out.name  # run_id is non-empty


def test_default_out_dir_prod_outside_repo(tmp_path):
    """Production must never default into the repo."""

    repo = tmp_path / "repo"
    out = default_out_dir("production", repo)
    assert not is_inside_repo(out, repo)


def test_os_app_data_dir_env_override(tmp_path, monkeypatch):
    """``PLANNER_APP_DATA_ROOT`` redirects ``os_app_data_dir()`` to a
    scratch directory so the GUI smoke harness does not write the
    user's real OS app-data store."""

    target = tmp_path / "scratch_app_data"
    monkeypatch.setenv("PLANNER_APP_DATA_ROOT", str(target))
    assert os_app_data_dir() == target.expanduser()


def test_default_config_path_env_override(tmp_path, monkeypatch):
    """``PLANNER_MODEL_CONFIG_PATH`` redirects ``default_config_path()``
    so the GUI smoke harness's PUT /api/model-config does not write
    the user's real OS app-data store."""

    from planner.model_config import default_config_path

    target = tmp_path / "model_config_override.json"
    monkeypatch.setenv("PLANNER_MODEL_CONFIG_PATH", str(target))
    assert default_config_path() == target.expanduser()


def test_resolve_out_dir_dev_accepts_user_path(tmp_path):
    repo = tmp_path / "repo"
    target = tmp_path / "scratch" / "run1"
    assert resolve_out_dir("development", target, repo) == target.resolve()


def test_resolve_out_dir_prod_rejects_repo_path(tmp_path):
    repo = tmp_path / "repo"
    forbidden = repo / "runs" / "production" / "x"
    with pytest.raises(EnvironmentBoundaryError) as exc_info:
        resolve_out_dir("production", forbidden, repo)
    assert "inside the project repository" in str(exc_info.value)


def test_resolve_out_dir_prod_accepts_outside_path(tmp_path):
    repo = tmp_path / "repo"
    ok = tmp_path / "external" / "run1"
    assert resolve_out_dir("production", ok, repo) == ok.resolve()


# --- RunService lifecycle --------------------------------------------


def _wait_for_status(registry: RunRegistry, run_id: str, timeout: float = 15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        rec = registry.get(run_id)
        if rec and rec.status in ("done", "failed"):
            return rec
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish in {timeout}s")


def _make_sample_script(repo: Path) -> Path:
    (repo / "data" / "development" / "input_scripts").mkdir(parents=True)
    script = repo / "data" / "development" / "input_scripts" / "EP01.txt"
    script.write_text(
        "EP01 — Test\n\n场 1 内 咖啡馆 日\n林夏坐下。\n",
        encoding="utf-8",
    )
    return script


def _make_repo(tmp_path: Path, *, env: str = "development") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config").mkdir()
    cfg = {
        "env": env,
        "allow_overwrite_runs": env != "production",
        "executor_default_status": "pending_manual_approval"
        if env == "production"
        else "pending",
        "submit_paid_jobs": False,
        "log_level": "DEBUG" if env == "development" else "INFO",
        "executor_dry_run": True,
        "data_root": f"data/{env}",
        "assets_root": f"assets/{env}",
        "runs_root": f"runs/{env}",
        "logs_root": f"logs/{env}",
        "schema_strict": env == "production",
        "planner_provider": "deterministic",
        "allow_provider_fallback": env != "production",
    }
    (repo / "config" / f"{env}.json").write_text(json.dumps(cfg), encoding="utf-8")
    return repo


def test_run_service_writes_artifacts(tmp_path):
    repo = _make_repo(tmp_path)
    script = _make_sample_script(repo)
    out_dir = repo / "runs" / "development" / "svc-test"

    registry = RunRegistry()
    service = RunService(registry)
    run_id, resolved = service.start_run(
        env="development",
        script_path=script,
        user_out_dir=out_dir,
        config_path=None,
        force=False,
        repo_root=repo,
    )
    assert resolved == out_dir

    rec = _wait_for_status(registry, run_id)
    assert rec.status == "done"
    assert rec.counts["shots"] >= 1

    # All 10 artifacts on disk.
    for name in (
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
    ):
        assert (out_dir / f"{name}.json").exists(), f"missing {name}"


def test_run_service_force_flag_dev(tmp_path):
    repo = _make_repo(tmp_path)
    script = _make_sample_script(repo)
    out_dir = repo / "runs" / "development" / "force-test"
    out_dir.mkdir(parents=True)

    registry = RunRegistry()
    service = RunService(registry)
    run_id, _ = service.start_run(
        env="development",
        script_path=script,
        user_out_dir=out_dir,
        config_path=None,
        force=True,
        repo_root=repo,
    )
    rec = _wait_for_status(registry, run_id)
    assert rec.status == "done"


def test_run_service_force_flag_prod_rejected(tmp_path):
    repo = _make_repo(tmp_path, env="production")
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
    script = _make_sample_script(repo)

    registry = RunRegistry()
    service = RunService(registry)
    with pytest.raises(EnvironmentBoundaryError) as exc_info:
        service.start_run(
            env="production",
            script_path=script,
            user_out_dir=None,
            config_path=None,
            force=True,
            repo_root=repo,
        )
    assert "Refusing --force in production" in str(exc_info.value)


def test_run_service_missing_script(tmp_path):
    repo = _make_repo(tmp_path)
    registry = RunRegistry()
    service = RunService(registry)
    with pytest.raises(EnvironmentBoundaryError) as exc_info:
        service.start_run(
            env="development",
            script_path=repo / "data" / "missing.txt",
            user_out_dir=None,
            config_path=None,
            force=False,
            repo_root=repo,
        )
    assert "Script not found" in str(exc_info.value)


def test_run_service_production_default_outside_repo(tmp_path):
    repo = _make_repo(tmp_path, env="production")
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
    script = _make_sample_script(repo)

    registry = RunRegistry()
    service = RunService(registry)
    run_id, resolved = service.start_run(
        env="production",
        script_path=script,
        user_out_dir=None,
        config_path=None,
        force=False,
        repo_root=repo,
    )
    assert not is_inside_repo(resolved, repo)
    _wait_for_status(registry, run_id)


def test_run_service_failure_marks_registry_and_cleans_empty_dir(tmp_path, monkeypatch):
    """When the pipeline raises, the registry flips to ``failed`` and
    an empty run directory is removed."""

    repo = _make_repo(tmp_path)
    script = _make_sample_script(repo)
    out_dir = repo / "runs" / "development" / "fail-test"

    registry = RunRegistry()
    service = RunService(registry)

    # Monkeypatch the pipeline.run to raise before any I/O happens.
    from planner.web import run_service as svc_mod

    def boom(**kwargs):
        raise EnvironmentBoundaryError("simulated failure for test")

    monkeypatch.setattr(svc_mod, "pipeline_run", boom)

    run_id, resolved = service.start_run(
        env="development",
        script_path=script,
        user_out_dir=out_dir,
        config_path=None,
        force=False,
        repo_root=repo,
    )
    rec = _wait_for_status(registry, run_id)
    assert rec.status == "failed"
    assert rec.error_type == "EnvironmentBoundaryError"
    assert "simulated failure" in (rec.error_message or "")
    # The pipeline raised before mkdir, so nothing should be on disk.
    assert not resolved.exists() or not any(resolved.iterdir())


def test_run_service_thread_is_not_daemon(tmp_path):
    """Background threads must not be daemon so the server can wait
    for in-flight runs on shutdown."""

    repo = _make_repo(tmp_path)
    script = _make_sample_script(repo)
    out_dir = repo / "runs" / "development" / "thread-test"

    registry = RunRegistry()
    service = RunService(registry)
    service.start_run(
        env="development",
        script_path=script,
        user_out_dir=out_dir,
        config_path=None,
        force=False,
        repo_root=repo,
    )
    # Find the thread by name and verify daemon flag.
    deadline = time.time() + 5
    found = None
    while time.time() < deadline:
        for t in threading.enumerate():
            if t.name.startswith("planner-run-"):
                found = t
                break
        if found:
            break
        time.sleep(0.05)
    assert found is not None, "background run thread did not start"
    assert found.daemon is False
    _wait_for_status(registry, out_dir.name)


# --- detect_repo_root -------------------------------------------------


def test_detect_repo_root_finds_pyproject(tmp_path):
    repo = tmp_path / "has_pyproject"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    assert detect_repo_root(repo) == repo


def test_detect_repo_root_finds_config_dir(tmp_path):
    repo = tmp_path / "has_config"
    repo.mkdir()
    (repo / "config").mkdir()
    (repo / "config" / "development.json").write_text("{}", encoding="utf-8")
    assert detect_repo_root(repo) == repo


def test_detect_repo_root_returns_none_when_missing(tmp_path):
    bare = tmp_path / "no_markers"
    bare.mkdir()
    assert detect_repo_root(bare) is None


# --- P3 polish coverage ----------------------------------------------


def test_generate_run_id_uniqueness_under_burst():
    """Two ``generate_run_id()`` calls in the same microsecond must
    still produce distinct ids (P3 polish: avoid collision when two
    dev POSTs land in the same second)."""

    ids = {generate_run_id() for _ in range(200)}
    assert len(ids) == 200, "generate_run_id() produced collisions under burst"


def test_generate_run_id_format():
    """Run id has second + microsecond + 4-hex-char suffix."""

    rid = generate_run_id()
    parts = rid.split("-")
    # YYYYMMDD-HHMMSS-microsecond-xxxx
    assert len(parts) == 4
    assert len(parts[0]) == 8
    assert len(parts[1]) == 6
    assert len(parts[2]) == 6
    assert len(parts[3]) == 4
    assert int(parts[0])
    assert int(parts[1])
    assert int(parts[2])
    int(parts[3], 16)  # must be valid hex


def test_run_service_facade_methods():
    """P3 polish: routes use ``service.get_run`` and ``service.list_runs``
    facade methods, not ``service._registry`` directly."""

    registry = RunRegistry()
    service = RunService(registry)
    # Facade returns None for unknown run.
    assert service.get_run("no-such") is None
    # Empty list when no runs registered.
    assert service.list_runs() == []