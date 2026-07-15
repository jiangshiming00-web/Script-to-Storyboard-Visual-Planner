"""Anthropic provider skeleton — Phase-1 adapter shape only.

This module is the mirror image of :mod:`planner.providers.openai_adapter`,
adapted to Anthropic's key namespace and optional SDK name. The same
hard boundaries apply:

* **No real API call** — the planning methods raise
  :class:`NotImplementedError`. Phase 1 never talks to the Anthropic
  Messages API or any other paid service.
* **No required dependency** — the ``anthropic`` package is checked
  via :func:`importlib.util.find_spec` and remains optional.
* **No new secrets** stored; we only inspect env-var presence.
* **Executor boundary unchanged** — Anthropic is a planning-layer
  provider; it never writes executor tasks.
* **Skeleton stays unhealthy**, even with full prerequisites (key +
  optional SDK present). See the companion note in
  :mod:`planner.providers.openai_adapter` — returning
  ``healthy=True`` here in Phase 1 would let the pipeline select the
  provider, ``mkdir`` the run directory, then crash via
  :class:`NotImplementedError` and break the
  ``fail-closed leaves no residue`` contract. Phase-1 always reports
  ``healthy=False`` and the pipeline routes the request to
  deterministic (development) or raises
  :class:`ProviderUnavailableError` (production).

v1.0 alignment with ``openai_compatible``
-----------------------------------------

v1.0 ships a runtime Chat-Completions transport via
:mod:`planner.providers.openai_compatible_adapter`. Anthropic's
Messages API has a different transport, so the runtime Chat-Completions
adapter does NOT drive ``api.anthropic.com`` in v1.0 — but the
canonical base URL is exposed as
:data:`planner.providers.openai_compatible_adapter.OFFICIAL_ANTHROPIC_BASE_URL`
so the GUI / CLI can show it as a known target for a future
Anthropic Messages transport.

The ``health_check.reason`` field on every branch ends with
:data:`planner.providers.openai_compatible_adapter.ALIGNMENT_HINT` so
operators hitting the implementation gate see the v1.0 model next
step inline. We do **not** swap the skeleton for a thin wrapper of
``OpenAICompatibleProvider`` — Anthropic needs its own Messages-API
adapter, which is tracked as a follow-up.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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
from .openai_compatible_adapter import ALIGNMENT_HINT
from .registry import register


#: Prefer the planner-namespaced env var; the provider-native
#: ``ANTHROPIC_API_KEY`` is accepted as a fallback for parity with
#: developer conventions.
_ANTHROPIC_KEY_PLANNER_ENV = "PLANNER_ANTHROPIC_API_KEY"
_ANTHROPIC_KEY_PROVIDER_ENV = "ANTHROPIC_API_KEY"

#: Optional SDK module name — must NOT be added to
#: ``pyproject.toml [project].dependencies``.
_ANTHROPIC_SDK_MODULE = "anthropic"

#: Sentinel value used in :attr:`ProviderHealth.details` to mark the
#: Phase-1 implementation gate as still closed. Same rationale and
#: contract as :data:`planner.providers.openai_adapter.IMPLEMENTED_FALSE`.
IMPLEMENTED_FALSE = "false"


def _anthropic_sdk_available() -> bool:
    """Return ``True`` if the optional ``anthropic`` SDK is importable.

    Same rationale as :func:`planner.providers.openai_adapter._openai_sdk_available`:
    cheap, side-effect-free, monkeypatchable for tests.

    .. note::

       **Monkeypatch contract.** Tests target this helper via
       ``monkeypatch.setattr(anthropic_adapter, "_anthropic_sdk_available",
       lambda: True)`` (see ``tests/test_openai_anthropic_adapter.py``).
       Same naming + module-binding rules as the OpenAI helper.
    """

    return importlib.util.find_spec(_ANTHROPIC_SDK_MODULE) is not None


def _resolve_anthropic_api_key() -> Optional[str]:
    """Return the configured Anthropic API key, or ``None``."""

    for env_name in (_ANTHROPIC_KEY_PLANNER_ENV, _ANTHROPIC_KEY_PROVIDER_ENV):
        value = os.environ.get(env_name)
        if value:
            return value.strip() or None
    return None


@register("anthropic")
class AnthropicProvider(BaseProvider):
    """Adapter skeleton for Anthropic-hosted models.

    Behaves identically to :class:`OpenAIProvider` modulo the env-var
    namespace and SDK name. The pipeline treats both adapters the same
    way, and **both report ``healthy=False`` in Phase 1** — even with
    full prerequisites — because the planning methods raise
    :class:`NotImplementedError`. See
    :mod:`planner.providers.openai_adapter` for the detailed reasoning.
    """

    #: Default for type checkers; ``register`` overrides at import.
    name: str = "anthropic"

    def probe(self, *, timeout_ms: int = 5000) -> ProviderProbeResult:
        """Phase-1 skeleton: probe is intentionally not implemented.

        Mirror of :meth:`OpenAIProvider.probe`: even with
        ``PLANNER_ANTHROPIC_API_KEY`` configured and the optional
        ``anthropic`` SDK installed, the Phase-1 implementation gate
        keeps the planning methods raising
        :class:`NotImplementedError`, and we mirror that stance for
        ``probe()``.

        ``timeout_ms`` is accepted to mirror the
        :meth:`BaseProvider.probe` signature; the skeleton has no
        network round-trip so the kwarg is ignored.

        Anthropic has no Messages-API Chat-Completions endpoint to
        model-list against without a paid call, so an opt-in
        network probe is genuinely N/A here until the future
        ``anthropic_messages_adapter`` lands. CLI top-level handler
        catches the exception, wraps to
        ``ProviderProbeError(reason="not_implemented")``, exits ``1``.
        """
        raise NotImplementedError(
            "AnthropicProvider.probe is intentionally not implemented "
            "in the Phase-1 skeleton. Anthropic has no cheap "
            "model-listing endpoint to probe against without a paid "
            "Messages call; configure provider='openai_compatible' for "
            "opt-in network probes against OpenAI-shape endpoints."
            + ALIGNMENT_HINT
        )

    def health_check(self) -> ProviderHealth:
        """Return the local-only readiness state for the Anthropic adapter."""
        details: Dict[str, str] = {
            "phase": "1-skeleton",
            "real_calls": "disabled",
        }

        api_key = _resolve_anthropic_api_key()
        if api_key is None:
            details["api_key_env"] = "missing"
            return ProviderHealth(
                name=self.name or "anthropic",
                healthy=False,
                reason=(
                    "Anthropic adapter is not configured: set "
                    f"{_ANTHROPIC_KEY_PLANNER_ENV} (preferred) or "
                    f"{_ANTHROPIC_KEY_PROVIDER_ENV} before requesting "
                    "this provider."
                    + ALIGNMENT_HINT
                ),
                details=details,
            )
        details["api_key_env"] = (
            _ANTHROPIC_KEY_PLANNER_ENV
            if os.environ.get(_ANTHROPIC_KEY_PLANNER_ENV)
            else _ANTHROPIC_KEY_PROVIDER_ENV
        )
        details["api_key_present"] = "true"

        if not _anthropic_sdk_available():
            return ProviderHealth(
                name=self.name or "anthropic",
                healthy=False,
                reason=(
                    "Anthropic adapter SDK not importable: install the "
                    "optional 'anthropic' package "
                    "(pip install 'anthropic>=0.30,<1') and retry. The "
                    "SDK remains an optional dependency and is "
                    "intentionally not listed in pyproject.toml's "
                    "[project].dependencies."
                    + ALIGNMENT_HINT
                ),
                details={**details, "sdk_module": _ANTHROPIC_SDK_MODULE, "sdk_installed": "false"},
            )
        details["sdk_module"] = _ANTHROPIC_SDK_MODULE
        details["sdk_installed"] = "true"

        # Phase-1 implementation gate. Even with the SDK installed
        # and the API key wired, ``healthy=False`` is the only safe
        # answer until the planning methods ship real
        # implementations — otherwise production would ``mkdir`` the
        # run directory then crash via NotImplementedError and leave
        # an empty run behind. The details still record every
        # configured signal so operators see that the prerequisites
        # are met.
        details["implemented"] = IMPLEMENTED_FALSE
        return ProviderHealth(
            name=self.name or "anthropic",
            healthy=False,
            reason=(
                "Anthropic adapter skeleton is configured locally "
                f"(api_key_env={details['api_key_env']}, "
                f"sdk={_ANTHROPIC_SDK_MODULE} installed) but the "
                "planning methods are not implemented in Phase 1. "
                "The pipeline will refuse to run this provider "
                "(fail-closed in production; auditable fallback to "
                "deterministic in development with "
                "allow_provider_fallback=true) until the implementation "
                "lands."
                + ALIGNMENT_HINT
            ),
            details=details,
        )

    # The five planning methods are unreachable under the standard
    # pipeline flow (``_select_provider`` runs ``health_check`` first).
    # They raise NotImplementedError so any future caller that bypasses
    # the gate gets a loud, semantic failure rather than a paid request.

    def build_bibles(
        self,
        script_text: str,
        *,
        script_id: str = "sample",
    ) -> Tuple[CharacterBible, LocationBible, PropBible]:
        raise NotImplementedError(
            "AnthropicProvider.build_bibles is intentionally not "
            "implemented in the Phase-1 skeleton. Phase-1 must not call "
            "real LLMs. Configure provider='deterministic' (or set "
            "allow_provider_fallback=true so the pipeline swaps to "
            "deterministic) instead."
        )

    def extract_beats(
        self,
        script_path: Path,
        *,
        episode_id: str = "EP01",
    ) -> List[StoryBeat]:
        raise NotImplementedError(
            "AnthropicProvider.extract_beats is intentionally not "
            "implemented in the Phase-1 skeleton."
        )

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
        raise NotImplementedError(
            "AnthropicProvider.generate_shots is intentionally not "
            "implemented in the Phase-1 skeleton."
        )

    def compile_image_prompts(
        self,
        shots: ShotList,
        characters: CharacterBible,
        locations: LocationBible,
        props: PropBible,
    ) -> ImagePrompts:
        raise NotImplementedError(
            "AnthropicProvider.compile_image_prompts is intentionally "
            "not implemented in the Phase-1 skeleton."
        )

    def compile_video_prompts(
        self,
        shots: ShotList,
        characters: CharacterBible,
        locations: LocationBible,
        props: PropBible,
    ) -> VideoPrompts:
        raise NotImplementedError(
            "AnthropicProvider.compile_video_prompts is intentionally "
            "not implemented in the Phase-1 skeleton."
        )
