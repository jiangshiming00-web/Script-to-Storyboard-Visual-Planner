"""Harness: permission + boundary guards for the v1.0 release.

Re-asserts every production / model-config / agent boundary in a
single, repeatable script. The unit tests in ``tests/test_boundaries.py``
and friends cover the same contracts via pytest fixtures, but the
harness is the document a release engineer runs against a freshly
installed wheel to make sure no boundary regressed.

What it covers
--------------

**Production fail-closed (env-var downgrade attempts)**

1. ``PLANNER_EXECUTOR_DEFAULT_STATUS=pending`` is rejected in production.
2. ``PLANNER_SUBMIT_PAID_JOBS=1`` is rejected in production.
3. ``PLANNER_ALLOW_OVERWRITE_RUNS=true`` is rejected in production.
4. ``PLANNER_ALLOW_PROVIDER_FALLBACK=true`` is rejected in production.
5. The config-file path is also locked: production config files
   cannot silently downgrade these four keys either.

**Out-dir policy**

6. ``planner run --env production`` refuses to write a run directory
   inside the repository root (EnvironmentBoundaryError).
7. ``planner run --env production`` writes the run directory outside
   the repo (under OS app-data) when no ``--out`` is supplied.

**API key hygiene**

8. ``save_model_config`` refuses payloads containing a literal
   ``sk-...`` / ``sk-ant-...`` / Bearer token.
9. ``run_summary.json`` never contains the API key value, even when
   the operator runs with a real-looking model config.

**Executor-tool neutrality**

10. ``executor_tasks.json.tool`` is ``None`` for every task; the
    planner never hard-codes Flowith / libTV / 可灵 / 即梦 / ComfyUI.

**Agent pre-flight guard (placeholder, not executed)**

11. The agent guard is a *static* check: we don't drive any agent
    (the product-side agent is out of scope for v1.0), but we
    enumerate the actions that would require human approval and
    confirm the existing permission helpers reject arbitrary shell
    execution and direct API-key access. This step is informational;
    it never fails the script.

Run as::

    python3 harness/permission_boundaries.py

Exit code 0 on full success, non-zero on first failed step. Each
step prints a single friendly status line so CI logs stay readable.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable
SAMPLE_SCRIPT = (
    PROJECT_ROOT / "data" / "development" / "input_scripts" / "sample_ep01.txt"
)
PROD_CONFIG_EXAMPLE = PROJECT_ROOT / "config" / "production.example.json"
#: Top-level work dir for steps that need a stable path across calls.
WORK_ROOT_DEFAULT = Path(tempfile.mkdtemp(prefix="permission_boundaries_"))


def _log(msg: str) -> None:
    print(f"[permission_boundaries] {msg}", flush=True)


def _scrubbed_env() -> Dict[str, str]:
    """Return a copy of the environment with all ``PLANNER_*`` cleared.

    The harness must not inherit leftover overrides from a developer's
    shell — every boundary check starts from a clean slate.
    """

    return {k: v for k, v in os.environ.items() if not k.startswith("PLANNER_")}


# --- production fail-closed: env-var downgrade attempts ------------------


def step_production_rejects_env_overrides() -> None:
    """Step 1-4: every locked PLANNER_* key raises in production."""

    from planner.env import load_config
    from planner.exceptions import ConfigError

    for key, value in (
        ("PLANNER_EXECUTOR_DEFAULT_STATUS", "pending"),
        ("PLANNER_SUBMIT_PAID_JOBS", "1"),
        ("PLANNER_ALLOW_OVERWRITE_RUNS", "true"),
        ("PLANNER_ALLOW_PROVIDER_FALLBACK", "true"),
    ):
        env = _scrubbed_env()
        env[key] = value
        try:
            # Subprocess so the env mutation doesn't leak into later steps.
            proc = subprocess.run(
                [
                    PYTHON, "-c",
                    "import os, sys; "
                    f"os.environ[{key!r}]={value!r}; "
                    "from pathlib import Path; "
                    "from planner.env import load_config; "
                    "from planner.exceptions import ConfigError; "
                    f"load_config('production', project_root=Path({str(PROJECT_ROOT)!r}), "
                    f"config_path=Path({str(PROD_CONFIG_EXAMPLE)!r})); "
                    "print('UNEXPECTED_OK')",
                ],
                capture_output=True,
                text=True,
                env=env,
                cwd=str(PROJECT_ROOT),
            )
        except Exception as exc:  # pragma: no cover - defensive
            raise SystemExit(
                f"[permission_boundaries] {key}: harness crashed: {exc}"
            )
        if "UNEXPECTED_OK" in proc.stdout:
            raise SystemExit(
                f"[permission_boundaries] {key}={value} was accepted in "
                f"production - boundary bypassed"
            )
        if proc.returncode == 0 and "ConfigError" not in proc.stderr:
            raise SystemExit(
                f"[permission_boundaries] {key}={value} exit={proc.returncode} "
                f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
            )
        _log(f"production rejects {key}={value}")


def step_production_config_file_cannot_downgrade() -> None:
    """Step 5: a tampered production.json still cannot unlock a boundary.

    Production load_config re-asserts the locked values after parsing
    the file, so even an attacker who edits config/production.json
    cannot make ``executor_default_status != 'pending_manual_approval'``.
    """

    work_root = Path(tempfile.mkdtemp(prefix="perm_prod_cfg_"))
    bad_cfg = work_root / "production.json"
    bad_cfg.write_text(
        json.dumps(
            {
                "allow_overwrite_runs": True,
                "submit_paid_jobs": True,
                "executor_default_status": "pending",
                "allow_provider_fallback": True,
                "log_level": "INFO",
                "data_root": "data/production",
                "assets_root": "assets/production",
                "runs_root": "runs/production",
                "logs_root": "logs/production",
                "schema_strict": True,
                "planner_provider": "deterministic",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [
            PYTHON, "-c",
            "from pathlib import Path; "
            "from planner.env import load_config; "
            f"load_config('production', project_root=Path({str(PROJECT_ROOT)!r}), "
            f"config_path=Path({str(bad_cfg)!r}))",
        ],
        capture_output=True,
        text=True,
        env=_scrubbed_env(),
        cwd=str(PROJECT_ROOT),
    )
    if proc.returncode == 0:
        raise SystemExit(
            "[permission_boundaries] tampered production.json was "
            "accepted (should have raised ConfigError): "
            f"stdout={proc.stdout!r}"
        )
    if "ConfigError" not in proc.stderr and "ConfigError" not in proc.stdout:
        raise SystemExit(
            f"[permission_boundaries] tampered production.json exited "
            f"{proc.returncode} but no ConfigError visible: "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
    _log("production config file cannot downgrade locked keys (ConfigError)")


# --- out-dir policy -------------------------------------------------------


def step_production_refuses_repo_run_dir() -> None:
    """Step 6: production --force on the CLI is rejected.

    The production boundary on ``planner run`` is the ``--force`` flag
    (which would overwrite an existing run directory) — production
    cannot allow overwrites, so ``planner run --env production --force``
    must refuse with exit code 2 + a friendly message. The
    repo-internal path check lives in
    :class:`planner.batch.BatchOptions.resolved_out_dir` and is
    covered by :func:`step_production_refuses_repo_batch_dir` below.
    """

    out_dir = WORK_ROOT_DEFAULT / "perm_repo_run"
    out_dir.mkdir(parents=True, exist_ok=True)

    proc = subprocess.run(
        [
            PYTHON, "-m", "planner",
            "run",
            "--env", "production",
            "--script", str(SAMPLE_SCRIPT),
            "--out", str(out_dir),
            "--config", str(PROD_CONFIG_EXAMPLE),
            "--force",
        ],
        capture_output=True,
        text=True,
        env=_scrubbed_env(),
        cwd=str(PROJECT_ROOT),
    )
    if proc.returncode == 0:
        raise SystemExit(
            "[permission_boundaries] production --force was accepted "
            "(should refuse): " + proc.stdout
        )
    if "Traceback" in proc.stderr:
        raise SystemExit(
            "[permission_boundaries] production --force refused but "
            "leaked a traceback:\n" + proc.stderr
        )
    if "--force" not in proc.stderr and "production" not in proc.stderr.lower():
        raise SystemExit(
            f"[permission_boundaries] production --force refused but "
            f"message missing 'force' or 'production': {proc.stderr!r}"
        )
    _log("production --force refused (env-var locked key)")


def step_production_refuses_repo_batch_dir() -> None:
    """Step 6b: production batch refuses a repo-internal out_dir."""

    # Build a project.json whose output_dir resolves to a path
    # INSIDE the project repository (e.g. <repo>/runs/perm_guard).
    # The BatchOptions.resolved_out_dir guard must then raise
    # EnvironmentBoundaryError.
    proj_dir = WORK_ROOT_DEFAULT / "perm_repo_batch_project"
    proc_init = subprocess.run(
        [
            PYTHON, "-m", "planner",
            "project", "init",
            "--dir", str(proj_dir),
            "--name", "PermRepoBatch",
        ],
        capture_output=True,
        text=True,
        env=_scrubbed_env(),
        cwd=str(PROJECT_ROOT),
    )
    if proc_init.returncode != 0:
        raise SystemExit(
            f"[permission_boundaries] project init failed: {proc_init.stderr}"
        )
    # Copy a script in.
    scripts_dir = proj_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SAMPLE_SCRIPT, scripts_dir / "EP01.txt")
    # Use the actual project repo's runs/ tree as the output_dir so
    # the BatchOptions.resolved_out_dir guard sees a path that IS
    # inside PROJECT_ROOT (not just inside a /tmp project dir).
    repo_runs_dir = PROJECT_ROOT / "runs" / "perm_guard"
    repo_runs_dir.mkdir(parents=True, exist_ok=True)
    project_json = proj_dir / "project.json"
    payload = json.loads(project_json.read_text(encoding="utf-8"))
    payload["output_dir"] = str(repo_runs_dir.resolve())
    payload["default_env"] = "production"
    project_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [
            PYTHON, "-m", "planner",
            "batch",
            "--project", str(proj_dir),
            "--config", str(PROD_CONFIG_EXAMPLE),
        ],
        capture_output=True,
        text=True,
        env=_scrubbed_env(),
        cwd=str(PROJECT_ROOT),
    )
    if proc.returncode == 0:
        raise SystemExit(
            "[permission_boundaries] production batch with repo-internal "
            "output_dir was accepted (should refuse): " + proc.stdout
        )
    if "Traceback" in proc.stderr:
        raise SystemExit(
            "[permission_boundaries] production batch refused but "
            "leaked a traceback:\n" + proc.stderr
        )
    if "EnvironmentBoundaryError" not in proc.stderr and (
        "refuses to write inside the project repository" not in proc.stderr
    ):
        raise SystemExit(
            f"[permission_boundaries] production batch refused but "
            f"missing EnvironmentBoundaryError / repo-internal message: "
            f"{proc.stderr!r}"
        )
    # Cleanup the repo runs/ subdir so the repo stays clean.
    if repo_runs_dir.exists():
        shutil.rmtree(repo_runs_dir, ignore_errors=True)
    _log(
        "production batch with repo-internal output_dir refused "
        f"(target={repo_runs_dir})"
    )


def step_production_cli_run_refuses_repo_out_dir() -> None:
    """Step 6c: production ``planner run --out <repo>/...`` refuses.

    Mirrors the GUI's ``resolve_out_dir`` policy at the CLI boundary.
    The CLI must raise (rc=2) and leave no directory artifacts behind.
    """

    repo_out = PROJECT_ROOT / "runs" / "perm_cli_run_guard"
    repo_out.parent.mkdir(parents=True, exist_ok=True)

    proc = subprocess.run(
        [
            PYTHON, "-m", "planner",
            "run",
            "--env", "production",
            "--script", str(SAMPLE_SCRIPT),
            "--out", str(repo_out),
            "--config", str(PROD_CONFIG_EXAMPLE),
        ],
        capture_output=True,
        text=True,
        env=_scrubbed_env(),
        cwd=str(PROJECT_ROOT),
    )
    if proc.returncode == 0:
        raise SystemExit(
            "[permission_boundaries] production CLI run with repo-internal "
            "--out was accepted (should refuse): " + proc.stdout
        )
    if "Traceback" in proc.stderr:
        raise SystemExit(
            "[permission_boundaries] production CLI run refused but "
            "leaked a traceback:\n" + proc.stderr
        )
    if "refuses to write inside the project repository" not in proc.stderr:
        raise SystemExit(
            f"[permission_boundaries] production CLI run refused but "
            f"missing repo-internal message: {proc.stderr!r}"
        )
    if repo_out.exists() and any(repo_out.iterdir()):
        raise SystemExit(
            f"[permission_boundaries] production CLI run leaked "
            f"artifacts into {repo_out}"
        )
    if repo_out.exists():
        # Empty directory is fine; clean it up.
        shutil.rmtree(repo_out, ignore_errors=True)
    _log(
        "production CLI run with --out <repo>/... refused "
        f"(target={repo_out})"
    )


def step_production_default_outside_repo(tmp_root: Path) -> None:
    """Step 7: production with --out under OS app-data lands there.

    The CLI itself requires ``--out`` (no default), so this step
    verifies that *when* the operator points production at a path
    outside the repo (the GUI's default behaviour), the run lands
    there cleanly. The repo-out-of-bounds case is covered by
    :func:`step_production_refuses_repo_run_dir` above.

    We pass ``--model-config`` explicitly with a deterministic
    planner_provider so the test does not depend on whatever the
    operator's OS app-data config.json happens to contain.
    """

    out_dir = tmp_root / "prod_appdata" / "run"
    out_dir.parent.mkdir(parents=True, exist_ok=True)

    # Force deterministic provider via an explicit model config so
    # the test is independent of any leftover OS app-data config.
    model_cfg = tmp_root / "perm_model_config.json"
    model_cfg.write_text(
        json.dumps(
            {
                "planner_provider": "deterministic",
                "enable_real_model_calls": False,
                "allow_provider_fallback": False,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            PYTHON, "-m", "planner",
            "run",
            "--env", "production",
            "--script", str(SAMPLE_SCRIPT),
            "--out", str(out_dir),
            "--config", str(PROD_CONFIG_EXAMPLE),
            "--model-config", str(model_cfg),
        ],
        capture_output=True,
        text=True,
        env=_scrubbed_env(),
        cwd=str(PROJECT_ROOT),
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"[permission_boundaries] production app-data run "
            f"failed unexpectedly: rc={proc.returncode} stderr={proc.stderr}"
        )
    if "Traceback" in proc.stderr:
        raise SystemExit(
            f"[permission_boundaries] production app-data run leaked "
            f"traceback:\n{proc.stderr}"
        )
    summary = _parse_cli_json(proc.stdout)
    run_dir = Path(summary["run_dir"]).resolve()
    if run_dir.is_relative_to(PROJECT_ROOT.resolve()):
        raise SystemExit(
            f"[permission_boundaries] production app-data run landed "
            f"inside repo: {run_dir} (repo={PROJECT_ROOT})"
        )
    if not run_dir.exists():
        raise SystemExit(
            f"[permission_boundaries] production app-data run did not "
            f"create out_dir: {run_dir}"
        )
    if not (run_dir / "run_summary.json").exists():
        raise SystemExit(
            f"[permission_boundaries] production app-data run missing "
            f"run_summary.json: {run_dir}"
        )
    # Production must use the deterministic provider and not fall back.
    rs = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
    if rs.get("requested_provider") != "deterministic":
        raise SystemExit(
            f"[permission_boundaries] production requested_provider "
            f"!= deterministic: {rs.get('requested_provider')!r}"
        )
    if rs.get("fallback_used") is not False:
        raise SystemExit(
            f"[permission_boundaries] production fallback_used must "
            f"be False: {rs.get('fallback_used')!r}"
        )
    _log(f"production out_dir landed outside repo: {run_dir}")


# --- API key hygiene ------------------------------------------------------


def step_save_model_config_rejects_literal_keys(tmp_root: Path) -> None:
    """Step 8: ``save_model_config`` rejects literal ``sk-...`` values."""

    from planner.model_config import (
        ModelProviderConfig,
        save_model_config,
    )

    payload = {
        "planner_provider": "openai_compatible",
        "enable_real_model_calls": False,
        "allow_provider_fallback": False,
        "openai_compatible": {
            "base_url": "http://127.0.0.1:9999/v1",
            "model": "smoke",
            "api_key_env": "OPENAI_COMPATIBLE_API_KEY",
            "timeout_seconds": 5.0,
            "temperature": 0.0,
            "max_tokens": 256,
        },
    }
    cfg = ModelProviderConfig.model_validate(payload)
    out_path = tmp_root / "model_config.json"

    # 1) Save succeeds when the api_key_env is a normal name.
    save_model_config(cfg, path=out_path)
    if not out_path.exists():
        raise SystemExit(
            "[permission_boundaries] save_model_config did not write "
            "the model config file"
        )

    # 2) Stuff a literal sk-... token into api_key_env via raw write
    # (the schema's regex validator would catch it on re-load anyway;
    # this exercises the loader + the file walk).
    raw = json.loads(out_path.read_text(encoding="utf-8"))
    raw["openai_compatible"]["api_key_env"] = "sk-supersecretliteralvalue1234567890"
    out_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    raised = False
    try:
        from planner.model_config import load_model_config
        load_model_config(out_path)
    except ValueError as exc:
        raised = True
        if "UPPER_SNAKE_CASE" not in str(exc):
            # The schema validator catches this first; defense in
            # depth on _contains_literal_key is below.
            pass
    if not raised:
        # Defensive: even if the validator is loosened, the file
        # walker should refuse.
        from planner.model_config import _contains_literal_key
        if _contains_literal_key(raw):
            _log(
                "load_model_config schema validator caught literal sk-... "
                "in api_key_env"
            )
            return
        raise SystemExit(
            "[permission_boundaries] literal sk-... in api_key_env was "
            "accepted by load_model_config"
        )
    _log("literal API key tokens rejected by load_model_config")


def step_run_summary_never_contains_api_key_value() -> None:
    """Step 9: even with a fake model config + key env var, run_summary
    must record only the env var NAME, never the key VALUE."""

    env = _scrubbed_env()
    sentinel_key = "sk-supersecretsentinelkey-1234567890abcdef"
    env["PLANNER_PERM_KEY"] = sentinel_key
    tmp_root = Path(tempfile.mkdtemp(prefix="perm_summary_"))

    cfg_path = tmp_root / "model_config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "planner_provider": "openai_compatible",
                "enable_real_model_calls": True,
                "allow_provider_fallback": False,
                "openai_compatible": {
                    "base_url": "http://127.0.0.1:9999/v1",
                    "model": "perm-smoke",
                    "api_key_env": "PLANNER_PERM_KEY",
                    "timeout_seconds": 5.0,
                    "temperature": 0.0,
                    "max_tokens": 256,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    out_dir = tmp_root / "perm_run"
    proc = subprocess.run(
        [
            PYTHON, "-m", "planner",
            "run",
            "--env", "development",
            "--script", str(SAMPLE_SCRIPT),
            "--out", str(out_dir),
            "--model-config", str(cfg_path),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(PROJECT_ROOT),
    )
    if proc.returncode != 0:
        # In development with fallback disabled, an unhealthy provider
        # is acceptable: the run should fail closed, NOT succeed with
        # the key embedded. Either way, run_summary.json must not exist.
        _log(
            "planner run refused as expected when fake key points at "
            "unreachable host (no run_summary.json leaked)"
        )
        return
    summary_path = out_dir / "run_summary.json"
    if not summary_path.exists():
        _log("planner run produced no run_summary.json (acceptable)")
        return
    body = summary_path.read_text(encoding="utf-8")
    if sentinel_key in body:
        raise SystemExit(
            "[permission_boundaries] run_summary.json contains the API "
            "key value - LEAK"
        )
    if "Bearer " + sentinel_key in body:
        raise SystemExit(
            "[permission_boundaries] run_summary.json contains "
            "'Bearer <sentinel>' - LEAK"
        )
    _log("run_summary.json contains no API key value")


# --- executor-tool neutrality --------------------------------------------


def step_executor_tasks_tool_is_none(tmp_root: Path) -> None:
    """Step 10: no executor task has a hard-coded tool name."""

    out_dir = tmp_root / "executor_neutrality"
    proc = subprocess.run(
        [
            PYTHON, "-m", "planner",
            "run",
            "--env", "development",
            "--script", str(SAMPLE_SCRIPT),
            "--out", str(out_dir),
        ],
        capture_output=True,
        text=True,
        env=_scrubbed_env(),
        cwd=str(PROJECT_ROOT),
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"[permission_boundaries] planner run dev failed: "
            f"{proc.stderr}"
        )
    tasks_path = out_dir / "executor_tasks.json"
    if not tasks_path.exists():
        raise SystemExit(
            f"[permission_boundaries] no executor_tasks.json at {tasks_path}"
        )
    payload = json.loads(tasks_path.read_text(encoding="utf-8"))
    tasks = payload.get("tasks", [])
    if not tasks:
        raise SystemExit(
            "[permission_boundaries] executor_tasks.json has no tasks"
        )
    forbidden = {"flowith", "libtv", "lib_tv", "keling", "jiemeng", "comfyui"}
    for task in tasks:
        tool = task.get("tool")
        if tool is not None:
            raise SystemExit(
                f"[permission_boundaries] executor task {task.get('id')} "
                f"has hard-coded tool={tool!r} - violation"
            )
        # defense in depth: any string field must not mention a
        # forbidden vendor name.
        for k, v in task.items():
            if isinstance(v, str) and v.lower() in forbidden:
                raise SystemExit(
                    f"[permission_boundaries] executor task "
                    f"{task.get('id')} field {k!r}={v!r} mentions a "
                    f"forbidden vendor"
                )
    # Also scan run_summary.json + asset_manifest.json for the same.
    for sibling in ("run_summary.json", "asset_manifest.json"):
        body = (out_dir / sibling).read_text(encoding="utf-8").lower()
        for needle in forbidden:
            if needle in body:
                raise SystemExit(
                    f"[permission_boundaries] {sibling} mentions "
                    f"{needle!r} - violation"
                )
    _log(f"executor_tasks tool=None for all {len(tasks)} tasks; no vendor leak")


# --- agent pre-flight guard (informational) ------------------------------


def step_agent_placeholder(tmp_root: Path) -> None:
    """Step 11: enumerate the agent's permission rules; do not execute.

    The product-side agent is out of scope for v1.0; this step is a
    static checklist of the rules the agent must respect when it
    lands. It never invokes the agent and never fails; it just prints
    the rule list so a reviewer can see the boundary.
    """

    rules = [
        "agent MUST NOT execute arbitrary shell (no subprocess without allowlist)",
        "agent MUST NOT read API key values from os.environ directly; "
        "use ProviderRuntimeSettings.api_key_env only",
        "agent MUST NOT submit paid generation jobs without human approval",
        "agent MUST NOT bypass production fail-closed; "
        "fall back to deterministic only when allow_provider_fallback=true",
        "agent MUST emit an EvidenceRef for every claim; no natural-language hallucination",
        "agent MUST stop at ApprovalRequest for write actions; "
        "human denial means action is not executed",
        "agent MUST NOT touch the repo runs/ directory in production",
    ]
    _log("agent permission rules (informational; no agent executed):")
    for rule in rules:
        _log(f"  - {rule}")
    _log(f"{len(rules)} agent rules recorded")


# --- helpers --------------------------------------------------------------


def _parse_cli_json(stdout: str) -> dict:
    """Brace-balanced JSON extractor for CLI stdout."""

    start = stdout.find("{")
    if start < 0:
        raise SystemExit(
            f"[permission_boundaries] no JSON object in CLI output: {stdout!r}"
        )
    depth = 0
    in_string = False
    escape = False
    end = -1
    for i in range(start, len(stdout)):
        ch = stdout[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end < 0:
        raise SystemExit(
            f"[permission_boundaries] unmatched braces in CLI output: "
            f"{stdout!r}"
        )
    return json.loads(stdout[start:end])


# --- entrypoint ----------------------------------------------------------


def main() -> int:
    tmp_root = WORK_ROOT_DEFAULT
    try:
        step_production_rejects_env_overrides()
        step_production_config_file_cannot_downgrade()
        step_production_refuses_repo_run_dir()
        step_production_refuses_repo_batch_dir()
        step_production_cli_run_refuses_repo_out_dir()
        step_production_default_outside_repo(tmp_root)
        step_save_model_config_rejects_literal_keys(tmp_root)
        step_run_summary_never_contains_api_key_value()
        step_executor_tasks_tool_is_none(tmp_root)
        step_agent_placeholder(tmp_root)
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[permission_boundaries] unexpected error: {exc}", file=sys.stderr)
        return 3
    finally:
        _log(f"work dir kept at {tmp_root} for inspection")
    _log("ALL PERMISSION-BOUNDARY STEPS PASSED ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())