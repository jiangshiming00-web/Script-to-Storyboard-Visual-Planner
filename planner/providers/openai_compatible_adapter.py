"""OpenAI-compatible provider for v1.0.

This is the first provider in the registry that can actually issue
real HTTP requests. It targets the OpenAI Chat Completions shape so
the same code drives:

- the official OpenAI API,
- third-party gateways that mirror OpenAI's API,
- internal model gateways (公司内部模型网关),
- local vLLM / Ollama compatible servers.

Hard rules (red lines; see :mod:`planner.exceptions` and the v1.0 plan
§5):

- **No required SDK dependency.** The HTTP client is the Python
  standard library :mod:`urllib.request` — no ``openai`` /
  ``anthropic`` package in ``pyproject.toml``. If a teammate wants
  the official SDK they can add it as an optional dependency and the
  provider still works (we just don't import it).
- **No real call without explicit operator consent.** Health check is
  local-only and returns ``healthy=False`` unless
  ``enable_real_model_calls=True`` is configured AND the API key env
  var is non-empty AND ``base_url`` is a parseable HTTP URL.
- **No silent fallback.** Production keeps the existing
  ``fail-closed`` contract from :mod:`planner.env`. JSON parse
  failures surface as :class:`ProviderOutputError`, never as a
  silent swap to deterministic.
- **No key in audit fields.** ``run_summary.json`` only records the
  env var name (``api_key_env``), never the key value. Error
  messages include a truncated excerpt of the response payload but
  strip the ``Authorization`` header.

This provider's :meth:`health_check` is the first one in the
registry that can report ``healthy=True``. The skeleton adapters
(``openai`` / ``anthropic``) still report ``healthy=False`` until
their planning methods ship real implementations; this one ships them
in v1.0 and so flips the gate.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

from pydantic import BaseModel

from ..exceptions import ProviderOutputError
from ..model_config import (
    ModelProviderConfig,
    OpenAICompatibleConfig,
    ProviderRuntimeSettings,
    resolve_runtime_settings,
)
from ..schema import (
    CharacterBible,
    ImagePrompts,
    LocationBible,
    PropBible,
    ShotList,
    StoryBeat,
    VideoPrompts,
)
from .base import BaseProvider, ProviderHealth, ProviderProbeResult
from .registry import register


# --- HTTP layer (stdlib only, swappable for tests) -----------------------


def _default_http_post(
    url: str,
    headers: Dict[str, str],
    body: bytes,
    timeout: float,
) -> Tuple[int, bytes]:
    """Default HTTP POST using :mod:`urllib.request`.

    Returns ``(status_code, body_bytes)``. ``urllib.error.HTTPError``
    is re-raised with ``.code`` and ``.read()`` so the caller can
    distinguish 4xx from 5xx and surface the body in the error
    message. ``timeout`` is in seconds.

    .. note::

       The signature is intentionally narrow (positional args + plain
       types) so tests can monkeypatch
       ``openai_compatible_adapter._default_http_post`` with a fake
       server returning a fixed JSON payload without spawning a real
       HTTP listener.
    """

    from urllib import request as _urlreq

    req = _urlreq.Request(
        url=url,
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with _urlreq.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except _urlreq.HTTPError as exc:  # pragma: no cover - depends on server
        return exc.code, exc.read() or b""


#: Module-level handle so tests can monkeypatch the HTTP layer.
http_post: Callable[[str, Dict[str, str], bytes, float], Tuple[int, bytes]] = (
    _default_http_post
)


def _default_http_get(
    url: str,
    headers: Dict[str, str],
    timeout: float,
) -> Tuple[int, bytes]:
    """Default HTTP GET using :mod:`urllib.request`.

    Returns ``(status_code, body_bytes)``. Same contract as
    :func:`_default_http_post`: ``HTTPError`` is converted to
    ``(exc.code, exc.read() or b"")`` so the caller can distinguish
    4xx from 5xx and surface the body in the result. ``timeout`` is
    in seconds.

    .. note::

       Signature mirrors ``_default_http_post`` minus the ``body``
       positional; tests can monkeypatch this with a fake-server
       style fixture the same way they monkeypatch the POST helper
       (``tests/test_provider_probe.py`` Round 2).
    """

    from urllib import request as _urlreq

    req = _urlreq.Request(url=url, headers=headers, method="GET")
    try:
        with _urlreq.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except _urlreq.HTTPError as exc:  # pragma: no cover - depends on server
        return exc.code, exc.read() or b""


#: Module-level handle so tests can monkeypatch the GET layer.
http_get: Callable[[str, Dict[str, str], float], Tuple[int, bytes]] = (
    _default_http_get
)


# --- sentinel constants (mirror openai / anthropic skeleton style) ------


#: Sentinel for ``ProviderHealth.details["implemented"]`` — we are
#: past Phase 1 now, so this is always ``"true"`` when the gate is
#: satisfied. Kept as a string for JSON round-trip stability, matching
#: the contract documented on :class:`ProviderHealth.details`.
IMPLEMENTED_TRUE = "true"

#: Sentinel for ``ProviderHealth.details["real_calls"]`` — explicit
#: string instead of bool, same rationale as ``IMPLEMENTED_TRUE``.
REAL_CALLS_ENABLED = "enabled"
REAL_CALLS_DISABLED = "disabled"


#: Canonical OpenAI Chat-Completions base URL. The ``openai`` skeleton
#: adapter and the GUI both reference this so the operator sees the
#: same hint wherever they look.
OFFICIAL_OPENAI_BASE_URL = "https://api.openai.com/v1"

#: Canonical Anthropic Messages-API base URL. Anthropic uses a
#: different transport (Messages API) than OpenAI Chat-Completions,
#: so the openai_compatible adapter does NOT route here in v1.0 — but
#: the URL is exposed so the GUI / CLI can show it as a known target
#: for a future Anthropic Messages transport.
OFFICIAL_ANTHROPIC_BASE_URL = "https://api.anthropic.com"

#: Reason suffix the ``openai`` / ``anthropic`` skeleton adapters
#: append to their ``health_check.reason`` so operators hitting the
#: Phase-1 implementation gate see a concrete next step. Kept in one
#: place so the message stays consistent across both adapters and the
#: GUI renders it identically.
ALIGNMENT_HINT = (
    " v1.0 ships the OpenAI Chat-Completions transport via "
    "provider=openai_compatible "
    f"(base_url={OFFICIAL_OPENAI_BASE_URL} for OpenAI). Configure "
    "planner_provider='openai_compatible' in the model config to call "
    "real models today; the openai/anthropic skeletons are reserved "
    "for SDK-based adapters in a future revision."
)


# --- helpers -------------------------------------------------------------


_TRUNCATE_LIMIT = 512

#: Patterns that look like API keys in upstream error bodies. We strip
#: them from operator-visible error messages because some gateways
#: echo back the ``Authorization`` header (or the key inside the
#: request payload) when reporting failures. The substitution runs
#: AFTER truncation so the ``<redacted>`` placeholder is always
#: present even when the original token was longer than the
#: truncation budget.
_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9_\-]{8,}")
_OPENAI_KEY_RE = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")
_ANTHROPIC_KEY_RE = re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}")
_GITHUB_KEY_RE = re.compile(r"gho_[A-Za-z0-9_\-]{8,}")


def _redact_secrets(text: str) -> str:
    """Replace any token that looks like an API key in ``text`` with
    the placeholder ``<redacted>``. Used to sanitize error-message
    payload excerpts so a misbehaving upstream gateway can't leak
    credentials through the planner's error path.
    """

    text = _BEARER_RE.sub(r"\1<redacted>", text)
    text = _OPENAI_KEY_RE.sub("<redacted>", text)
    text = _ANTHROPIC_KEY_RE.sub("<redacted>", text)
    text = _GITHUB_KEY_RE.sub("<redacted>", text)
    return text


def _truncate(value: str, limit: int = _TRUNCATE_LIMIT) -> str:
    """Return ``value`` truncated to ``limit`` characters with an
    explicit "..." marker. Used to keep error messages short while
    still showing the operator where the JSON parse went wrong.
    """

    if len(value) <= limit:
        return value
    return value[:limit] + f"... <truncated {len(value) - limit} chars>"


def _safe_excerpt(value: str, limit: int = _TRUNCATE_LIMIT) -> str:
    """Truncate AND redact secrets. The canonical pre-flight for any
    string that lands in a :class:`ProviderOutputError` message."""

    return _redact_secrets(_truncate(value, limit=limit))


@dataclass
class _StepErrorContext:
    """Bundle of (provider, model, step) used for error messages."""

    provider: str
    model: str
    step: str

    def format(self, message: str) -> str:
        return (
            f"[{self.provider}/{self.model}::{self.step}] {message}"
        )


def _parse_payload(
    raw_text: str,
    *,
    ctx: _StepErrorContext,
    model: Type[BaseModel],
) -> BaseModel:
    """Parse ``raw_text`` as JSON and validate against ``model``.

    Raises :class:`ProviderOutputError` (a :class:`PlannerError`
    subclass) on JSON parse failure or schema mismatch. The error
    message includes the provider / model / step and a truncated,
    secret-redacted payload excerpt.
    """

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ProviderOutputError(
            ctx.format(
                f"response is not valid JSON: {exc.msg} at "
                f"line {exc.lineno} col {exc.colno}. "
                f"payload excerpt: {_safe_excerpt(raw_text)!r}"
            )
        ) from exc

    try:
        return model.model_validate(data)
    except Exception as exc:
        # pydantic's ValidationError str() can echo the full input
        # dict — including any secrets echoed by an upstream gateway.
        # Sanitize the pydantic message before embedding it in our
        # operator-visible error.
        raw_exc_msg = _safe_excerpt(str(exc))
        raise ProviderOutputError(
            ctx.format(
                f"response JSON does not match {model.__name__} schema: "
                f"{raw_exc_msg}. payload excerpt: {_safe_excerpt(raw_text)!r}"
            )
        ) from exc


def _strip_code_fence(text: str) -> str:
    r"""Strip triple-backtick code fences (```json ... ```) some LLMs
    wrap their JSON in.

    Only used as a defensive parse aid; if the inner content is not
    valid JSON the parse error still surfaces with the original text
    attached, so we don't lose information.
    """

    fenced = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    return text.strip()


# --- prompt fragments ----------------------------------------------------


_SYSTEM_PROMPT = (
    "You are a strict JSON generator for a short-drama planner. "
    "Always reply with one valid JSON object that matches the schema "
    "the user describes. Do not include prose, code fences, or "
    "comments around the JSON."
)


# --- the provider --------------------------------------------------------


@register("openai_compatible")
class OpenAICompatibleProvider(BaseProvider):
    """OpenAI Chat-Completions-shaped provider.

    Talks to any endpoint that accepts
    ``POST {base_url}/chat/completions`` with the standard
    OpenAI request body and returns the standard response. Drives
    OpenAI itself, vLLM, Ollama's OpenAI compat layer, internal
    gateways, etc.
    """

    #: Default for type checkers; ``register`` overrides this at import.
    name: str = "openai_compatible"

    def __init__(
        self,
        settings: Optional[ProviderRuntimeSettings] = None,
    ) -> None:
        self._settings = settings

    # --- health check -------------------------------------------------

    def probe(self, *, timeout_ms: int = 5000) -> ProviderProbeResult:
        """Opt-in network reachability probe.

        Implements the brief ``docs/design/provider_probe_design.md``
        §2.5 endpoint contract: ``GET {settings.base_url.rstrip("/")}/
        models``. Mirrors the runtime chat-completion join at line 431
        (``settings.base_url.rstrip("/") + "/chat/completions"``) so
        the same rstrip handles default OpenAI URLs
        (``https://api.openai.com/v1``) and Ollama/vLLM-style URLs
        (``http://localhost:11434/v1``) without doubling ``/v1``.

        Lifecycle:

        1. Resolve ``settings`` via :meth:`_require_settings`
           (raises if provider was instantiated without explicit
           settings — the CLI never hits this path because it
           builds settings from the model config first).
        2. Issue one HTTPS GET to the model-listing endpoint with a
           socket timeout driven by ``timeout_ms`` (default 5000ms
           per brief §2.2). Network-level failures (DNS, TCP, TLS,
           timeout) are caught and returned as
           ``ProviderProbeResult(healthy=False)`` — **not** raised,
           so the CLI exits ``2`` with a structured reason instead
           of a Python traceback.
        3. HTTP 4xx / 5xx → ``healthy=False`` with the status code
           and a redacted body excerpt in ``reason``.
        4. HTTP 2xx → ``healthy=True`` with optional ``latency_ms``.

        Strict-separation invariants (brief §2.7):

        * ``health_check`` is NOT called here — we go straight to
          ``_require_settings`` to skip the always-on gate.
        * No on-disk side effects (no ``run_summary.json`` writes).
        * Every string field goes through :func:`_redact_secrets`
          before reaching ``reason`` / ``details``. The URL itself
          is redacted before reuse — operators sometimes wire a
          gateway path or query string with a literal key, and the
          endpoint must never echo that back. The
          ``Authorization: Bearer <key>`` header is included in the
          request only — it is **not** echoed back into the result
          (only ``api_key_env`` — the env-var **name** — appears).

        Module-level :data:`http_get` is the seam tests use to fake
        a server; this method never invents a hard-coded
        ``urllib.request.urlopen`` call.
        """
        settings = self._require_settings()
        url = settings.base_url.rstrip("/") + "/models"
        # Redact the URL **before** any operator-visible field uses
        # it. Operators can wire secrets directly into ``base_url``
        # (a vLLM gateway path with the key baked in, a query
        # string, etc.), so the endpoint echoed in ``reason`` /
        # ``details`` MUST go through the same 4-regex
        # ``_redact_secrets`` filter as body / header values. The
        # raw ``url`` is still used for the actual HTTP request.
        safe_url = _redact_secrets(url)

        # Probe outer timeout: ``timeout_ms`` is the CLI-facing knob
        # (brief §2.2 default 5000ms). Defense in depth: the value
        # is applied at the URLopen level too, so a wedged DNS
        # resolution cannot hang the CLI.
        timeout_seconds = max(timeout_ms, 1) / 1000.0

        headers = {
            "Authorization": f"Bearer {settings.api_key() or ''}",
        }

        started = time.monotonic()
        try:
            status_code, body = http_get(url, headers, timeout_seconds)
        except Exception as exc:  # pragma: no cover - depends on host
            return ProviderProbeResult(
                name=self.name,
                healthy=False,
                reason=(
                    f"probe request to {safe_url} raised "
                    f"{type(exc).__name__}: {_redact_secrets(repr(exc))}"
                ),
                details={
                    "endpoint": safe_url,
                    "error_type": type(exc).__name__,
                    "api_key_env": settings.api_key_env,
                },
            )
        elapsed_ms = int((time.monotonic() - started) * 1000)

        redacted_body = _redact_secrets(
            body.decode("utf-8", errors="replace")
        )
        if 200 <= status_code < 300:
            return ProviderProbeResult(
                name=self.name,
                healthy=True,
                reason=f"GET {safe_url} returned {status_code}",
                latency_ms=elapsed_ms,
                details={
                    "endpoint": safe_url,
                    "http_status": str(status_code),
                    "api_key_env": settings.api_key_env,
                },
            )
        return ProviderProbeResult(
            name=self.name,
            healthy=False,
            reason=(
                f"GET {safe_url} returned {status_code}; "
                f"body excerpt: {redacted_body[:120]!r}"
            ),
            latency_ms=elapsed_ms,
            details={
                "endpoint": safe_url,
                "http_status": str(status_code),
                "api_key_env": settings.api_key_env,
            },
        )

    def health_check(self) -> ProviderHealth:
        """Local-only readiness for the OpenAI-compatible adapter.

        Returns ``healthy=True`` only when ALL of the following are
        true (no network, no SDK import, no paid probe):

        - a non-empty API key env var name is configured,
        - the env var resolves to a non-empty value,
        - ``enable_real_model_calls=True`` (operator's explicit
          consent — the default is OFF),
        - ``base_url`` parses as ``http://`` or ``https://`` (the
          :class:`OpenAICompatibleConfig` validator already
          enforces this at config-load time).

        Any missing precondition returns ``healthy=False`` with a
        descriptive ``reason`` and a ``details`` dict that records
        every signal so the operator can fix it.
        """

        settings = self._settings
        if settings is None:
            # Provider instantiated without explicit settings — fall back
            # to defaults + the ``planner_provider`` literal. This keeps
            # ``get_provider("openai_compatible")`` callable for the
            # registry / health-check path even when the CLI hasn't
            # loaded a model config yet.
            settings = resolve_runtime_settings(
                ModelProviderConfig(planner_provider="openai_compatible"),
                provider_name="openai_compatible",
            )

        details: Dict[str, str] = {
            "base_url": settings.base_url,
            "model": settings.model,
            "api_key_env": settings.api_key_env,
            "real_calls": (
                REAL_CALLS_ENABLED
                if settings.enable_real_model_calls
                else REAL_CALLS_DISABLED
            ),
            "implemented": IMPLEMENTED_TRUE,
            "phase": "1-runtime",
        }

        if not settings.enable_real_model_calls:
            return ProviderHealth(
                name=self.name,
                healthy=False,
                reason=(
                    "Real model calls are disabled. Flip "
                    "enable_real_model_calls=true in the model config "
                    f"({_config_hint()}) before requesting this provider."
                ),
                details=details,
            )

        key_value = settings.api_key()
        if key_value is None:
            return ProviderHealth(
                name=self.name,
                healthy=False,
                reason=(
                    f"API key env var {settings.api_key_env!r} is unset "
                    f"or empty. Set it before requesting this provider."
                ),
                details=details,
            )
        details["api_key_present"] = "true"

        # base_url shape was validated at config-load time, but be
        # defensive in case someone bypassed load_model_config.
        if not settings.base_url.startswith(("http://", "https://")):
            return ProviderHealth(
                name=self.name,
                healthy=False,
                reason=(
                    f"base_url {settings.base_url!r} is not an http(s) URL."
                ),
                details=details,
            )

        return ProviderHealth(
            name=self.name,
            healthy=True,
            reason=(
                f"openai_compatible provider configured "
                f"(model={settings.model}, base_url={settings.base_url})."
            ),
            details=details,
        )

    # --- chat completion wrapper --------------------------------------

    def _chat_json(
        self,
        *,
        step: str,
        user_prompt: str,
        response_model: Type[BaseModel],
    ) -> BaseModel:
        """Issue a single chat-completion and parse the response.

        ``step`` is included verbatim in error messages so an operator
        looking at ``run_summary.json`` can tell which planning step
        failed. ``user_prompt`` should describe the expected JSON
        schema (``response_model.model_json_schema()`` is a good
        default; callers build the prompt).
        """

        settings = self._require_settings()
        ctx = _StepErrorContext(
            provider=self.name,
            model=settings.model,
            step=step,
        )

        url = settings.base_url.rstrip("/") + "/chat/completions"
        body_dict = {
            "model": settings.model,
            "temperature": settings.temperature,
            "max_tokens": settings.max_tokens,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        }
        body = json.dumps(body_dict).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.api_key() or ''}",
        }

        status_code, response_bytes = http_post(
            url, headers, body, settings.timeout_seconds,
        )
        if status_code < 200 or status_code >= 300:
            excerpt = _safe_excerpt(
                response_bytes.decode("utf-8", errors="replace")
            )
            raise ProviderOutputError(
                ctx.format(
                    f"HTTP {status_code} from {url}. "
                    f"body excerpt: {excerpt!r}"
                )
            )

        try:
            envelope = json.loads(response_bytes.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ProviderOutputError(
                ctx.format(
                    f"response envelope is not valid JSON: {exc.msg}. "
                    f"body excerpt: "
                    f"{_safe_excerpt(response_bytes.decode('utf-8', errors='replace'))!r}"
                )
            ) from exc

        try:
            choices = envelope["choices"]
            message = choices[0]["message"]
            content = message["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderOutputError(
                ctx.format(
                    f"response envelope missing choices[0].message.content: {exc}. "
                    f"envelope excerpt: {_safe_excerpt(json.dumps(envelope))!r}"
                )
            ) from exc

        if not isinstance(content, str):
            raise ProviderOutputError(
                ctx.format(
                    f"choices[0].message.content is not a string "
                    f"(got {type(content).__name__})."
                )
            )

        return _parse_payload(_strip_code_fence(content), ctx=ctx, model=response_model)

    def _require_settings(self) -> ProviderRuntimeSettings:
        """Return the bound settings, or raise :class:`ProviderOutputError`
        if the provider was constructed without them.

        The pipeline calls ``health_check`` before any planning method,
        so this branch is only reachable for direct (bypass) calls —
        we still raise a friendly ``PlannerError`` so the caller sees a
        structured failure rather than an ``AttributeError``.
        """

        if self._settings is None:
            raise ProviderOutputError(
                f"{self.name} provider was constructed without "
                "ProviderRuntimeSettings; refusing to issue an HTTP "
                "request. The CLI/GUI must load a model config first."
            )
        return self._settings

    # --- the five planning methods -------------------------------------

    def build_bibles(
        self,
        script_text: str,
        *,
        script_id: str = "sample",
    ) -> Tuple[CharacterBible, LocationBible, PropBible]:
        from ..schema import (
            CharacterBible as _Char,
            LocationBible as _Loc,
            PropBible as _Prop,
        )

        user_prompt = (
            "Extract characters, locations, and props from the "
            "following script. Return a JSON object with three keys: "
            "'characters', 'locations', 'props'. Each list element "
            "must conform to the relevant schema. Use canonical "
            "snake_case ids derived from display names. Script: \n\n"
            f"{script_text}"
        )
        envelope = self._chat_json(
            step="build_bibles",
            user_prompt=user_prompt,
            response_model=_BibleEnvelope,
        )
        # Pydantic doesn't natively express "three independent models
        # in one envelope", so the envelope model is internal.
        return (
            _Char(characters=envelope.characters),
            _Loc(locations=envelope.locations),
            _Prop(props=envelope.props),
        )

    def extract_beats(
        self,
        script_path: Path,
        *,
        episode_id: str = "EP01",
    ) -> List[StoryBeat]:
        script_text = script_path.read_text(encoding="utf-8")
        user_prompt = (
            "Extract the ordered story beats for the following script. "
            "Return a JSON object with a 'beats' key whose value is a "
            "list of objects, each with id/label/summary/span fields. "
            f"Episode id: {episode_id}. Script:\n\n{script_text}"
        )
        envelope = self._chat_json(
            step="extract_beats",
            user_prompt=user_prompt,
            response_model=_BeatsEnvelope,
        )
        return envelope.beats

    def generate_shots(
        self,
        *,
        script_text: str,
        episode_id: str,
        location_ids: List[str],
        character_ids: List[str],
        prop_ids: List[str],
        beats: List[StoryBeat],
        display_to_character_id: Optional[Dict[str, str]] = None,
    ) -> ShotList:
        from ..schema import ShotList as _Shots

        beat_payload = [b.model_dump() for b in beats]
        user_prompt = (
            f"Generate a shot list for episode {episode_id}. "
            f"Allowed location_ids={location_ids}, "
            f"character_ids={character_ids}, prop_ids={prop_ids}. "
            "Return a JSON object with a 'shots' key. Each shot must "
            "reference ONLY the allowed ids. Beats: "
            f"{beat_payload}"
        )
        # NB: this prompt intentionally refers to the bibles by their
        # canonical ids so the LLM cannot invent unknown ids. The
        # downstream BrokenReferenceError check will catch anything
        # that slips through.
        envelope = self._chat_json(
            step="generate_shots",
            user_prompt=user_prompt,
            response_model=_ShotsEnvelope,
        )
        return _Shots(shots=envelope.shots)

    def compile_image_prompts(
        self,
        shots: ShotList,
        characters: CharacterBible,
        locations: LocationBible,
        props: PropBible,
    ) -> ImagePrompts:
        from ..schema import ImagePrompts as _Img

        user_prompt = (
            "Compose image-generation prompts for each shot. Return a "
            "JSON object with an 'image_prompts' key. Each prompt must "
            "include '场景：', '人物：', '道具：' headers so reviewers "
            "can confirm references. "
            f"Shots: {[s.model_dump() for s in shots.shots]}"
        )
        envelope = self._chat_json(
            step="compile_image_prompts",
            user_prompt=user_prompt,
            response_model=_ImagePromptsEnvelope,
        )
        return _Img(image_prompts=envelope.image_prompts)

    def compile_video_prompts(
        self,
        shots: ShotList,
        characters: CharacterBible,
        locations: LocationBible,
        props: PropBible,
    ) -> VideoPrompts:
        from ..schema import VideoPrompts as _Vid

        user_prompt = (
            "Compose video-generation prompts for each shot. Return a "
            "JSON object with a 'video_prompts' key. Include motion, "
            "camera and avoid fields per prompt. "
            f"Shots: {[s.model_dump() for s in shots.shots]}"
        )
        envelope = self._chat_json(
            step="compile_video_prompts",
            user_prompt=user_prompt,
            response_model=_VideoPromptsEnvelope,
        )
        return _Vid(video_prompts=envelope.video_prompts)


# --- envelope models ----------------------------------------------------


class _BibleEnvelope(BaseModel):
    """Internal envelope for the ``build_bibles`` response."""

    characters: List[Dict[str, Any]]
    locations: List[Dict[str, Any]]
    props: List[Dict[str, Any]]


class _BeatsEnvelope(BaseModel):
    """Internal envelope for the ``extract_beats`` response."""

    beats: List[StoryBeat]


class _ShotsEnvelope(BaseModel):
    """Internal envelope for the ``generate_shots`` response."""

    shots: List[Dict[str, Any]]


class _ImagePromptsEnvelope(BaseModel):
    """Internal envelope for the ``compile_image_prompts`` response."""

    image_prompts: List[Dict[str, Any]]


class _VideoPromptsEnvelope(BaseModel):
    """Internal envelope for the ``compile_video_prompts`` response."""

    video_prompts: List[Dict[str, Any]]


# --- config hint helper -------------------------------------------------


def _config_hint() -> str:
    """Return a short hint pointing operators at the config file
    location. Kept lazy so unit tests that monkeypatch the env don't
    have to worry about import-time side effects.
    """

    try:
        from ..model_config import default_config_path
        return f"defaults to {default_config_path()}"
    except Exception:  # pragma: no cover - defensive
        return "(see model config file)"


__all__ = [
    "OpenAICompatibleProvider",
    "ALIGNMENT_HINT",
    "IMPLEMENTED_TRUE",
    "OFFICIAL_ANTHROPIC_BASE_URL",
    "OFFICIAL_OPENAI_BASE_URL",
    "REAL_CALLS_DISABLED",
    "REAL_CALLS_ENABLED",
    "http_post",
]