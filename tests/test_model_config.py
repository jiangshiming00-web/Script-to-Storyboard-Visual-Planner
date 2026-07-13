"""Tests for ``planner.model_config`` — v1.0 typed model configuration.

Pins the contracts the v1.0 release plan §4 requires:

- Default ``planner_provider`` is ``deterministic`` (zero-config works).
- ``enable_real_model_calls`` defaults to ``False`` (no paid calls
  without an explicit operator action).
- API keys are NEVER persisted — only the env var name.
- ``run_summary.json``-friendly audit fields (``api_key_env``) come
  out of ``ProviderRuntimeSettings`` but never the key value.
- ``default_config_path`` lives in OS app-data so the file survives
  reinstalls and stays out of the repo.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from planner.model_config import (
    APP_DIR_NAME,
    ModelProviderConfig,
    OpenAICompatibleConfig,
    ProviderRuntimeSettings,
    default_config_path,
    load_model_config,
    resolve_runtime_settings,
    save_model_config,
)


# --- defaults ------------------------------------------------------------


def test_default_provider_is_deterministic() -> None:
    cfg = ModelProviderConfig()
    assert cfg.planner_provider == "deterministic"
    # Safety toggle defaults to OFF.
    assert cfg.enable_real_model_calls is False
    assert cfg.allow_provider_fallback is False


def test_default_per_provider_sections_have_sane_urls() -> None:
    cfg = ModelProviderConfig()
    assert cfg.openai.base_url.startswith("https://")
    assert cfg.openai.api_key_env == "OPENAI_API_KEY"
    assert cfg.anthropic.api_key_env == "ANTHROPIC_API_KEY"
    assert cfg.openai_compatible.api_key_env == "OPENAI_COMPATIBLE_API_KEY"
    # All three sections must declare a non-empty model name.
    for section in (cfg.openai, cfg.anthropic, cfg.openai_compatible):
        assert section.model
        assert section.timeout_seconds > 0
        assert 0.0 <= section.temperature <= 2.0
        assert section.max_tokens >= 1


def test_provider_name_literal_is_locked() -> None:
    """The v1.0 registry only supports four names; the config layer
    mirrors that so operators get a clean validation error rather than
    a runtime ``Unknown planner_provider``."""

    with pytest.raises(ValidationError):
        ModelProviderConfig(planner_provider="flowith")  # type: ignore[arg-type]


# --- validators ----------------------------------------------------------


def test_api_key_env_must_be_upper_snake() -> None:
    with pytest.raises(ValidationError):
        OpenAICompatibleConfig(api_key_env="lowercase-key")
    with pytest.raises(ValidationError):
        OpenAICompatibleConfig(api_key_env="1_leading_digit")
    # Valid forms succeed.
    OpenAICompatibleConfig(api_key_env="PLANNER_OPENAI_API_KEY")
    OpenAICompatibleConfig(api_key_env="X")


def test_base_url_must_be_http() -> None:
    with pytest.raises(ValidationError):
        OpenAICompatibleConfig(base_url="ftp://example.com/v1")
    with pytest.raises(ValidationError):
        OpenAICompatibleConfig(base_url="example.com")
    # Trailing slashes are stripped (cosmetic; tests behaviour).
    cfg = OpenAICompatibleConfig(base_url="https://x.example.com/v1/")
    assert cfg.base_url == "https://x.example.com/v1"


def test_extra_fields_are_rejected() -> None:
    """Operators sometimes paste a full JSON blob; we MUST NOT silently
    keep unknown fields (they would never be read back)."""

    with pytest.raises(ValidationError):
        ModelProviderConfig.model_validate(
            {"planner_provider": "openai", "secret_admin_token": "x"}
        )


def test_temperature_bounds() -> None:
    with pytest.raises(ValidationError):
        OpenAICompatibleConfig(temperature=-0.1)
    with pytest.raises(ValidationError):
        OpenAICompatibleConfig(temperature=3.0)
    OpenAICompatibleConfig(temperature=0.0)
    OpenAICompatibleConfig(temperature=2.0)


# --- file round-trip -----------------------------------------------------


def test_load_returns_defaults_when_missing(tmp_path: Path) -> None:
    """A fresh install with no config file MUST return defaults, not
    raise — otherwise the CLI/GUI fails to start on a teammate's
    machine."""

    cfg = load_model_config(path=tmp_path / "nope.json")
    assert cfg.planner_provider == "deterministic"


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    cfg = ModelProviderConfig(
        planner_provider="openai_compatible",
        enable_real_model_calls=False,
        allow_provider_fallback=True,
        openai_compatible=OpenAICompatibleConfig(
            base_url="http://localhost:9999/v1",
            model="qwen2.5-7b",
            api_key_env="MY_GATEWAY_KEY",
            timeout_seconds=12.0,
            temperature=0.3,
            max_tokens=512,
        ),
    )
    written = save_model_config(cfg, path=target)
    assert written == target
    payload = json.loads(target.read_text(encoding="utf-8"))
    # The file MUST NOT carry a literal API key value anywhere.
    text = target.read_text(encoding="utf-8")
    assert "MY_GATEWAY_KEY" in text  # env var name is fine
    # Reload must produce an equal config.
    reloaded = load_model_config(path=target)
    assert reloaded == cfg


def test_save_refuses_literal_api_key(tmp_path: Path) -> None:
    """Defense-in-depth: even if a future regression relaxes the schema,
    ``save_model_config`` must still refuse to write a literal key."""

    target = tmp_path / "config.json"
    bad = ModelProviderConfig.model_construct()  # bypass validators
    bad.planner_provider = "openai"
    bad.enable_real_model_calls = True
    bad.allow_provider_fallback = False
    bad.openai = OpenAICompatibleConfig.model_construct(
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        # NB: bypass schema validators; bypass the upper-snake check.
        api_key_env="sk-abcdefghijklmnopqrstuvwxyz123456",
        timeout_seconds=30.0,
        temperature=0.7,
        max_tokens=2048,
    )
    with pytest.raises(ValueError, match="literal API key"):
        save_model_config(bad, path=target)
    assert not target.exists()


def test_save_is_atomic(tmp_path: Path) -> None:
    """``save_model_config`` writes via ``tmp + replace`` so a partial
    file never lands in the app-data directory."""

    target = tmp_path / "config.json"
    cfg = ModelProviderConfig()
    save_model_config(cfg, path=target)
    # The tmp sibling should be gone after the rename.
    siblings = list(tmp_path.iterdir())
    assert siblings == [target]
    # And the file is well-formed JSON.
    json.loads(target.read_text(encoding="utf-8"))


def test_load_rejects_malformed_json(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    target.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="Malformed"):
        load_model_config(path=target)


def test_load_rejects_invalid_shape(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    # timeout_seconds must be a number; passing a non-numeric string is
    # an unambiguous shape violation (truthy bool-string is not — pydantic
    # v2 coerces common truthy strings).
    target.write_text(
        json.dumps(
            {
                "planner_provider": "openai",
                "openai": {"timeout_seconds": "not-a-number"},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Invalid"):
        load_model_config(path=target)


# --- default path --------------------------------------------------------


def test_default_config_path_uses_app_dir(monkeypatch) -> None:
    """The default path MUST live under OS app-data and contain the
    app dir name so a teammate's GUI install does not pollute the
    repo. We don't pin to an absolute path (CI runs on different OSes)
    but the suffix is constant."""

    p = default_config_path()
    parts = p.parts
    assert APP_DIR_NAME in parts
    assert p.name == "config.json"


def test_default_config_path_honours_xdg(monkeypatch, tmp_path: Path) -> None:
    import sys as _sys

    monkeypatch.setattr(_sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    p = default_config_path()
    assert str(tmp_path) in str(p)
    assert APP_DIR_NAME in p.parts


def test_default_config_path_linux_falls_back_to_local_share(
    monkeypatch, tmp_path: Path
) -> None:
    import sys as _sys

    monkeypatch.setattr(_sys, "platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    p = default_config_path()
    # Path is ~/local/share/ShortDramaPlanner/config.json when
    # XDG_DATA_HOME is unset.
    assert str(p).startswith(str(tmp_path))
    assert p.parts[-4:] == (".local", "share", APP_DIR_NAME, "config.json")


def test_default_config_path_windows_honours_appdata(
    monkeypatch, tmp_path: Path
) -> None:
    import sys as _sys

    monkeypatch.setattr(_sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    p = default_config_path()
    assert str(tmp_path) in str(p)
    assert APP_DIR_NAME in p.parts


# --- runtime settings ----------------------------------------------------


def test_resolve_runtime_settings_for_each_provider() -> None:
    cfg = ModelProviderConfig(
        openai_compatible=OpenAICompatibleConfig(
            base_url="http://localhost:11434/v1",
            model="llama3.1",
            api_key_env="OLLAMA_KEY",
            timeout_seconds=45.0,
            temperature=0.2,
            max_tokens=1024,
        ),
    )
    settings = resolve_runtime_settings(cfg, provider_name="openai_compatible")
    assert settings.name == "openai_compatible"
    assert settings.base_url == "http://localhost:11434/v1"
    assert settings.model == "llama3.1"
    assert settings.api_key_env == "OLLAMA_KEY"
    assert settings.timeout_seconds == 45.0
    assert settings.temperature == 0.2
    assert settings.max_tokens == 1024
    # The global safety toggle propagates into per-provider settings.
    assert settings.enable_real_model_calls is False


def test_resolve_runtime_settings_defaults_to_planner_provider() -> None:
    cfg = ModelProviderConfig(planner_provider="anthropic")
    settings = resolve_runtime_settings(cfg)
    assert settings.name == "anthropic"
    assert settings.api_key_env == "ANTHROPIC_API_KEY"


def test_resolve_runtime_settings_rejects_deterministic() -> None:
    cfg = ModelProviderConfig(planner_provider="deterministic")
    with pytest.raises(ValueError, match="Cannot resolve runtime settings"):
        resolve_runtime_settings(cfg)


def test_resolve_runtime_settings_rejects_unknown() -> None:
    cfg = ModelProviderConfig(planner_provider="openai")
    with pytest.raises(ValueError, match="Cannot resolve runtime settings"):
        resolve_runtime_settings(cfg, provider_name="flowith")


def test_provider_runtime_settings_api_key_reads_env(monkeypatch) -> None:
    """The runtime settings object exposes ``api_key()`` which reads
    from the env. The KEY VALUE must never appear on the object itself
    (so serializing it for ``run_summary.json`` stays safe)."""

    monkeypatch.setenv("MY_TEST_KEY", "super-secret-xyz")
    settings = ProviderRuntimeSettings(
        name="openai_compatible",
        base_url="https://example.com/v1",
        model="x",
        api_key_env="MY_TEST_KEY",
        timeout_seconds=10.0,
        temperature=0.5,
        max_tokens=100,
        enable_real_model_calls=False,
    )
    assert settings.api_key() == "super-secret-xyz"
    # And the secret is NOT a field on the model itself.
    dumped = settings.model_dump()
    assert "super-secret-xyz" not in json.dumps(dumped)
    assert dumped["api_key_env"] == "MY_TEST_KEY"


def test_provider_runtime_settings_api_key_treats_empty_as_missing(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MY_TEST_KEY", "")
    settings = ProviderRuntimeSettings(
        name="openai",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        api_key_env="MY_TEST_KEY",
        timeout_seconds=10.0,
        temperature=0.5,
        max_tokens=100,
        enable_real_model_calls=False,
    )
    assert settings.api_key() is None


def test_provider_runtime_settings_api_key_missing_env() -> None:
    # Use an env var that we know is unset; if a CI shell sets it for
    # some reason the test still passes by explicitly deleting.
    os.environ.pop("PLANNER_NONEXISTENT_KEY_FOR_TEST", None)
    settings = ProviderRuntimeSettings(
        name="openai",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        api_key_env="PLANNER_NONEXISTENT_KEY_FOR_TEST",
        timeout_seconds=10.0,
        temperature=0.5,
        max_tokens=100,
        enable_real_model_calls=False,
    )
    assert settings.api_key() is None