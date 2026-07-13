"""Asset manifest + executor task skeleton builder."""

from __future__ import annotations

from typing import List, Optional

from .schema import (
    AssetEntry,
    AssetManifest,
    AssetStatus,
    ExecutorTask,
    ExecutorTasks,
    ShotAssets,
    ShotList,
)


def build_manifest(
    shots: ShotList, default_status: AssetStatus = AssetStatus.PENDING
) -> AssetManifest:
    assets = []
    for shot in shots.shots:
        assets.append(
            ShotAssets(
                shot_id=shot.id,
                image=AssetEntry(status=default_status),
                video=AssetEntry(status=default_status),
            )
        )
    return AssetManifest(assets=assets)


def build_executor_tasks(
    shots: ShotList,
    image_prompts_path: str,
    manifest_path: str,
    *,
    default_status: AssetStatus = AssetStatus.PENDING_MANUAL_APPROVAL,
    tool: Optional[str] = None,
) -> ExecutorTasks:
    """Build the executor task skeleton.

    The ``tool`` field is intentionally left ``None`` by default. The
    Phase-1 planner must not hard-code any specific executor (Flowith,
    libTV, ...). A future executor adapter is responsible for choosing
    a concrete ``tool`` value before tasks are submitted.
    """

    tasks: List[ExecutorTask] = []
    for shot in shots.shots:
        tasks.append(
            ExecutorTask(
                id=f"task_{shot.id}_image_v001",
                shot_id=shot.id,
                kind="image_generation",
                tool=tool,
                status=default_status,
                input_prompt_ref=f"{image_prompts_path}#{shot.id}",
                output_asset_ref=f"{manifest_path}#{shot.id}.image",
            )
        )
        tasks.append(
            ExecutorTask(
                id=f"task_{shot.id}_video_v001",
                shot_id=shot.id,
                kind="video_generation",
                tool=tool,
                status=default_status,
                input_prompt_ref=f"{image_prompts_path}#{shot.id}",
                output_asset_ref=f"{manifest_path}#{shot.id}.video",
            )
        )
    return ExecutorTasks(tasks=tasks)