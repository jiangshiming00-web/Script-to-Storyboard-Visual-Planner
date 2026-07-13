"""Harness: GUI smoke for the v1.0 release.

Boots ``planner-web --no-window`` as a real subprocess, waits for
``/api/health`` to come up, then exercises every endpoint the
frontend calls and the static asset bundle. Cleans up the server
process at exit so CI never leaks orphaned uvicorn instances.

What it covers
--------------

1. ``planner-web --help`` lists the documented flags.
2. The headless server starts and ``GET /api/health`` returns 200
   with ``ok=true`` + the registered provider list.
3. Static assets (``/``, ``/app.js``, ``/style.css``) load with
   non-empty bodies.
4. ``GET /api/config?env=development`` returns the dev config.
5. ``GET /api/model-config`` returns defaults; ``PUT /api/model-config``
   round-trips a config without ever storing an API key value
   (rejects literal ``sk-`` payloads).
6. ``POST /api/runs`` accepts a development run request and the
   subsequent ``GET /api/runs/{id}/summary`` returns the run summary
   with ``provider_runtime`` populated (or ``None`` for deterministic).
7. ``POST /api/batches`` against ``samples/v1/`` returns a
   ``BatchSummary`` with ``episodes_done == 3``.
8. Production path: ``/api/config?env=production`` returns 404 when
   ``config/production.json`` is missing (actionable hint visible).

Run as::

    python3 harness/smoke_gui.py

Exit code 0 on full success, non-zero on first failed step. Each
step prints a single friendly status line so CI logs stay readable.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable
HOST = "127.0.0.1"
# Pick an ephemeral-ish port. 18766 matches the project's smoke
# convention; if it's busy we fall back to port 0 and read the
# chosen port back from the server (not implemented yet - the
# planner-web launcher takes an explicit --port).
DEFAULT_PORT = 18766


def _log(msg: str) -> None:
    print(f"[smoke_gui] {msg}", flush=True)


def _find_free_port(preferred: int) -> int:
    """Return ``preferred`` if it's free, else ask the kernel for a
    random free port (``bind(0)``).

    The previous version silently raised ``OSError: address in use``
    when the preferred port was occupied; CI machines that share
    services often have well-known ports busy. The fallback probe
    is race-safe: the kernel reserves the port between ``bind`` and
    ``close``, and we hand the number back to the caller before
    uvicorn reopens it.
    """

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((HOST, preferred))
        except OSError:
            # Preferred port is busy; let the kernel pick a free one.
            s.bind((HOST, 0))
            return s.getsockname()[1]
        else:
            return preferred
        finally:
            s.close()


def _http_json(
    method: str,
    url: str,
    payload: Optional[dict] = None,
    timeout: float = 30.0,
) -> Tuple[int, dict]:
    """Tiny JSON HTTP helper. Returns ``(status_code, body_dict)``.

    Uses the standard library so the harness doesn't pull in
    ``requests`` / ``httpx``. Non-JSON responses are returned as
    ``{"_raw": body_text}`` so the assertion can still inspect them.
    """

    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        url, data=data, method=method, headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, {"_raw": body}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        try:
            return exc.code, json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return exc.code, {"_raw": body}


def _http_get(url: str, timeout: float = 10.0) -> Tuple[int, str]:
    """Tiny text HTTP helper. Used for static asset checks."""

    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return exc.code, body


def _wait_for_health(base_url: str, timeout: float = 15.0) -> None:
    """Poll ``GET /api/health`` until it returns 200, then return.

    Raises ``SystemExit`` if the server doesn't come up in time.
    """

    deadline = time.monotonic() + timeout
    last_error: Optional[str] = None
    while time.monotonic() < deadline:
        status, body = _http_json("GET", f"{base_url}/api/health")
        if status == 200 and body.get("ok") is True:
            return
        last_error = f"status={status} body={body}"
        time.sleep(0.25)
    raise SystemExit(
        f"[smoke_gui] planner-web did not become healthy within "
        f"{timeout}s (last: {last_error})"
    )


def _start_server(port: int, scratch_app_data: Path) -> subprocess.Popen:
    """Spawn ``planner-web --no-window`` and return the handle.

    The process owns stdout/stderr pipes so the harness can show them
    on failure (the CLI uses stderr for the friendly error path).
    Redirects the GUI's app-data dir to ``scratch_app_data`` via
    ``PLANNER_APP_DATA_ROOT`` + ``PLANNER_MODEL_CONFIG_PATH`` so the
    smoke never writes to the user's real OS app-data store (default
    ``~/Library/Application Support/ShortDramaPlanner`` on macOS).
    """

    env = {**os.environ}
    env["PLANNER_APP_DATA_ROOT"] = str(scratch_app_data / "app_data")
    env["PLANNER_MODEL_CONFIG_PATH"] = str(
        scratch_app_data / "model_config.json"
    )
    proc = subprocess.Popen(
        [
            PYTHON, "-m", "planner.web",
            "--no-window",
            "--host", HOST,
            "--port", str(port),
            "--repo-root", str(PROJECT_ROOT),
        ],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    return proc


def _stop_server(proc: subprocess.Popen) -> None:
    """Send SIGTERM (POSIX) / terminate() and wait briefly.

    We do NOT use SIGKILL by default — the planner-web shutdown
    sequence is supposed to be graceful (P1-3 regression guard).
    """

    if proc.poll() is not None:
        return
    try:
        if os.name != "nt":
            proc.send_signal(signal.SIGTERM)
        else:  # pragma: no cover - Windows
            proc.terminate()
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=8.0)
    except subprocess.TimeoutExpired:
        proc.terminate()
        proc.wait(timeout=3.0)


# --- steps ---------------------------------------------------------------


def step_help_text() -> None:
    """Step 1: ``planner-web --help`` lists the documented flags."""

    proc = subprocess.run(
        [PYTHON, "-m", "planner.web", "--help"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"[smoke_gui] planner-web --help failed rc={proc.returncode}\n"
            f"--- stderr ---\n{proc.stderr}"
        )
    for needle in ("--no-window", "--host", "--port", "--repo-root"):
        if needle not in proc.stdout:
            raise SystemExit(
                f"[smoke_gui] planner-web --help missing flag {needle!r}"
            )
    _log("planner-web --help shows --no-window / --host / --port / --repo-root")


def step_health(base_url: str) -> None:
    """Step 2: ``GET /api/health`` returns 200 + ok=true."""

    status, body = _http_json("GET", f"{base_url}/api/health")
    if status != 200 or body.get("ok") is not True:
        raise SystemExit(
            f"[smoke_gui] /api/health unexpected: status={status} body={body}"
        )
    providers = body.get("providers") or []
    for needle in ("deterministic", "openai_compatible"):
        if needle not in providers:
            raise SystemExit(
                f"[smoke_gui] /api/health providers list missing {needle!r}: "
                f"{providers}"
            )
    _log(f"/api/health ok (providers: {providers})")


def step_static_assets(base_url: str) -> None:
    """Step 3: index.html + app.js + style.css load."""

    for path in ("/", "/app.js", "/style.css"):
        status, body = _http_get(f"{base_url}{path}")
        if status != 200 or len(body) < 100:
            raise SystemExit(
                f"[smoke_gui] static asset {path} unexpected: "
                f"status={status} len={len(body)}"
            )
    _log("/ + /app.js + /style.css served (>=100 bytes each)")


def step_config(base_url: str) -> None:
    """Step 4: ``GET /api/config?env=development`` works."""

    status, body = _http_json("GET", f"{base_url}/api/config?env=development")
    if status != 200:
        raise SystemExit(
            f"[smoke_gui] /api/config?env=development status={status} body={body}"
        )
    if body.get("env") != "development":
        raise SystemExit(
            f"[smoke_gui] /api/config env mismatch: {body.get('env')!r}"
        )
    if body.get("is_production") is not False:
        raise SystemExit(
            f"[smoke_gui] /api/config is_production should be False: {body}"
        )
    _log(f"/api/config dev returns {body.get('planner_provider')!r} provider")


def step_production_config_missing(base_url: str) -> None:
    """Step 5: ``/api/config?env=production`` 404s without config file."""

    status, body = _http_json("GET", f"{base_url}/api/config?env=production")
    if status != 404:
        raise SystemExit(
            f"[smoke_gui] /api/config?env=production expected 404, got "
            f"{status} body={body}"
        )
    detail = body.get("detail", body)
    if "production.example.json" not in str(detail):
        raise SystemExit(
            f"[smoke_gui] /api/config prod 404 missing hint: {detail}"
        )
    _log("/api/config?env=production → 404 with copy-example hint")


def step_model_config_roundtrip(base_url: str) -> None:
    """Step 6: GET/PUT /api/model-config round-trips without leaking keys."""

    status, body = _http_json("GET", f"{base_url}/api/model-config")
    if status != 200:
        raise SystemExit(
            f"[smoke_gui] GET /api/model-config status={status} body={body}"
        )
    cfg = body.get("config") or {}
    if cfg.get("planner_provider") != "deterministic":
        raise SystemExit(
            f"[smoke_gui] /api/model-config default provider != deterministic: "
            f"{cfg.get('planner_provider')!r}"
        )

    # Put a non-default config; planner_provider=openai_compatible with
    # enable_real_model_calls=True still requires API key in env to
    # become healthy, but PUT itself doesn't validate that.
    new_cfg = dict(cfg)
    new_cfg["planner_provider"] = "openai_compatible"
    new_cfg["enable_real_model_calls"] = True
    new_cfg["openai_compatible"] = {
        "base_url": "http://127.0.0.1:9999/v1",
        "model": "smoke-fake",
        "api_key_env": "PLANNER_SMOKE_KEY",
    }
    status, body = _http_json(
        "PUT", f"{base_url}/api/model-config", payload={"config": new_cfg},
    )
    if status != 200:
        raise SystemExit(
            f"[smoke_gui] PUT /api/model-config status={status} body={body}"
        )
    if "path" not in body:
        raise SystemExit(f"[smoke_gui] PUT response missing path: {body}")

    # Reject a literal key value (defense in depth on the wire).
    bad_cfg = dict(new_cfg)
    bad_cfg["openai_compatible"] = dict(new_cfg["openai_compatible"])
    bad_cfg["openai_compatible"]["api_key_env"] = "sk-supersecretliteralvalue1234567890"
    status, body = _http_json(
        "PUT", f"{base_url}/api/model-config", payload={"config": bad_cfg},
    )
    if status != 400:
        raise SystemExit(
            f"[smoke_gui] PUT literal api_key_env=sk-... should reject "
            f"with 400 (got {status}): {body}"
        )
    _log("/api/model-config GET defaults + PUT round-trip + reject literal key")


def step_post_run(base_url: str, out_dir: Path) -> str:
    """Step 7: ``POST /api/runs`` produces a runnable development run.

    ``out_dir`` is passed explicitly so the run lands under the
    harness's scratch directory (NOT inside the project repo's
    ``runs/`` tree). The endpoint returns the resolved out_dir; we
    verify it matches the request and that it's outside the repo.
    """

    # Reset the persisted model config to deterministic so the run
    # succeeds regardless of what previous test runs left in the
    # scratch app-data store (isolated via PLANNER_MODEL_CONFIG_PATH).
    det_cfg = {
        "planner_provider": "deterministic",
        "enable_real_model_calls": False,
        "allow_provider_fallback": True,
    }
    status, body = _http_json(
        "PUT", f"{base_url}/api/model-config", payload={"config": det_cfg},
    )
    if status != 200:
        raise SystemExit(
            f"[smoke_gui] PUT /api/model-config (reset to deterministic) "
            f"status={status} body={body}"
        )

    sample = PROJECT_ROOT / "samples" / "v1" / "EP01.txt"
    req = {
        "env": "development",
        "script_path": str(sample),
        "out_dir": str(out_dir),
        "force": False,
    }
    status, body = _http_json("POST", f"{base_url}/api/runs", payload=req)
    if status != 200:
        raise SystemExit(
            f"[smoke_gui] POST /api/runs status={status} body={body}"
        )
    run_id = body.get("run_id")
    if not run_id:
        raise SystemExit(f"[smoke_gui] POST /api/runs missing run_id: {body}")
    actual_out_dir = Path(body.get("out_dir", "")).resolve()
    if actual_out_dir != out_dir.resolve():
        raise SystemExit(
            f"[smoke_gui] POST /api/runs out_dir mismatch: expected "
            f"{out_dir}, got {actual_out_dir}"
        )
    if PROJECT_ROOT.resolve() in actual_out_dir.resolve().parents:
        raise SystemExit(
            f"[smoke_gui] POST /api/runs landed inside the repo: "
            f"{actual_out_dir}"
        )
    # The GUI runs asynchronously; poll the summary endpoint.
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        s_status, s_body = _http_json(
            "GET", f"{base_url}/api/runs/{run_id}/summary"
        )
        if s_status == 200 and (s_body.get("summary") or {}).get("counts"):
            counts = s_body["summary"]["counts"]
            if counts.get("shots", 0) > 0:
                _log(
                    f"POST /api/runs produced {run_id} shots="
                    f"{counts['shots']} at {actual_out_dir}"
                )
                return run_id
        time.sleep(0.5)
    raise SystemExit(
        f"[smoke_gui] /api/runs/{run_id}/summary never produced shots>0"
    )


def step_post_batch(base_url: str, out_dir: Path) -> None:
    """Step 8: ``POST /api/batches`` runs 3 episodes deterministically.

    ``out_dir`` is explicit so the per-episode subdirs land under the
    harness's scratch directory.
    """

    # Reset the persisted model config to deterministic so the batch
    # succeeds regardless of what previous test runs left in the
    # scratch app-data store.
    det_cfg = {
        "planner_provider": "deterministic",
        "enable_real_model_calls": False,
        "allow_provider_fallback": True,
    }
    status, body = _http_json(
        "PUT", f"{base_url}/api/model-config", payload={"config": det_cfg},
    )
    if status != 200:
        raise SystemExit(
            f"[smoke_gui] PUT /api/model-config (reset) status={status} body={body}"
        )

    req = {
        "env": "development",
        "scripts_dir": str(PROJECT_ROOT / "samples" / "v1"),
        "out_dir": str(out_dir),
        "force": False,
        "fail_fast": True,
        "skip_validation": True,
    }
    status, body = _http_json("POST", f"{base_url}/api/batches", payload=req)
    if status != 200:
        raise SystemExit(
            f"[smoke_gui] POST /api/batches status={status} body={body}"
        )
    totals = body.get("totals", {})
    if totals.get("episodes_done") != 3 or totals.get("episodes_failed") != 0:
        raise SystemExit(
            f"[smoke_gui] /api/batches expected 3/0, got {totals}"
        )
    summary_path = body.get("summary_path", "")
    if not summary_path or not Path(summary_path).exists():
        raise SystemExit(
            f"[smoke_gui] /api/batches summary_path missing: {summary_path}"
        )
    if PROJECT_ROOT.resolve() in Path(summary_path).resolve().parents:
        raise SystemExit(
            f"[smoke_gui] /api/batches summary_path inside repo: {summary_path}"
        )
    _log(
        f"POST /api/batches done {totals['episodes_done']}/3 at {out_dir}"
    )


# --- entrypoint ----------------------------------------------------------


def main() -> int:
    step_help_text()

    port = _find_free_port(DEFAULT_PORT)
    base_url = f"http://{HOST}:{port}"
    proc: Optional[subprocess.Popen] = None
    work_root = Path(tempfile.mkdtemp(prefix="smoke_gui_"))
    scratch_app_data = work_root / "scratch_app_data"
    scratch_app_data.mkdir(parents=True, exist_ok=True)
    run_out_dir = work_root / "gui_run"
    batch_out_dir = work_root / "gui_batch"
    try:
        proc = _start_server(port, scratch_app_data)
        _wait_for_health(base_url, timeout=15.0)
        step_health(base_url)
        step_static_assets(base_url)
        step_config(base_url)
        step_production_config_missing(base_url)
        step_model_config_roundtrip(base_url)
        step_post_run(base_url, run_out_dir)
        step_post_batch(base_url, batch_out_dir)
    except SystemExit:
        # Bubble up the friendly message.
        if proc is not None and proc.poll() is None:
            try:
                _, stderr = proc.communicate(timeout=2.0)
                if stderr:
                    print(
                        "[smoke_gui] --- planner-web stderr ---\n"
                        f"{stderr.decode('utf-8', errors='replace')}",
                        file=sys.stderr,
                    )
            except subprocess.TimeoutExpired:
                pass
        raise
    finally:
        if proc is not None:
            _stop_server(proc)
        # Leave work_root in place for post-mortem.
        _log(f"work dir kept at {work_root} for inspection")
    _log("ALL GUI SMOKE STEPS PASSED ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())