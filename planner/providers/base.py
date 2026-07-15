"""Abstract provider interface.

Every provider must implement the five extraction / planning /
compilation capabilities listed below. The shapes (arguments and
return types) are pinned by the data contract in
``specs/DATA_CONTRACTS.md``; deviating from them breaks the on-disk
artifacts regardless of which provider is configured.

In addition, every provider must expose :meth:`BaseProvider.health_check`
so the pipeline can audit its readiness without making real expensive
network calls. Health checks must be cheap and local-only — checking
config presence, optional-dependency availability, and similar — and
must NEVER issue model-inference or paid API requests. Providers that
need network reachability (e.g. a future OpenAI adapter) should expose
an opt-in :meth:`probe` separately so the planner never silently spends
money on a health check.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from ..model_config import ProviderRuntimeSettings

from ..schema import (
    CharacterBible,
    ImagePrompts,
    LocationBible,
    PropBible,
    ShotList,
    StoryBeat,
    VideoPrompts,
)


@dataclass
class ProviderHealth:
    """Outcome of a provider's local health check.

    ``healthy`` is the only required field. ``reason`` should be a
    short, human-readable string suitable for inclusion in
    ``run_summary.json`` (no secrets, no stack traces). ``details`` may
    carry structured debug info (config keys present, optional SDK
    installed, etc.) and is intentionally permissive about shape —
    providers own their own keys.

    .. note::

       ``details`` values are **string sentinels** (e.g. ``"true"`` /
       ``"false"``), not booleans. The string typing keeps the
       dataclass JSON-round-trip stable (no surprises with
       ``bool`` ↔ ``str`` coercion across Pydantic versions) and
       makes ``run_summary.json`` literals self-describing when
       operators read the file by hand. Providers should expose
       named constants for any sentinel that is part of their
       public contract (see e.g.
       :data:`planner.providers.openai_adapter.IMPLEMENTED_FALSE`).
    """

    name: str
    healthy: bool
    reason: Optional[str] = None
    details: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderProbeResult:
    """Outcome of one operator-initiated ``probe()`` call.

    Distinct from :class:`ProviderHealth`: ``health_check`` is the
    cheap local-only readiness gate that ``_select_provider`` runs
    before every pipeline invocation; ``probe`` is the
    opt-in network reachability check that **only** the
    ``planner provider-probe`` CLI subcommand (or future GUI
    analog) triggers. They share NO code path — see
    ``docs/design/provider_probe_design.md`` §2.7 for the
    8-dimension strict-separation table.

    Field contracts:

    * ``name``: echoes ``self.name`` so the CLI can label the row
      even when the operator probes multiple providers in batch.
    * ``healthy``: ``True`` only when the probe completed AND the
      endpoint responded with a status the adapter considers
      usable. ``False`` covers every "tried but failed" mode
      (DNS / TCP / TLS / 4xx / 5xx / timeout).
    * ``reason``: short human-readable explanation; **redacted**
      via :func:`planner.agent.redact.redact_secrets_text` before
      going to stderr / stdout. Never echoes raw API key values.
    * ``latency_ms``: optional wall-clock latency of the round-trip;
      ``None`` when the provider cannot self-time.
    * ``details``: string-sentinel free-form dict (mirrors the
      :class:`ProviderHealth.details` contract). Owned by the
      provider; CLI surfaces under ``--verbose`` only, always
      redacted.

    The dataclass is ``frozen=True`` because probe results are
    immutable point-in-time snapshots; mutating them after a
    round-trip would suggest the probe was re-run, which is a
    different operation and warrants a fresh result object.
    """

    name: str
    healthy: bool
    reason: Optional[str] = None
    latency_ms: Optional[int] = None
    details: Dict[str, str] = field(default_factory=dict)


class BaseProvider(ABC):
    """Provider abstraction for the non-visual planner steps.

    Implementations are stateless w.r.t. the pipeline run; :mod:`pipeline`
    instantiates the provider once per run via :func:`registry.get_provider`.

    The optional ``settings`` argument carries
    :class:`planner.model_config.ProviderRuntimeSettings` for providers
    that talk to a remote endpoint (``openai_compatible`` and future
    real-model adapters). Providers that don't need remote settings
    (``deterministic``, ``openai`` / ``anthropic`` skeletons) accept
    and ignore it - this keeps :func:`registry.get_provider` uniform
    so the pipeline never has to branch on provider type.
    """

    #: Public name used in ``config/planner_provider`` and CLI logs.
    name: str = ""

    def __init__(self, settings: Optional[ProviderRuntimeSettings] = None) -> None:
        # Stored but not read by deterministic / skeleton providers.
        # ``OpenAICompatibleProvider`` overrides ``__init__`` and uses
        # this to drive its HTTP client + health_check.
        self._settings = settings

    @abstractmethod
    def build_bibles(
        self,
        script_text: str,
        *,
        script_id: str = "sample",
    ) -> Tuple[CharacterBible, LocationBible, PropBible]:
        """Extract characters, locations and props from the script.

        Implementations should populate ``inference_level`` and
        ``confidence`` on every entry so consumers can tell seed from
        inferred data.
        """

    @abstractmethod
    def extract_beats(
        self,
        script_path: Path,
        *,
        episode_id: str = "EP01",
    ) -> List[StoryBeat]:
        """Return the ordered list of story beats for the episode."""

    @abstractmethod
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
        """Plan the shot list, referencing bible ids only.

        Implementations must NOT inline visual copy — every shot must
        reference its characters / location / props via the canonical
        ids passed in. The ``display_to_character_id`` map lets the
        provider collapse Chinese display names (e.g. ``林夏``) onto
        the seeded canonical id (e.g. ``lin_xia``) so reference
        integrity passes.
        """

    @abstractmethod
    def compile_image_prompts(
        self,
        shots: ShotList,
        characters: CharacterBible,
        locations: LocationBible,
        props: PropBible,
    ) -> ImagePrompts:
        """Compose image-generation prompts.

        The prompt for each shot must include explicit
        ``场景：xxx`` / ``人物：xxx`` / ``道具：xxx`` headers so
        validators and humans can confirm the shot references the right
        bible entries.
        """

    @abstractmethod
    def compile_video_prompts(
        self,
        shots: ShotList,
        characters: CharacterBible,
        locations: LocationBible,
        props: PropBible,
    ) -> VideoPrompts:
        """Compose video-generation prompts."""

    @abstractmethod
    def health_check(self) -> ProviderHealth:
        """Return the provider's current local health.

        Implementations must be side-effect free w.r.t. paid services:
        no real LLM calls, no account logins, no paid-tier probes.
        Acceptable signals include:

        - presence of required config keys / env vars,
        - presence of optional SDK dependencies,
        - static sanity checks on configured model names.

        The deterministic provider always returns ``healthy=True``.
        Future LLM providers should return ``healthy=False`` with a
        descriptive ``reason`` when their preconditions are not met so
        the pipeline can fall back to deterministic in development or
        fail-closed in production.
        """

    @abstractmethod
    def probe(self) -> "ProviderProbeResult":
        """Optional network reachability / sanity check.

        Distinct from :meth:`health_check`. The two are deliberately
        decoupled:

        * ``health_check()`` is the always-on local-only signal that
          ``_select_provider`` reads in the pipeline hot path.
        * ``probe()`` is the opt-in network round-trip that **only**
          the ``planner provider-probe`` CLI subcommand triggers.

        Default implementation raises :class:`NotImplementedError`.
        Adapters that don't expose a remote endpoint
        (``deterministic``, the ``openai`` / ``anthropic``
        Phase-1 skeletons) keep the default — the CLI top-level
        catches the exception, wraps it into a structured
        :class:`~planner.exceptions.ProviderProbeError` carrying
        ``reason="not_implemented"``, and exits **1**. Adapters that
        do expose a remote endpoint (``openai_compatible``) override
        and return a :class:`ProviderProbeResult` whose ``healthy``
        field drives the CLI exit code (``True`` → **0**,
        ``False`` → **2**).

        Implementation requirements for adapters that override:

        * one HTTPS / HTTP round-trip at most (no polling, no retry).
        * no LLM / paid inference calls — model-listing endpoints
          (``{base_url.rstrip('/')}/models``) are the canonical
          pattern.
        * no on-disk side effects (never touches
          ``run_summary.json``).
        * outer wall-clock timeout enforced by the CLI; the adapter
          SHOULD also set its own socket timeout (defense in depth).
        * every string field returned goes through
          :func:`planner.agent.redact.redact_secrets_text` before
          reaching stderr / stdout. Bearer tokens, ``sk-...``,
          ``sk-ant-...``, ``gho_...`` MUST NOT appear in the result.

        Strict isolation contract (see brief §2.7):

        * ``health_check`` MUST NOT call ``probe``.
        * ``probe`` MUST NOT call ``health_check``.

        Invoked only via the ``planner provider-probe`` CLI
        subcommand, which checks ``PLANNER_PROBE=1`` env var + the
        subcommand invocation itself as a double-gate before
        reaching here (see brief §2.2).
        """