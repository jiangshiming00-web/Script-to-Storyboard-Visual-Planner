"""CLI subprocess tests for ``planner provider-probe`` (Phase 3 P2 Round 2).

Scope (per brief ``docs/design/provider_probe_design.md`` §4.2):

* 4 distinct exit codes: gate close=2 / unhealthy=2 / healthy=0 / NotImpl=1.
* 1 no-``--probe``-flag guard (the subcommand is the only trigger).
* 5 misc: env-only-no-subcommand sanity / no-subcommand-no-env sanity /
  stderr secret redaction / no run-dir creation / no traceback on failure.

These tests are subprocess tests — they invoke
``python3 -m planner provider-probe ...`` in a child process and
assert on exit code / stdout JSON / stderr text. For the healthy /
unhealthy paths, we bind a real local ``http.server`` in a thread
and point ``base_url`` at it via a temporary ``model_config.json``
file; this exercises the full CLI stack (Click → provider →
``http_get`` → urllib) without mocking at the Python level, which
matches what an operator would actually run.

A run starts a local server on ``127.0.0.1:<free_port>`` whose
``/v1/models`` endpoint returns the canned status / body configured
per test. When the test ends, the server thread is joined and the
port is released.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable


# ---- local HTTP server fixture ---------------------------------------


def _find_free_port() -> int:
    """Bind to port 0 and read back the kernel-assigned port. Mirrors
    the ``_find_free_port`` helper in ``harness/smoke_gui.py``."""

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


class _Handler(BaseHTTPRequestHandler):
    """Handler whose response is configured by the test via the
    ``server.response`` module-level attribute. Keeps the test code
    free of class boilerplate per test."""

    response: Tuple[int, bytes] = (200, b"{}")

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        status, body = _Handler.response
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # silence stderr noise
        return


@contextmanager
def _local_probe_server(
    response: Tuple[int, bytes],
) -> Iterator[Tuple[HTTPServer, int]]:
    """Start a local HTTP server with the given canned response. Yield
    ``(server, port)``; tear down on exit."""

    _Handler.response = response
    port = _find_free_port()
    server = HTTPServer(("127.0.0.1", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _scrubbed_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Return ``os.environ`` with all ``PLANNER_`` vars stripped (so a
    test that needs ``PLANNER_PROBE`` is not polluted by the parent
    shell's state), plus optional overrides."""

    base = {k: v for k, v in os.environ.items() if not k.startswith("PLANNER_")}
    if extra:
        base.update(extra)
    return base


def _model_config_path(tmp: Path, base_url: str) -> Path:
    """Write a deterministic / OpenAI-compatible model_config.json
    pointing ``base_url`` at ``base_url``. The api_key_env is set to
    a real env var the test exports, so the probe has a key to
    carry in the request header."""

    cfg = {
        "planner_provider": "openai_compatible",
        "enable_real_model_calls": False,
        "allow_provider_fallback": False,
        "openai_compatible": {
            "base_url": base_url,
            "model": "probe-test-model",
            "api_key_env": "PLANNER_PROBE_TEST_KEY",
            "timeout_seconds": 10.0,
            "temperature": 0.5,
            "max_tokens": 256,
        },
    }
    path = tmp / "model_config.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _run_probe(
    *args: str,
    env_extra: Optional[Dict[str, str]] = None,
    cwd: Optional[Path] = None,
) -> "subprocess.CompletedProcess[str]":
    """Run ``python3 -m planner provider-probe ...`` and return the
    completed process. Uses PLANNER_PROBE_TEST_KEY to satisfy the
    api_key_env declared in the model config."""

    extra = {"PLANNER_PROBE_TEST_KEY": "sk-cli-probe-test-1234567890"}
    if env_extra:
        extra.update(env_extra)
    return subprocess.run(
        [PYTHON, "-m", "planner", "provider-probe", *args],
        capture_output=True,
        text=True,
        env=_scrubbed_env(extra),
        cwd=str(cwd or PROJECT_ROOT),
        timeout=60,
    )


# ---- 4 distinct exit codes (4) ---------------------------------------


def test_provider_probe_subcommand_only_no_env_exits_two(
    tmp_path: Path,
) -> None:
    """Subcommand triggered + ``PLANNER_PROBE`` unset → exit 2 + one-
    line stderr policy refusal, no traceback."""

    proc = _run_probe("--provider", "openai_compatible", env_extra={"PLANNER_PROBE": ""})
    assert proc.returncode == 2
    # One-line stderr; exact wording matches the gate-closed branch.
    assert "opt-in only" in proc.stderr
    assert "PLANNER_PROBE=1" in proc.stderr
    assert "Traceback" not in proc.stderr
    # Stdout should be empty (the policy refusal goes to stderr).
    assert proc.stdout.strip() == ""


def test_provider_probe_unhealthy_returns_exit_two(tmp_path: Path) -> None:
    """A 404 from the local model-listing endpoint → healthy=False +
    exit 2, with the structured JSON on stdout."""

    with _local_probe_server((404, b"Not Found")) as (_server, port):
        cfg = _model_config_path(tmp_path, f"http://127.0.0.1:{port}/v1")
        proc = _run_probe(
            "--provider", "openai_compatible",
            "--model-config", str(cfg),
            env_extra={"PLANNER_PROBE": "1"},
        )
    assert proc.returncode == 2
    payload = json.loads(proc.stdout)
    assert payload["healthy"] is False
    assert payload["provider"] == "openai_compatible"
    assert "404" in payload["reason"]


def test_provider_probe_healthy_returns_exit_zero(tmp_path: Path) -> None:
    """A 200 from the local model-listing endpoint → healthy=True +
    exit 0 + JSON on stdout."""

    with _local_probe_server((200, b'{"object":"list","data":[]}')) as (
        _server, port
    ):
        cfg = _model_config_path(tmp_path, f"http://127.0.0.1:{port}/v1")
        proc = _run_probe(
            "--provider", "openai_compatible",
            "--model-config", str(cfg),
            env_extra={"PLANNER_PROBE": "1"},
        )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["healthy"] is True
    assert payload["provider"] == "openai_compatible"
    assert "latency_ms" in payload


def test_provider_probe_not_implemented_returns_exit_one() -> None:
    """``--provider deterministic`` → NotImplementedError → CLI
    wraps to exit 1 + stderr message. No model_config needed;
    deterministic ignores settings."""

    proc = _run_probe(
        "--provider", "deterministic",
        env_extra={"PLANNER_PROBE": "1"},
    )
    assert proc.returncode == 1
    # CLI wraps to "provider probe not implemented for 'deterministic'"
    # on stderr (one line, no traceback).
    assert "not implemented" in proc.stderr
    assert "deterministic" in proc.stderr
    assert "Traceback" not in proc.stderr


# ---- 1 no-`--probe`-flag guard ---------------------------------------


def test_provider_probe_subprocess_subcommand_required_no_alias(
    tmp_path: Path,
) -> None:
    """Brief §2.2 rationale: probe trigger is the **subcommand**
    ``planner provider-probe``, NOT a ``--probe`` flag on
    ``planner run``. Verify both that ``planner run`` has no
    ``--probe`` flag, and that ``planner provider-probe --help``
    shows only the subcommand-level options."""

    # (a) ``planner run`` should reject ``--probe`` (Click error + non-zero).
    proc_run = subprocess.run(
        [PYTHON, "-m", "planner", "run", "--probe"],
        capture_output=True,
        text=True,
        env=_scrubbed_env({"PLANNER_PROBE": "1"}),
        cwd=str(PROJECT_ROOT),
        timeout=20,
    )
    assert proc_run.returncode != 0
    assert "no such option" in proc_run.stderr.lower() or "no such option" in proc_run.stdout.lower()

    # (b) ``planner provider-probe --help`` shows the subcommand-level
    #     options (``--provider``, ``--model-config``, ``--timeout-ms``,
    #     ``--verbose``) and does NOT include a ``--probe`` flag.
    proc_help = subprocess.run(
        [PYTHON, "-m", "planner", "provider-probe", "--help"],
        capture_output=True,
        text=True,
        env=_scrubbed_env(),
        cwd=str(PROJECT_ROOT),
        timeout=20,
    )
    assert proc_help.returncode == 0
    help_text = proc_help.stdout
    for expected in ("--provider", "--model-config", "--timeout-ms", "--verbose"):
        assert expected in help_text
    # And no --probe flag in the help text.
    assert "--probe" not in help_text


# ---- 5 misc ---------------------------------------------------------


def test_provider_probe_env_only_no_subcommand_does_not_invoke_probe() -> None:
    """``PLANNER_PROBE=1`` set in env but the user never invokes
    ``planner provider-probe`` — nothing happens. This is sanity: we
    just assert that ``planner --help`` still works (the env var
    doesn't make any subcommand auto-trigger)."""

    proc = subprocess.run(
        [PYTHON, "-m", "planner", "--help"],
        capture_output=True,
        text=True,
        env=_scrubbed_env({"PLANNER_PROBE": "1"}),
        cwd=str(PROJECT_ROOT),
        timeout=20,
    )
    assert proc.returncode == 0
    assert "provider-probe" in proc.stdout
    # And listing subcommands doesn't auto-invoke probe (rc=0, no JSON output).
    assert "Traceback" not in proc.stderr


def test_provider_probe_no_subcommand_no_env_is_baseline_zero() -> None:
    """Sanity baseline: with no ``PLANNER_PROBE`` env and no probe
    subcommand invocation, ``planner --help`` still exits 0 (no
    crash, no probe attempt)."""

    proc = subprocess.run(
        [PYTHON, "-m", "planner", "--help"],
        capture_output=True,
        text=True,
        env=_scrubbed_env(),
        cwd=str(PROJECT_ROOT),
        timeout=20,
    )
    assert proc.returncode == 0
    assert "Traceback" not in proc.stderr


def test_provider_probe_redacts_stderr_secret(tmp_path: Path) -> None:
    """A 4xx response whose body contains ``sk-...`` MUST be redacted
    before reaching stderr / stdout."""

    secret = "sk-leak-cli-secret-1234567890"
    body = f'{{"error":"upstream rejected key={secret}"}}'.encode("utf-8")
    with _local_probe_server((401, body)) as (_server, port):
        cfg = _model_config_path(tmp_path, f"http://127.0.0.1:{port}/v1")
        proc = _run_probe(
            "--provider", "openai_compatible",
            "--model-config", str(cfg),
            "--verbose",
            env_extra={"PLANNER_PROBE": "1"},
        )
    # Exit 2 because the response is unhealthy (401).
    assert proc.returncode == 2
    # The raw token MUST NOT appear on either stream.
    assert secret not in proc.stdout
    assert secret not in proc.stderr
    # And the redacted placeholder is present in stdout (the reason
    # field on the JSON payload).
    payload = json.loads(proc.stdout)
    assert "<redacted>" in payload["reason"]


def test_provider_probe_does_not_create_run_dir(tmp_path: Path) -> None:
    """Probe MUST NOT create any run / batch artifact anywhere on
    disk. The brief §2.7 "写盘" row: "绝不写盘". We verify by
    snapshotting ``tmp_path`` before/after the call."""

    with _local_probe_server((200, b"{}")) as (_server, port):
        cfg = _model_config_path(tmp_path, f"http://127.0.0.1:{port}/v1")
        before = set(p.name for p in tmp_path.iterdir())
        proc = _run_probe(
            "--provider", "openai_compatible",
            "--model-config", str(cfg),
            cwd=tmp_path,
            env_extra={"PLANNER_PROBE": "1"},
        )
        after = set(p.name for p in tmp_path.iterdir())

    assert proc.returncode == 0
    # ``tmp_path`` was used for ``model_config.json`` and a server log;
    # the probe MUST NOT have added any new files (no run dirs, no
    # batch summaries, no probe_history.jsonl).
    new_files = after - before
    assert new_files == set(), (
        f"probe() must not create files; new files: {sorted(new_files)}"
    )


def test_provider_probe_traceback_absent_on_failure(
    tmp_path: Path,
) -> None:
    """Failure paths MUST NOT leak a Python traceback to stderr. We
    drive three failure modes and assert no ``Traceback`` in any
    of them:

    1. Gate closed (env unset) → one-line stderr.
    2. NotImplementedError (deterministic) → one-line stderr.
    3. Unhealthy (404) → structured JSON on stdout, empty stderr.
    """

    # Mode 1: gate closed.
    proc1 = _run_probe(
        "--provider", "deterministic",
        env_extra={"PLANNER_PROBE": ""},
    )
    assert "Traceback" not in proc1.stderr
    assert proc1.returncode == 2

    # Mode 2: NotImplementedError.
    proc2 = _run_probe(
        "--provider", "deterministic",
        env_extra={"PLANNER_PROBE": "1"},
    )
    assert "Traceback" not in proc2.stderr
    assert proc2.returncode == 1

    # Mode 3: unhealthy response.
    with _local_probe_server((500, b"boom")) as (_server, port):
        cfg = _model_config_path(tmp_path, f"http://127.0.0.1:{port}/v1")
        proc3 = _run_probe(
            "--provider", "openai_compatible",
            "--model-config", str(cfg),
            env_extra={"PLANNER_PROBE": "1"},
        )
    assert "Traceback" not in proc3.stderr
    assert "Traceback" not in proc3.stdout
    assert proc3.returncode == 2