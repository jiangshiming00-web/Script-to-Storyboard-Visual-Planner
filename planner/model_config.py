"""Structured model configuration for the v1.0 planner.

Background
----------

The v1.0 release plan (``docs/PROMA_V1_RELEASE_PLAN.md`` §4) requires
a typed model configuration layer so provider settings — model name,
base URL, API key env name, timeout, temperature, max tokens — don't
sprout across ``env.py``, GUI forms, ``run_summary.json`` and ad-hoc
CLI flags. Every caller that wants to talk to a provider should resolve
its settings through this module.

Hard rules (red lines; see :mod:`planner.hard_boundaries` in memory):

- **API keys are NEVER persisted.** Only the env var name
  (``api_key_env``) is stored; the key itself stays in the OS
  environment. ``run_summary.json`` only records ``api_key_env`` —
  never the value.
- **Real model calls are off by default.** ``enable_real_model_calls
  = False``; flipping it requires an explicit operator action (CLI
  flag or GUI toggle) and is recorded in the audit fields.
- **Production keeps the existing fail-closed contract.** Silent
  fallback to deterministic is rejected by
  :func:`planner.env._enforce_boundaries` regardless of what's in
  this file.
- **No required SDK dependency.** The HTTP client used to talk to
  providers is opt-in; this module only describes configuration.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


#: Default app-config folder name (mirrors ``planner/web/run_service.py``
#: ``APP_DIR_NAME`` so GUI and CLI agree on the OS app-data location).
APP_DIR_NAME = "ShortDramaPlanner"

#: Subpath relative to the OS app data directory.
DEFAULT_CONFIG_RELATIVE = Path("config.json")


# --- typed settings ------------------------------------------------------


class OpenAICompatibleConfig(BaseModel):
    """Connection settings for an OpenAI-compatible endpoint.

    Used for:

    - the official ``openai`` provider,
    - the ``openai_compatible`` provider (third-party gateways,
      vLLM/Ollama proxies, internal model gateways),
    - the ``anthropic`` provider when its transport is OpenAI-shaped
      (the canonical Anthropic transport differs but a future rev
      may route through this config).
    """

    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(
        default="https://api.openai.com/v1",
        description="HTTP base URL of the chat-completions endpoint.",
    )
    model: str = Field(
        default="gpt-4o-mini",
        description="Model identifier passed to the endpoint.",
    )
    api_key_env: str = Field(
        default="OPENAI_API_KEY",
        description=(
            "Name of the environment variable that holds the API key. "
            "The KEY VALUE itself must never be persisted."
        ),
    )
    timeout_seconds: float = Field(default=30.0, ge=0.1, le=600.0)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=1, le=128_000)

    @field_validator("api_key_env")
    @classmethod
    def _env_var_name_shape(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", value or ""):
            raise ValueError(
                f"api_key_env must be an UPPER_SNAKE_CASE env var name, "
                f"got {value!r}"
            )
        return value

    @field_validator("base_url")
    @classmethod
    def _http_only(cls, value: str) -> str:
        v = (value or "").strip().rstrip("/")
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError(
                f"base_url must start with http:// or https://, got {value!r}"
            )
        return v


class ModelProviderConfig(BaseModel):
    """Top-level v1.0 model configuration persisted to disk.

    Holds the provider selection, the ``enable_real_model_calls``
    safety toggle, the production fail-closed toggle, and three
    per-provider endpoint configurations (``openai`` /
    ``anthropic`` / ``openai_compatible``).
    """

    model_config = ConfigDict(extra="forbid")

    planner_provider: Literal[
        "deterministic", "openai", "anthropic", "openai_compatible"
    ] = "deterministic"

    enable_real_model_calls: bool = Field(
        default=False,
        description=(
            "When False, all real-model providers refuse to issue HTTP "
            "requests and report healthy=False. Operators must flip this "
            "explicitly after confirming key storage and budget."
        ),
    )

    allow_provider_fallback: bool = Field(
        default=False,
        description=(
            "When True (development only), an unhealthy requested provider "
            "swaps to deterministic. Production ignores this — see "
            "planner.env._enforce_boundaries."
        ),
    )

    openai: OpenAICompatibleConfig = Field(default_factory=OpenAICompatibleConfig)
    anthropic: OpenAICompatibleConfig = Field(
        default_factory=lambda: OpenAICompatibleConfig(
            base_url="https://api.anthropic.com",
            model="claude-3-5-sonnet-latest",
            api_key_env="ANTHROPIC_API_KEY",
        )
    )
    openai_compatible: OpenAICompatibleConfig = Field(
        default_factory=lambda: OpenAICompatibleConfig(
            base_url="http://localhost:8000/v1",
            model="local-model",
            api_key_env="OPENAI_COMPATIBLE_API_KEY",
        )
    )


class ProviderRuntimeSettings(BaseModel):
    """Resolved runtime settings for a single provider invocation.

    Built by :func:`resolve_runtime_settings` from
    :class:`ModelProviderConfig`. Carries concrete values ready to feed
    into a provider's HTTP client (base URL, model, timeouts, the env
    var name where the API key lives, and the safety toggle).

    Notably, **the API key value is never present** — only the env var
    name. This makes it safe to log / serialize ``ProviderRuntimeSettings``
    for audit purposes without leaking credentials.
    """

    model_config = ConfigDict(extra="forbid")

    name: Literal["openai", "anthropic", "openai_compatible"]
    base_url: str
    model: str
    api_key_env: str
    timeout_seconds: float
    temperature: float
    max_tokens: int
    enable_real_model_calls: bool

    def api_key(self) -> Optional[str]:
        """Return the API key from the environment, or ``None``.

        Looked up via ``api_key_env`` so callers never need to thread
        secrets through their own state. Empty / whitespace-only values
        are treated as missing.
        """

        raw = os.environ.get(self.api_key_env)
        if raw is None:
            return None
        return raw.strip() or None


# --- file location helpers ------------------------------------------------


def default_config_path() -> Path:
    """OS-specific per-user config location.

    - macOS: ``~/Library/Application Support/ShortDramaPlanner/config.json``
    - Windows: ``%APPDATA%/ShortDramaPlanner/config.json`` (falls back to
      ``~/AppData/Roaming`` if ``APPDATA`` is unset)
    - Linux / other: ``$XDG_DATA_HOME/ShortDramaPlanner/config.json`` or
      ``~/.local/share/ShortDramaPlanner/config.json`` as fallback.

    Returning a path inside the OS app-data directory keeps the config
    off the repo and survives reinstalls (per v1.0 plan §4).

    Override for tests / CI: setting ``PLANNER_MODEL_CONFIG_PATH`` to an
    absolute path redirects ``save_model_config`` / ``load_model_config``
    (when called without an explicit ``path=``) to that location. The
    GUI's ``GET/PUT /api/model-config`` reads this same function, so
    setting the env var also isolates the GUI smoke harness from the
    user's real OS app-data store.
    """

    override = os.environ.get("PLANNER_MODEL_CONFIG_PATH")
    if override:
        return Path(override).expanduser()

    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / APP_DIR_NAME / DEFAULT_CONFIG_RELATIVE


# --- load / save ----------------------------------------------------------


# Heuristic to catch accidentally-pasted API key values. Not perfect but
# raises a clear error if someone tries to commit a secret in the JSON.
_LIKELY_API_KEY_RE = re.compile(
    r"^(sk-[A-Za-z0-9_\-]{16,}|sk-ant-[A-Za-z0-9_\-]{16,}|"
    r"gho_[A-Za-z0-9]{16,}|[A-Za-z0-9]{40,})$"
)


def _contains_literal_key(payload: Mapping[str, Any]) -> bool:
    """Walk ``payload`` and reject if any leaf string looks like an
    API key value. The schema already forbids literal keys, this is a
    defense-in-depth check on the ``save`` path."""

    def _walk(obj: Any) -> bool:
        if isinstance(obj, dict):
            return any(_walk(v) for v in obj.values())
        if isinstance(obj, list):
            return any(_walk(v) for v in obj)
        if isinstance(obj, str):
            return bool(_LIKELY_API_KEY_RE.fullmatch(obj.strip()))
        return False

    return _walk(payload)


def load_model_config(path: Optional[Path] = None) -> ModelProviderConfig:
    """Load :class:`ModelProviderConfig` from ``path`` or
    :func:`default_config_path`. If the file does not exist, return a
    defaults-only instance. Malformed JSON raises
    :class:`ValueError` with the path and parser message.
    """

    p = path or default_config_path()
    if not p.exists():
        return ModelProviderConfig()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed model config JSON at {p}: {exc}") from exc
    try:
        return ModelProviderConfig.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError or shape mismatch
        raise ValueError(
            f"Invalid model config at {p}: {exc}"
        ) from exc


def save_model_config(
    cfg: ModelProviderConfig, path: Optional[Path] = None
) -> Path:
    """Persist ``cfg`` to ``path`` or :func:`default_config_path`.

    Returns the path written. Refuses to persist if any string field
    looks like a literal API key. Writes atomically via
    ``tmp + replace`` so a half-written file never lands.
    """

    p = path or default_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = cfg.model_dump(mode="json")
    if _contains_literal_key(payload):
        raise ValueError(
            "Refusing to write model config: literal API key detected. "
            "Only api_key_env (the env var name) is permitted in this file."
        )
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(p)
    return p


# --- resolution -----------------------------------------------------------


def resolve_runtime_settings(
    cfg: ModelProviderConfig,
    provider_name: Optional[str] = None,
) -> ProviderRuntimeSettings:
    """Resolve :class:`ProviderRuntimeSettings` for ``provider_name``
    (defaulting to ``cfg.planner_provider``).

    The deterministic provider does NOT go through here — it has no
    remote endpoint. Asking for it raises :class:`ValueError`.
    """

    name = provider_name or cfg.planner_provider
    if name == "openai_compatible":
        section = cfg.openai_compatible
    elif name == "openai":
        section = cfg.openai
    elif name == "anthropic":
        section = cfg.anthropic
    else:
        raise ValueError(
            f"Cannot resolve runtime settings for provider {name!r}; "
            "expected one of 'openai', 'anthropic', 'openai_compatible'."
        )
    return ProviderRuntimeSettings(
        name=name,  # type: ignore[arg-type]
        base_url=section.base_url,
        model=section.model,
        api_key_env=section.api_key_env,
        timeout_seconds=section.timeout_seconds,
        temperature=section.temperature,
        max_tokens=section.max_tokens,
        enable_real_model_calls=cfg.enable_real_model_calls,
    )


__all__ = [
    "APP_DIR_NAME",
    "DEFAULT_CONFIG_RELATIVE",
    "ModelProviderConfig",
    "OpenAICompatibleConfig",
    "ProviderRuntimeSettings",
    "default_config_path",
    "load_model_config",
    "resolve_runtime_settings",
    "save_model_config",
]