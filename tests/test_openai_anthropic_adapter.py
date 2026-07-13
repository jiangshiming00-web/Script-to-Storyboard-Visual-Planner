"""Tests for the OpenAI / Anthropic provider skeletons.

These tests pin the Phase-1 contract for the skeleton adapters:

1. ``openai`` and ``anthropic`` register with the provider registry at
   package import time.
2. ``health_check()`` answers only local questions (env-var presence,
   optional SDK importability) and never makes a network call. It
   performs **no login, no SDK import (only ``find_spec``), and no
   paid probe** — see ``planner/providers/base.py`` for the
   ``BaseProvider.health_check`` contract.
3. **Phase-1 implementation gate (P1 fix)**: even with both
   preconditions satisfied (``api_key_present=true`` AND
   ``sdk_installed=true``) the skeleton reports ``healthy=False``
   because the five planning methods are not implemented yet. Only
   when the planning methods are filled in will a future revision flip
   this to ``healthy=True``. With either precondition missing the
   adapter also reports ``healthy=False`` with a descriptive
   ``reason`` that points the operator at the missing piece.
4. The five planning methods raise :class:`NotImplementedError`
   immediately so a stray direct call cannot accidentally hit a paid
   service. This is **defense in depth**: under the normal pipeline
   flow ``_select_provider`` would have already fail-closed or
   fallen back to deterministic before any such call could be
   reached. ``NotImplementedError`` is preserved even after the
   implementation gate exists so that any future refactor that
   accidentally widens the health check still catches the call.
5. The existing ``planner_provider: "openai"`` / ``"anthropic"``
   config-select path: ``development`` falls back to deterministic
   with full audit fields (``fallback_reason`` references the
   skeleton-implementation gap); ``production`` raises
   :class:`ProviderUnavailableError` and leaves no ``out_dir``
   residue. The ``executor`` boundary stays untouched in both cases
   (``tool=None``, ``status=pending_manual_approval``).

These tests must NEVER require a real API key, real network call, or
the optional ``openai`` / ``anthropic`` SDK to be installed — every
relevant test monkeypatches the SDK detection helper explicitly.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from planner.env import PlannerConfig, load_config
from planner.exceptions import ProviderUnavailableError
from planner.pipeline import run as run_pipeline
from planner.providers import (
    AnthropicProvider,
    BaseProvider,
    OpenAIProvider,
    ProviderHealth,
    available_providers,
    get_provider,
)
from planner.providers import anthropic_adapter as anthropic_adapter_mod
from planner.providers import openai_adapter as openai_adapter_mod
from planner.providers.registry import _REGISTRY
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
    """Drop any PLANNER_* / provider-native env vars so each test starts
    with a clean slate. Tests that want to simulate a configured setup
    opt back in explicitly.
    """

    scrubbed_prefixes = (
        "PLANNER_",
        "OPENAI_",
        "ANTHROPIC_",
    )
    for key in list(os.environ):
        if any(key.startswith(prefix) for prefix in scrubbed_prefixes):
            monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture(autouse=True)
def _isolate_registry():
    """The test session only registers ``deterministic``, ``openai``,
    and ``anthropic`` at package import; we don't want stub leakage.
    """

    snapshot = dict(_REGISTRY)
    yield
    _REGISTRY.clear()
    _REGISTRY.update(snapshot)


# ---- registry -------------------------------------------------------


def test_openai_and_anthropic_are_registered() -> None:
    """Importing ``planner.providers`` must auto-register both skeleton
    adapters so configs can name them without an explicit plugin
    import.
    """

    avail = set(available_providers())
    assert "openai" in avail
    assert "anthropic" in avail
    # Deterministic must remain the default; we are additive only.
    assert "deterministic" in avail


def test_get_provider_returns_skeleton_instances() -> None:
    p_openai = get_provider("openai")
    p_anthropic = get_provider("anthropic")
    assert isinstance(p_openai, OpenAIProvider)
    assert isinstance(p_anthropic, AnthropicProvider)
    assert p_openai.name == "openai"
    assert p_anthropic.name == "anthropic"


# ---- env-only unhealthy contracts (no SDK involvement) ----------------


def test_openai_health_check_unhealthy_when_no_env(monkeypatch) -> None:
    """``health_check`` must report unhealthy when no key is set,
    regardless of SDK availability. We force SDK-present here so the
    unhealthy result can only come from the missing-key branch.
    """

    monkeypatch.setattr(openai_adapter_mod, "_openai_sdk_available", lambda: True)
    p = get_provider("openai")
    h = p.health_check()
    assert h.healthy is False
    assert h.name == "openai"
    assert h.reason is not None
    assert "PLANNER_OPENAI_API_KEY" in h.reason
    assert h.details["api_key_env"] == "missing"


def test_anthropic_health_check_unhealthy_when_no_env(monkeypatch) -> None:
    monkeypatch.setattr(
        anthropic_adapter_mod, "_anthropic_sdk_available", lambda: True
    )
    p = get_provider("anthropic")
    h = p.health_check()
    assert h.healthy is False
    assert h.reason is not None
    assert "PLANNER_ANTHROPIC_API_KEY" in h.reason
    assert h.details["api_key_env"] == "missing"


def test_openai_health_check_unhealthy_when_sdk_missing_but_key_set(
    monkeypatch,
) -> None:
    """Key alone is not enough. Missing optional SDK must also fail
    the check so the operator sees the issue before any real call
    could be attempted.
    """

    monkeypatch.setenv("PLANNER_OPENAI_API_KEY", "sk-test-1234")
    monkeypatch.setattr(openai_adapter_mod, "_openai_sdk_available", lambda: False)

    h = get_provider("openai").health_check()
    assert h.healthy is False
    assert "SDK not importable" in h.reason
    assert h.details["sdk_module"] == "openai"
    assert h.details["sdk_installed"] == "false"
    # Key was set; we only record which namespace it came from.
    assert h.details["api_key_env"] == "PLANNER_OPENAI_API_KEY"


def test_anthropic_health_check_unhealthy_when_sdk_missing_but_key_set(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PLANNER_ANTHROPIC_API_KEY", "sk-test-ant")
    monkeypatch.setattr(
        anthropic_adapter_mod, "_anthropic_sdk_available", lambda: False
    )

    h = get_provider("anthropic").health_check()
    assert h.healthy is False
    assert "SDK not importable" in h.reason
    assert h.details["sdk_module"] == "anthropic"
    assert h.details["sdk_installed"] == "false"


# ---- preconditions registered but skeleton still unhealthy -----------
#
# P1 contract: even with API key + optional SDK both present the
# Phase-1 skeleton MUST report ``healthy=False`` because the planning
# methods raise ``NotImplementedError``. Returning ``healthy=True``
# here would let the pipeline select this provider, ``mkdir`` the
# run directory, then crash via ``NotImplementedError`` — a non-
# ``PlannerError`` exception that slips past the CLI's
# ``try/except PlannerError`` handler AND breaks the
# ``fail-closed leaves no residue`` contract. The implementation
# gate is therefore load-bearing in Phase 1.


def test_openai_health_check_unhealthy_even_when_key_and_sdk_present(
    monkeypatch,
) -> None:
    """With both preconditions in place the adapter is still
    unhealthy in Phase 1. ``details`` records every configured
    signal so operators can audit that the prerequisites passed,
    but ``healthy=False`` stays put until the planning methods ship
    real implementations.
    """

    monkeypatch.setenv("PLANNER_OPENAI_API_KEY", "sk-test-1234")
    monkeypatch.setattr(openai_adapter_mod, "_openai_sdk_available", lambda: True)

    h = get_provider("openai").health_check()
    assert h.healthy is False, (
        "skeleton must stay unhealthy while planning methods raise "
        "NotImplementedError; returning healthy=True here would let "
        "the pipeline select the provider and crash via "
        "NotImplementedError on the planning call"
    )
    assert "planning methods are not implemented" in h.reason
    assert "fail-closed" in h.reason
    # Details still record every configured signal.
    assert h.details["api_key_env"] == "PLANNER_OPENAI_API_KEY"
    assert h.details["api_key_present"] == "true"
    assert h.details["sdk_module"] == "openai"
    assert h.details["sdk_installed"] == "true"
    assert h.details["implemented"] == "false"
    assert h.details["real_calls"] == "disabled"
    assert h.details["phase"] == "1-skeleton"


def test_anthropic_health_check_unhealthy_even_when_key_and_sdk_present(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PLANNER_ANTHROPIC_API_KEY", "sk-test-ant")
    monkeypatch.setattr(
        anthropic_adapter_mod, "_anthropic_sdk_available", lambda: True
    )

    h = get_provider("anthropic").health_check()
    assert h.healthy is False
    assert "planning methods are not implemented" in h.reason
    assert h.details["api_key_env"] == "PLANNER_ANTHROPIC_API_KEY"
    assert h.details["api_key_present"] == "true"
    assert h.details["sdk_module"] == "anthropic"
    assert h.details["sdk_installed"] == "true"
    assert h.details["implemented"] == "false"
    assert h.details["real_calls"] == "disabled"


def test_openai_prefers_provider_native_namespace_as_fallback(
    monkeypatch,
) -> None:
    """When only the provider-native env var is set, the adapter must
    accept it so existing developer setups keep working. Still
    reports unhealthy in Phase 1 — this just verifies that the
    namespace bookkeeping is correct.
    """

    monkeypatch.setenv("OPENAI_API_KEY", "sk-native-1234")
    monkeypatch.setattr(openai_adapter_mod, "_openai_sdk_available", lambda: True)

    h = get_provider("openai").health_check()
    assert h.healthy is False
    assert h.details["api_key_env"] == "OPENAI_API_KEY"
    assert h.details["implemented"] == "false"


def test_anthropic_prefers_provider_native_namespace_as_fallback(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-native-ant")
    monkeypatch.setattr(
        anthropic_adapter_mod, "_anthropic_sdk_available", lambda: True
    )

    h = get_provider("anthropic").health_check()
    assert h.healthy is False
    assert h.details["api_key_env"] == "ANTHROPIC_API_KEY"
    assert h.details["implemented"] == "false"


def test_openai_planner_env_wins_when_both_set(monkeypatch) -> None:
    """If both namespaces are set, planner-owned wins so a misconfigured
    global key does not silently enable the adapter for a production
    run that did not intentionally opt in.
    """

    monkeypatch.setenv("PLANNER_OPENAI_API_KEY", "sk-planner-1234")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-native-1234")
    monkeypatch.setattr(openai_adapter_mod, "_openai_sdk_available", lambda: True)

    h = get_provider("openai").health_check()
    assert h.healthy is False
    assert h.details["api_key_env"] == "PLANNER_OPENAI_API_KEY"


@pytest.mark.parametrize(
    "provider_name,key_env,sdk_helper_attr",
    [
        ("openai", "PLANNER_OPENAI_API_KEY", "_openai_sdk_available"),
        ("anthropic", "PLANNER_ANTHROPIC_API_KEY", "_anthropic_sdk_available"),
    ],
)
def test_empty_string_env_var_is_treated_as_missing(
    monkeypatch, provider_name: str, key_env: str, sdk_helper_attr: str
) -> None:
    """Empty strings and whitespace-only env vars are common when an
    operator un-sets a key without unsetting the variable. We must
    not let those silently count as "configured". Symmetric across
    both adapters (P3 polish: original implementation only covered
    OpenAI).
    """

    adapter_module = _adapter_module_for(provider_name)
    monkeypatch.setenv(key_env, "   ")
    monkeypatch.setattr(adapter_module, sdk_helper_attr, lambda: True)
    h = get_provider(provider_name).health_check()
    assert h.healthy is False
    assert h.details["api_key_env"] == "missing"


# ---- planning methods raise NotImplementedError ----------------------


def test_openai_planning_methods_raise_not_implemented(monkeypatch) -> None:
    """Direct API callers that somehow bypass ``_select_provider``
    must still fail safely: ``health_check`` returns ``healthy=False``
    in Phase 1 (the new contract), but a caller that ignores the
    signal and invokes a planning method gets
    :class:`NotImplementedError` with a message that points at the
    skeleton (i.e. ``provider='deterministic'`` /
    ``allow_provider_fallback=true``) so the user knows what to do.
    """

    monkeypatch.setenv("PLANNER_OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(openai_adapter_mod, "_openai_sdk_available", lambda: True)

    p = get_provider("openai")
    # ``health_check`` is allowed; it MUST report unhealthy in Phase 1.
    h = p.health_check()
    assert isinstance(h, ProviderHealth)
    assert h.healthy is False

    sample_script = "林夏 走进 咖啡店。"
    sample_path = Path("/tmp/__openai_skull_sample__.txt")
    sample_path.write_text(sample_script, encoding="utf-8")
    try:
        with pytest.raises(NotImplementedError, match="intentionally not implemented"):
            p.build_bibles(sample_script, script_id="EP01")
        with pytest.raises(NotImplementedError):
            p.extract_beats(sample_path, episode_id="EP01")
        with pytest.raises(NotImplementedError):
            p.generate_shots(
                script_text=sample_script,
                episode_id="EP01",
                location_ids=[],
                character_ids=[],
                prop_ids=[],
                beats=[],
                display_to_character_id=None,
            )
        with pytest.raises(NotImplementedError):
            p.compile_image_prompts(
                ShotList(shots=[]),
                CharacterBible(characters=[]),
                LocationBible(locations=[]),
                PropBible(props=[]),
            )
        with pytest.raises(NotImplementedError):
            p.compile_video_prompts(
                ShotList(shots=[]),
                CharacterBible(characters=[]),
                LocationBible(locations=[]),
                PropBible(props=[]),
            )
    finally:
        sample_path.unlink(missing_ok=True)


def test_anthropic_planning_methods_raise_not_implemented(monkeypatch) -> None:
    monkeypatch.setenv("PLANNER_ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(
        anthropic_adapter_mod, "_anthropic_sdk_available", lambda: True
    )

    p = get_provider("anthropic")
    h = p.health_check()
    assert isinstance(h, ProviderHealth)
    assert h.healthy is False

    sample_script = "Anthropic sample."
    sample_path = Path("/tmp/__anthropic_skull_sample__.txt")
    sample_path.write_text(sample_script, encoding="utf-8")
    try:
        with pytest.raises(NotImplementedError, match="intentionally not implemented"):
            p.build_bibles(sample_script, script_id="EP01")
        with pytest.raises(NotImplementedError):
            p.extract_beats(sample_path, episode_id="EP01")
        with pytest.raises(NotImplementedError):
            p.generate_shots(
                script_text=sample_script,
                episode_id="EP01",
                location_ids=[],
                character_ids=[],
                prop_ids=[],
                beats=[],
                display_to_character_id=None,
            )
        with pytest.raises(NotImplementedError):
            p.compile_image_prompts(
                ShotList(shots=[]),
                CharacterBible(characters=[]),
                LocationBible(locations=[]),
                PropBible(props=[]),
            )
        with pytest.raises(NotImplementedError):
            p.compile_video_prompts(
                ShotList(shots=[]),
                CharacterBible(characters=[]),
                LocationBible(locations=[]),
                PropBible(props=[]),
            )
    finally:
        sample_path.unlink(missing_ok=True)


# ---- end-to-end: dev falls back, production fail-closes ---------------


def _write_dev_config_with_provider(
    project_root: Path, provider_name: str
) -> Path:
    raw = json.loads(
        (project_root / "config" / "development.json").read_text("utf-8")
    )
    raw["planner_provider"] = provider_name
    raw["allow_provider_fallback"] = True
    alt = project_root / "runs" / f"_adapter_dev_{provider_name}.json"
    alt.parent.mkdir(parents=True, exist_ok=True)
    alt.write_text(json.dumps(raw))
    return alt


@pytest.mark.parametrize(
    "provider_name",
    ["openai", "anthropic"],
)
def test_development_fallback_records_full_audit(
    project_root: Path,
    sample_script_path: Path,
    tmp_path: Path,
    monkeypatch,
    provider_name: str,
) -> None:
    """With the request unhealthy in development (fallback allowed),
    the pipeline must swap to deterministic, write the standard 10
    artifacts, and the audit fields in ``run_summary.json`` must
    surface the requested / effective / fallback / provider_health
    fields identically to the existing fallback tests.
    """

    alt = _write_dev_config_with_provider(project_root, provider_name)
    try:
        cfg = load_config("development", project_root=project_root, config_path=alt)
        assert cfg.planner_provider == provider_name
        assert cfg.allow_provider_fallback is True

        out_dir = tmp_path / f"adapter_dev_{provider_name}"
        result = run_pipeline(
            script_path=sample_script_path, out_dir=out_dir, config=cfg
        )

        assert result.requested_provider == provider_name
        assert result.effective_provider == "deterministic"
        assert result.fallback_used is True
        assert result.fallback_reason and "not configured" in result.fallback_reason
        assert provider_name in result.provider_health
        assert "deterministic" in result.provider_health
        assert result.provider_health[provider_name]["healthy"] is False
        assert result.provider_health["deterministic"]["healthy"] is True

        summary = json.loads((out_dir / "run_summary.json").read_text("utf-8"))
        assert summary["requested_provider"] == provider_name
        assert summary["effective_provider"] == "deterministic"
        assert summary["fallback_used"] is True
        # Backward-compat alias matches ``requested_provider``.
        assert summary["planner_provider"] == provider_name

        # Validation must still pass against the swapped path.
        report = validate_run(out_dir, expected_env="development")
        assert report.ok, f"errors: {report.errors}"
        assert report.fallback_used is True

        # Executor tasks must keep the production-style neutral
        # defaults: status='pending' (development), tool=None.
        tasks = json.loads((out_dir / "executor_tasks.json").read_text("utf-8"))
        assert tasks["tasks"], "expected at least one executor task"
        for task in tasks["tasks"]:
            assert task["tool"] is None
            assert task["status"] == "pending"
        assert summary["executor_status"] == "pending"
    finally:
        alt.unlink(missing_ok=True)


@pytest.mark.parametrize(
    "provider_name",
    ["openai", "anthropic"],
)
def test_development_fallback_artifact_bytes_match_clean_run(
    project_root: Path,
    sample_script_path: Path,
    tmp_path: Path,
    provider_name: str,
) -> None:
    """Fallback after the skeleton adapter must produce byte-identical
    artifacts to a clean deterministic run — so operator confidence in
    the audit guarantees established for ``unhealthy_stub`` carries
    over to the new skeletons.
    """

    alt = _write_dev_config_with_provider(project_root, provider_name)
    try:
        cfg = load_config("development", project_root=project_root, config_path=alt)
        out_fallback = tmp_path / f"adapter_fallback_{provider_name}"
        run_pipeline(
            script_path=sample_script_path, out_dir=out_fallback, config=cfg
        )

        out_clean = tmp_path / f"adapter_clean_{provider_name}"
        cfg_clean = load_config("development", project_root=project_root)
        run_pipeline(
            script_path=sample_script_path, out_dir=out_clean, config=cfg_clean
        )

        for fname in (
            "script_parse.json",
            "character_bible.json",
            "location_bible.json",
            "prop_bible.json",
            "shot_list.json",
            "image_prompts.json",
            "video_prompts.json",
        ):
            assert (
                out_fallback / fname
            ).read_text("utf-8") == (out_clean / fname).read_text(
                "utf-8"
            ), f"{fname} diverged for {provider_name} adapter fallback"
    finally:
        alt.unlink(missing_ok=True)


@pytest.mark.parametrize(
    "provider_name",
    ["openai", "anthropic"],
)
def test_production_fail_closed_no_residue(
    project_root: Path,
    sample_script_path: Path,
    tmp_path: Path,
    provider_name: str,
) -> None:
    """Production must be fail-closed: requesting the OpenAI / Anthropic
    skeleton (always unhealthy in our test env) raises
    :class:`ProviderUnavailableError` AND must NOT leave an empty
    ``out_dir`` behind. A subsequent production run on the same path
    must succeed, proving the path was not poisoned by the failure.
    """

    # Stage a production config that points at the skeleton.
    raw = json.loads(
        (project_root / "config" / "production.example.json").read_text("utf-8")
    )
    raw["planner_provider"] = provider_name
    raw["allow_provider_fallback"] = False
    alt = project_root / "runs" / f"_adapter_prod_{provider_name}.json"
    alt.parent.mkdir(parents=True, exist_ok=True)
    alt.write_text(json.dumps(raw))
    try:
        # Production + skeleton provider: config load still passes
        # because the registry now knows the name. The failure is
        # deferred to ``_select_provider`` so the operator gets a
        # health-check reason rather than a config-load confusion.
        cfg = load_config(
            "production", project_root=project_root, config_path=alt
        )
        assert cfg.is_production
        assert cfg.planner_provider == provider_name
        assert cfg.allow_provider_fallback is False

        out_dir = tmp_path / f"adapter_prod_{provider_name}"
        assert not out_dir.exists(), "test setup: out_dir must not pre-exist"

        with pytest.raises(ProviderUnavailableError, match=provider_name):
            run_pipeline(
                script_path=sample_script_path, out_dir=out_dir, config=cfg
            )

        # Post-condition: fail-closed leaves no residue.
        assert not out_dir.exists(), (
            f"production fail-closed left residue at {out_dir} for "
            f"provider {provider_name}; the next run on this path would "
            f"be blocked by the overwrite guard"
        )

        # A subsequent normal production run on the same path is clean.
        cfg_ok = load_config(
            "production",
            project_root=project_root,
            config_path=project_root / "config" / "production.example.json",
        )
        run_pipeline(
            script_path=sample_script_path, out_dir=out_dir, config=cfg_ok
        )
        assert out_dir.exists()
        assert (out_dir / "run_summary.json").exists()
        summary = json.loads((out_dir / "run_summary.json").read_text("utf-8"))
        assert summary["executor_status"] == "pending_manual_approval"
        for task in json.loads(
            (out_dir / "executor_tasks.json").read_text("utf-8")
        )["tasks"]:
            assert task["tool"] is None
            assert task["status"] == "pending_manual_approval"
    finally:
        alt.unlink(missing_ok=True)


# ---- P1 contract end-to-end: key + SDK present does NOT unlock skeleton --
#
# The previous tests exercise the contract without env keys / SDK
# because the autouse ``_scrub_planner_env`` fixture clears them.
# The two tests below are the load-bearing regressions for the P1
# review: even when the operator wires an API key AND installs the
# optional SDK, the skeleton must NOT silently light up. Development
# still audits a fallback to deterministic; production still raises
# ``ProviderUnavailableError`` with no ``out_dir`` residue.


def _adapter_module_for(provider_name: str):
    return openai_adapter_mod if provider_name == "openai" else anthropic_adapter_mod


@pytest.mark.parametrize(
    "provider_name,key_env",
    [
        ("openai", "PLANNER_OPENAI_API_KEY"),
        ("anthropic", "PLANNER_ANTHROPIC_API_KEY"),
    ],
)
def test_development_key_and_sdk_present_still_falls_back(
    project_root: Path,
    sample_script_path: Path,
    tmp_path: Path,
    monkeypatch,
    provider_name: str,
    key_env: str,
) -> None:
    """P1 regression: key + SDK present in development must still
    audit a fallback to deterministic. The skeleton returns
    ``healthy=False`` even with both preconditions in place, so the
    pipeline swaps to deterministic and the ``fallback_reason``
    references the *implementation* gap, not the missing-key gap.
    """

    adapter_module = _adapter_module_for(provider_name)
    monkeypatch.setenv(key_env, "sk-test-key-sdk-present")
    monkeypatch.setattr(
        adapter_module,
        "_openai_sdk_available" if provider_name == "openai"
        else "_anthropic_sdk_available",
        lambda: True,
    )

    alt = _write_dev_config_with_provider(project_root, provider_name)
    try:
        cfg = load_config("development", project_root=project_root, config_path=alt)
        out_dir = tmp_path / f"dev_key_sdk_{provider_name}"
        result = run_pipeline(
            script_path=sample_script_path, out_dir=out_dir, config=cfg
        )

        # Audit fields show the swap was triggered by the skeleton
        # gate (not by a missing key).
        assert result.requested_provider == provider_name
        assert result.effective_provider == "deterministic"
        assert result.fallback_used is True
        assert result.fallback_reason is not None
        assert "planning methods are not implemented" in result.fallback_reason
        adapter_health = result.provider_health[provider_name]
        assert adapter_health["healthy"] is False
        assert adapter_health["details"]["implemented"] == "false"
        assert adapter_health["details"]["api_key_present"] == "true"
        assert adapter_health["details"]["sdk_installed"] == "true"

        # The on-disk artifacts are byte-identical to a clean run
        # against deterministic so operators get the same audit
        # guarantees regardless of how the request fails through the
        # provider health check.
        out_clean = tmp_path / f"dev_clean_{provider_name}"
        cfg_clean = load_config("development", project_root=project_root)
        run_pipeline(
            script_path=sample_script_path, out_dir=out_clean, config=cfg_clean
        )
        for fname in (
            "script_parse.json",
            "character_bible.json",
            "location_bible.json",
            "prop_bible.json",
            "shot_list.json",
            "image_prompts.json",
            "video_prompts.json",
        ):
            assert (out_dir / fname).read_text("utf-8") == (
                out_clean / fname
            ).read_text("utf-8"), (
                f"{fname} diverged for {provider_name} key+SDK fallback"
            )

        # Executor boundary is untouched.
        tasks = json.loads((out_dir / "executor_tasks.json").read_text("utf-8"))
        for task in tasks["tasks"]:
            assert task["tool"] is None
            assert task["status"] == "pending"
    finally:
        alt.unlink(missing_ok=True)


@pytest.mark.parametrize(
    "provider_name,key_env",
    [
        ("openai", "PLANNER_OPENAI_API_KEY"),
        ("anthropic", "PLANNER_ANTHROPIC_API_KEY"),
    ],
)
def test_production_key_and_sdk_present_fails_closed_with_no_residue(
    project_root: Path,
    sample_script_path: Path,
    tmp_path: Path,
    monkeypatch,
    provider_name: str,
    key_env: str,
) -> None:
    """P1 regression: key + SDK present in production must still
    raise ``ProviderUnavailableError`` AND leave no ``out_dir``
    residue. The retry on the same path with the default prod
    config proves the path was not poisoned.
    """

    adapter_module = _adapter_module_for(provider_name)
    monkeypatch.setenv(key_env, "sk-test-key-sdk-present-prod")
    monkeypatch.setattr(
        adapter_module,
        "_openai_sdk_available" if provider_name == "openai"
        else "_anthropic_sdk_available",
        lambda: True,
    )

    raw = json.loads(
        (project_root / "config" / "production.example.json").read_text("utf-8")
    )
    raw["planner_provider"] = provider_name
    raw["allow_provider_fallback"] = False
    alt = project_root / "runs" / f"_adapter_prod_key_sdk_{provider_name}.json"
    alt.parent.mkdir(parents=True, exist_ok=True)
    alt.write_text(json.dumps(raw))
    try:
        cfg = load_config(
            "production", project_root=project_root, config_path=alt
        )
        assert cfg.is_production
        assert cfg.planner_provider == provider_name
        assert cfg.allow_provider_fallback is False

        out_dir = tmp_path / f"prod_key_sdk_{provider_name}"
        assert not out_dir.exists(), "test setup: out_dir must not pre-exist"

        with pytest.raises(ProviderUnavailableError, match=provider_name):
            run_pipeline(
                script_path=sample_script_path, out_dir=out_dir, config=cfg
            )

        # Hard contract: fail-closed leaves no residue even when the
        # skeleton has full prerequisites and looks "configured".
        assert not out_dir.exists(), (
            f"production fail-closed left residue at {out_dir} for "
            f"{provider_name} skeleton with key+SDK present; the next "
            f"run on this path would be blocked by the overwrite guard"
        )

        # Retry with default prod config succeeds on the same path,
        # proving the failed attempt did not block the path.
        cfg_ok = load_config(
            "production",
            project_root=project_root,
            config_path=project_root / "config" / "production.example.json",
        )
        run_pipeline(
            script_path=sample_script_path, out_dir=out_dir, config=cfg_ok
        )
        assert out_dir.exists()
        assert (out_dir / "run_summary.json").exists()
        # Production hard boundaries stay in place.
        summary = json.loads((out_dir / "run_summary.json").read_text("utf-8"))
        assert summary["executor_status"] == "pending_manual_approval"
        for task in json.loads(
            (out_dir / "executor_tasks.json").read_text("utf-8")
        )["tasks"]:
            assert task["tool"] is None
            assert task["status"] == "pending_manual_approval"
    finally:
        alt.unlink(missing_ok=True)
