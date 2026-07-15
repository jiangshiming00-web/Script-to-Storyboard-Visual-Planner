"""Tests for provider health check & fallback design.

These tests pin the Phase-1 contract for ``BaseProvider.health_check``
and the pipeline's fallback policy:

1. The deterministic provider always reports healthy.
2. Unknown providers are still rejected at config-load time.
3. In development, an unhealthy requested provider falls back to
   ``deterministic`` and the swap is recorded in ``run_summary``.
4. In production, an unhealthy requested provider is a loud failure
   (``ProviderUnavailableError``); no silent fallback is permitted.
5. ``allow_provider_fallback`` is a hard-pinned production key:
   setting it to ``true`` (config or env var) in production must raise
   ``ConfigError`` at config-load time.
6. The fallback path must preserve ``script_parse.json`` shape,
   reference integrity, schema validation, and the production
   executor boundaries (``pending_manual_approval`` + ``tool=None``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Tuple

import pytest

from planner.env import PlannerConfig, load_config
from planner.exceptions import ConfigError, ProviderUnavailableError
from planner.pipeline import run as run_pipeline
from planner.providers import (
    BaseProvider,
    DeterministicProvider,
    ProviderHealth,
    get_provider,
    register,
)
from planner.providers.registry import _REGISTRY
from planner.providers import unregister
from planner.schema import (
    CharacterBible,
    ImagePrompts,
    LocationBible,
    PropBible,
    ShotList,
    StoryBeat,
    VideoPrompts,
)
from planner.validate import validate_run


# ---- shared fixtures ----------------------------------------------------


@pytest.fixture(autouse=True)
def _scrub_planner_env(monkeypatch):
    """Drop any PLANNER_* env from the host so each test runs from a
    clean slate. The fallback config-load tests will explicitly opt in
    to individual env vars as needed.
    """

    for key in list(os.environ):
        if key.startswith("PLANNER_"):
            monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Snapshot the provider registry around each test. Stubs registered
    in one test must not leak into the next test, even when the test
    fails before its teardown hook.
    """

    snapshot = dict(_REGISTRY)
    yield
    _REGISTRY.clear()
    _REGISTRY.update(snapshot)


# ---- unhealthy stub ----------------------------------------------------


class _UnhealthyStubProvider(BaseProvider):
    """Provider whose ``health_check`` always returns unhealthy.

    The five extraction methods forward to ``deterministic`` so the
    pipeline can still run end-to-end after the fallback swap; this
    proves the fallback path produces the same artifacts, not just a
    no-op.
    """

    def __init__(self, settings=None, reason: str = "stub_forced_unhealthy") -> None:
        self._reason = reason

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            name="unhealthy_stub",
            healthy=False,
            reason=self._reason,
            details={"stub": "true"},
        )

    def probe(self):  # type: ignore[override]
        """Stub plugin has no remote endpoint; mirror the
        ``BaseProvider.probe`` default raise so ``get_provider`` can
        instantiate this class for the swap-and-audit exercise.

        See :class:`tests.test_providers._EchoProvider.probe` for the
        rationale — adding a new abstract method to
        :class:`BaseProvider` must not silently regress the
        ``registry accepts third-party subclasses`` happy path.
        """
        raise NotImplementedError(
            "_UnhealthyStubProvider.probe is a smoke-test stub; "
            "deterministic / skeleton adapters keep the default raise."
        )

    def build_bibles(self, script_text, *, script_id="sample"):
        return get_provider("deterministic").build_bibles(
            script_text, script_id=script_id
        )

    def extract_beats(self, script_path, *, episode_id="EP01"):
        return get_provider("deterministic").extract_beats(
            script_path, episode_id=episode_id
        )

    def generate_shots(self, **kwargs) -> ShotList:
        return get_provider("deterministic").generate_shots(**kwargs)

    def compile_image_prompts(self, shots, characters, locations, props):
        return get_provider("deterministic").compile_image_prompts(
            shots, characters, locations, props
        )

    def compile_video_prompts(self, shots, characters, locations, props):
        return get_provider("deterministic").compile_video_prompts(
            shots, characters, locations, props
        )


# Use a module-level registration so the stub is discoverable from the
# config-load phase. Cleanup happens via ``_isolate_registry`` above,
# which restores the original registry after every test.
register("unhealthy_stub")(_UnhealthyStubProvider)


# ---- health check contract ----------------------------------------------


def test_deterministic_provider_health_check_is_healthy() -> None:
    """The deterministic provider is the safe fallback target; it
    must always report healthy so the fallback path can rely on it.
    """

    p = get_provider("deterministic")
    assert isinstance(p, DeterministicProvider)
    h = p.health_check()
    assert h.healthy is True
    assert h.name == "deterministic"
    # ``reason`` may be None or a string; either way no exception.
    assert h.reason is None or isinstance(h.reason, str)


def test_unhealthy_stub_health_check_reports_unhealthy() -> None:
    """A provider with no configured prerequisites must report unhealthy
    with a descriptive reason so operators can diagnose the issue from
    ``run_summary.json`` alone.
    """

    p = get_provider("unhealthy_stub")
    h = p.health_check()
    assert h.healthy is False
    assert h.name == "unhealthy_stub"
    assert h.reason, "unhealthy stub must provide a reason"
    assert "stub_forced_unhealthy" in h.reason


def test_base_provider_requires_health_check_subclass() -> None:
    """Future providers that forget ``health_check`` should be caught at
    registration time. ``abc.ABCMeta`` enforces this, so simply
    subclassing without the method must fail to instantiate.
    """

    class _NoHealthCheck(BaseProvider):
        def build_bibles(self, *args, **kwargs):
            raise NotImplementedError

        def extract_beats(self, *args, **kwargs):
            raise NotImplementedError

        def generate_shots(self, **kwargs):
            raise NotImplementedError

        def compile_image_prompts(self, *args, **kwargs):
            raise NotImplementedError

        def compile_video_prompts(self, *args, **kwargs):
            raise NotImplementedError

    with pytest.raises(TypeError):
        _NoHealthCheck()  # type: ignore[abstract]


# ---- config-load guardrails ---------------------------------------------


def test_unknown_provider_still_rejected_at_load(project_root: Path) -> None:
    """The fallback design must not weaken the existing config-load
    rejection of unknown providers.
    """

    config_path = project_root / "config" / "development.json"
    raw = json.loads(config_path.read_text("utf-8"))
    raw["planner_provider"] = "no_such_provider_for_test"
    alt = project_root / "runs" / "_fallback_unknown_config.json"
    alt.parent.mkdir(parents=True, exist_ok=True)
    alt.write_text(json.dumps(raw))
    try:
        with pytest.raises(ConfigError, match="Unknown planner_provider"):
            load_config("development", project_root=project_root, config_path=alt)
    finally:
        alt.unlink(missing_ok=True)


def test_production_rejects_allow_provider_fallback_true(
    project_root: Path,
) -> None:
    """Production must remain fail-closed: enabling fallback in
    production config must raise ConfigError at load.
    """

    raw = json.loads(
        (project_root / "config" / "production.example.json").read_text("utf-8")
    )
    raw["allow_provider_fallback"] = True
    alt = project_root / "runs" / "_fallback_prod_allow.json"
    alt.parent.mkdir(parents=True, exist_ok=True)
    alt.write_text(json.dumps(raw))
    try:
        with pytest.raises(ConfigError, match="allow_provider_fallback"):
            load_config("production", project_root=project_root, config_path=alt)
    finally:
        alt.unlink(missing_ok=True)


def test_production_env_var_allow_provider_fallback_rejected(
    project_root: Path, monkeypatch
) -> None:
    """PLANNER_ALLOW_PROVIDER_FALLBACK must be rejected loudly in
    production just like the other locked keys.
    """

    monkeypatch.setenv("PLANNER_ALLOW_PROVIDER_FALLBACK", "true")
    with pytest.raises(ConfigError, match="ALLOW_PROVIDER_FALLBACK"):
        load_config(
            "production",
            project_root=project_root,
            config_path=project_root / "config" / "production.example.json",
        )


# ---- development: silent fallback is allowed & audited ------------------


def _dev_config_with_unhealthy_provider(
    project_root: Path,
) -> PlannerConfig:
    """Build a development config that requests the unhealthy stub."""

    raw = json.loads(
        (project_root / "config" / "development.json").read_text("utf-8")
    )
    raw["planner_provider"] = "unhealthy_stub"
    raw["allow_provider_fallback"] = True
    alt = project_root / "runs" / "_fallback_dev_unhealthy.json"
    alt.parent.mkdir(parents=True, exist_ok=True)
    alt.write_text(json.dumps(raw))
    return load_config("development", project_root=project_root, config_path=alt)


def test_development_falls_back_to_deterministic_and_records_swap(
    project_root: Path, sample_script_path: Path, tmp_path: Path
) -> None:
    """When the requested provider is unhealthy in development (with
    fallback allowed), the pipeline must swap to deterministic AND
    record every audit field in ``run_summary.json``.
    """

    cfg = _dev_config_with_unhealthy_provider(project_root)
    assert cfg.allow_provider_fallback is True
    assert cfg.planner_provider == "unhealthy_stub"

    out_dir = tmp_path / "fallback_dev_run"
    result = run_pipeline(
        script_path=sample_script_path, out_dir=out_dir, config=cfg
    )

    # RunResult surfaces the swap to callers (e.g. CLI logs).
    assert result.requested_provider == "unhealthy_stub"
    assert result.effective_provider == "deterministic"
    assert result.fallback_used is True
    assert result.fallback_reason and "stub_forced_unhealthy" in result.fallback_reason
    assert "unhealthy_stub" in result.provider_health
    assert "deterministic" in result.provider_health
    assert result.provider_health["unhealthy_stub"]["healthy"] is False
    assert result.provider_health["deterministic"]["healthy"] is True

    summary = json.loads((out_dir / "run_summary.json").read_text("utf-8"))
    assert summary["requested_provider"] == "unhealthy_stub"
    assert summary["effective_provider"] == "deterministic"
    assert summary["fallback_used"] is True
    assert summary["fallback_reason"]
    # Backward-compat alias kept equal to requested_provider.
    assert summary["planner_provider"] == "unhealthy_stub"
    assert "provider_health" in summary
    assert summary["provider_health"]["unhealthy_stub"]["healthy"] is False
    assert summary["provider_health"]["deterministic"]["healthy"] is True

    # Cleanup helper used inside the temp config factory.
    cfg.config_path.unlink(missing_ok=True)


def test_fallback_run_passes_validation(
    project_root: Path, sample_script_path: Path, tmp_path: Path
) -> None:
    """After a fallback swap, all downstream validators must still
    pass: schema, references, script_parse source-path consistency.
    """

    cfg = _dev_config_with_unhealthy_provider(project_root)
    out_dir = tmp_path / "fallback_dev_validate"
    run_pipeline(script_path=sample_script_path, out_dir=out_dir, config=cfg)

    report = validate_run(out_dir, expected_env="development")
    assert report.ok, f"errors: {report.errors}"
    # ValidationReport exposes the same audit fields to validators /
    # downstream tools.
    assert report.requested_provider == "unhealthy_stub"
    assert report.effective_provider == "deterministic"
    assert report.fallback_used is True
    assert report.fallback_reason

    cfg.config_path.unlink(missing_ok=True)


def test_fallback_preserves_script_parse_and_references(
    project_root: Path, sample_script_path: Path, tmp_path: Path
) -> None:
    """Fallback must not corrupt ``script_parse.json`` or reference
    integrity: the deterministic fallback reuses the same canonical
    parser, so every byte of the audit-relevant artifacts must match a
    clean deterministic run.
    """

    cfg_fallback = _dev_config_with_unhealthy_provider(project_root)
    out_fallback = tmp_path / "fallback_a"
    out_clean = tmp_path / "clean_a"

    run_pipeline(script_path=sample_script_path, out_dir=out_fallback, config=cfg_fallback)

    cfg_clean = load_config("development", project_root=project_root)
    run_pipeline(script_path=sample_script_path, out_dir=out_clean, config=cfg_clean)

    # script_parse.json must be byte-identical.
    a = (out_fallback / "script_parse.json").read_text("utf-8")
    b = (out_clean / "script_parse.json").read_text("utf-8")
    assert a == b, "fallback must not change script_parse.json"

    # Shot list and bibles must be identical too (deterministic on both
    # sides, since fallback targets the same code path).
    for fname in (
        "character_bible.json",
        "location_bible.json",
        "prop_bible.json",
        "shot_list.json",
        "image_prompts.json",
        "video_prompts.json",
    ):
        assert (out_fallback / fname).read_text("utf-8") == (
            out_clean / fname
        ).read_text("utf-8"), f"{fname} diverged after fallback"

    cfg_fallback.config_path.unlink(missing_ok=True)


def test_development_fallback_disabled_fails_closed(
    project_root: Path, monkeypatch
) -> None:
    """Development with ``allow_provider_fallback=False`` must also
    fail-closed (no silent swap), so operators can opt out of the
    auto-fallback behavior per-environment.
    """

    monkeypatch.setenv("PLANNER_PLANNER_PROVIDER", "unhealthy_stub")
    monkeypatch.setenv("PLANNER_ALLOW_PROVIDER_FALLBACK", "false")
    cfg = load_config("development", project_root=project_root)

    sample = project_root / "data" / "development" / "input_scripts" / "sample_ep01.txt"
    out_dir = project_root / "runs" / "_fallback_disabled"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        with pytest.raises(ProviderUnavailableError, match="unhealthy_stub"):
            run_pipeline(script_path=sample, out_dir=out_dir, config=cfg)
    finally:
        out_dir.rmdir() if out_dir.exists() and not any(out_dir.iterdir()) else None
        # out_dir may be empty; rmdir only if no leftover. Force cleanup
        # of any partial artifacts.
        for child in out_dir.glob("*"):
            child.unlink()
        if out_dir.exists():
            out_dir.rmdir()


# ---- production: never fall back ---------------------------------------


def test_production_fails_closed_when_requested_provider_unhealthy(
    project_root: Path, sample_script_path: Path, tmp_path: Path
) -> None:
    """Production config that points at an unhealthy provider (loaded
    directly, bypassing the ``planner_provider`` config-load validator
    to simulate a future provider that loaded successfully but became
    unhealthy at runtime) must NOT fall back. It must raise
    :class:`ProviderUnavailableError` so the operator notices.
    """

    raw = json.loads(
        (project_root / "config" / "production.example.json").read_text("utf-8")
    )
    # ``allow_provider_fallback`` is enforced False in production; we
    # keep it as False here, which is the production default.
    raw["allow_provider_fallback"] = False
    alt = project_root / "runs" / "_fallback_prod_unhealthy.json"
    alt.parent.mkdir(parents=True, exist_ok=True)
    alt.write_text(json.dumps(raw))
    try:
        cfg = load_config("production", project_root=project_root, config_path=alt)

        out_dir = tmp_path / "prod_no_fallback"
        # Sanity: cfg reflects production defaults.
        assert cfg.is_production
        assert cfg.allow_provider_fallback is False

        # Patch the pipeline provider resolver to look up our stub so we
        # can exercise the runtime health-check branch without editing
        # the production config (which the config-load guardrail would
        # reject).
        from planner import pipeline as pipeline_mod
        from planner.providers import get_provider as _get_provider

        original_get = pipeline_mod.get_provider
        try:
            def fake_get(name, settings=None):
                if name == "unhealthy_stub":
                    return get_provider("unhealthy_stub")
                return original_get(name)

            pipeline_mod.get_provider = fake_get  # type: ignore[assignment]

            # Build a config object whose planner_provider points at the
            # stub so the runtime path picks it up.
            patched_cfg = cfg.__class__(
                env=cfg.env,
                config_path=cfg.config_path,
                allow_overwrite_runs=cfg.allow_overwrite_runs,
                executor_default_status=cfg.executor_default_status,
                submit_paid_jobs=cfg.submit_paid_jobs,
                log_level=cfg.log_level,
                executor_dry_run=cfg.executor_dry_run,
                data_root=cfg.data_root,
                assets_root=cfg.assets_root,
                runs_root=cfg.runs_root,
                logs_root=cfg.logs_root,
                schema_strict=cfg.schema_strict,
                planner_provider="unhealthy_stub",
                allow_provider_fallback=cfg.allow_provider_fallback,
                overrides=dict(cfg.overrides),
            )

            with pytest.raises(ProviderUnavailableError, match="fail"):
                run_pipeline(
                    script_path=sample_script_path,
                    out_dir=out_dir,
                    config=patched_cfg,
                )
        finally:
            pipeline_mod.get_provider = original_get  # type: ignore[assignment]
    finally:
        alt.unlink(missing_ok=True)


def test_production_unhealthy_provider_leaves_no_out_dir(
    project_root: Path, sample_script_path: Path, tmp_path: Path
) -> None:
    """When production fail-closed raises ``ProviderUnavailableError``,
    the failed run must NOT have created ``out_dir``. Otherwise the
    production overwrite guard would refuse the next invocation on the
    same path and force manual cleanup, contradicting the
    ``fail-closed leaves no residue`` contract.
    """

    raw = json.loads(
        (project_root / "config" / "production.example.json").read_text("utf-8")
    )
    raw["allow_provider_fallback"] = False
    alt = project_root / "runs" / "_fallback_prod_unhealthy_no_residue.json"
    alt.parent.mkdir(parents=True, exist_ok=True)
    alt.write_text(json.dumps(raw))
    try:
        cfg = load_config("production", project_root=project_root, config_path=alt)

        out_dir = tmp_path / "prod_no_residue"
        # Pre-condition: the directory must NOT exist yet, so the
        # post-condition check is meaningful.
        assert not out_dir.exists(), (
            f"test setup error: out_dir {out_dir} already exists"
        )

        from planner import pipeline as pipeline_mod

        original_get = pipeline_mod.get_provider
        try:
            def fake_get(name, settings=None):
                if name == "unhealthy_stub":
                    return get_provider("unhealthy_stub")
                return original_get(name)

            pipeline_mod.get_provider = fake_get  # type: ignore[assignment]

            patched_cfg = cfg.__class__(
                env=cfg.env,
                config_path=cfg.config_path,
                allow_overwrite_runs=cfg.allow_overwrite_runs,
                executor_default_status=cfg.executor_default_status,
                submit_paid_jobs=cfg.submit_paid_jobs,
                log_level=cfg.log_level,
                executor_dry_run=cfg.executor_dry_run,
                data_root=cfg.data_root,
                assets_root=cfg.assets_root,
                runs_root=cfg.runs_root,
                logs_root=cfg.logs_root,
                schema_strict=cfg.schema_strict,
                planner_provider="unhealthy_stub",
                allow_provider_fallback=cfg.allow_provider_fallback,
                overrides=dict(cfg.overrides),
            )

            with pytest.raises(ProviderUnavailableError):
                run_pipeline(
                    script_path=sample_script_path,
                    out_dir=out_dir,
                    config=patched_cfg,
                )

            # Post-condition: fail-closed leaves no residue. No empty
            # ``out_dir``, no half-written artifacts.
            assert not out_dir.exists(), (
                f"fail-closed path left residue at {out_dir}; the next "
                f"production run on this path would be blocked by the "
                f"overwrite guard"
            )

            # A subsequent invocation on the same path should be a clean
            # attempt, not a refuse-to-overwrite error.
            cfg_ok = load_config(
                "production",
                project_root=project_root,
                config_path=project_root / "config" / "production.example.json",
            )
            pipeline_mod.get_provider = original_get  # type: ignore[assignment]
            run_pipeline(
                script_path=sample_script_path,
                out_dir=out_dir,
                config=cfg_ok,
            )
            assert out_dir.exists()
            assert (out_dir / "run_summary.json").exists()
        finally:
            pipeline_mod.get_provider = original_get  # type: ignore[assignment]
    finally:
        alt.unlink(missing_ok=True)


# ---- production hard boundaries stay intact ----------------------------


def test_fallback_design_does_not_change_production_executor_defaults(
    project_root: Path, sample_script_path: Path, tmp_path: Path
) -> None:
    """Production runs must always emit ``pending_manual_approval`` +
    ``tool=None``, regardless of provider / fallback additions. The
    fallback plumbing lives in the planning layer only and must not
    leak into executor tasks.
    """

    cfg = load_config(
        "production",
        project_root=project_root,
        config_path=project_root / "config" / "production.example.json",
    )
    assert cfg.executor_default_status == "pending_manual_approval"
    assert cfg.allow_provider_fallback is False

    out_dir = tmp_path / "prod_boundaries"
    run_pipeline(script_path=sample_script_path, out_dir=out_dir, config=cfg)

    tasks = json.loads((out_dir / "executor_tasks.json").read_text("utf-8"))
    for task in tasks["tasks"]:
        assert task["tool"] is None
        assert task["status"] == "pending_manual_approval"

    summary = json.loads((out_dir / "run_summary.json").read_text("utf-8"))
    assert summary["executor_status"] == "pending_manual_approval"
    # Fallback is not allowed in production, so audit fields confirm
    # the run was deterministic end-to-end.
    assert summary["requested_provider"] == "deterministic"
    assert summary["effective_provider"] == "deterministic"
    assert summary["fallback_used"] is False
    assert summary["fallback_reason"] is None


# ---- registry unregister helper -----------------------------------------


def test_unregister_helper_removes_provider() -> None:
    """The registry's ``unregister`` helper exists so test teardown can
    clean up stub providers. Verify the contract here directly.
    """

    @register("ephemeral_stub")
    class _Ephemeral(BaseProvider):
        name = "ephemeral_stub"

        def build_bibles(self, *args, **kwargs):  # pragma: no cover
            raise NotImplementedError

        def extract_beats(self, *args, **kwargs):  # pragma: no cover
            raise NotImplementedError

        def generate_shots(self, **kwargs):  # pragma: no cover
            raise NotImplementedError

        def compile_image_prompts(self, *args, **kwargs):  # pragma: no cover
            raise NotImplementedError

        def compile_video_prompts(self, *args, **kwargs):  # pragma: no cover
            raise NotImplementedError

        def probe(self):  # type: ignore[override]
            raise NotImplementedError

    from planner.providers import get_provider as _gp

    assert "ephemeral_stub" in {p for p in _gp.__globals__["_REGISTRY"].keys()}

    unregister("ephemeral_stub")
    with pytest.raises(ConfigError):
        _gp("ephemeral_stub")

    # Idempotent: calling unregister again is a silent no-op.
    unregister("ephemeral_stub")