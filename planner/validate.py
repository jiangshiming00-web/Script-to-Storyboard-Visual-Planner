"""Reference / continuity validation for a completed run."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .exceptions import BrokenReferenceError, ScriptReadError
from .io_utils import read_json
from .pipeline import load_run


@dataclass
class ValidationReport:
    ok: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)
    run_env: Optional[str] = None
    env_mismatch: bool = False
    planner_provider: Optional[str] = None
    requested_provider: Optional[str] = None
    effective_provider: Optional[str] = None
    fallback_used: Optional[bool] = None
    fallback_reason: Optional[str] = None


def validate_run(run_dir: Path, *, expected_env: Optional[str] = None) -> ValidationReport:
    data = load_run(run_dir)
    errors: List[str] = []
    warnings: List[str] = []
    run_env: Optional[str] = None
    env_mismatch = False
    planner_provider: Optional[str] = None
    requested_provider: Optional[str] = None
    effective_provider: Optional[str] = None
    fallback_used: Optional[bool] = None
    fallback_reason: Optional[str] = None

    summary_path = run_dir / "run_summary.json"
    if summary_path.exists():
        try:
            summary = read_json(summary_path)
        except Exception:  # pragma: no cover - defensive
            summary = {}
        run_env = summary.get("env")
        # ``planner_provider`` predates the fallback design; keep reading
        # it for backward compatibility with pre-fallback runs.
        planner_provider = summary.get("planner_provider")
        # New runs (post-fallback) carry the explicit audit fields. If
        # they are present, prefer them over the legacy alias.
        requested_provider = (
            summary.get("requested_provider") or planner_provider
        )
        effective_provider = summary.get("effective_provider")
        fallback_used = summary.get("fallback_used")
        fallback_reason = summary.get("fallback_reason")
        if expected_env is not None and run_env is not None and run_env != expected_env:
            env_mismatch = True
            warnings.append(
                f"Run was produced under env={run_env!r} but "
                f"validate invoked with --env {expected_env!r}"
            )
        if planner_provider is None and requested_provider is None:
            warnings.append(
                "run_summary.json missing planner_provider / "
                "requested_provider; the run was produced before "
                "provider tracking was added."
            )
        # Production runs must never fall back; treat that as an error
        # so operators notice the silent swap.
        if run_env == "production" and fallback_used:
            errors.append(
                f"Production run used provider fallback "
                f"({requested_provider!r} -> {effective_provider!r}, "
                f"reason={fallback_reason!r}); production must remain "
                f"fail-closed and never silently swap providers."
            )
        # Schema sanity: new runs must record fallback_used and
        # fallback_reason explicitly so audits can rely on the fields.
        if (
            requested_provider is not None
            and effective_provider is None
        ):
            warnings.append(
                "run_summary.json missing effective_provider; cannot "
                "audit fallback. The run predates the fallback design."
            )
        if (
            requested_provider is not None
            and effective_provider is not None
            and fallback_used is None
        ):
            warnings.append(
                "run_summary.json missing fallback_used flag; the run "
                "predates the fallback design."
            )

    # script_parse.json must reference the same source as run_summary.
    script_parse = data["script_parse.json"]
    parse_source = script_parse.get("source_path")
    if summary_path.exists() and parse_source and "script" in summary:
        if parse_source != summary["script"]:
            errors.append(
                f"script_parse.json source_path ({parse_source!r}) does not "
                f"match run_summary.script ({summary['script']!r}); this run "
                f"appears to mix artifacts from different scripts."
            )
    parse_block_count = len(script_parse.get("blocks", []))
    if parse_block_count == 0:
        warnings.append("script_parse.json contains zero blocks")

    char_index = {
        c["id"]: c for c in data["character_bible.json"]["characters"]
    }
    loc_index = {
        loc["id"]: loc for loc in data["location_bible.json"]["locations"]
    }
    prop_index = {p["id"]: p for p in data["prop_bible.json"]["props"]}

    shots = data["shot_list.json"]["shots"]
    image_prompts = {p["shot_id"]: p for p in data["image_prompts.json"]["image_prompts"]}
    video_prompts = {p["shot_id"]: p for p in data["video_prompts.json"]["video_prompts"]}

    stats = {
        "script_blocks": parse_block_count,
        "shots": len(shots),
        "characters": len(char_index),
        "locations": len(loc_index),
        "props": len(prop_index),
        "image_prompts": len(image_prompts),
        "video_prompts": len(video_prompts),
    }

    for shot in shots:
        sid = shot["id"]
        if shot["location_id"] not in loc_index:
            errors.append(f"{sid}: unknown location_id {shot['location_id']!r}")
        for cid in shot.get("character_ids", []):
            if cid not in char_index:
                errors.append(f"{sid}: unknown character_id {cid!r}")
        for pid in shot.get("prop_ids", []):
            if pid not in prop_index:
                errors.append(f"{sid}: unknown prop_id {pid!r}")

        ip = image_prompts.get(sid)
        if not ip:
            errors.append(f"{sid}: missing image_prompt")
        else:
            prompt = ip["prompt"]
            if shot.get("character_ids"):
                names = [char_index[c]["name"] for c in shot["character_ids"] if c in char_index]
                if names and not any(name in prompt for name in names):
                    warnings.append(f"{sid}: image prompt missing character names")
            if shot.get("location_id") in loc_index:
                loc_name = loc_index[shot["location_id"]]["name"]
                if loc_name and loc_name not in prompt:
                    warnings.append(f"{sid}: image prompt missing location text")
            if shot.get("prop_ids"):
                for pid in shot["prop_ids"]:
                    if pid in prop_index and prop_index[pid]["name"] not in prompt:
                        warnings.append(
                            f"{sid}: image prompt missing prop text for {pid}"
                        )
            if "镜头" not in prompt and "shot" not in prompt.lower():
                warnings.append(f"{sid}: image prompt missing cinematography hint")

        vp = video_prompts.get(sid)
        if not vp:
            errors.append(f"{sid}: missing video_prompt")

    manifest_shot_ids = {
        a["shot_id"] for a in data["asset_manifest.json"]["assets"]
    }
    for shot in shots:
        if shot["id"] not in manifest_shot_ids:
            errors.append(
                f"{shot['id']}: missing from asset_manifest.json"
            )

    return ValidationReport(
        ok=not errors,
        errors=errors,
        warnings=warnings,
        stats=stats,
        run_env=run_env,
        env_mismatch=env_mismatch,
        planner_provider=planner_provider,
        requested_provider=requested_provider,
        effective_provider=effective_provider,
        fallback_used=fallback_used,
        fallback_reason=fallback_reason,
    )


def raise_on_errors(report: ValidationReport) -> None:
    if not report.ok:
        raise BrokenReferenceError(
            "Validation failed:\n  - " + "\n  - ".join(report.errors)
        )