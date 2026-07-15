"""Tests for the LLM provider abstraction layer.

Phase-1 only ships the ``deterministic`` provider. These tests pin:

1. The default ``planner_provider`` is ``deterministic`` when the config
   file omits it.
2. Explicit ``planner_provider: "deterministic"`` keeps the existing
   artifact shape (validator passes with no errors).
3. Unknown providers are rejected at config-load time with a clear
   :class:`ConfigError`.
4. Swapping to a different registered provider (via an inline test
   provider) only changes the planning output; it does NOT relax the
   production executor / tool conventions.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest

from planner.env import PlannerConfig, load_config
from planner.exceptions import ConfigError
from planner.providers import (
    BaseProvider,
    DeterministicProvider,
    ProviderHealth,
    available_providers,
    get_provider,
    register,
)
from planner.providers import registry as registry_mod
from planner.providers.registry import _REGISTRY
from planner.schema import (
    AssetStatus,
    CharacterBible,
    ImagePrompts,
    LocationBible,
    PropBible,
    ShotList,
    StoryBeat,
    VideoPrompts,
)


@pytest.fixture(autouse=True)
def _scrub_planner_env(monkeypatch):
    """Make sure no PLANNER_* env from the host leaks into each test."""

    for key in list(os.environ):
        if key.startswith("PLANNER_"):
            monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture(autouse=True)
def _isolate_registry(monkeypatch):
    """Snapshot the registry around each test so test-only providers
    don't leak between tests."""

    snapshot = dict(_REGISTRY)
    yield
    _REGISTRY.clear()
    _REGISTRY.update(snapshot)


# ---- default-provider semantics ------------------------------------------


def test_default_provider_is_deterministic(project_root: Path) -> None:
    """When config has no ``planner_provider`` key, load_config must
    default to the deterministic stub so Phase-1 stays usable out of
    the box.
    """

    cfg = load_config("development", project_root=project_root)
    assert cfg.planner_provider == "deterministic"


def test_explicit_deterministic_provider_validates(
    project_root: Path, sample_script_path: Path, tmp_path: Path
) -> None:
    """A development run with ``planner_provider: deterministic`` must
    produce artifacts that pass the runner + validator without
    changing the on-disk shape.
    """

    from planner.pipeline import run as run_pipeline
    from planner.validate import validate_run

    cfg = load_config("development", project_root=project_root)
    assert cfg.planner_provider == "deterministic"

    out_dir = tmp_path / "dev_run"
    run_pipeline(script_path=sample_script_path, out_dir=out_dir, config=cfg)

    report = validate_run(out_dir, expected_env="development")
    assert report.ok, f"validation failed: {report.errors}"
    assert report.run_env == "development"


def test_unknown_provider_raises_config_error(project_root: Path) -> None:
    """Misconfigured provider name must be rejected at load time, not
    discovered later by an AttributeError in pipeline.run.
    """

    config_path = project_root / "config" / "development.json"
    raw = json.loads(config_path.read_text("utf-8"))
    raw["planner_provider"] = "definitely-not-a-real-provider"
    alt = project_root / "runs" / "_tmp_provider_config.json"
    alt.parent.mkdir(parents=True, exist_ok=True)
    alt.write_text(json.dumps(raw))

    try:
        with pytest.raises(ConfigError, match="Unknown planner_provider"):
            load_config("development", project_root=project_root, config_path=alt)
    finally:
        alt.unlink(missing_ok=True)


def test_unknown_provider_lists_available(project_root: Path) -> None:
    """Error message must be actionable — show what's registered."""

    config_path = project_root / "config" / "development.json"
    raw = json.loads(config_path.read_text("utf-8"))
    raw["planner_provider"] = "openai_xyz"
    alt = project_root / "runs" / "_alt_provider_config.json"
    alt.parent.mkdir(parents=True, exist_ok=True)
    alt.write_text(json.dumps(raw))
    try:
        with pytest.raises(ConfigError) as excinfo:
            load_config("development", project_root=project_root, config_path=alt)
        msg = str(excinfo.value)
        assert "deterministic" in msg, (
            f"error message should list available providers; got: {msg}"
        )
    finally:
        alt.unlink(missing_ok=True)


# ---- registry invariants --------------------------------------------------


def test_get_provider_returns_stateful_instance_with_correct_name() -> None:
    p = get_provider("deterministic")
    assert isinstance(p, DeterministicProvider)
    assert p.name == "deterministic"


def test_empty_provider_name_is_rejected() -> None:
    with pytest.raises(ConfigError, match="non-empty"):
        get_provider("")


def test_available_providers_lists_at_least_deterministic() -> None:
    avail = available_providers()
    assert "deterministic" in avail


def test_registry_rejects_duplicate_registration() -> None:
    """Two different classes trying to claim the same provider name
    must be rejected so the registry never silently shadows one plugin
    with another.
    """

    @register("dup_test_first")
    class _First(BaseProvider):
        name = "dup_test_first"

    class _Second(BaseProvider):
        name = "dup_test_second"

    with pytest.raises(RuntimeError, match="already registered"):
        register("dup_test_first")(_Second)


def test_register_requires_subclass_of_base_provider() -> None:
    class NotAProvider:
        pass

    with pytest.raises(TypeError, match="subclass BaseProvider"):
        register("bogus_for_test")(NotAProvider)


# ---- provider does not break production boundaries -----------------------


def test_provider_abstraction_keeps_production_tool_none(
    project_root: Path, sample_script_path: Path, tmp_path: Path
) -> None:
    """Swapping providers must not change production executor output.

    The planner stays tool-agnostic (``tool=None``) and status stays
    ``pending_manual_approval`` regardless of which provider produced
    the planning artifacts.
    """

    from planner.pipeline import run as run_pipeline

    cfg = load_config("production", project_root=project_root,
                      config_path=project_root / "config" / "production.example.json")
    assert cfg.planner_provider == "deterministic"
    assert cfg.executor_default_status == "pending_manual_approval"

    out_dir = tmp_path / "prod_run"
    run_pipeline(script_path=sample_script_path, out_dir=out_dir, config=cfg)

    tasks = json.loads((out_dir / "executor_tasks.json").read_text("utf-8"))
    assert tasks["tasks"], "expected at least one executor task"
    for task in tasks["tasks"]:
        assert task["tool"] is None, f"task {task['id']} hard-codes tool"
        assert task["status"] == "pending_manual_approval", (
            f"task {task['id']} status changed: {task['status']}"
        )


# ---- plugin-shaped custom provider still works ---------------------------


@register("echo_for_test")
class _EchoProvider(BaseProvider):
    """Minimal smoke provider. Keeps the deterministic pipeline intact
    but proves the registry accepts third-party subclasses.
    """

    def build_bibles(
        self, script_text, *, script_id="sample"
    ) -> Tuple[CharacterBible, LocationBible, PropBible]:
        # Reuse the deterministic work so the smoke is meaningful.
        return get_provider("deterministic").build_bibles(
            script_text, script_id=script_id
        )

    def extract_beats(self, script_path, *, episode_id="EP01"):
        return get_provider("deterministic").extract_beats(
            script_path, episode_id=episode_id
        )

    def generate_shots(self, **kwargs) -> ShotList:
        # Delegate via positional/keyword args since signature is fixed.
        return get_provider("deterministic").generate_shots(**kwargs)

    def compile_image_prompts(
        self, shots, characters, locations, props
    ) -> ImagePrompts:
        return get_provider("deterministic").compile_image_prompts(
            shots, characters, locations, props
        )

    def compile_video_prompts(
        self, shots, characters, locations, props
    ) -> VideoPrompts:
        return get_provider("deterministic").compile_video_prompts(
            shots, characters, locations, props
        )

    def health_check(self) -> ProviderHealth:
        # Smoke plugin reuses the deterministic backend; report
        # healthy so the test exercises the happy path.
        return ProviderHealth(
            name="echo_for_test",
            healthy=True,
            reason="smoke plugin delegates to deterministic",
        )

    def probe(self):  # type: ignore[override]
        """Smoke plugin has no remote endpoint; mirror the
        ``BaseProvider.probe`` default raise.

        Phase 3 P2 added ``probe()`` as an abstract method on
        :class:`BaseProvider`. The deterministic / skeleton adapters
        (real production code) keep the default raise so the CLI
        top-level handler can convert the
        ``NotImplementedError`` into a structured
        ``ProviderProbeError(reason="not_implemented")`` + exit ``1``.
        Test plugins that exercise the happy-path plumbing of
        ``get_provider`` should mirror this stance so that adding a
        new ``BaseProvider`` abstract method does not silently
        regress the registry contract.
        """
        raise NotImplementedError(
            "_EchoProvider.probe is a smoke-test stub; the Phase 3 P2 "
            "probe design lands ``probe()`` as opt-in only and this "
            "test plugin follows the deterministic / skeleton pattern."
        )


def test_registered_plugin_can_be_selected_via_config(
    project_root: Path, sample_script_path: Path, tmp_path: Path
) -> None:
    """A registered non-default provider selected via config must
    produce the same artifact shape (since this smoke reuses the
    deterministic backend).
    """

    from planner.pipeline import run as run_pipeline
    from planner.validate import validate_run

    # Build a temp config that picks our plugin provider.
    config_path = project_root / "config" / "development.json"
    raw = json.loads(config_path.read_text("utf-8"))
    raw["planner_provider"] = "echo_for_test"
    alt = project_root / "runs" / "_alt_plugin_config.json"
    alt.parent.mkdir(parents=True, exist_ok=True)
    alt.write_text(json.dumps(raw))

    try:
        cfg = load_config(
            "development", project_root=project_root, config_path=alt
        )
        assert cfg.planner_provider == "echo_for_test"

        out_dir = tmp_path / "plugin_run"
        run_pipeline(script_path=sample_script_path, out_dir=out_dir, config=cfg)
        report = validate_run(out_dir, expected_env="development")
        assert report.ok, f"plugin output failed validation: {report.errors}"
    finally:
        alt.unlink(missing_ok=True)
