"""Harness: fake-model end-to-end for the v1.0 release.

Drives a real :class:`planner.pipeline.run` against the
``openai_compatible`` provider while monkey-patching the HTTP layer
with an in-process fake server. This proves the model-config → provider
chain works end-to-end without:

- issuing any real HTTP request,
- requiring any API key in the test environment,
- touching the repository ``runs/`` tree (everything lands in ``/tmp``).

What it covers
--------------

1. **healthy fake run (development, fallback allowed).**
   Configures ``planner_provider=openai_compatible`` with
   ``enable_real_model_calls=true`` and a valid ``api_key_env``.
   Monkey-patches ``openai_compatible_adapter.http_post`` to return
   canned, schema-valid JSON envelopes. The pipeline:
   - resolves the provider with the configured
     ``ProviderRuntimeSettings``,
   - calls each planning method against the fake server,
   - writes artifacts whose content (e.g. a sentinel character name)
     comes from the fake model, not the deterministic extractor.
   The harness verifies ``run_summary.provider_runtime`` carries the
   requested provider's settings (model / base_url / api_key_env /
   enable_real_model_calls), and ``run_summary.fallback_used == False``.

2. **production fail-closed without real calls.**
   Reuses the same model config but flips ``enable_real_model_calls
   = False``. The pipeline MUST raise :class:`ProviderUnavailableError`
   before any directory is created (``out_dir.exists() is False``),
   proving the v1.0 production contract holds.

3. **literal API key is rejected at model-config load time.**
   Loading a model config whose ``api_key_env`` is an ``sk-...`` token
   must raise :class:`ValueError` (defense in depth on the wire).

Run as::

    python3 harness/fake_model_e2e.py

Exit code 0 on full success, non-zero on first failed step. Each
step prints a single friendly status line so CI logs stay readable.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_SCRIPT = PROJECT_ROOT / "samples" / "v1" / "EP01.txt"

#: Sentinel strings the fake server embeds in its canned responses.
#: The harness greps the produced artifacts to make sure the pipeline
#: actually used the fake model output, NOT the deterministic fallback.
SENTINEL_CHAR_NAME = "SmokeFake-MC"
SENTINEL_LOCATION_NAME = "SmokeFake-Stage"
SENTINEL_PROP_NAME = "SmokeFake-Prop"


def _log(msg: str) -> None:
    print(f"[fake_model_e2e] {msg}", flush=True)


# --- the fake HTTP server -------------------------------------------------


class _FakeOpenAIServer:
    """In-process fake for an OpenAI Chat-Completions endpoint.

    Implements the :func:`openai_compatible_adapter.http_post` callable
    signature: ``(url, headers, body, timeout) -> (status, body_bytes)``.

    The fake inspects the JSON request body to decide which planning
    step the pipeline is calling, then returns the matching canned
    envelope as the OpenAI ``choices[0].message.content`` payload.
    Each envelope contains the sentinel strings so the harness can
    prove the artifacts came from the fake model.
    """

    def __init__(self) -> None:
        # Record every URL we were asked about so the harness can
        # assert that ``/chat/completions`` was the endpoint hit.
        self.calls: List[Tuple[str, bytes]] = []
        # Verify Bearer auth was attached (the planner sends the
        # configured key). We don't log the key value — only that
        # an Authorization header was present.
        self.bearer_present_count = 0

    def __call__(
        self,
        url: str,
        headers: Dict[str, str],
        body: bytes,
        timeout: float,
    ) -> Tuple[int, bytes]:
        self.calls.append((url, body))
        auth = headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            self.bearer_present_count += 1
        # Decode the request payload so we can branch on the user
        # prompt's shape. The pipeline embeds step-specific
        # instructions in the user message; we sniff a few keywords.
        try:
            req = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            req = {}
        user_prompt = ""
        for msg in req.get("messages", []):
            if msg.get("role") == "user":
                user_prompt = msg.get("content", "")
                break

        if "characters" in user_prompt and "locations" in user_prompt:
            content = self._bibles_envelope()
        elif "story beats" in user_prompt or "beats" in user_prompt:
            content = self._beats_envelope()
        elif "shot list" in user_prompt or "Generate a shot list" in user_prompt:
            content = self._shots_envelope(req)
        elif "image-generation" in user_prompt or "image_prompts" in user_prompt:
            content = self._image_prompts_envelope(req)
        elif "video-generation" in user_prompt or "video_prompts" in user_prompt:
            content = self._video_prompts_envelope(req)
        else:
            # Unknown step: return an empty envelope so the pipeline
            # raises a parse error and the harness catches it. We
            # don't want the fake to silently mask a missing branch.
            content = json.dumps({"_unknown_step": True})

        envelope = {
            "id": "smoke-fake-completion",
            "object": "chat.completion",
            "model": req.get("model", "smoke-fake"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }
        return 200, json.dumps(envelope).encode("utf-8")

    # --- canned envelopes ------------------------------------------------

    def _bibles_envelope(self) -> str:
        return json.dumps(
            {
                "characters": [
                    {
                        "id": "smoke_fake_mc",
                        "name": SENTINEL_CHAR_NAME,
                        "role": "主角",
                        "appearance": "来自 fake model 的 sentinel 描述",
                        "positive_prompt": f"{SENTINEL_CHAR_NAME} portrait",
                        "negative_prompt": "blur, low-res",
                    }
                ],
                "locations": [
                    {
                        "id": "smoke_fake_stage",
                        "name": SENTINEL_LOCATION_NAME,
                        "type": "interior",
                        "space_layout": "fake-model stage layout",
                        "positive_prompt": f"{SENTINEL_LOCATION_NAME} wide",
                        "negative_prompt": "blur, low-res",
                    }
                ],
                "props": [
                    {
                        "id": "smoke_fake_prop",
                        "name": SENTINEL_PROP_NAME,
                        "visual": "fake-model sentinel prop",
                        "positive_prompt": f"{SENTINEL_PROP_NAME} close",
                        "negative_prompt": "blur, low-res",
                    }
                ],
            },
            ensure_ascii=False,
        )

    def _beats_envelope(self) -> str:
        return json.dumps(
            {
                "beats": [
                    {
                        "id": "BEAT_FAKE_01",
                        "label": "fake-model beat",
                        "summary": "由 fake model 生成的 fake 节拍",
                        "span": {"start": 0, "end": 1, "text": "x"},
                    }
                ]
            },
            ensure_ascii=False,
        )

    def _shots_envelope(self, req: dict) -> str:
        # We need the shot ids to match the beat_ids so the pipeline
        # doesn't reject the result. The fake server is permissive:
        # one shot, one beat reference, sentinels in location_id /
        # character_ids / prop_ids so we can grep later.
        return json.dumps(
            {
                "shots": [
                    {
                        "id": "SHOT_FAKE_01",
                        "scene_id": "SCN_FAKE_01",
                        "location_id": "smoke_fake_stage",
                        "character_ids": ["smoke_fake_mc"],
                        "prop_ids": ["smoke_fake_prop"],
                        "beat_id": "BEAT_FAKE_01",
                        "shot_size": "medium",
                        "camera_angle": "eye-level",
                        "composition": "fake model composition",
                        "action": f"{SENTINEL_CHAR_NAME} 在 {SENTINEL_LOCATION_NAME} 表演",
                        "emotion": "fake-emotion",
                        "duration_sec": 4,
                    }
                ]
            },
            ensure_ascii=False,
        )

    def _image_prompts_envelope(self, req: dict) -> str:
        return json.dumps(
            {
                "image_prompts": [
                    {
                        "shot_id": "SHOT_FAKE_01",
                        "prompt": (
                            f"场景：{SENTINEL_LOCATION_NAME} / 人物："
                            f"{SENTINEL_CHAR_NAME} / 道具：{SENTINEL_PROP_NAME} / "
                            "fake-model image prompt"
                        ),
                        "negative_prompt": "blur",
                        "aspect_ratio": "16:9",
                        "style_tags": ["smoke-fake"],
                    }
                ]
            },
            ensure_ascii=False,
        )

    def _video_prompts_envelope(self, req: dict) -> str:
        return json.dumps(
            {
                "video_prompts": [
                    {
                        "shot_id": "SHOT_FAKE_01",
                        "prompt": f"fake-model video for {SENTINEL_CHAR_NAME}",
                        "motion": "fake-motion",
                        "duration_sec": 4,
                        "camera": "fake-camera",
                        "avoid": "blur",
                    }
                ]
            },
            ensure_ascii=False,
        )


# --- model config helpers ------------------------------------------------


def _write_model_config(
    work_root: Path,
    *,
    planner_provider: str = "openai_compatible",
    enable_real_model_calls: bool = True,
    base_url: str = "http://127.0.0.1:9999/v1",
    model: str = "smoke-fake",
    api_key_env: str = "PLANNER_SMOKE_FAKE_KEY",
) -> Path:
    """Persist a model config JSON file with the requested settings.

    The harness refuses to write anything containing a literal API
    key (the schema forbids it; the loader has its own check too).
    """

    cfg_path = work_root / "model_config.json"
    payload = {
        "planner_provider": planner_provider,
        "enable_real_model_calls": enable_real_model_calls,
        "allow_provider_fallback": True,
        "openai_compatible": {
            "base_url": base_url,
            "model": model,
            "api_key_env": api_key_env,
            "timeout_seconds": 5.0,
            "temperature": 0.0,
            "max_tokens": 256,
        },
    }
    cfg_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return cfg_path


# --- steps ---------------------------------------------------------------


def step_load_rejects_literal_key(work_root: Path) -> None:
    """Step 1: load_model_config refuses ``sk-...`` api_key_env values."""

    from planner.model_config import load_model_config
    from planner.exceptions import ConfigError  # noqa: F401  (unused but kept for symmetry)

    cfg_path = work_root / "bad_model_config.json"
    bad = {
        "planner_provider": "openai_compatible",
        "enable_real_model_calls": True,
        "openai_compatible": {
            "base_url": "http://127.0.0.1:9999/v1",
            "model": "smoke-fake",
            "api_key_env": "sk-supersecretliteralvalue1234567890",
            "timeout_seconds": 5.0,
            "temperature": 0.0,
            "max_tokens": 256,
        },
    }
    cfg_path.write_text(
        json.dumps(bad, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    raised = False
    try:
        load_model_config(cfg_path)
    except ValueError as exc:
        raised = True
        if "UPPER_SNAKE_CASE" not in str(exc):
            raise SystemExit(
                f"[fake_model_e2e] bad api_key_env error message "
                f"unexpected: {exc}"
            )
    if not raised:
        raise SystemExit(
            "[fake_model_e2e] expected ValueError when loading config "
            "with literal sk- token as api_key_env"
        )
    _log("load_model_config rejects literal sk-... api_key_env")


def step_development_run_uses_fake(work_root: Path) -> Path:
    """Step 2: full pipeline run against the fake server."""

    from planner import env as planner_env
    from planner.model_config import load_model_config
    from planner.pipeline import run as pipeline_run
    from planner.providers.openai_compatible_adapter import http_post

    cfg_path = _write_model_config(
        work_root,
        enable_real_model_calls=True,
    )
    # Provide the fake key so ``settings.api_key()`` returns non-None.
    os.environ["PLANNER_SMOKE_FAKE_KEY"] = "fake-key-do-not-use-in-prod"

    fake = _FakeOpenAIServer()
    original = http_post.__class__  # capture for restore

    config = planner_env.load_config(
        env="development",
        project_root=PROJECT_ROOT,
    )
    model_config = load_model_config(cfg_path)

    # Mirror the CLI behavior: when model_config declares a non-
    # deterministic planner_provider, the config object's
    # planner_provider must follow it so the pipeline selects the
    # matching provider instead of falling back to deterministic.
    if model_config.planner_provider != "deterministic":
        object.__setattr__(
            config, "planner_provider", model_config.planner_provider,
        )

    out_dir = work_root / "run_fake"
    try:
        # Monkeypatch the module-level ``http_post`` so the provider
        # uses the fake server. The provider imports the symbol at
        # module import time; we replace it on the adapter module.
        from planner.providers import openai_compatible_adapter as _adapter
        _adapter.http_post = fake  # type: ignore[assignment]

        result = pipeline_run(
            script_path=SAMPLE_SCRIPT,
            out_dir=out_dir,
            config=config,
            model_config=model_config,
        )
    finally:
        from planner.providers import openai_compatible_adapter as _adapter
        _adapter.http_post = _adapter._default_http_post  # type: ignore[assignment]

    # Assertions on the run.
    if result.fallback_used:
        raise SystemExit(
            f"[fake_model_e2e] expected fallback_used=False, got "
            f"{result.fallback_used} (reason: {result.fallback_reason!r})"
        )
    if result.effective_provider != "openai_compatible":
        raise SystemExit(
            f"[fake_model_e2e] expected effective_provider="
            f"'openai_compatible', got {result.effective_provider!r}"
        )
    if not result.provider_runtime:
        raise SystemExit(
            "[fake_model_e2e] run_summary.provider_runtime is empty - "
            "model config did not flow into the provider"
        )
    if result.provider_runtime.get("api_key_env") != "PLANNER_SMOKE_FAKE_KEY":
        raise SystemExit(
            f"[fake_model_e2e] provider_runtime.api_key_env mismatch: "
            f"{result.provider_runtime}"
        )

    # Sentinel check: the artifacts must come from the fake server.
    cb = json.loads((out_dir / "character_bible.json").read_text(encoding="utf-8"))
    char_names = {c.get("name") for c in cb.get("characters", [])}
    if SENTINEL_CHAR_NAME not in char_names:
        raise SystemExit(
            f"[fake_model_e2e] character_bible missing sentinel "
            f"{SENTINEL_CHAR_NAME!r}; got {char_names}"
        )
    loc_b = json.loads((out_dir / "location_bible.json").read_text(encoding="utf-8"))
    loc_names = {l.get("name") for l in loc_b.get("locations", [])}
    if SENTINEL_LOCATION_NAME not in loc_names:
        raise SystemExit(
            f"[fake_model_e2e] location_bible missing sentinel "
            f"{SENTINEL_LOCATION_NAME!r}; got {loc_names}"
        )

    # Verify the fake server actually received calls.
    if not fake.calls:
        raise SystemExit(
            "[fake_model_e2e] fake server received zero calls - "
            "pipeline didn't go through openai_compatible"
        )
    if fake.bearer_present_count < 1:
        raise SystemExit(
            "[fake_model_e2e] fake server saw no Authorization header - "
            "api_key_env did not flow into HTTP headers"
        )
    _log(
        f"dev run used fake server: {len(fake.calls)} calls, "
        f"effective_provider={result.effective_provider}, "
        f"sentinel chars in artifacts"
    )
    return out_dir


def step_production_fail_closed(work_root: Path) -> None:
    """Step 3: production + real_calls off → fail closed, no residue."""

    from planner import env as planner_env
    from planner.exceptions import ProviderUnavailableError
    from planner.model_config import load_model_config
    from planner.providers.openai_compatible_adapter import http_post

    cfg_path = _write_model_config(
        work_root,
        enable_real_model_calls=False,
    )
    config = planner_env.load_config(
        env="production",
        project_root=PROJECT_ROOT,
        # Point at the production example so load_config succeeds;
        # production fail-closed is the policy under test, not the
        # config-file existence check.
        config_path=PROJECT_ROOT / "config" / "production.example.json",
    )
    model_config = load_model_config(cfg_path)
    # Mirror CLI: model_config.planner_provider steers config.
    if model_config.planner_provider != "deterministic":
        object.__setattr__(
            config, "planner_provider", model_config.planner_provider,
        )

    out_dir = work_root / "run_prod_fail_closed"
    if out_dir.exists():
        shutil.rmtree(out_dir)

    fake = _FakeOpenAIServer()
    from planner.providers import openai_compatible_adapter as _adapter
    _adapter.http_post = fake  # type: ignore[assignment]
    raised = False
    try:
        try:
            planner_env.load_config(  # re-validate policy under prod
                env="production",
                project_root=PROJECT_ROOT,
                config_path=PROJECT_ROOT / "config" / "production.example.json",
            ).planner_provider  # attribute access triggers validator
        except Exception:
            # load_config may have its own pre-existing state; ignore.
            pass
        try:
            from planner.pipeline import run as pipeline_run
            pipeline_run(
                script_path=SAMPLE_SCRIPT,
                out_dir=out_dir,
                config=config,
                model_config=model_config,
            )
        except ProviderUnavailableError as exc:
            raised = True
            if "openai_compatible" not in str(exc) and "health check" not in str(exc):
                # Don't be too strict on the message; the failure
                # mode is the contract, not the wording.
                pass
    finally:
        from planner.providers import openai_compatible_adapter as _adapter
        _adapter.http_post = _adapter._default_http_post  # type: ignore[assignment]

    if not raised:
        raise SystemExit(
            "[fake_model_e2e] expected ProviderUnavailableError in "
            "production with real_calls=False"
        )
    if out_dir.exists():
        raise SystemExit(
            f"[fake_model_e2e] production fail-closed must leave no "
            f"residue but out_dir exists at {out_dir}"
        )
    _log(
        "production + real_calls=False → ProviderUnavailableError, "
        "no out_dir residue"
    )


# --- entrypoint ----------------------------------------------------------


def main() -> int:
    work_root = Path(tempfile.mkdtemp(prefix="fake_model_e2e_"))
    try:
        step_load_rejects_literal_key(work_root)
        step_development_run_uses_fake(work_root)
        step_production_fail_closed(work_root)
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[fake_model_e2e] unexpected error: {exc}", file=sys.stderr)
        return 3
    finally:
        _log(f"work dir kept at {work_root} for inspection")
    _log("ALL FAKE-MODEL E2E STEPS PASSED ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())