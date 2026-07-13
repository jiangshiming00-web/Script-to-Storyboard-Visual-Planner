"""Pydantic models implementing the data contracts.

These models mirror ``specs/DATA_CONTRACTS.md``. Field names are stable
public API; additions must remain backward-compatible.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class InferenceLevel(str, Enum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    HUMAN_CONFIRMED = "human_confirmed"


class SourceSpan(BaseModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, v: int, info) -> int:  # type: ignore[no-untyped-def]
        start = info.data.get("start", 0)
        if v < start:
            raise ValueError("source_span.end must be >= source_span.start")
        return v


class ConfidenceMixin(BaseModel):
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    inference_level: InferenceLevel = InferenceLevel.EXPLICIT


# ---------- Script parse ----------


class ScriptBlockKind(str, Enum):
    SCENE = "scene"
    ACTION = "action"
    DIALOGUE = "dialogue"
    BEAT = "beat"
    UNKNOWN = "unknown"


class ScriptBlock(BaseModel):
    kind: ScriptBlockKind
    text: str
    character: Optional[str] = None
    span: SourceSpan


class ScriptParse(BaseModel):
    script_id: str
    source_path: str
    blocks: List[ScriptBlock]


# ---------- Story beats ----------


class StoryBeat(BaseModel):
    id: str
    label: str
    summary: str
    span: SourceSpan
    confidence: float = 1.0
    inference_level: InferenceLevel = InferenceLevel.EXPLICIT


# ---------- Character bible ----------


class CharacterRelationship(BaseModel):
    target_character_id: str
    relationship: str


class Character(ConfidenceMixin):
    id: str
    name: str
    role: Optional[str] = None
    age: Optional[str] = None
    identity: Optional[str] = None
    appearance: str
    wardrobe: Optional[str] = None
    temperament: Optional[str] = None
    relationships: List[CharacterRelationship] = Field(default_factory=list)
    positive_prompt: str
    negative_prompt: str
    continuity_rules: List[str] = Field(default_factory=list)
    reference_assets: List[str] = Field(default_factory=list)
    source_span: Optional[SourceSpan] = None


class CharacterBible(BaseModel):
    characters: List[Character]


# ---------- Location bible ----------


class LocationType(str, Enum):
    INTERIOR = "interior"
    EXTERIOR = "exterior"
    OTHER = "other"


class Location(ConfidenceMixin):
    id: str
    name: str
    type: LocationType = LocationType.OTHER
    time_of_day: Optional[str] = None
    space_layout: str
    lighting: Optional[str] = None
    mood: Optional[str] = None
    positive_prompt: str
    negative_prompt: str
    continuity_rules: List[str] = Field(default_factory=list)
    source_span: Optional[SourceSpan] = None


class LocationBible(BaseModel):
    locations: List[Location]


# ---------- Prop bible ----------


class Prop(ConfidenceMixin):
    id: str
    name: str
    visual: str
    story_function: Optional[str] = None
    positive_prompt: str
    negative_prompt: str
    continuity_rules: List[str] = Field(default_factory=list)
    source_span: Optional[SourceSpan] = None


class PropBible(BaseModel):
    props: List[Prop]


# ---------- Shot list ----------


class ShotSize(str, Enum):
    EXTREME_WIDE = "extreme-wide"
    WIDE = "wide"
    MEDIUM = "medium"
    MEDIUM_CLOSE = "medium-close-up"
    CLOSE_UP = "close-up"
    EXTREME_CLOSE_UP = "extreme-close-up"


class Shot(BaseModel):
    id: str
    scene_id: str
    location_id: str
    character_ids: List[str] = Field(default_factory=list)
    prop_ids: List[str] = Field(default_factory=list)
    beat_id: Optional[str] = None
    shot_size: ShotSize
    camera_angle: str
    composition: str
    action: str
    emotion: str
    duration_sec: int = Field(ge=1, le=60, default=4)
    continuity_notes: List[str] = Field(default_factory=list)


class ShotList(BaseModel):
    shots: List[Shot]


# ---------- Prompts ----------


class ImagePrompt(BaseModel):
    shot_id: str
    prompt: str
    negative_prompt: str
    aspect_ratio: str = "16:9"
    style_tags: List[str] = Field(default_factory=list)


class ImagePrompts(BaseModel):
    image_prompts: List[ImagePrompt]


class VideoPrompt(BaseModel):
    shot_id: str
    prompt: str
    motion: str
    duration_sec: int = Field(ge=1, le=60, default=4)
    camera: str
    avoid: str


class VideoPrompts(BaseModel):
    video_prompts: List[VideoPrompt]


# ---------- Asset manifest ----------


class AssetStatus(str, Enum):
    PENDING = "pending"
    PENDING_MANUAL_APPROVAL = "pending_manual_approval"
    SUBMITTED = "submitted"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    APPROVED = "approved"
    REJECTED = "rejected"
    SKIPPED = "skipped"


class AssetEntry(BaseModel):
    status: AssetStatus = AssetStatus.PENDING
    local_path: Optional[str] = None
    tool: Optional[str] = None
    task_id: Optional[str] = None
    approved: bool = False


class ShotAssets(BaseModel):
    shot_id: str
    image: AssetEntry = Field(default_factory=AssetEntry)
    video: AssetEntry = Field(default_factory=AssetEntry)


class AssetManifest(BaseModel):
    assets: List[ShotAssets]


# ---------- Executor tasks (skeleton, not used in Phase 1 core) ----------


class ExecutorTask(BaseModel):
    id: str
    shot_id: str
    kind: str
    tool: Optional[str] = None
    status: AssetStatus = AssetStatus.PENDING_MANUAL_APPROVAL
    input_prompt_ref: str
    output_asset_ref: Optional[str] = None


class ExecutorTasks(BaseModel):
    tasks: List[ExecutorTask]


# ---------- Batch summary (Phase Core-1) ----------


class EpisodeRunSummary(BaseModel):
    """Per-episode record emitted by ``planner batch`` into
    ``batch_summary.json``.

    Carries the audit fields the GUI / downstream tooling already
    relies on, plus validation status so a batch operator can see at
    a glance which episodes need re-work.
    """

    run_id: str
    episode_id: str
    run_dir: str
    status: str  # "done" | "failed"
    script_path: str
    started_at: str
    finished_at: Optional[str] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    counts: dict = Field(default_factory=dict)
    requested_provider: Optional[str] = None
    effective_provider: Optional[str] = None
    fallback_used: Optional[bool] = None
    fallback_reason: Optional[str] = None
    # P2 fix: surface the full per-provider health snapshot so the
    # GUI can render the same audit card the single-run endpoint
    # shows. Shape mirrors ``run_summary.provider_health``
    # (``Dict[str, ProviderHealth]`` serialized as plain dict).
    provider_health: Optional[dict] = None
    validation_ok: Optional[bool] = None
    validation_errors: int = 0
    validation_warnings: int = 0


class BatchSummary(BaseModel):
    """Top-level summary written by ``planner batch``.

    Lives next to per-episode subdirs in the ``--out`` directory.
    Always includes all episodes the operator requested — failures
    are recorded inline, not silently skipped, so the contract is
    "you see everything that was attempted".
    """

    batch_id: str
    started_at: str
    finished_at: Optional[str] = None
    env: str
    scripts_dir: str
    episodes: List[EpisodeRunSummary]
    totals: dict = Field(default_factory=dict)