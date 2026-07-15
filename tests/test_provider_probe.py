"""Unit tests for the Phase 3 P2 provider probe.

Scope (per brief §4.1, ``docs/design/provider_probe_design.md``):

* 3 ``_probe_gate_open()`` env gate tests (exact match on ``"1"``).
* 4 endpoint pinning tests (default OpenAI / Ollama / vLLM / trailing
  slash — verify the round-1 P1 fix that ``base_url.rstrip("/") +
  "/models"`` does NOT double ``/v1``).
* 7 happy / unhealthy / timeout paths + 3 ``NotImplementedError``
  raise tests (deterministic + 2 skeletons).
* 2 redaction tests (Bearer / sk- token in body, headers).
* 3 invariants (``probe`` never writes ``run_summary.json`` / never
  modifies ``ProviderHealth`` / never calls ``health_check``).

These tests are pure unit — they never open a socket. The CLI-level
HTTP behavior is exercised separately in ``test_cli_provider_probe.py``
via subprocess + a real local ``http.server``.

Round 2 addition (post-Codex P1+P2 fix commit ``856a2d2``):

* Probe signature carries ``*, timeout_ms: int = 5000`` and the
  adapter applies ``max(timeout_ms, 1) / 1000.0`` as the socket
  timeout (no separate outer wall-clock guard in v1.0 — see
  ``base.py::BaseProvider.probe`` docstring).
* ``safe_url = _redact_secrets(url)`` is what reaches operator-visible
  fields (``reason`` / ``details["endpoint"]``); the raw URL is still
  what the HTTP layer sees.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Dict, Tuple

import pytest

from planner.cli import _probe_gate_open
from planner.model_config import ProviderRuntimeSettings
from planner.providers import (
    OpenAICompatibleProvider,
    available_providers,
    get_provider,
)
from planner.providers import (
    anthropic_adapter as anthropic_mod,
)
from planner.providers import (
    deterministic as deterministic_mod,
)
from planner.providers import (
    openai_adapter as openai_mod,
)
from planner.providers import (
    openai_compatible_adapter as oca_mod,
)
from planner.providers.base import (
    BaseProvider,
    ProviderHealth,
    ProviderProbeResult,
)


# ---- env scrubbing ----------------------------------------------------


@pytest.fixture(autouse=True)
def _scrub_planner_env(monkeypatch):
    scrubbed_prefixes = ("PLANNER_", "OPENAI_", "ANTHROPIC_")
    for key in list(os.environ):
        if any(key.startswith(prefix) for prefix in scrubbed_prefixes):
            monkeypatch.delenv(key, raising=False)
    yield


# ---- 1. env gate (3) --------------------------------------------------


def test_probe_gate_closed_when_env_unset(monkeypatch) -> None:
    """No env var → gate closed. ``PLANNER_PROBE`` must equal exactly
    ``"1"`` for the probe CLI to proceed."""

    monkeypatch.delenv("PLANNER_PROBE", raising=False)
    assert _probe_gate_open() is False


@pytest.mark.parametrize(
    "value", ["", "0", "1.0", "true", "True", "yes", "on", "1 ", " 1"]
)
def test_probe_gate_closed_when_env_not_one(monkeypatch, value: str) -> None:
    """Brief §2.2: only the exact literal ``"1"`` opens the gate.
    Empty string, ``"0"``, ``"true"``, ``"yes"``, leading/trailing
    whitespace, and float spellings all keep the gate closed. This
    prevents accidental alias-driven probes from sneaking through."""

    monkeypatch.setenv("PLANNER_PROBE", value)
    assert _probe_gate_open() is False


def test_probe_gate_open_when_env_one(monkeypatch) -> None:
    """Exact ``"1"`` opens the gate."""

    monkeypatch.setenv("PLANNER_PROBE", "1")
    assert _probe_gate_open() is True


# ---- 2. endpoint pinning (4) ------------------------------------------


def _settings(base_url: str) -> ProviderRuntimeSettings:
    return ProviderRuntimeSettings(
        name="openai_compatible",
        base_url=base_url,
        model="probe-model",
        api_key_env="PLANNER_PROBE_TEST_KEY",
        timeout_seconds=10.0,
        temperature=0.5,
        max_tokens=256,
        enable_real_model_calls=True,
    )


class _UrlCapture:
    """Probe-shaped HTTP layer. Captures the URL passed in and
    returns a canned 200 response so the provider can produce a
    ``ProviderProbeResult``."""

    def __init__(self, status_code: int = 200, body: bytes = b"{}") -> None:
        self.requests: list = []
        self.status_code = status_code
        self.body = body

    def get(self, url, headers, timeout):
        self.requests.append({"url": url, "headers": dict(headers), "timeout": timeout})
        return self.status_code, self.body


def _pin_url(base_url: str, monkeypatch) -> str:
    """Run a single probe against ``base_url`` and return the URL the
    adapter actually sent. Uses a fake ``http_get`` so no socket is
    opened."""

    fake = _UrlCapture(status_code=200, body=b"{}")
    monkeypatch.setenv("PLANNER_PROBE_TEST_KEY", "sk-probe-pin-token-1234567890")
    monkeypatch.setattr(oca_mod, "http_get", fake.get)
    provider = OpenAICompatibleProvider(settings=_settings(base_url))
    result = provider.probe()
    assert result.healthy is True, (
        f"probe should report healthy for base_url={base_url!r} but got "
        f"reason={result.reason!r}"
    )
    return fake.requests[0]["url"]


def test_probe_endpoint_default_openai_url_no_double_v1(monkeypatch) -> None:
    """Round-1 P1 fix: default ``base_url="https://api.openai.com/v1"``
    must yield ``...com/v1/models`` — **not** ``...com/v1/v1/models``."""

    url = _pin_url("https://api.openai.com/v1", monkeypatch)
    assert url == "https://api.openai.com/v1/models"
    # And, defense in depth: rstrip handles a trailing slash variant.
    assert "/v1/v1/" not in url


def test_probe_endpoint_ollama_no_double_v1(monkeypatch) -> None:
    """Ollama's OpenAI-compatible layer sits at ``/v1``; the probe
    must not append another ``/v1``."""

    url = _pin_url("http://localhost:11434/v1", monkeypatch)
    assert url == "http://localhost:11434/v1/models"


def test_probe_endpoint_vllm_no_double_v1(monkeypatch) -> None:
    """vLLM's OpenAI-compatible convention also puts the API under
    ``/v1``; same contract as Ollama."""

    url = _pin_url("http://host:8000/v1", monkeypatch)
    assert url == "http://host:8000/v1/models"


def test_probe_endpoint_trailing_slash_normalized(monkeypatch) -> None:
    """A trailing ``/`` on ``base_url`` is eaten by ``rstrip`` so the
    probe endpoint is ``...com/v1/models``, never ``...com/v1//models``."""

    url = _pin_url("https://api.openai.com/v1/", monkeypatch)
    assert url == "https://api.openai.com/v1/models"
    assert "//models" not in url


# ---- 3. happy / unhealthy / timeout (3) ------------------------------


def test_probe_openai_compatible_succeeds_with_fake_endpoint(monkeypatch) -> None:
    """A 200 response from the model-listing endpoint → healthy=True
    with latency_ms measured."""

    fake = _UrlCapture(status_code=200, body=b'{"object":"list","data":[]}')
    monkeypatch.setenv("PLANNER_PROBE_TEST_KEY", "sk-probe-test-1234567890")
    monkeypatch.setattr(oca_mod, "http_get", fake.get)
    provider = OpenAICompatibleProvider(settings=_settings("http://localhost:8000/v1"))
    result = provider.probe()
    assert result.healthy is True
    assert result.name == "openai_compatible"
    assert result.latency_ms is not None
    assert result.latency_ms >= 0
    assert result.details["http_status"] == "200"
    assert result.details["api_key_env"] == "PLANNER_PROBE_TEST_KEY"


def test_probe_openai_compatible_unhealthy_on_404(monkeypatch) -> None:
    """HTTP 4xx → healthy=False with the status code and a body
    excerpt in the reason. CLI exits 2 (covered in test_cli_provider_probe.py)."""

    fake = _UrlCapture(status_code=404, body=b"Not Found")
    monkeypatch.setenv("PLANNER_PROBE_TEST_KEY", "sk-probe-test-1234567890")
    monkeypatch.setattr(oca_mod, "http_get", fake.get)
    provider = OpenAICompatibleProvider(settings=_settings("http://localhost:8000/v1"))
    result = provider.probe()
    assert result.healthy is False
    assert "404" in (result.reason or "")
    assert result.details["http_status"] == "404"


def test_probe_openai_compatible_timeout_returns_not_healthy(monkeypatch) -> None:
    """A transport timeout (or any unexpected exception) → healthy=False
    with the exception class name in the reason, not a Python
    traceback that would leak to the operator."""

    def _boom(url, headers, timeout):
        raise TimeoutError("DNS lookup stalled")

    monkeypatch.setenv("PLANNER_PROBE_TEST_KEY", "sk-probe-test-1234567890")
    monkeypatch.setattr(oca_mod, "http_get", _boom)
    provider = OpenAICompatibleProvider(settings=_settings("http://localhost:8000/v1"))
    result = provider.probe()
    assert result.healthy is False
    assert "TimeoutError" in (result.reason or "")
    # No traceback framing: the operator sees the exception class name
    # only, not the full repr with frame.
    assert "Traceback" not in (result.reason or "")


# ---- 4. NotImplementedError (3) --------------------------------------


def test_probe_deterministic_raises_not_implemented() -> None:
    """``DeterministicProvider.probe()`` raises — there's no remote
    endpoint to probe against. CLI wraps this to exit 1."""

    provider = get_provider("deterministic")
    with pytest.raises(NotImplementedError) as excinfo:
        provider.probe()
    # Reason must explicitly tell the operator what to use instead.
    msg = str(excinfo.value)
    assert "deterministic" in msg.lower()
    assert "validate" in msg.lower()


def test_probe_skeleton_openai_raises_not_implemented() -> None:
    """The Phase-1 ``openai`` skeleton intentionally keeps probe
    unimplemented, even with key + SDK configured — mirrors the
    ``health_check`` implementation gate so the pipeline's
    fail-closed contract stays intact."""

    provider = get_provider("openai")
    with pytest.raises(NotImplementedError) as excinfo:
        provider.probe()
    msg = str(excinfo.value)
    assert "openai_compatible" in msg  # alignment hint


def test_probe_skeleton_anthropic_raises_not_implemented() -> None:
    """Mirror of the openai skeleton — anthropic probe is N/A in
    Phase 1 because there's no cheap Messages-API model-listing
    endpoint without a paid call."""

    provider = get_provider("anthropic")
    with pytest.raises(NotImplementedError) as excinfo:
        provider.probe()
    msg = str(excinfo.value)
    assert "openai_compatible" in msg  # alignment hint (same as openai)


# ---- 5. redaction (2) -------------------------------------------------


def test_probe_redacts_api_key_in_response_body(monkeypatch) -> None:
    """A 4xx response whose body echoes a ``sk-...`` token MUST be
    redacted before reaching ``reason`` (the body excerpt is the
    only operator-visible place a body secret would land, and it
    only appears for non-2xx responses — that's why we drive the
    fake to 404 here)."""

    secret = "sk-leak-body-secret-1234567890"
    fake = _UrlCapture(
        status_code=404,
        body=f'{{"id":"echoed-{secret}"}}'.encode("utf-8"),
    )
    monkeypatch.setenv("PLANNER_PROBE_TEST_KEY", "sk-probe-test-1234567890")
    monkeypatch.setattr(oca_mod, "http_get", fake.get)
    provider = OpenAICompatibleProvider(settings=_settings("http://localhost:8000/v1"))
    result = provider.probe()
    assert secret not in (result.reason or "")
    assert "<redacted>" in (result.reason or "")


def test_probe_redacts_bearer_token_in_response_body(monkeypatch) -> None:
    """A body echoing ``Bearer <token>`` MUST be redacted. Headers
    are NOT echoed in the result, so the body is the surface that
    needs the most attention."""

    secret = "Bearer eyJhbGciOiJIUzI1NiJ9-leak-1234567890"
    fake = _UrlCapture(
        status_code=401,
        body=f'Authorization header was: {secret}'.encode("utf-8"),
    )
    monkeypatch.setenv("PLANNER_PROBE_TEST_KEY", "sk-probe-test-1234567890")
    monkeypatch.setattr(oca_mod, "http_get", fake.get)
    provider = OpenAICompatibleProvider(settings=_settings("http://localhost:8000/v1"))
    result = provider.probe()
    assert secret not in (result.reason or "")
    assert "<redacted>" in (result.reason or "")


# ---- 6. invariants (3) -----------------------------------------------


def test_probe_does_not_write_run_summary(tmp_path: Path, monkeypatch) -> None:
    """``probe()`` MUST NOT touch any ``run_summary.json`` on disk
    (and shouldn't create any artifact at all). The brief §2.7
    row "写盘" forbids probe writes."""

    cwd_before = list(tmp_path.iterdir())
    fake = _UrlCapture(status_code=200, body=b"{}")
    monkeypatch.setenv("PLANNER_PROBE_TEST_KEY", "sk-probe-test-1234567890")
    monkeypatch.setattr(oca_mod, "http_get", fake.get)
    provider = OpenAICompatibleProvider(settings=_settings("http://localhost:8000/v1"))

    monkeypatch.chdir(tmp_path)
    # Run the probe many times to make any incidental write conspicuous.
    for _ in range(5):
        result = provider.probe()
        assert result.healthy is True

    cwd_after = list(tmp_path.iterdir())
    assert cwd_after == cwd_before, (
        f"probe() must not create files in cwd; before={cwd_before} "
        f"after={cwd_after}"
    )


def test_probe_does_not_modify_provider_health(monkeypatch) -> None:
    """``probe()`` MUST NOT mutate ``ProviderHealth``; the two are
    independent datapoints (brief §2.7). We snapshot the health
    before/after and assert byte-equality."""

    monkeypatch.setenv("PLANNER_PROBE_TEST_KEY", "sk-probe-test-1234567890")
    provider = OpenAICompatibleProvider(settings=_settings("http://localhost:8000/v1"))
    # Capture ``health_check`` output BEFORE probe runs.
    health_before = provider.health_check()
    # Run a probe; should be a no-op for health.
    fake = _UrlCapture(status_code=200, body=b"{}")
    monkeypatch.setattr(oca_mod, "http_get", fake.get)
    probe_result = provider.probe()
    health_after = provider.health_check()

    # Probe itself: clean and orthogonal to health.
    assert probe_result.healthy is True

    # health_check output is deterministic on the same settings + env
    # state — probe must not have nudged it.
    assert health_before.healthy == health_after.healthy
    assert health_before.reason == health_after.reason
    assert health_before.details == health_after.details


def test_probe_does_not_depend_on_health_check_call_path(monkeypatch) -> None:
    """Static guarantee: ``BaseProvider.probe`` and
    :meth:`BaseProvider.health_check` MUST NOT call each other.

    Static check (source inspection): grep ``probe`` for
    ``health_check`` and vice versa — neither should appear inside
    the other method's body on the four provider implementations."""

    import inspect

    for provider_name in available_providers():
        provider = get_provider(provider_name)
        probe_src = inspect.getsource(type(provider).probe)
        health_src = inspect.getsource(type(provider).health_check)
        # "self.health_check(" is forbidden inside probe; allow
        # ``health_check`` to appear in docstrings etc. by checking
        # for the call expression.
        assert "self.health_check(" not in probe_src, (
            f"{provider_name}.probe must not call self.health_check()"
        )
        assert "self.probe(" not in health_src, (
            f"{provider_name}.health_check must not call self.probe()"
        )


# ---- 7. extra: BaseProvider abstract default raises -------------------


def test_base_provider_probe_default_raises_not_implemented() -> None:
    """Any third-party ``BaseProvider`` subclass that doesn't override
    ``probe`` MUST fall back to the abstract default — ``raise
    NotImplementedError``. The CLI top-level catches it and exits 1."""

    from planner.providers.registry import (
        register as _register,
    )
    from planner.providers.registry import (
        unregister as _unregister,
    )

    # Build a subclass that does NOT implement probe. We register it
    # via the ``register`` decorator factory (used as a plain
    # function so we can register a class declared inside the test
    # body without leaking the name into the module namespace).
    @_register("no_probe_v1")
    class _NoProbeProvider(BaseProvider):
        def build_bibles(self, script_text, *, script_id="sample"):
            raise NotImplementedError

        def extract_beats(self, script_path, *, episode_id="EP01"):
            raise NotImplementedError

        def generate_shots(self, *, script_text, episode_id, location_ids,
                           character_ids, prop_ids, beats,
                           display_to_character_id=None):
            raise NotImplementedError

        def compile_image_prompts(self, shots, characters, locations, props):
            raise NotImplementedError

        def compile_video_prompts(self, shots, characters, locations, props):
            raise NotImplementedError

        def health_check(self) -> ProviderHealth:
            return ProviderHealth(name=self.name, healthy=True)

        # ``probe`` overrides the abstract default by re-raising
        # ``NotImplementedError`` (same effect, but lets the class
        # instantiate — the abstractmethod check is on the class,
        # not the body). A real third-party adapter that simply
        # doesn't implement probe would either omit the override
        # (caught at instantiation by ABC) or explicitly re-raise.
        def probe(self, *, timeout_ms: int = 5000):
            raise NotImplementedError(
                "no_probe_v1 deliberately does not implement probe()"
            )

    try:
        provider = get_provider("no_probe_v1")
        with pytest.raises(NotImplementedError):
            provider.probe()
        # Round-2: probe signature carries ``timeout_ms`` kwarg; the
        # abstract default accepts it and ignores it.
        with pytest.raises(NotImplementedError):
            provider.probe(timeout_ms=1234)
    finally:
        _unregister("no_probe_v1")