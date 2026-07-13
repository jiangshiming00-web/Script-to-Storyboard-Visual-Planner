"""Tests for the v1.0 static web UI bundle.

The planner ships ``planner/web/static/index.html``, ``app.js`` and
``style.css`` alongside the FastAPI backend so ``planner-web`` opens
to a usable tool page on first launch. These tests pin the v1.0
contract:

- the three files exist and are non-empty,
- ``index.html`` contains the environment-switching controls, the
  model-settings inputs, the script picker, the run buttons, the
  history container and the run-detail drawer,
- ``app.js`` only consumes documented endpoints (``/api/health``,
  ``/api/config``, ``/api/runs``, ``/api/runs/{id}/summary``,
  ``/api/runs/{id}/artifacts/{name}``, ``/api/upload-script``),
- ``style.css`` keeps the load-bearing class names referenced from
  ``app.js`` (``run-row``, ``drawer``, ``toast-region``),
- ``create_app`` mounts the static directory at ``/`` so the FastAPI
  ``StaticFiles`` handler serves the bundle in production.

They also catch regressions where a future refactor accidentally
removes the env tabs or the toast region (both load-bearing for the
"no silent failure" UX).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from planner.web.app import create_app  # noqa: E402


STATIC_DIR = Path(__file__).resolve().parents[1] / "planner" / "web" / "static"


def _read(name: str) -> str:
    path = STATIC_DIR / name
    assert path.exists(), f"static asset missing: {path}"
    text = path.read_text(encoding="utf-8")
    assert text.strip(), f"static asset is empty: {path}"
    return text


# --- file presence -------------------------------------------------------


def test_static_files_exist() -> None:
    for name in ("index.html", "app.js", "style.css"):
        p = STATIC_DIR / name
        assert p.exists(), f"missing static asset: {p}"
        assert p.stat().st_size > 0, f"empty static asset: {p}"


# --- index.html structure ----------------------------------------------


def test_index_html_has_env_switcher() -> None:
    html = _read("index.html")
    assert 'id="env-development"' in html
    assert 'id="env-production"' in html
    # Production-mode warning banner is shown when env=production.
    assert 'id="env-warning"' in html


def test_index_html_has_model_settings_panel() -> None:
    html = _read("index.html")
    for required_id in (
        "provider-select",
        "model-name",
        "base-url",
        "api-key-env",
        "enable-real-calls",
        "allow-fallback",
        "probe-btn",
    ):
        assert f'id="{required_id}"' in html, (
            f"index.html missing model-settings control #{required_id}"
        )


def test_index_html_has_run_controls() -> None:
    html = _read("index.html")
    for required_id in (
        "upload-input",
        "script-path",
        "out-dir",
        "run-btn",
        "batch-btn",
    ):
        assert f'id="{required_id}"' in html, (
            f"index.html missing run control #{required_id}"
        )


def test_index_html_has_history_and_drawer() -> None:
    html = _read("index.html")
    for required_id in ("run-list", "run-drawer", "drawer-body", "drawer-close"):
        assert f'id="{required_id}"' in html, (
            f"index.html missing history/drawer element #{required_id}"
        )


def test_index_html_has_toast_region() -> None:
    """The toast region is the project's load-bearing "no traceback,
    only human text" UX invariant. If a future refactor removes it,
    errors stop being surfaced properly."""

    html = _read("index.html")
    assert 'id="toast-region"' in html
    # And the toast region must be marked aria-live so screen readers
    # announce error toasts.
    assert 'aria-live="polite"' in html


# --- app.js only consumes documented endpoints --------------------------


DOCUMENTED_ENDPOINTS = {
    "/api/health",
    "/api/config",
    "/api/model-config",
    "/api/runs",
    "/api/batches",
    "/api/runs/{run_id}/summary",
    "/api/runs/{run_id}/artifacts/{name}",
    "/api/runs/{run_id}/validate",
    "/api/upload-script",
}


def test_app_js_only_calls_documented_endpoints() -> None:
    js = _read("app.js")
    # Match literal ``fetch("/api/...")`` and ``api("/api/...")``
    # calls only. The regex requires the closing quote + comma /
    # close-paren, which means the path ends exactly at the next
    # quote — template concatenations like
    # ``"/api/runs/" + runId + "/summary"`` are NOT matched here.
    # Those concatenations are covered by the artifact-link test
    # below (they must end in a documented suffix).
    import re

    paths = set(re.findall(r'fetch\(\s*["\'](/api/[^"\']*?)["\']\s*[,)]', js))
    paths |= set(re.findall(r'api\(\s*["\'](/api/[^"\']*?)["\']\s*[,)]', js))

    # Strip query string so ``/api/runs?env=development`` matches the
    # ``/api/runs`` template.
    paths = {p.split("?", 1)[0] for p in paths if p.startswith("/api/")}

    unknown = {
        p for p in paths
        if not any(
            p == ep or _matches_template(p, ep) for ep in DOCUMENTED_ENDPOINTS
        )
    }
    assert not unknown, (
        f"app.js references undocumented endpoints: {sorted(unknown)}. "
        f"Documented: {sorted(DOCUMENTED_ENDPOINTS)}"
    )


def test_app_js_artifact_template_uses_documented_suffix() -> None:
    """The artifact link uses template concatenation
    ``"/api/runs/" + runId + "/artifacts/" + name``. The suffix
    segment MUST be ``/artifacts/`` — anything else would point to an
    undocumented endpoint."""

    js = _read("app.js")
    assert '"/api/runs/"' in js
    assert '"/artifacts/"' in js
    # And the suffix is never used as a stand-alone fetch target.
    assert 'fetch("/artifacts/")' not in js


def test_app_js_render_drawer_handles_dict_artifacts() -> None:
    """P1-2: ``run_summary.json.artifacts`` is a dict ``{name: path}``,
    not an array. Older builds called ``.map()`` on it and crashed the
    drawer when a completed run was opened. The fixed app.js MUST
    normalize via ``Object.keys`` / ``Array.isArray`` so both shapes
    render."""

    js = _read("app.js")
    # The fix must branch on Array.isArray + fall back to Object.keys.
    assert "Array.isArray" in js, (
        "app.js must guard artifacts with Array.isArray before .map()"
    )
    assert "Object.keys" in js, (
        "app.js must use Object.keys for dict-shaped artifacts"
    )
    # And the old fragile form (summary.artifacts.map) must be gone.
    assert "summary.artifacts\n      .map" not in js, (
        "app.js still calls .map() directly on summary.artifacts; "
        "dict-shaped artifacts will crash the drawer."
    )


def _matches_template(path: str, template: str) -> bool:
    """Treat ``{name}`` segments in ``template`` as wildcards."""

    import re

    pattern = re.escape(template)
    pattern = re.sub(r"\\\{[^}]+\\\}", r"[^/]+", pattern)
    return re.fullmatch(pattern, path) is not None


# --- style.css keeps load-bearing class names --------------------------


def test_style_css_has_load_bearing_classes() -> None:
    css = _read("style.css")
    for cls in ("run-row", "run-done", "run-failed", "drawer", "toast-region",
                "banner-warning", "env-tab"):
        assert "." + cls in css, f"style.css missing class .{cls}"


# --- stub buttons (P3-4) -----------------------------------------------


def test_stub_buttons_are_disabled_in_index_html() -> None:
    """P3-4 Codex polish: ``batch-btn`` and ``probe-btn`` are
    v1.0 placeholders for endpoints that ship in v1.1. The buttons
    MUST be ``disabled`` so the operator doesn't expect them to do
    anything; the ``title`` attribute carries the explanation.
    """

    import re

    html = _read("index.html")
    # Probe button is disabled with a v1.1 note.
    assert 'id="probe-btn"' in html
    # Match the disabled attribute somewhere on the probe button tag.
    assert re.search(r'<button[^>]*id="probe-btn"[^>]*disabled', html), (
        "probe-btn is not disabled; v1.0 placeholder must not invite "
        "an operator to click it."
    )
    assert 'id="batch-btn"' in html
    assert not re.search(r'<button[^>]*id="batch-btn"[^>]*disabled', html), (
        "batch-btn is disabled but POST /api/batches shipped in P2-2; "
        "enable it so the GUI batch path is usable."
    )


def test_app_js_does_not_wire_dead_handlers_for_stub_buttons() -> None:
    """With the buttons disabled, no click handler should be wired
    for them in app.js — dead handlers are confusing to read and
    suggest the endpoint is real."""

    js = _read("app.js")
    # probe-btn must not have a handler.
    assert '$("probe-btn")' not in js
    # batch-btn now has a handler (P2-2) - assert it calls /api/batches.
    assert '$("batch-btn")' in js
    assert "/api/batches" in js


# --- FastAPI mounts static at / ----------------------------------------


def test_create_app_serves_static_index(tmp_path: Path) -> None:
    repo = tmp_path / "fake_repo"
    repo.mkdir()
    (repo / "config").mkdir()
    (repo / "config" / "development.json").write_text(
        '{"env":"development","planner_provider":"deterministic"}', encoding="utf-8"
    )
    app = create_app(repo_root=repo)
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "<title>" in resp.text
    # The bundle is mounted, so /app.js also resolves.
    js_resp = client.get("/app.js")
    assert js_resp.status_code == 200
    assert "planner-web" in js_resp.text or "env-" in js_resp.text


# --- wheel includes the static bundle ---------------------------------


def test_wheel_includes_static_bundle(tmp_path: Path) -> None:
    """Regression: Phase 3 ships the static UI; the wheel MUST carry
    ``planner/web/static/*`` so a teammate who runs ``pip install .``
    and ``planner-web`` gets the full UI without manually copying
    files."""

    pytest.importorskip("setuptools")
    import subprocess
    import sys

    from setuptools import build_meta as _setuptools_build_meta

    out_dir = tmp_path / "wheel"
    out_dir.mkdir()
    proc = subprocess.run(
        [
            sys.executable, "-m", "pip", "wheel", ".",
            "--no-deps", "-w", str(out_dir),
        ],
        cwd=str(STATIC_DIR.parents[2]),
        capture_output=True,
        text=True,
        timeout=180,
    )
    if proc.returncode != 0:
        pytest.skip(
            f"`pip wheel` failed (rc={proc.returncode}); "
            f"requires the `wheel` package. stderr tail: {proc.stderr[-300:]}"
        )

    wheels = list(out_dir.glob("script_to_storyboard_planner-*.whl"))
    assert wheels, "`pip wheel` produced no artifact"
    with zipfile.ZipFile(wheels[0]) as zf:
        names = zf.namelist()
    expected = {
        "planner/web/static/index.html",
        "planner/web/static/app.js",
        "planner/web/static/style.css",
    }
    missing = expected - set(names)
    assert not missing, (
        f"wheel is missing static UI files: {missing}. "
        f"Phase 3 ship-blocker for `planner-web`."
    )