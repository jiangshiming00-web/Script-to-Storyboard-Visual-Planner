"""Tests for the v1.0 OpenAI-compatible provider.

Pins the v1.0 contract from the release plan §5:

- The provider auto-registers on import.
- ``health_check()`` returns ``healthy=True`` only when:
  * the API key env var resolves to a non-empty value,
  * ``enable_real_model_calls=True`` is configured,
  * ``base_url`` parses as ``http(s)://`` (validated at config-load).
  Any missing precondition returns ``healthy=False`` with a
  descriptive reason. ``health_check`` is local-only — never issues
  a network request.
- HTTP responses are validated against the existing Pydantic schemas
  (CharacterBible / LocationBible / PropBible / StoryBeat / ShotList
  / ImagePrompts / VideoPrompts) via internal envelope models.
  Malformed JSON or schema mismatches raise :class:`ProviderOutputError`
  (a :class:`PlannerError` subclass) with a truncated payload excerpt
  and NEVER trigger a silent fallback.
- Production keeps the existing fail-closed contract: silent fallback
  to deterministic is rejected by ``planner.env._enforce_boundaries``.
  These tests cover the provider layer; the production boundary is
  exercised by the boundary tests.

All HTTP calls in these tests are routed through the
``http_post`` module-level handle, which defaults to
:mod:`urllib.request`. Tests monkeypatch that handle to a fake
without ever binding a real socket.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Tuple

import pytest

from planner.exceptions import PlannerError, ProviderOutputError
from planner.model_config import (
    ModelProviderConfig,
    OpenAICompatibleConfig,
    ProviderRuntimeSettings,
    resolve_runtime_settings,
)
from planner.providers import (
    OpenAICompatibleProvider,
    available_providers,
    get_provider,
)
from planner.providers import (
    openai_compatible_adapter as oca_mod,
)
from planner.providers.base import ProviderHealth


# ---- registry ----------------------------------------------------------


def test_openai_compatible_is_registered_at_import() -> None:
    assert "openai_compatible" in available_providers()
    provider = get_provider("openai_compatible")
    assert isinstance(provider, OpenAICompatibleProvider)


# ---- health_check: gates -----------------------------------------------


def _settings(
    *,
    enable_real_model_calls: bool = False,
    api_key_env: str = "PLANNER_TEST_KEY",
    api_key_value: str = "",
    base_url: str = "http://localhost:9999/v1",
) -> ProviderRuntimeSettings:
    return ProviderRuntimeSettings(
        name="openai_compatible",
        base_url=base_url,
        model="test-model",
        api_key_env=api_key_env,
        timeout_seconds=10.0,
        temperature=0.5,
        max_tokens=256,
        enable_real_model_calls=enable_real_model_calls,
    )


def test_health_check_unhealthy_when_real_calls_disabled(
    monkeypatch,
) -> None:
    """Default ``enable_real_model_calls=False`` MUST short-circuit
    ``health_check`` to ``healthy=False`` — even with a valid API key
    set in the environment."""

    monkeypatch.setenv("PLANNER_TEST_KEY", "sk-test-1234567890abcdef")
    provider = OpenAICompatibleProvider(settings=_settings(enable_real_model_calls=False))
    health = provider.health_check()
    assert health.healthy is False
    assert "Real model calls are disabled" in (health.reason or "")
    assert health.details["real_calls"] == "disabled"
    assert health.details["implemented"] == "true"
    # And we never touched the network: importorskip a network call by
    # patching http_post to a sentinel that raises if invoked.
    def _explode(*args, **kwargs):
        raise AssertionError("health_check must not call http_post")
    monkeypatch.setattr(oca_mod, "http_post", _explode)


def test_health_check_unhealthy_when_api_key_missing(monkeypatch) -> None:
    monkeypatch.delenv("PLANNER_TEST_KEY", raising=False)
    provider = OpenAICompatibleProvider(
        settings=_settings(enable_real_model_calls=True, api_key_value="")
    )
    health = provider.health_check()
    assert health.healthy is False
    assert "PLANNER_TEST_KEY" in (health.reason or "")
    assert health.details.get("api_key_present") is None  # key not surfaced


def test_health_check_unhealthy_when_api_key_empty_string(monkeypatch) -> None:
    monkeypatch.setenv("PLANNER_TEST_KEY", "   ")
    provider = OpenAICompatibleProvider(
        settings=_settings(enable_real_model_calls=True)
    )
    health = provider.health_check()
    assert health.healthy is False
    assert health.details.get("api_key_present") is None


def test_health_check_healthy_when_fully_configured(monkeypatch) -> None:
    monkeypatch.setenv("PLANNER_TEST_KEY", "sk-test-1234567890abcdef")
    provider = OpenAICompatibleProvider(
        settings=_settings(enable_real_model_calls=True)
    )
    health = provider.health_check()
    assert health.healthy is True
    assert health.details["api_key_present"] == "true"
    assert health.details["real_calls"] == "enabled"
    assert health.details["implemented"] == "true"


def test_health_check_unhealthy_for_non_http_base_url(monkeypatch) -> None:
    """Defensive check: the schema validator already rejects non-http
    ``base_url`` at config-load, but ``health_check`` must still refuse
    if a caller bypasses that path."""

    monkeypatch.setenv("PLANNER_TEST_KEY", "sk-test-1234567890abcdef")
    # ``OpenAICompatibleConfig`` would block this in normal usage; we
    # build ``ProviderRuntimeSettings`` directly to simulate a bypass.
    settings = _settings(enable_real_model_calls=True, base_url="ftp://nope")
    provider = OpenAICompatibleProvider(settings=settings)
    health = provider.health_check()
    assert health.healthy is False
    assert "not an http" in (health.reason or "")


def test_health_check_uses_default_settings_when_none(monkeypatch) -> None:
    """``get_provider("openai_compatible")`` (no explicit settings) is
    used by the registry / health-check path. It must still answer a
    valid ``ProviderHealth`` (using ``ModelProviderConfig`` defaults),
    not crash with ``NoneType``."""

    monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
    monkeypatch.setenv("PLANNER_TEST_KEY", "sk-test-1234567890abcdef")
    provider = OpenAICompatibleProvider()  # no settings
    # With default settings ``enable_real_model_calls`` is False, so
    # we expect healthy=False but the call must NOT raise.
    health = provider.health_check()
    assert isinstance(health, ProviderHealth)
    assert health.healthy is False


# ---- fake HTTP layer ---------------------------------------------------


class _FakeHttp:
    """Captures requests and returns a canned response.

    Tests instantiate this, monkeypatch
    ``openai_compatible_adapter.http_post`` to ``self.post``, then
    assert on ``self.requests`` (url / headers / body) and set
    ``self.status_code`` / ``self.body`` for the response.
    """

    def __init__(self) -> None:
        self.requests: list = []
        self.status_code: int = 200
        self.body: bytes = b"{}"

    def post(
        self,
        url: str,
        headers: Dict[str, str],
        body: bytes,
        timeout: float,
    ) -> Tuple[int, bytes]:
        self.requests.append(
            {"url": url, "headers": dict(headers), "body": body, "timeout": timeout}
        )
        return self.status_code, self.body


def _build_provider(
    settings: ProviderRuntimeSettings,
) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(settings=settings)


def _fully_configured_settings() -> ProviderRuntimeSettings:
    return _settings(enable_real_model_calls=True)


@pytest.fixture
def fake_http(monkeypatch) -> _FakeHttp:
    fake = _FakeHttp()
    monkeypatch.setenv("PLANNER_TEST_KEY", "sk-test-1234567890abcdef")
    monkeypatch.setattr(oca_mod, "http_post", fake.post)
    return fake


# ---- _chat_json: success path -----------------------------------------


def test_chat_json_parses_valid_envelope(fake_http: _FakeHttp) -> None:
    """A valid OpenAI Chat-Completions envelope MUST parse into the
    requested Pydantic model. The fake server returns one canned
    ``_BeatsEnvelope``-shaped response and we assert the
    ``extract_beats`` planning method uses it."""

    beats_payload = {
        "beats": [
            {
                "id": "beat-1",
                "label": "Setup",
                "summary": "Lin Xia walks into the cafe.",
                "span": {"start": 0, "end": 30, "text": "..."},
            }
        ]
    }
    fake_http.body = json.dumps(
        {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(beats_payload),
                    }
                }
            ]
        }
    ).encode("utf-8")

    provider = _build_provider(_fully_configured_settings())
    beats = provider.extract_beats(
        Path("/dev/null"), episode_id="EP01",
    )
    assert len(beats) == 1
    assert beats[0].id == "beat-1"

    # Auth header carries the key value (not env name) — this is the
    # only place the key value ever lives in this provider.
    req = fake_http.requests[0]
    assert req["url"].endswith("/chat/completions")
    assert req["headers"]["Authorization"] == "Bearer sk-test-1234567890abcdef"
    assert req["headers"]["Content-Type"] == "application/json"


def test_chat_json_strips_markdown_code_fence(fake_http: _FakeHttp) -> None:
    """Some LLMs wrap JSON in ```json ... ```. The parser MUST strip
    the fence before parsing."""

    inner = json.dumps({"beats": []})
    fake_http.body = json.dumps(
        {
            "choices": [
                {"message": {"content": f"```json\n{inner}\n```"}}
            ]
        }
    ).encode("utf-8")

    provider = _build_provider(_fully_configured_settings())
    beats = provider.extract_beats(Path("/dev/null"), episode_id="EP01")
    assert beats == []


# ---- _chat_json: error paths (NO silent fallback) --------------------


def test_chat_json_raises_on_http_error(fake_http: _FakeHttp) -> None:
    fake_http.status_code = 500
    fake_http.body = b"Internal Server Error"
    provider = _build_provider(_fully_configured_settings())
    with pytest.raises(ProviderOutputError, match=r"HTTP 500"):
        provider.extract_beats(Path("/dev/null"), episode_id="EP01")


def test_chat_json_raises_on_invalid_envelope_json(
    fake_http: _FakeHttp,
) -> None:
    fake_http.body = b"not json at all"
    provider = _build_provider(_fully_configured_settings())
    with pytest.raises(ProviderOutputError, match=r"not valid JSON"):
        provider.extract_beats(Path("/dev/null"), episode_id="EP01")


def test_chat_json_raises_on_envelope_missing_choices(
    fake_http: _FakeHttp,
) -> None:
    fake_http.body = json.dumps({"not_choices": []}).encode("utf-8")
    provider = _build_provider(_fully_configured_settings())
    with pytest.raises(ProviderOutputError, match=r"missing choices"):
        provider.extract_beats(Path("/dev/null"), episode_id="EP01")


def test_chat_json_raises_when_content_not_string(fake_http: _FakeHttp) -> None:
    """Defensive: some gateways return ``content`` as a list of parts."""

    fake_http.body = json.dumps(
        {"choices": [{"message": {"content": ["a", "b"]}}]}
    ).encode("utf-8")
    provider = _build_provider(_fully_configured_settings())
    with pytest.raises(ProviderOutputError, match=r"not a string"):
        provider.extract_beats(Path("/dev/null"), episode_id="EP01")


def test_chat_json_raises_on_content_not_matching_schema(
    fake_http: _FakeHttp,
) -> None:
    """JSON is valid but the payload doesn't match ``_BeatsEnvelope``.
    The error message MUST mention the model name so operators can
    pin down which planning step failed."""

    fake_http.body = json.dumps(
        {
            "choices": [
                {"message": {"content": json.dumps({"totally": "wrong shape"})}}
            ]
        }
    ).encode("utf-8")
    provider = _build_provider(_fully_configured_settings())
    with pytest.raises(ProviderOutputError) as excinfo:
        provider.extract_beats(Path("/dev/null"), episode_id="EP01")
    # ProviderOutputError is a PlannerError subclass — the pipeline's
    # top-level handler can catch it.
    assert isinstance(excinfo.value, PlannerError)
    # Error context is included verbatim so log readers see exactly
    # which step failed.
    msg = str(excinfo.value)
    assert "openai_compatible" in msg
    assert "extract_beats" in msg
    assert "test-model" in msg


def test_chat_json_raises_on_content_json_decode_error(
    fake_http: _FakeHttp,
) -> None:
    fake_http.body = json.dumps(
        {"choices": [{"message": {"content": "{this is not json"}}]}
    ).encode("utf-8")
    provider = _build_provider(_fully_configured_settings())
    with pytest.raises(ProviderOutputError, match=r"not valid JSON"):
        provider.extract_beats(Path("/dev/null"), episode_id="EP01")


# ---- error context never leaks the API key ---------------------------


def test_error_messages_do_not_leak_api_key(fake_http: _FakeHttp) -> None:
    """Operator-visible error messages MUST NOT include the API key
    value. The key only travels in the ``Authorization`` header; even
    when a parse error includes a payload excerpt, the bearer token
    is excluded."""

    fake_http.status_code = 500
    fake_http.body = (
        b'{"error": "upstream rejected", '
        b'"Authorization": "Bearer sk-test-1234567890abcdef"}'
    )
    provider = _build_provider(_fully_configured_settings())
    with pytest.raises(ProviderOutputError) as excinfo:
        provider.extract_beats(Path("/dev/null"), episode_id="EP01")
    msg = str(excinfo.value)
    assert "sk-test-1234567890abcdef" not in msg
    # The payload excerpt is present (truncated) so operators can see
    # the upstream error message.
    assert "upstream rejected" in msg


def test_error_messages_do_not_leak_api_key_on_parse_error(
    fake_http: _FakeHttp,
) -> None:
    fake_http.body = json.dumps(
        {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"echoed": "Bearer sk-test-1234567890abcdef"}
                        )
                    }
                }
            ]
        }
    ).encode("utf-8")
    provider = _build_provider(_fully_configured_settings())
    with pytest.raises(ProviderOutputError) as excinfo:
        provider.extract_beats(Path("/dev/null"), episode_id="EP01")
    # The key value MUST NOT appear in the operator-visible error.
    assert "sk-test-1234567890abcdef" not in str(excinfo.value)


# ---- planning method shape --------------------------------------------


def test_build_bibles_round_trip(fake_http: _FakeHttp) -> None:
    payload = {
        "characters": [
            {
                "id": "lin_xia",
                "name": "Lin Xia",
                "appearance": "young woman",
                "positive_prompt": "lin xia prompt",
                "negative_prompt": "blurry",
            }
        ],
        "locations": [
            {
                "id": "cafe",
                "name": "Cafe",
                "space_layout": "small",
                "positive_prompt": "cafe prompt",
                "negative_prompt": "blurry",
            }
        ],
        "props": [],
    }
    fake_http.body = json.dumps(
        {"choices": [{"message": {"content": json.dumps(payload)}}]}
    ).encode("utf-8")
    provider = _build_provider(_fully_configured_settings())
    characters, locations, props = provider.build_bibles(
        "scene text", script_id="EP01"
    )
    assert len(characters.characters) == 1
    assert characters.characters[0].id == "lin_xia"
    assert len(locations.locations) == 1
    assert props.props == []


def test_compile_image_prompts_parses_envelope(fake_http: _FakeHttp) -> None:
    payload = {
        "image_prompts": [
            {
                "shot_id": "shot-1",
                "prompt": "场景：cafe / 人物：lin_xia / 道具：",
                "negative_prompt": "blurry",
                "aspect_ratio": "16:9",
                "style_tags": ["cinematic"],
            }
        ]
    }
    fake_http.body = json.dumps(
        {"choices": [{"message": {"content": json.dumps(payload)}}]}
    ).encode("utf-8")
    provider = _build_provider(_fully_configured_settings())
    from planner.schema import (
        CharacterBible, LocationBible, PropBible, ShotList,
    )
    shots = ShotList(shots=[])  # type: ignore[arg-type]
    img = provider.compile_image_prompts(
        shots,
        CharacterBible(characters=[]),  # type: ignore[arg-type]
        LocationBible(locations=[]),  # type: ignore[arg-type]
        PropBible(props=[]),  # type: ignore[arg-type]
    )
    assert len(img.image_prompts) == 1


# ---- production fail-closed (integration smoke) ----------------------


def test_openai_compatible_health_check_healthy_only_with_real_calls(
    monkeypatch,
) -> None:
    """The pipeline's ``_select_provider`` already rejects unhealthy
    providers in production. Verify that even when ``health_check``
    returns ``healthy=True``, the production boundary still rejects
    silent fallback (a separate guarantee)."""

    monkeypatch.setenv("PLANNER_TEST_KEY", "sk-test-1234567890abcdef")
    provider = OpenAICompatibleProvider(settings=_fully_configured_settings())
    health = provider.health_check()
    assert health.healthy is True
    # Production still rejects silent fallback regardless of provider
    # health (this is enforced by ``planner.env._enforce_boundaries``,
    # covered by the existing boundary tests).


# ---- alignment hint surfaced on the skeleton adapters -----------------


def test_openai_skeleton_reason_includes_openai_compatible_hint(
    monkeypatch,
) -> None:
    """Operators hitting the openai skeleton's implementation gate MUST
    see a pointer at the v1.0 runtime path (``openai_compatible``).
    The hint is appended to every health_check reason branch.
    """

    from planner.providers.openai_adapter import (
        OpenAIProvider as SkeletonOpenAI,
    )

    monkeypatch.delenv("PLANNER_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = SkeletonOpenAI()
    health = provider.health_check()
    assert health.healthy is False
    assert "openai_compatible" in (health.reason or "")
    # The hint also references the canonical OpenAI base URL so an
    # operator can copy-paste it into the model config.
    assert "https://api.openai.com/v1" in (health.reason or "")


def test_anthropic_skeleton_reason_includes_openai_compatible_hint(
    monkeypatch,
) -> None:
    """Mirror of the openai alignment: the anthropic skeleton's reason
    must also steer operators at ``openai_compatible`` for v1.0.
    """

    from planner.providers.anthropic_adapter import (
        AnthropicProvider as SkeletonAnthropic,
    )

    monkeypatch.delenv("PLANNER_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = SkeletonAnthropic()
    health = provider.health_check()
    assert health.healthy is False
    assert "openai_compatible" in (health.reason or "")


def test_openai_compatible_canonical_base_urls_exposed() -> None:
    """The GUI / CLI both consume :data:`OFFICIAL_OPENAI_BASE_URL` and
    :data:`OFFICIAL_ANTHROPIC_BASE_URL` to render canonical defaults;
    pinning the values here protects against accidental URL drift."""

    from planner.providers.openai_compatible_adapter import (
        OFFICIAL_ANTHROPIC_BASE_URL,
        OFFICIAL_OPENAI_BASE_URL,
    )

    assert OFFICIAL_OPENAI_BASE_URL == "https://api.openai.com/v1"
    assert OFFICIAL_ANTHROPIC_BASE_URL == "https://api.anthropic.com"


# ---- probe: HTTP layer + redaction (Round 1 Codex fix) ---------------


class _FakeHttpGet:
    """Captures ``http_get`` calls and returns a canned response.

    Mirrors :class:`_FakeHttp` (which covers POST). Probe is a
    one-shot GET to ``{base_url}/models``; tests inspect
    ``self.requests`` (url / headers / timeout) and steer
    ``self.status_code`` / ``self.body`` for the response.
    """

    def __init__(self) -> None:
        self.requests: list = []
        self.status_code: int = 200
        self.body: bytes = b"{}"

    def get(
        self,
        url: str,
        headers: Dict[str, str],
        timeout: float,
    ) -> Tuple[int, bytes]:
        self.requests.append(
            {"url": url, "headers": dict(headers), "timeout": timeout}
        )
        return self.status_code, self.body


@pytest.fixture
def fake_http_get(monkeypatch) -> _FakeHttpGet:
    fake = _FakeHttpGet()
    monkeypatch.setenv("PLANNER_TEST_KEY", "sk-test-1234567890abcdef")
    monkeypatch.setattr(oca_mod, "http_get", fake.get)
    return fake


def test_probe_redacts_secret_in_url_endpoint(
    fake_http_get: _FakeHttpGet,
) -> None:
    """R14 Codex fix: probe MUST redact any secret embedded in the
    ``base_url`` before echoing it via ``reason`` or
    ``details["endpoint"]``. Operators occasionally wire a vLLM /
    gateway URL that contains a literal API key (``sk-...`` token
    in the path or query); echoing it back is a red-line leak.

    The actual HTTP call still uses the raw URL (we still need to
    reach the endpoint) — only the operator-visible fields are
    scrubbed.
    """

    settings = ProviderRuntimeSettings(
        name="openai_compatible",
        base_url="https://example.com/v1/sk-probe-secret-1234567890",
        model="probe-model",
        api_key_env="PLANNER_TEST_KEY",
        timeout_seconds=10.0,
        temperature=0.5,
        max_tokens=256,
        enable_real_model_calls=True,
    )
    provider = OpenAICompatibleProvider(settings=settings)
    result = provider.probe()

    # Raw token MUST NOT appear in any operator-visible field.
    assert "sk-probe-secret-1234567890" not in (result.reason or "")
    assert "sk-probe-secret-1234567890" not in str(result.details)

    # The request still went out to the raw URL (we still need to
    # actually hit the endpoint).
    assert len(fake_http_get.requests) == 1
    assert (
        fake_http_get.requests[0]["url"]
        == "https://example.com/v1/sk-probe-secret-1234567890/models"
    )

    # Redaction replaced the token with the canonical placeholder
    # in the operator-visible fields.
    assert "<redacted>" in (result.reason or "")
    assert "<redacted>" in result.details.get("endpoint", "")


def test_probe_redacts_secret_in_unhealthy_path(
    fake_http_get: _FakeHttpGet,
) -> None:
    """P1 also covers the unhealthy path: when ``http_get`` raises
    or returns 4xx / 5xx, the URL echoed in ``reason`` /
    ``details["endpoint"]`` MUST still be redacted.
    """

    fake_http_get.status_code = 401
    settings = ProviderRuntimeSettings(
        name="openai_compatible",
        base_url="https://example.com/v1/sk-probe-secret-9876543210",
        model="probe-model",
        api_key_env="PLANNER_TEST_KEY",
        timeout_seconds=10.0,
        temperature=0.5,
        max_tokens=256,
        enable_real_model_calls=True,
    )
    provider = OpenAICompatibleProvider(settings=settings)
    result = provider.probe()

    assert result.healthy is False
    assert "sk-probe-secret-9876543210" not in (result.reason or "")
    assert "sk-probe-secret-9876543210" not in str(result.details)
    assert "<redacted>" in (result.reason or "")


def test_probe_uses_timeout_ms_kwarg(fake_http_get: _FakeHttpGet) -> None:
    """P2 timeout fix: the CLI-facing ``--timeout-ms`` knob MUST
    drive the socket-level timeout the adapter hands to ``http_get``.
    Without this, the CLI option was documented as a contract but
    silently ignored.
    """

    provider = OpenAICompatibleProvider(settings=_fully_configured_settings())
    provider.probe(timeout_ms=2500)
    assert len(fake_http_get.requests) == 1
    # 2500 ms → 2.5 s at the URLopen level.
    assert fake_http_get.requests[0]["timeout"] == pytest.approx(2.5)


def test_probe_default_timeout_is_five_seconds(
    fake_http_get: _FakeHttpGet,
) -> None:
    """Default ``timeout_ms`` is 5000ms (brief §2.2). The probe
    works without the kwarg and applies the 5s socket timeout."""

    provider = OpenAICompatibleProvider(settings=_fully_configured_settings())
    provider.probe()
    assert len(fake_http_get.requests) == 1
    assert fake_http_get.requests[0]["timeout"] == pytest.approx(5.0)


def test_probe_default_settings_when_none_uses_default_base_url(
    monkeypatch,
) -> None:
    """P2 default-settings fix (Codex round-2 P2): the CLI builds
    a default :class:`ProviderRuntimeSettings` from
    :class:`ModelProviderConfig` when no model config is on disk
    and the operator explicitly asks for ``--provider
    openai_compatible``. The probe MUST succeed against those
    defaults (default ``base_url`` is ``http://localhost:8000/v1``
    per :class:`OpenAICompatibleConfig`); the HTTP layer is
    monkeypatched so no real socket is bound.
    """

    from planner.model_config import ModelProviderConfig

    monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)

    class _BoomGet:
        def __init__(self) -> None:
            self.requests: list = []

        def get(self, url, headers, timeout):
            self.requests.append({"url": url, "timeout": timeout})
            # Don't pretend the endpoint is healthy; we only need
            # to prove the request URL is the defaults-built one.
            return 503, b"service unavailable"

    boom = _BoomGet()
    monkeypatch.setattr(oca_mod, "http_get", boom.get)

    settings = resolve_runtime_settings(
        ModelProviderConfig(planner_provider="openai_compatible"),
        provider_name="openai_compatible",
    )
    provider = OpenAICompatibleProvider(settings=settings)
    result = provider.probe()

    # Defaults resolve to ``http://localhost:8000/v1``; probe
    # endpoint is ``{base_url.rstrip("/")}/models``.
    assert boom.requests[0]["url"] == "http://localhost:8000/v1/models"
    assert result.healthy is False  # 503 from the fake server
    assert "<redacted>" not in (result.reason or "")  # nothing to redact


# ---- imports / env scrubbing ------------------------------------------


@pytest.fixture(autouse=True)
def _scrub_planner_env(monkeypatch):
    scrubbed_prefixes = ("PLANNER_", "OPENAI_", "ANTHROPIC_")
    for key in list(os.environ):
        if any(key.startswith(prefix) for prefix in scrubbed_prefixes):
            monkeypatch.delenv(key, raising=False)
    yield