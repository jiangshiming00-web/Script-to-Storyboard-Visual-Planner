"""OpenAI provider skeleton — Phase-1 adapter shape only.

This module ships the **interface** for an OpenAI-backed provider without
ever issuing a real API request. It exists so that:

1. The registry can resolve ``planner_provider: "openai"`` at config-load
   time without raising ``ConfigError`` (so production configs can be
   staged without breaking CLI startup).
2. :meth:`BaseProvider.health_check` answers the right questions before
   the pipeline ever calls a planning method — config presence + optional
   SDK availability — both of which are local signals that do not cost
   money and do not touch the network.
3. The pipeline's existing ``_select_provider`` fail-closed / fallback
   semantics continue to work, *even when the operator has wired an
   API key and installed the optional SDK*. Phase-1 reports
   ``healthy=False`` until the planning methods ship real
   implementations; production raises ``ProviderUnavailableError`` and
   development (with ``allow_provider_fallback=true``) silently swaps
   to deterministic while recording the swap in ``run_summary.json``.

Hard boundaries observed here:

* **No real API call** is ever made. The planning methods raise
  :class:`NotImplementedError` so any caller that bypasses the
  pipeline's health-check gate sees a loud, semantics-clear failure
  instead of a paid inference request.
* **No required dependency** is added to ``pyproject.toml``. The
  ``openai`` package remains optional; we detect it with
  :func:`importlib.util.find_spec` at health-check time.
* **No new secrets** are read or written; we only inspect whether an
  env var is non-empty.
* **Executor boundary** is untouched: this adapter is a planning-layer
  provider only and never writes executor tasks.
* **Skeleton stays unhealthy**, even with full prerequisites. A
  previous version of this module returned ``healthy=True`` when
  both the key and the SDK were present — that broke the
  ``fail-closed leaves no residue`` contract because the pipeline
  would select this provider, ``mkdir`` the run directory, then
  crash via :class:`NotImplementedError` (which is not a
  :class:`PlannerError` and slipped past the CLI's
  ``try/except PlannerError`` handler). Phase-1 must therefore
  always report ``healthy=False``.

v1.0 alignment with ``openai_compatible``
-----------------------------------------

v1.0 ships a runtime Chat-Completions transport via
:mod:`planner.providers.openai_compatible_adapter`. To call the OpenAI
API today, configure ``planner_provider="openai_compatible"`` with
``base_url="https://api.openai.com/v1"`` and ``api_key_env="OPENAI_API_KEY"``
(or ``PLANNER_OPENAI_API_KEY``).

The ``openai`` skeleton here is preserved as a forward-compatibility
placeholder for an SDK-based adapter in a future revision. The
``health_check.reason`` field on every branch ends with
:data:`planner.providers.openai_compatible_adapter.ALIGNMENT_HINT` so
operators hitting the implementation gate see the next step inline
instead of having to read the docs.

We do **not** swap the skeleton for a thin wrapper of
``OpenAICompatibleProvider`` because doing so would silently flip
``healthy=True`` once prerequisites are met and break the Phase-1
``fail-closed leaves no residue`` contract documented above.
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


#: Prefer the planner-namespaced env var so operators can wire secrets
#: without leaking into the global ``OPENAI_API_KEY`` slot. We still
#: accept the provider-native ``OPENAI_API_KEY`` as a fallback so
#: existing developer setups keep working.
_OPENAI_KEY_PLANNER_ENV = "PLANNER_OPENAI_API_KEY"
_OPENAI_KEY_PROVIDER_ENV = "OPENAI_API_KEY"

#: Module of the optional SDK. Must NOT be added to
#: ``pyproject.toml [project].dependencies``.
_OPENAI_SDK_MODULE = "openai"

#: Sentinel value used in :attr:`ProviderHealth.details` to mark the
#: Phase-1 implementation gate as still closed. Kept as a string to
#: stay compatible with the ``Dict[str, str]`` typing of ``details``
#: (so the dict round-trips through JSON unchanged) and to make the
#: "openai / anthropic skeleton" branching obvious in
#: ``run_summary.json`` (operators reading the file see literal
#: ``"false"`` rather than a Pythonic ``False`` that other code
#: might serialize inconsistently). Tests assert against this exact
#: string; treat changes here as a contract change.
IMPLEMENTED_FALSE = "false"


def _openai_sdk_available() -> bool:
    """Return ``True`` if the optional ``openai`` SDK can be imported.

    Detection is by :func:`importlib.util.find_spec` so we do not pay
    the cost of actually importing the SDK at health-check time. The
    function is intentionally a module-level helper so tests can
    monkeypatch it to simulate install / uninstall without touching
    the production env.

    .. note::

       **Monkeypatch contract.** Tests target this helper via
       ``monkeypatch.setattr(openai_adapter, "_openai_sdk_available",
       lambda: True)`` (see ``tests/test_openai_anthropic_adapter.py``).
       A future refactor that moves this helper onto a class
       (e.g. ``OpenAIProvider._sdk_available``) or renames it must
       keep the same name and module binding, or update the test
       file in lockstep — otherwise the ``_isolate_registry``
       fixture will silently keep the SDK-detection bypass in place
       and the key+SDK-present contract tests will lose coverage.
    """

    return importlib.util.find_spec(_OPENAI_SDK_MODULE) is not None


def _resolve_openai_api_key() -> Optional[str]:
    """Return the configured API key (preferred namespace first).

    Order:

    1. ``PLANNER_OPENAI_API_KEY`` — planner-owned namespace; should be
       the default for production-bound configurations.
    2. ``OPENAI_API_KEY`` — provider-native namespace, kept for parity
       with developer conventions.

    Returns ``None`` when neither is set or both are empty strings.
    """

    for env_name in (_OPENAI_KEY_PLANNER_ENV, _OPENAI_KEY_PROVIDER_ENV):
        value = os.environ.get(env_name)
        if value:
            return value.strip() or None
    return None


@register("openai")
class OpenAIProvider(BaseProvider):
    """Adapter skeleton for OpenAI-hosted models.

    The provider is **opt-in** in every meaningful sense. In Phase 1 it
    reports ``healthy=False`` even with full prerequisites (API key +
    optional SDK present) because the five planning methods are not
    implemented yet — they raise :class:`NotImplementedError`. The
    ``always unhealthy in skeleton`` stance keeps the pipeline's
    fail-closed / fallback contract intact: a future change that lights
    up ``healthy=True`` must come with real implementations of every
    planning method, otherwise production would ``mkdir`` then crash
    via ``NotImplementedError`` and leave an empty run directory
    behind — exactly the contract violation the Codex review
    flagged.

    The provider is therefore selective at TWO layers:

    1. ``ConfigError`` at config-load if the name is unknown (existing
       contract).
    2. ``health_check()`` returns healthy only when the implementation
       lands (Phase 2+); until then it always reports unhealthy and
       the pipeline routes the request to deterministic (development)
       or raises :class:`ProviderUnavailableError` (production).
    """

    #: Default for type checkers; ``register`` overrides this at import.
    name: str = "openai"

    # ---- health check -------------------------------------------------

    def probe(self) -> ProviderProbeResult:
        """Phase-1 skeleton: probe is intentionally not implemented.

        Even when the operator has wired ``PLANNER_OPENAI_API_KEY``
        and installed the optional ``openai`` SDK, the Phase-1
        implementation gate keeps the planning methods raising
        :class:`NotImplementedError`. We mirror that stance for
        ``probe()`` — exposing a probe here would imply real-model
        reachability is verifiable, which is a v1.x concern tied to
        the planning method rollout.

        The CLI top-level handler catches the exception, wraps to
        ``ProviderProbeError(reason="not_implemented")``, and exits
        ``1``. Operators who want a live reachability check against
        the OpenAI endpoint should configure
        ``planner_provider="openai_compatible"`` with
        ``base_url="https://api.openai.com/v1"`` — that adapter
        ships a real ``probe()`` per the brief §3 contract.
        """
        raise NotImplementedError(
            "OpenAIProvider.probe is intentionally not implemented in "
            "the Phase-1 skeleton. Configure provider='openai_compatible' "
            "for an opt-in network probe against the OpenAI endpoint."
            + ALIGNMENT_HINT
        )

    def health_check(self) -> ProviderHealth:
        """Return local-only readiness for the OpenAI adapter.

        Checks (no network, no SDK import, no auth):

        * Is a non-empty API key configured via the planner-namespace
          or provider-namespace env var?
        * Is the optional ``openai`` SDK importable?
        * **Are the planning methods implemented for Phase 1?**

        The third gate is the load-bearing one. Even when both
        preconditions above pass, ``healthy`` MUST be ``False`` until
        the five planning methods ship real implementations. The
        skeleton raises :class:`NotImplementedError`, which is not a
        :class:`PlannerError` and therefore slips past the CLI's
        ``try/except PlannerError`` guard. Letting the pipeline select
        this provider when ``healthy=True`` would mean

        * in development: a stray :class:`NotImplementedError` reaching
          the user instead of a planned ``ProviderUnavailableError`` /
          deterministic fallback; and
        * in production: the same exception type risks leaking past the
          CLI error handler AND, more importantly, breaks the
          ``fail-closed leaves no residue`` contract because
          ``mkdir`` already happened before ``build_bibles`` threw.

        Reporting ``healthy=False`` keeps the existing pipeline
        machinery — ``_select_provider`` will then fail-closed in
        production (raising :class:`ProviderUnavailableError`) or
        fall back to deterministic in development — and that contract
        is independent of any specific implementation detail here.
        """

        details: Dict[str, str] = {
            "phase": "1-skeleton",
            "real_calls": "disabled",
        }

        api_key = _resolve_openai_api_key()
        if api_key is None:
            details["api_key_env"] = "missing"
            return ProviderHealth(
                name=self.name or "openai",
                healthy=False,
                reason=(
                    "OpenAI adapter is not configured: set "
                    f"{_OPENAI_KEY_PLANNER_ENV} (preferred) or "
                    f"{_OPENAI_KEY_PROVIDER_ENV} before requesting this "
                    "provider."
                    + ALIGNMENT_HINT
                ),
                details=details,
            )
        # Record which namespace won, but never log the value itself.
        details["api_key_env"] = (
            _OPENAI_KEY_PLANNER_ENV
            if os.environ.get(_OPENAI_KEY_PLANNER_ENV)
            else _OPENAI_KEY_PROVIDER_ENV
        )
        details["api_key_present"] = "true"

        if not _openai_sdk_available():
            return ProviderHealth(
                name=self.name or "openai",
                healthy=False,
                reason=(
                    "OpenAI adapter SDK not importable: install the "
                    "optional 'openai' package "
                    "(pip install 'openai>=1,<2') and retry. The SDK "
                    "remains an optional dependency and is intentionally "
                    "not listed in pyproject.toml's [project].dependencies."
                    + ALIGNMENT_HINT
                ),
                details={**details, "sdk_module": _OPENAI_SDK_MODULE, "sdk_installed": "false"},
            )
        details["sdk_module"] = _OPENAI_SDK_MODULE
        details["sdk_installed"] = "true"

        # Phase-1 implementation gate. The pipeline contract REQUIRES
        # ``healthy=False`` until the planning methods are real; we
        # report every configured signal in ``details`` so operators
        # can see the prerequisites passed, then we still return
        # ``healthy=False`` with a reason explaining the gap.
        details["implemented"] = IMPLEMENTED_FALSE
        return ProviderHealth(
            name=self.name or "openai",
            healthy=False,
            reason=(
                "OpenAI adapter skeleton is configured locally "
                f"(api_key_env={details['api_key_env']}, "
                f"sdk={_OPENAI_SDK_MODULE} installed) but the "
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

    # ---- planning methods --------------------------------------------
    #
    # The five methods below are unreachable under normal pipeline
    # flow because ``_select_provider`` runs ``health_check`` first and
    # either swap-to-deterministic (development with fallback allowed)
    # or raises ``ProviderUnavailableError`` (production or fallback
    # disabled). We raise ``NotImplementedError`` so any future caller
    # that bypasses the gate gets a loud, semantic failure that does
    # NOT look like a transient network issue and does NOT bubble up
    # through ``PlannerError``.

    def build_bibles(
        self,
        script_text: str,
        *,
        script_id: str = "sample",
    ) -> Tuple[CharacterBible, LocationBible, PropBible]:
        raise NotImplementedError(
            "OpenAIProvider.build_bibles is intentionally not "
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
            "OpenAIProvider.extract_beats is intentionally not "
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
            "OpenAIProvider.generate_shots is intentionally not "
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
            "OpenAIProvider.compile_image_prompts is intentionally not "
            "implemented in the Phase-1 skeleton."
        )

    def compile_video_prompts(
        self,
        shots: ShotList,
        characters: CharacterBible,
        locations: LocationBible,
        props: PropBible,
    ) -> VideoPrompts:
        raise NotImplementedError(
            "OpenAIProvider.compile_video_prompts is intentionally not "
            "implemented in the Phase-1 skeleton."
        )
