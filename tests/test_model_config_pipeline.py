"""End-to-end tests for the v1.0 P1-1 model-config -> provider ->
pipeline integration.

These pin the contract the v1.0 review plan §P1-1 requires:

- ``planner run --model-config cfg.json`` loads the config, steers the
  provider choice, and injects :class:`ProviderRuntimeSettings` into
  the provider instance so it actually talks to the configured
  ``base_url`` with the configured ``model`` and ``api_key_env``.
- ``run_summary.json`` records ``provider_runtime`` audit fields
  (model / base_url / api_key_env / enable_real_model_calls) and
  NEVER the API key value.
- Production fail-closes when ``enable_real_model_calls=False`` or
  the API key env is unset, and leaves no empty ``out_dir`` residue.

All HTTP calls are routed through a monkeypatched
``openai_compatible_adapter.http_post`` - no real socket is opened.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from planner.env import load_config
from planner.exceptions import ProviderUnavailableError
from planner.model_config import (
    ModelProviderConfig,
    OpenAICompatibleConfig,
    default_config_path,
    load_model_config,
    save_model_config,
)
from planner.pipeline import run as run_pipeline


# ---- helpers -----------------------------------------------------------


def _write_model_config(
    tmp_path: Path,
    *,
    planner_provider: str = "openai_compatible",
    enable_real_model_calls: bool = True,
    base_url: str = "http://localhost:9999/v1",
    model: str = "test-model",
    api_key_env: str = "PLANNER_TEST_OAI_KEY",
) -> Path:
    """Write a model config JSON to tmp_path and return the path."""

    cfg = ModelProviderConfig(
        planner_provider=planner_provider,
        enable_real_model_calls=enable_real_model_calls,
        openai_compatible=OpenAICompatibleConfig(
            base_url=base_url,
            model=model,
            api_key_env=api_key_env,
        ),
    )
    path = tmp_path / "model_config.json"
    save_model_config(cfg, path=path)
    return path


def _fake_http_factory(captured: List[Dict[str, Any]]):
    """Return a fake ``http_post`` that records each call into
    ``captured`` and returns a schema-valid Chat-Completions envelope
    whose ``content`` matches the step implied by the user prompt."""

    def _fake_post(
        url: str,
        headers: Dict[str, str],
        body: bytes,
        timeout: float,
    ) -> Tuple[int, bytes]:
        body_dict = json.loads(body.decode("utf-8"))
        user_msg = ""
        for m in body_dict.get("messages", []):
            if m.get("role") == "user":
                user_msg = m.get("content", "")
                break
        # Pick the step payload based on the prompt's leading verb.
        if "characters" in user_msg:
            payload: Dict[str, Any] = {"characters": [], "locations": [], "props": []}
        elif "story beats" in user_msg:
            payload = {"beats": []}
        elif "shot list" in user_msg:
            payload = {"shots": []}
        elif "image-generation" in user_msg:
            payload = {"image_prompts": []}
        elif "video-generation" in user_msg:
            payload = {"video_prompts": []}
        else:  # pragma: no cover - defensive
            payload = {}
        captured.append(
            {
                "url": url,
                "headers": dict(headers),
                "body": body_dict,
                "timeout": timeout,
            }
        )
        envelope = {
            "choices": [
                {"message": {"content": json.dumps(payload)}}
            ]
        }
        return 200, json.dumps(envelope).encode("utf-8")

    return _fake_post


def _dev_config(repo_root: Path) -> Path:
    """Write a minimal development.json under repo_root/config/."""

    cfg_dir = repo_root / "config"
    cfg_dir.mkdir(exist_ok=True)
    path = cfg_dir / "development.json"
    path.write_text(
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
    return path


def _prod_config(repo_root: Path) -> Path:
    cfg_dir = repo_root / "config"
    cfg_dir.mkdir(exist_ok=True)
    path = cfg_dir / "production.json"
    path.write_text(
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
    return path


def _sample_script(repo_root: Path) -> Path:
    scripts = repo_root / "data" / "development" / "input_scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    path = scripts / "EP01.txt"
    path.write_text(
        "EP01 - Test\n\n场 1 内景 咖啡馆 - 日\n林夏走进咖啡馆。\n",
        encoding="utf-8",
    )
    return path


# ---- P1-1: settings injection -----------------------------------------


def test_pipeline_injects_model_config_settings_into_provider(
    tmp_path: Path, monkeypatch
) -> None:
    """``pipeline.run(model_config=...)`` MUST resolve the provider's
    runtime settings from the config and inject them into the
    provider instance. The fake HTTP layer records the request so we
    can assert the provider actually used the configured
    ``base_url`` / ``model`` / ``api_key_env``.
    """

    from planner.providers import openai_compatible_adapter as oca_mod

    monkeypatch.setenv("PLANNER_TEST_OAI_KEY", "sk-test-1234567890abcdef")
    repo = tmp_path / "repo"
    repo.mkdir()
    _dev_config(repo)
    script = _sample_script(repo)

    model_cfg_path = _write_model_config(tmp_path)
    model_config = load_model_config(model_cfg_path)

    captured: List[Dict[str, Any]] = []
    monkeypatch.setattr(oca_mod, "http_post", _fake_http_factory(captured))

    config = load_config(env="development", project_root=repo)
    # model_config's planner_provider (openai_compatible) overrides the
    # env config's deterministic default - mirroring what the CLI does.
    assert model_config.planner_provider == "openai_compatible"
    object.__setattr__(config, "planner_provider", model_config.planner_provider)

    out_dir = tmp_path / "out" / "EP01"
    result = run_pipeline(
        script_path=script, out_dir=out_dir, config=config, model_config=model_config,
    )

    # The provider issued 5 HTTP calls (one per planning method).
    assert len(captured) == 5, f"expected 5 HTTP calls, got {len(captured)}"

    # Every call hit the configured base_url.
    for call in captured:
        assert call["url"] == "http://localhost:9999/v1/chat/completions", call["url"]
        assert call["body"]["model"] == "test-model", call["body"]
        assert call["headers"]["Authorization"] == "Bearer sk-test-1234567890abcdef"
        assert call["timeout"] == 30.0

    # run_summary.json carries the provider_runtime audit fields.
    summary = json.loads((out_dir / "run_summary.json").read_text(encoding="utf-8"))
    assert summary["requested_provider"] == "openai_compatible"
    assert summary["effective_provider"] == "openai_compatible"
    assert summary["fallback_used"] is False
    rt = summary["provider_runtime"]
    assert rt is not None
    assert rt["model"] == "test-model"
    assert rt["base_url"] == "http://localhost:9999/v1"
    assert rt["api_key_env"] == "PLANNER_TEST_OAI_KEY"
    assert rt["enable_real_model_calls"] is True

    # The API key value MUST NEVER appear in run_summary.json.
    summary_text = (out_dir / "run_summary.json").read_text(encoding="utf-8")
    assert "sk-test-1234567890abcdef" not in summary_text

    # Artifacts were written by the (fake) LLM, not deterministic.
    assert (out_dir / "character_bible.json").exists()
    assert (out_dir / "shot_list.json").exists()
    assert result.effective_provider == "openai_compatible"


def test_pipeline_provider_runtime_is_none_for_deterministic(
    tmp_path: Path,
) -> None:
    """A deterministic run (no model_config) MUST write
    ``provider_runtime: null`` so reviewers can distinguish clean
    deterministic runs from configured-but-fell-back runs."""

    repo = tmp_path / "repo"
    repo.mkdir()
    _dev_config(repo)
    script = _sample_script(repo)

    config = load_config(env="development", project_root=repo)
    out_dir = tmp_path / "out" / "EP01"
    run_pipeline(script_path=script, out_dir=out_dir, config=config)

    summary = json.loads((out_dir / "run_summary.json").read_text(encoding="utf-8"))
    assert summary["provider_runtime"] is None
    assert summary["requested_provider"] == "deterministic"


# ---- P1-1: production fail-closed --------------------------------------


def test_production_fail_closed_when_real_calls_disabled(
    tmp_path: Path, monkeypatch
) -> None:
    """When ``enable_real_model_calls=False`` the openai_compatible
    provider reports unhealthy. Production (``allow_provider_fallback
    =False``) MUST raise :class:`ProviderUnavailableError` and leave
    NO empty ``out_dir`` residue (fail-closed leaves no residue).
    """

    from planner.providers import openai_compatible_adapter as oca_mod

    monkeypatch.setenv("PLANNER_TEST_OAI_KEY", "sk-test-1234567890abcdef")
    repo = tmp_path / "repo"
    repo.mkdir()
    _prod_config(repo)
    script = _sample_script(repo)

    model_cfg_path = _write_model_config(
        tmp_path, enable_real_model_calls=False,
    )
    model_config = load_model_config(model_cfg_path)

    # http_post must NEVER be called in this path.
    def _explode(*a, **kw):
        raise AssertionError("health_check must not call http_post")
    monkeypatch.setattr(oca_mod, "http_post", _explode)

    config = load_config(env="production", project_root=repo)
    object.__setattr__(config, "planner_provider", model_config.planner_provider)

    out_dir = tmp_path / "prod_out" / "EP01"
    assert not out_dir.exists()
    with pytest.raises(ProviderUnavailableError):
        run_pipeline(
            script_path=script, out_dir=out_dir, config=config,
            model_config=model_config,
        )
    # No residue.
    assert not out_dir.exists(), (
        f"fail-closed must leave no out_dir residue, but {out_dir} exists"
    )


def test_production_fail_closed_when_api_key_missing(
    tmp_path: Path, monkeypatch
) -> None:
    """``enable_real_model_calls=True`` but the API key env var is
    unset -> provider unhealthy -> production fail-closes with no
    residue."""

    from planner.providers import openai_compatible_adapter as oca_mod

    monkeypatch.delenv("PLANNER_TEST_OAI_KEY", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    _prod_config(repo)
    script = _sample_script(repo)

    model_cfg_path = _write_model_config(
        tmp_path, enable_real_model_calls=True,
    )
    model_config = load_model_config(model_cfg_path)

    monkeypatch.setattr(
        oca_mod, "http_post",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("health_check must not call http_post")
        ),
    )

    config = load_config(env="production", project_root=repo)
    object.__setattr__(config, "planner_provider", model_config.planner_provider)

    out_dir = tmp_path / "prod_out" / "EP01"
    with pytest.raises(ProviderUnavailableError):
        run_pipeline(
            script_path=script, out_dir=out_dir, config=config,
            model_config=model_config,
        )
    assert not out_dir.exists()


# ---- P1-1: development fallback records provider_runtime audit --------


def test_development_fallback_records_provider_runtime_audit(
    tmp_path: Path, monkeypatch
) -> None:
    """When dev ``allow_provider_fallback=True`` and the requested
    openai_compatible provider is unhealthy (real calls off), the
    pipeline falls back to deterministic BUT ``provider_runtime``
    still records what was *requested* so the reviewer sees the
    misconfiguration."""

    from planner.providers import openai_compatible_adapter as oca_mod

    monkeypatch.setenv("PLANNER_TEST_OAI_KEY", "sk-test-1234567890abcdef")
    repo = tmp_path / "repo"
    repo.mkdir()
    _dev_config(repo)
    script = _sample_script(repo)

    model_cfg_path = _write_model_config(
        tmp_path, enable_real_model_calls=False,
    )
    model_config = load_model_config(model_cfg_path)

    monkeypatch.setattr(
        oca_mod, "http_post",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("fallback path must not call http_post")
        ),
    )

    config = load_config(env="development", project_root=repo)
    object.__setattr__(config, "planner_provider", model_config.planner_provider)

    out_dir = tmp_path / "out" / "EP01"
    result = run_pipeline(
        script_path=script, out_dir=out_dir, config=config,
        model_config=model_config,
    )

    assert result.fallback_used is True
    assert result.effective_provider == "deterministic"
    assert result.requested_provider == "openai_compatible"

    summary = json.loads((out_dir / "run_summary.json").read_text(encoding="utf-8"))
    # provider_runtime records the REQUESTED provider's settings even
    # though the run fell back to deterministic.
    assert summary["provider_runtime"] is not None
    assert summary["provider_runtime"]["model"] == "test-model"
    assert summary["provider_runtime"]["enable_real_model_calls"] is False
    assert summary["fallback_used"] is True


# ---- P1-1: CLI --model-config end-to-end ------------------------------


def test_cli_run_with_model_config_end_to_end(
    tmp_path: Path, monkeypatch
) -> None:
    """``planner run --model-config cfg.json`` loads the config,
    steers the provider, and writes artifacts. The CLI is the
    user-facing path so we exercise it via Click's CliRunner."""

    pytest.importorskip("click")
    from click.testing import CliRunner

    from planner.cli import run_cmd
    from planner.providers import openai_compatible_adapter as oca_mod

    monkeypatch.setenv("PLANNER_TEST_OAI_KEY", "sk-test-1234567890abcdef")
    repo = tmp_path / "repo"
    repo.mkdir()
    _dev_config(repo)
    script = _sample_script(repo)

    model_cfg_path = _write_model_config(tmp_path)

    captured: List[Dict[str, Any]] = []
    monkeypatch.setattr(oca_mod, "http_post", _fake_http_factory(captured))

    runner = CliRunner()
    out_dir = tmp_path / "cli_out" / "EP01"
    result = runner.invoke(
        run_cmd,
        [
            "--env", "development",
            "--script", str(script),
            "--out", str(out_dir),
            "--config", str(repo / "config" / "development.json"),
            "--model-config", str(model_cfg_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(captured) == 5
    # Artifacts written.
    assert (out_dir / "run_summary.json").exists()
    assert (out_dir / "character_bible.json").exists()
    summary = json.loads((out_dir / "run_summary.json").read_text(encoding="utf-8"))
    assert summary["requested_provider"] == "openai_compatible"
    assert summary["provider_runtime"]["model"] == "test-model"


def test_cli_run_without_model_config_uses_deterministic(
    tmp_path: Path, monkeypatch
) -> None:
    """Without ``--model-config`` (and no OS app-data file), the CLI
    MUST fall back to the env config's deterministic provider - no
    HTTP calls, no behaviour change vs pre-v1.0."""

    pytest.importorskip("click")
    from click.testing import CliRunner

    from planner.cli import run_cmd
    from planner.providers import openai_compatible_adapter as oca_mod

    # Ensure default_config_path() does not pick up a real config.
    import planner.model_config as _mc

    monkeypatch.setattr(
        _mc, "default_config_path",
        lambda: tmp_path / "nonexistent_model_config.json",
    )

    repo = tmp_path / "repo"
    repo.mkdir()
    _dev_config(repo)
    script = _sample_script(repo)

    def _explode(*a, **kw):
        raise AssertionError("deterministic path must not call http_post")
    monkeypatch.setattr(oca_mod, "http_post", _explode)

    runner = CliRunner()
    out_dir = tmp_path / "cli_out" / "EP01"
    result = runner.invoke(
        run_cmd,
        [
            "--env", "development",
            "--script", str(script),
            "--out", str(out_dir),
            "--config", str(repo / "config" / "development.json"),
        ],
    )
    assert result.exit_code == 0, result.output
    summary = json.loads((out_dir / "run_summary.json").read_text(encoding="utf-8"))
    assert summary["requested_provider"] == "deterministic"
    assert summary["provider_runtime"] is None


# ---- P1-1: run_summary never leaks API key ----------------------------


def test_provider_output_error_never_leaks_api_key_value(
    tmp_path: Path, monkeypatch
) -> None:
    """When the upstream returns HTTP 500 with the bearer token echoed
    in the body, the resulting :class:`ProviderOutputError` message
    MUST NOT contain the key value. The provider's ``_redact_secrets``
    sanitizes the payload excerpt before it reaches the operator.

    Note: a planning-method HTTP failure raises ``ProviderOutputError``
    AFTER ``_select_provider`` has selected the provider. Dev fallback
    only applies at health-check time, so this error surfaces to the
    caller (correct - we never silently swallow provider output
    errors). The contract being pinned here is "no key leak in the
    error message", not "fallback on HTTP 500".
    """

    from planner.exceptions import ProviderOutputError
    from planner.providers import openai_compatible_adapter as oca_mod

    monkeypatch.setenv("PLANNER_TEST_OAI_KEY", "sk-test-1234567890abcdef")
    repo = tmp_path / "repo"
    repo.mkdir()
    _dev_config(repo)
    script = _sample_script(repo)

    model_cfg_path = _write_model_config(tmp_path)
    model_config = load_model_config(model_cfg_path)

    # Fake server returns an HTTP 500 with the bearer token echoed in
    # the body - a realistic upstream misbehaviour.
    def _leaky_post(url, headers, body, timeout):
        return 500, b'{"error":"upstream","auth":"Bearer sk-test-1234567890abcdef"}'

    monkeypatch.setattr(oca_mod, "http_post", _leaky_post)

    config = load_config(env="development", project_root=repo)
    object.__setattr__(config, "planner_provider", model_config.planner_provider)

    out_dir = tmp_path / "out" / "EP01"
    with pytest.raises(ProviderOutputError) as excinfo:
        run_pipeline(
            script_path=script, out_dir=out_dir, config=config,
            model_config=model_config,
        )
    # The error message MUST NOT contain the API key value - it must
    # have been redacted to <redacted> by _safe_excerpt.
    assert "sk-test-1234567890abcdef" not in str(excinfo.value), (
        f"API key value leaked into ProviderOutputError message: {excinfo.value}"
    )
    assert "<redacted>" in str(excinfo.value)