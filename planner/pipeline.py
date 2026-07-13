"""Pipeline orchestrator.

Glues together parser, bible builder, beat extractor, shot planner,
prompt compiler and manifest builder. The CLI calls into :func:`run`
which writes the 8 core JSON artifacts (plus an ``executor_tasks.json``
skeleton) under the requested output directory.

The non-visual intelligence is delegated to a configurable provider
(:mod:`planner.providers`). The default ``deterministic`` provider is
a pass-through to the existing :mod:`bible`, :mod:`beats`,
:mod:`shots`, :mod:`prompts` modules; future LLM adapters slot into
the same interface without changing this file.

Provider health & fallback:

Every provider exposes :meth:`BaseProvider.health_check`. The pipeline
runs the check on the configured (``requested``) provider BEFORE
invoking any extraction step. If the check is healthy we proceed
normally. If the check fails we look at ``allow_provider_fallback``:

* ``development`` with ``allow_provider_fallback=True`` (default):
  swap to the ``deterministic`` provider and record the swap in
  ``run_summary.json`` under ``fallback_used`` / ``fallback_reason`` /
  ``effective_provider``. This keeps development unblocked while
  making the substitution auditable.

* ``production`` (or development with fallback disabled): raise
  :class:`ProviderUnavailableError` so the operator sees a loud
  failure instead of silently running a different provider.

Fallback only changes the **planning** provider. It must not change
the executor boundary (production is still ``pending_manual_approval``
with ``tool=None``); that guarantee is enforced separately by
:func:`planner.env._enforce_boundaries` and
:func:`planner.manifest.build_executor_tasks`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .env import PlannerConfig
from .exceptions import (
    BrokenReferenceError,
    ConfigError,
    EnvironmentBoundaryError,
    ProviderUnavailableError,
    ScriptReadError,
)
from .io_utils import read_text, write_json
from .manifest import build_executor_tasks, build_manifest
from .model_config import ModelProviderConfig, resolve_runtime_settings
from .parser import parse_script
from .providers import BaseProvider, ProviderHealth, get_provider
from .schema import AssetStatus


FALLBACK_PROVIDER_NAME = "deterministic"


@dataclass
class RunResult:
    run_dir: Path
    artifacts: Dict[str, Path]
    character_count: int
    location_count: int
    prop_count: int
    shot_count: int
    requested_provider: str
    effective_provider: str
    fallback_used: bool
    fallback_reason: Optional[str]
    provider_health: Dict[str, dict]
    # v1.0 P1-1: audit the runtime settings the *requested* provider was
    # given (model / base_url / api_key_env / enable_real_model_calls).
    # ``None`` for deterministic runs or when no model_config was
    # supplied. Never contains the API key value - only the env var
    # name. Lets a reviewer tell "openai_compatible was configured with
    # model=gpt-4o-mini but fell back to deterministic" from a clean
    # deterministic run.
    provider_runtime: Optional[dict] = None


def _episode_id_from_path(script_path: Path) -> str:
    stem = script_path.stem
    m = re.match(r"^(EP\d+)", stem, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    return "EP01"


def _health_to_dict(h: ProviderHealth) -> dict:
    return {
        "name": h.name,
        "healthy": h.healthy,
        "reason": h.reason,
        "details": dict(h.details),
    }


def _select_provider(
    config: PlannerConfig,
    model_config: Optional[ModelProviderConfig] = None,
) -> Tuple[BaseProvider, str, str, bool, Optional[str], Dict[str, dict], Optional[dict]]:
    """Resolve the provider that should actually run this pipeline.

    Returns a tuple of (provider, requested_name, effective_name,
    fallback_used, fallback_reason, provider_health_dict,
    provider_runtime_audit).

    When ``model_config`` is supplied and the requested provider is a
    real-model provider (``openai_compatible`` / ``openai`` /
    ``anthropic``), the matching :class:`ProviderRuntimeSettings` is
    resolved and injected into the provider instance via
    :func:`registry.get_provider`. Deterministic ignores settings;
    skeleton providers accept and ignore them (their ``health_check``
    still gates on env/SDK, not settings).

    The ``provider_runtime_audit`` dict records the *requested*
    provider's runtime settings (model / base_url / api_key_env /
    enable_real_model_calls) so a reviewer can see what was configured
    even when the run fell back to deterministic. It never contains
    the API key value.

    Raises:
        ProviderUnavailableError: if the requested provider is
            unhealthy and the environment is fail-closed (production
            or development with ``allow_provider_fallback=False``).
    """

    requested_name = config.planner_provider

    # Resolve runtime settings for real-model providers. Deterministic
    # has no remote endpoint; skeleton providers ignore settings but
    # still accept them so get_provider stays uniform.
    settings = None
    provider_runtime: Optional[dict] = None
    if requested_name != FALLBACK_PROVIDER_NAME and model_config is not None:
        try:
            settings = resolve_runtime_settings(model_config, requested_name)
        except ValueError:
            # Provider name not in model_config's known sections
            # (shouldn't happen for the four registered providers, but
            # be defensive). Fall through with settings=None; the
            # provider's own health_check handles the rest.
            settings = None
        if settings is not None:
            provider_runtime = {
                "model": settings.model,
                "base_url": settings.base_url,
                "api_key_env": settings.api_key_env,
                "enable_real_model_calls": settings.enable_real_model_calls,
            }

    requested_provider = get_provider(requested_name, settings=settings)
    requested_health = requested_provider.health_check()

    provider_health: Dict[str, dict] = {
        requested_name: _health_to_dict(requested_health)
    }

    if requested_health.healthy:
        return (
            requested_provider,
            requested_name,
            requested_name,
            False,
            None,
            provider_health,
            provider_runtime,
        )

    # Requested provider is unhealthy. Decide whether to fall back.
    reason = requested_health.reason or "health_check returned healthy=False"

    if not config.allow_provider_fallback:
        # Fail closed: production, or development that has explicitly
        # disabled fallback. The operator must fix the underlying issue
        # rather than have the planner silently swap providers.
        raise ProviderUnavailableError(
            f"Provider {requested_name!r} failed health check "
            f"({reason!r}) and allow_provider_fallback is "
            f"{'False' if config.is_production else 'disabled'}; "
            f"refusing to fall back. Fix the provider configuration or "
            f"set allow_provider_fallback=true (development only) to "
            f"auto-fall-back to deterministic."
        )

    if requested_name == FALLBACK_PROVIDER_NAME:
        # The fallback target itself is unhealthy — recursive failure,
        # so do not silently loop. Surface the error.
        raise ProviderUnavailableError(
            f"Fallback provider {FALLBACK_PROVIDER_NAME!r} also failed "
            f"health check ({reason!r}); cannot complete the run."
        )

    fallback_provider = get_provider(FALLBACK_PROVIDER_NAME)
    fallback_health = fallback_provider.health_check()
    provider_health[FALLBACK_PROVIDER_NAME] = _health_to_dict(fallback_health)

    if not fallback_health.healthy:
        # Deterministic should always be healthy; if it isn't the
        # situation is severe enough to deserve a loud failure.
        raise ProviderUnavailableError(
            f"Requested provider {requested_name!r} is unhealthy "
            f"({reason!r}); fallback {FALLBACK_PROVIDER_NAME!r} also "
            f"reports unhealthy ({fallback_health.reason!r})."
        )

    return (
        fallback_provider,
        requested_name,
        FALLBACK_PROVIDER_NAME,
        True,
        reason,
        provider_health,
        # Keep the requested provider's runtime audit even when we
        # fell back - the reviewer needs to see what was configured.
        provider_runtime,
    )


def run(
    *,
    script_path: Path,
    out_dir: Path,
    config: PlannerConfig,
    model_config: Optional[ModelProviderConfig] = None,
) -> RunResult:
    """Execute the full Phase-1 pipeline and write artifacts.

    Args:
        script_path: Input script ``.txt``.
        out_dir: Where to write the 11 JSON artifacts.
        config: Resolved environment config (dev/prod boundaries).
        model_config: Optional v1.0 model configuration. When the
            configured ``planner_provider`` is a real-model provider
            (``openai_compatible`` / ``openai`` / ``anthropic``) the
            matching :class:`ProviderRuntimeSettings` is injected into
            the provider instance. Deterministic ignores it. When
            omitted, real-model providers fall back to defaults (which
            report ``healthy=False`` for ``enable_real_model_calls=False``
            and trigger the existing fail-closed / fallback path).

    Raises:
        EnvironmentBoundaryError: if production would overwrite an
            existing run directory.
        ScriptReadError: if the script cannot be read.
        BrokenReferenceError: if the planner produced inconsistent
            references (caught early so the user sees the issue).
        ProviderUnavailableError: if the configured provider fails its
            health check and the environment is fail-closed.
    """

    if not script_path.exists():
        raise ScriptReadError(f"Script not found: {script_path}")

    # Order matters: preflight checks before any directory I/O.
    #
    # 1. Resolve the provider via ``_select_provider`` FIRST. Fail-closed
    #    semantics demand that an unhealthy provider raises BEFORE we
    #    create ``out_dir``; otherwise a failed production run would
    #    leave behind an empty run directory that the production
    #    overwrite guard would then refuse to reuse, forcing a manual
    #    cleanup. This both honors ``fail-closed leaves no residue``
    #    and keeps the next invocation clean.
    # 2. THEN evaluate the overwrite guard (which inspects
    #    ``out_dir.exists()`` but does not create the directory yet).
    # 3. THEN ``mkdir`` + write artifacts.
    provider, requested_provider_name, effective_provider_name, fallback_used, fallback_reason, provider_health, provider_runtime = _select_provider(config, model_config=model_config)

    if out_dir.exists():
        if config.is_production and not config.allow_overwrite_runs:
            raise EnvironmentBoundaryError(
                f"Production refuses to overwrite existing run directory: "
                f"{out_dir}. Remove it manually or set "
                f"allow_overwrite_runs=true."
            )
        if not config.is_production and not config.allow_overwrite_runs:
            # Local safety net: never silently destroy previous work.
            raise EnvironmentBoundaryError(
                f"Refusing to overwrite existing run directory: {out_dir}. "
                f"Remove it manually or pass --force."
            )

    out_dir.mkdir(parents=True, exist_ok=True)

    script_text = read_text(script_path)
    episode_id = _episode_id_from_path(script_path)

    # 0. Script parse. Always run the deterministic parser here so the
    # on-disk ``script_parse.json`` is stable regardless of which
    # provider produced the bibles / shots. Downstream providers can
    # still re-parse internally (e.g. for LLM context) but the
    # canonical artifact must match the script byte-for-byte so
    # validators can cross-check source spans.
    script_parse = parse_script(script_path, script_id=episode_id)

    # 1. Build bibles.
    characters, locations, props = provider.build_bibles(
        script_text, script_id=episode_id
    )

    # 2. Beats.
    beats = provider.extract_beats(script_path, episode_id=episode_id)

    # Build display-name → canonical id map for characters so that the
    # shot planner reuses seeded ids rather than re-slugifying display
    # names like "林夏" into a different id from "lin_xia".
    display_to_id: Dict[str, str] = {}
    for c in characters.characters:
        display_to_id[c.name] = c.id
        display_to_id[c.id] = c.id

    # 3. Shot list.
    shots = provider.generate_shots(
        script_text=script_text,
        episode_id=episode_id,
        location_ids=[loc.id for loc in locations.locations],
        character_ids=[c.id for c in characters.characters],
        prop_ids=[p.id for p in props.props],
        beats=beats,
        display_to_character_id=display_to_id,
    )

    # 4. Reference integrity check.
    _check_references(shots, characters, locations, props)

    # 5. Prompts.
    image_prompts = provider.compile_image_prompts(shots, characters, locations, props)
    video_prompts = provider.compile_video_prompts(shots, characters, locations, props)

    # 6. Asset manifest + executor tasks skeleton.
    executor_status = _status_from_config(config)
    manifest = build_manifest(shots, default_status=executor_status)

    artifacts: Dict[str, Path] = {}
    artifacts["script_parse"] = out_dir / "script_parse.json"
    artifacts["character_bible"] = out_dir / "character_bible.json"
    artifacts["location_bible"] = out_dir / "location_bible.json"
    artifacts["prop_bible"] = out_dir / "prop_bible.json"
    artifacts["story_beats"] = out_dir / "story_beats.json"
    artifacts["shot_list"] = out_dir / "shot_list.json"
    artifacts["image_prompts"] = out_dir / "image_prompts.json"
    artifacts["video_prompts"] = out_dir / "video_prompts.json"
    artifacts["asset_manifest"] = out_dir / "asset_manifest.json"

    write_json(artifacts["script_parse"], script_parse.model_dump(mode="json"))
    write_json(artifacts["character_bible"], characters.model_dump(mode="json"))
    write_json(artifacts["location_bible"], locations.model_dump(mode="json"))
    write_json(artifacts["prop_bible"], props.model_dump(mode="json"))
    write_json(
        artifacts["story_beats"],
        {"beats": [b.model_dump(mode="json") for b in beats]},
    )
    write_json(artifacts["shot_list"], shots.model_dump(mode="json"))
    write_json(artifacts["image_prompts"], image_prompts.model_dump(mode="json"))
    write_json(artifacts["video_prompts"], video_prompts.model_dump(mode="json"))
    write_json(artifacts["asset_manifest"], manifest.model_dump(mode="json"))

    # 7. Executor tasks (skeleton only).
    executor_tasks = build_executor_tasks(
        shots,
        image_prompts_path="image_prompts.json",
        manifest_path="asset_manifest.json",
        default_status=executor_status,
    )
    artifacts["executor_tasks"] = out_dir / "executor_tasks.json"
    write_json(artifacts["executor_tasks"], executor_tasks.model_dump(mode="json"))

    # 8. Run summary.
    # Provider audit fields:
    # - ``requested_provider`` / ``effective_provider``: name the
    #   requested and actually-used providers so downstream tools can
    #   tell deterministic runs from LLM runs, and so fallback swaps
    #   are visible in audit.
    # - ``fallback_used`` / ``fallback_reason``: a bool flag and a
    #   nullable human-readable reason. Production runs must have
    #   ``fallback_used=False``; validate.py enforces this.
    # - ``provider_health``: dict of provider name -> health record, so
    #   even healthy-only runs carry the deterministic provider's
    #   health for audit symmetry.
    # - ``provider_runtime`` (v1.0 P1-1): the *requested* provider's
    #   runtime settings (model / base_url / api_key_env /
    #   enable_real_model_calls). ``None`` for deterministic runs or
    #   when no model_config was supplied. Never contains the API key
    #   value - only the env var name. Lets a reviewer distinguish
    #   "openai_compatible configured with model=gpt-4o-mini but fell
    #   back" from a clean deterministic run.
    # - ``planner_provider``: kept for backward compatibility with
    #   pre-fallback tooling. New code should prefer
    #   ``requested_provider``.
    summary = {
        "run_id": out_dir.name,
        "env": config.env,
        "script": str(script_path),
        "episode_id": episode_id,
        "planner_provider": requested_provider_name,
        "requested_provider": requested_provider_name,
        "effective_provider": effective_provider_name,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "provider_health": provider_health,
        "provider_runtime": provider_runtime,
        "counts": {
            "characters": len(characters.characters),
            "locations": len(locations.locations),
            "props": len(props.props),
            "beats": len(beats),
            "shots": len(shots.shots),
        },
        "executor_status": executor_status.value,
        "artifacts": {name: str(path) for name, path in artifacts.items()},
    }
    write_json(out_dir / "run_summary.json", summary)

    return RunResult(
        run_dir=out_dir,
        artifacts=artifacts,
        character_count=len(characters.characters),
        location_count=len(locations.locations),
        prop_count=len(props.props),
        shot_count=len(shots.shots),
        requested_provider=requested_provider_name,
        effective_provider=effective_provider_name,
        fallback_used=fallback_used,
        fallback_reason=fallback_reason,
        provider_health=provider_health,
        provider_runtime=provider_runtime,
    )


def _status_from_config(config: PlannerConfig) -> AssetStatus:
    try:
        return AssetStatus(config.executor_default_status)
    except ValueError as exc:
        raise ConfigError(
            f"Unknown executor_default_status: {config.executor_default_status}"
        ) from exc


def _check_references(shots, characters, locations, props) -> None:
    char_ids = {c.id for c in characters.characters}
    loc_ids = {l.id for l in locations.locations}
    prop_ids = {p.id for p in props.props}

    broken: List[str] = []
    for shot in shots.shots:
        if shot.location_id not in loc_ids:
            broken.append(
                f"{shot.id}: location_id {shot.location_id!r} not in bible"
            )
        for cid in shot.character_ids:
            if cid not in char_ids:
                broken.append(
                    f"{shot.id}: character_id {cid!r} not in bible"
                )
        for pid in shot.prop_ids:
            if pid not in prop_ids:
                broken.append(f"{shot.id}: prop_id {pid!r} not in bible")

    if broken:
        raise BrokenReferenceError(
            "Reference integrity check failed:\n  - " + "\n  - ".join(broken)
        )


def load_run(run_dir: Path) -> Dict[str, dict]:
    """Read all artifacts under ``run_dir`` into memory."""

    files = [
        "script_parse.json",
        "character_bible.json",
        "location_bible.json",
        "prop_bible.json",
        "story_beats.json",
        "shot_list.json",
        "image_prompts.json",
        "video_prompts.json",
        "asset_manifest.json",
    ]
    data: Dict[str, dict] = {}
    for name in files:
        path = run_dir / name
        if not path.exists():
            raise ScriptReadError(f"Missing artifact in run: {path}")
        data[name] = json.loads(path.read_text(encoding="utf-8"))
    return data