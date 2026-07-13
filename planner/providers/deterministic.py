"""Deterministic provider — Phase-1 default implementation.

This provider does NOT call any LLM. It is a thin wrapper that
forwards each method to the existing :mod:`bible`, :mod:`beats`,
:mod:`shots`, and :mod:`prompts` modules. We wrap rather than
reimplement so:

1. The single source of truth for "what the deterministic output
   looks like today" stays in one set of files.
2. Future LLM adapters can ship behind the same interface without
   touching the production schema or the pipeline orchestration.

If a future change needs to alter the deterministic output, edit the
underlying modules — :class:`DeterministicProvider` itself should
remain a pass-through.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .. import bible as _bible
from .. import beats as _beats
from .. import prompts as _prompts
from .. import shots as _shots
from ..schema import (
    CharacterBible,
    ImagePrompts,
    LocationBible,
    PropBible,
    ShotList,
    StoryBeat,
    VideoPrompts,
)
from .base import BaseProvider, ProviderHealth
from .registry import register


@register("deterministic")
class DeterministicProvider(BaseProvider):
    """Pass-through provider backed by the deterministic extractors."""

    # ``name`` is set by ``register``; declare a default for type
    # checkers that don't follow decorator side-effects.
    name: str = "deterministic"

    def build_bibles(
        self,
        script_text: str,
        *,
        script_id: str = "sample",
    ) -> Tuple[CharacterBible, LocationBible, PropBible]:
        return _bible.build_bibles(script_text, script_id=script_id)

    def extract_beats(
        self,
        script_path: Path,
        *,
        episode_id: str = "EP01",
    ) -> List[StoryBeat]:
        return _beats.extract_beats(script_path, episode_id=episode_id)

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
        return _shots.generate_shots(
            script_text=script_text,
            episode_id=episode_id,
            location_ids=location_ids,
            character_ids=character_ids,
            prop_ids=prop_ids,
            beats=beats,
            display_to_character_id=display_to_character_id,
        )

    def compile_image_prompts(
        self,
        shots: ShotList,
        characters: CharacterBible,
        locations: LocationBible,
        props: PropBible,
    ) -> ImagePrompts:
        return _prompts.compile_image_prompts(shots, characters, locations, props)

    def compile_video_prompts(
        self,
        shots: ShotList,
        characters: CharacterBible,
        locations: LocationBible,
        props: PropBible,
    ) -> VideoPrompts:
        return _prompts.compile_video_prompts(shots, characters, locations, props)

    def health_check(self) -> ProviderHealth:
        """Deterministic provider has no external dependencies.

        The pipeline can always fall back to deterministic safely;
        therefore the health check is a constant ``healthy=True``. This
        contract is load-bearing: future ``fallback_used=True`` audits
        rely on the deterministic provider being healthy by definition.
        """

        return ProviderHealth(
            name=self.name or "deterministic",
            healthy=True,
            reason="deterministic provider has no external dependencies",
            details={"external_calls": "none", "phase": "1"},
        )
