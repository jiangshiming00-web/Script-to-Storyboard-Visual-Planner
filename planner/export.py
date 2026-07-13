"""Export run / batch artifacts to Markdown / HTML / CSV.

v1.0 priority is letting a human review the planner's output, so the
export formats are deliberately human-friendly. Every report bundles
the same content:

- project + episode identification,
- provider audit (requested / effective / fallback / fallback_reason),
- validation result,
- character / location / prop bibles,
- story beats,
- shot list,
- image / video prompts,
- executor tasks (skeleton, tool=null in Phase 1).

Markdown is the canonical "drop into a doc" format. HTML is the
canonical "open in a browser" format with inline CSS so a teammate
can email the file without external assets. CSV is one logical row
per artifact element so the operator can pivot in a spreadsheet.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape as _html_escape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .exceptions import PlannerError


VALID_FORMATS = ("markdown", "html", "csv")


# --- artifact readers ---------------------------------------------------


@dataclass
class _RunData:
    """Bundle of artifacts loaded from a single run directory."""

    run_dir: Path
    run_id: str
    summary: Dict[str, Any]
    script_parse: Dict[str, Any]
    character_bible: Dict[str, Any]
    location_bible: Dict[str, Any]
    prop_bible: Dict[str, Any]
    story_beats: Dict[str, Any]
    shot_list: Dict[str, Any]
    image_prompts: Dict[str, Any]
    video_prompts: Dict[str, Any]
    asset_manifest: Dict[str, Any]
    executor_tasks: Dict[str, Any]
    validation: Optional[Dict[str, Any]] = None

    @property
    def project_name(self) -> str:
        return str(self.summary.get("project_name") or self.run_id)


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_run(run_dir: Path) -> _RunData:
    """Read all 11 artifacts from ``run_dir`` into a :class:`_RunData`."""

    run_dir = Path(run_dir).resolve()
    if not (run_dir / "run_summary.json").exists():
        raise PlannerError(
            f"No run_summary.json at {run_dir}; is this a planner run directory?"
        )
    return _RunData(
        run_dir=run_dir,
        run_id=run_dir.name,
        summary=_read_json(run_dir / "run_summary.json"),
        script_parse=_read_json(run_dir / "script_parse.json"),
        character_bible=_read_json(run_dir / "character_bible.json"),
        location_bible=_read_json(run_dir / "location_bible.json"),
        prop_bible=_read_json(run_dir / "prop_bible.json"),
        story_beats=_read_json(run_dir / "story_beats.json"),
        shot_list=_read_json(run_dir / "shot_list.json"),
        image_prompts=_read_json(run_dir / "image_prompts.json"),
        video_prompts=_read_json(run_dir / "video_prompts.json"),
        asset_manifest=_read_json(run_dir / "asset_manifest.json"),
        executor_tasks=_read_json(run_dir / "executor_tasks.json"),
    )


def load_batch(batch_dir: Path) -> List[_RunData]:
    """Read every per-episode run directory under ``batch_dir``."""

    batch_dir = Path(batch_dir).resolve()
    summary_path = batch_dir / "batch_summary.json"
    if not summary_path.exists():
        raise PlannerError(
            f"No batch_summary.json at {batch_dir}; is this a planner batch directory?"
        )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    runs: List[_RunData] = []
    for ep in summary.get("episodes", []):
        run_dir_path = ep.get("run_dir")
        if not run_dir_path:
            continue
        run_path = Path(run_dir_path)
        if not run_path.is_absolute():
            run_path = batch_dir / run_path
        if (run_path / "run_summary.json").exists():
            runs.append(load_run(run_path))
    return runs


# --- renderers ----------------------------------------------------------


def _audit_lines(summary: Dict[str, Any]) -> List[str]:
    return [
        f"- requested_provider: `{summary.get('requested_provider', '?')}`",
        f"- effective_provider: `{summary.get('effective_provider', '?')}`",
        f"- fallback_used: `{summary.get('fallback_used', False)}`",
        f"- fallback_reason: `{summary.get('fallback_reason') or '—'}`",
        f"- env: `{summary.get('env', '?')}`",
        f"- executor_status: `{summary.get('executor_status', '?')}`",
    ]


def _render_markdown(run: _RunData) -> str:
    s = run.summary
    lines: List[str] = []
    lines.append(f"# {run.project_name} — Run {run.run_id}")
    lines.append("")
    lines.append(f"_Exported {datetime.now(timezone.utc).isoformat()}_")
    lines.append("")
    lines.append("## Provider audit")
    lines.extend(_audit_lines(s))
    lines.append("")
    lines.append("## Script parse")
    if run.script_parse.get("blocks"):
        lines.append(
            f"- source: `{run.script_parse.get('source_path', '?')}`"
        )
        lines.append(f"- blocks: {len(run.script_parse['blocks'])}")
    else:
        lines.append("- (empty)")
    lines.append("")

    for heading, bible, kind in (
        ("Character bible", run.character_bible, "characters"),
        ("Location bible", run.location_bible, "locations"),
        ("Prop bible", run.prop_bible, "props"),
    ):
        lines.append(f"## {heading}")
        items = bible.get(kind, [])
        if not items:
            lines.append("- (none)")
        else:
            for entry in items:
                lines.append(
                    f"- **{entry.get('name', entry.get('id', '?'))}** "
                    f"(`{entry.get('id', '?')}`)"
                )
        lines.append("")

    lines.append("## Story beats")
    beats = run.story_beats.get("beats", [])
    if not beats:
        lines.append("- (none)")
    else:
        for b in beats:
            lines.append(
                f"- `{b.get('id', '?')}` — {b.get('label', '')}: {b.get('summary', '')}"
            )
    lines.append("")

    lines.append("## Shot list")
    shots = run.shot_list.get("shots", [])
    if not shots:
        lines.append("- (none)")
    else:
        for sh in shots:
            lines.append(
                f"- `{sh.get('id', '?')}` ({sh.get('shot_size', '?')}) — "
                f"{sh.get('action', '')}"
            )
    lines.append("")

    lines.append("## Image prompts")
    prompts = run.image_prompts.get("image_prompts", [])
    if not prompts:
        lines.append("- (none)")
    else:
        for p in prompts:
            lines.append(f"- `{p.get('shot_id', '?')}` — {p.get('prompt', '')}")
    lines.append("")

    lines.append("## Video prompts")
    prompts = run.video_prompts.get("video_prompts", [])
    if not prompts:
        lines.append("- (none)")
    else:
        for p in prompts:
            lines.append(f"- `{p.get('shot_id', '?')}` — {p.get('prompt', '')}")
    lines.append("")

    lines.append("## Executor tasks")
    tasks = run.executor_tasks.get("tasks", [])
    if not tasks:
        lines.append("- (none)")
    else:
        for t in tasks:
            lines.append(
                f"- `{t.get('id', '?')}` shot=`{t.get('shot_id', '?')}` "
                f"tool=`{t.get('tool') or 'None'}` status=`{t.get('status', '?')}`"
            )
    lines.append("")

    return "\n".join(lines)


def _render_html(run: _RunData) -> str:
    """Single-file HTML with inline CSS; safe to email or open offline."""

    body_md = _render_markdown(run)
    body_html = _md_to_html(body_md)
    style = (
        "<style>"
        "body{font:14px/1.4 -apple-system,BlinkMacSystemFont,sans-serif;"
        "max-width:780px;margin:24px auto;padding:0 16px;color:#1a1d21;}"
        "h1{border-bottom:1px solid #d9dde3;padding-bottom:6px;}"
        "h2{margin-top:32px;border-bottom:1px solid #eee;padding-bottom:4px;}"
        "code{background:#f3f4f6;padding:2px 4px;border-radius:3px;font-size:12px;}"
        "ul{padding-left:20px;}"
        "</style>"
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>" + _html_escape(f"{run.project_name} run {run.run_id}") + "</title>"
        + style + "</head><body>" + body_html + "</body></html>"
    )


def _md_to_html(md: str) -> str:
    """Tiny Markdown → HTML for headings + lists + paragraphs. Avoids
    a Markdown dependency for v1.0's offline-friendly export."""

    out: List[str] = []
    in_list = False
    for raw in md.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append("<h2>" + _html_escape(line[3:]) + "</h2>")
        elif line.startswith("# "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append("<h1>" + _html_escape(line[2:]) + "</h1>")
        elif line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append("<li>" + _html_escape(line[2:]) + "</li>")
        elif line.strip() == "":
            if in_list:
                out.append("</ul>")
                in_list = False
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append("<p>" + _html_escape(line) + "</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def _render_csv(run: _RunData) -> str:
    """One CSV with a per-section header row + section name column so a
    spreadsheet user can pivot / filter easily."""

    buf = io.StringIO()
    w = csv.writer(buf)

    def _section(title: str, headers: List[str], rows: Iterable[List[Any]]) -> None:
        w.writerow([f"### {title}"])
        w.writerow(headers)
        for row in rows:
            w.writerow(row)
        w.writerow([])

    s = run.summary
    _section(
        "run_summary",
        ["key", "value"],
        [
            ["requested_provider", s.get("requested_provider", "")],
            ["effective_provider", s.get("effective_provider", "")],
            ["fallback_used", s.get("fallback_used", "")],
            ["fallback_reason", s.get("fallback_reason", "")],
            ["env", s.get("env", "")],
            ["executor_status", s.get("executor_status", "")],
        ],
    )
    _section(
        "characters",
        ["id", "name", "role", "appearance"],
        [
            [
                c.get("id", ""),
                c.get("name", ""),
                c.get("role", "") or "",
                c.get("appearance", "") or "",
            ]
            for c in run.character_bible.get("characters", [])
        ],
    )
    _section(
        "locations",
        ["id", "name", "type", "space_layout"],
        [
            [
                loc.get("id", ""),
                loc.get("name", ""),
                loc.get("type", "") or "",
                loc.get("space_layout", "") or "",
            ]
            for loc in run.location_bible.get("locations", [])
        ],
    )
    _section(
        "props",
        ["id", "name", "visual"],
        [
            [p.get("id", ""), p.get("name", ""), p.get("visual", "") or ""]
            for p in run.prop_bible.get("props", [])
        ],
    )
    _section(
        "story_beats",
        ["id", "label", "summary"],
        [
            [b.get("id", ""), b.get("label", ""), b.get("summary", "")]
            for b in run.story_beats.get("beats", [])
        ],
    )
    _section(
        "shots",
        ["id", "location_id", "shot_size", "action", "duration_sec"],
        [
            [
                sh.get("id", ""),
                sh.get("location_id", ""),
                sh.get("shot_size", ""),
                sh.get("action", "") or "",
                sh.get("duration_sec", ""),
            ]
            for sh in run.shot_list.get("shots", [])
        ],
    )
    _section(
        "image_prompts",
        ["shot_id", "prompt", "negative_prompt"],
        [
            [p.get("shot_id", ""), p.get("prompt", ""), p.get("negative_prompt", "")]
            for p in run.image_prompts.get("image_prompts", [])
        ],
    )
    _section(
        "video_prompts",
        ["shot_id", "prompt", "duration_sec", "camera"],
        [
            [
                p.get("shot_id", ""),
                p.get("prompt", ""),
                p.get("duration_sec", ""),
                p.get("camera", "") or "",
            ]
            for p in run.video_prompts.get("video_prompts", [])
        ],
    )
    _section(
        "executor_tasks",
        ["id", "shot_id", "kind", "tool", "status"],
        [
            [
                t.get("id", ""),
                t.get("shot_id", ""),
                t.get("kind", ""),
                t.get("tool") or "",
                t.get("status", ""),
            ]
            for t in run.executor_tasks.get("tasks", [])
        ],
    )
    return buf.getvalue()


# --- public API ---------------------------------------------------------


def export_run(run_dir: Path, fmt: str, output: Optional[Path] = None) -> Path:
    """Render ``run_dir`` to a single Markdown / HTML / CSV file.

    Returns the path written. If ``output`` is omitted, the file
    lands next to ``run_dir`` with an appropriate extension
    (``<run_id>.md`` / ``.html`` / ``.csv``).
    """

    if fmt not in VALID_FORMATS:
        raise PlannerError(
            f"Unknown export format: {fmt!r}. Valid: {VALID_FORMATS}."
        )
    run = load_run(run_dir)
    if fmt == "markdown":
        text = _render_markdown(run)
        ext = ".md"
    elif fmt == "html":
        text = _render_html(run)
        ext = ".html"
    else:
        text = _render_csv(run)
        ext = ".csv"

    target = Path(output) if output else run.run_dir.parent / f"{run.run_id}{ext}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def export_batch(
    batch_dir: Path,
    fmt: str,
    output: Optional[Path] = None,
) -> Path:
    """Render every run in ``batch_dir`` to a single combined file.

    For Markdown / HTML the sections are concatenated with ``---``
    separators; for CSV the sections stack under per-run headers.
    """

    if fmt not in VALID_FORMATS:
        raise PlannerError(
            f"Unknown export format: {fmt!r}. Valid: {VALID_FORMATS}."
        )
    runs = load_batch(batch_dir)
    if not runs:
        raise PlannerError(
            f"No runs found under {batch_dir}; nothing to export."
        )
    parts: List[str] = []
    for i, run in enumerate(runs):
        if i > 0:
            parts.append("\n\n---\n\n")
        if fmt == "markdown":
            parts.append(_render_markdown(run))
        elif fmt == "html":
            parts.append(_render_html(run))
        else:
            parts.append(_render_csv(run))
    text = "".join(parts)
    ext = {fmt: ext for fmt, ext in [
        ("markdown", ".md"), ("html", ".html"), ("csv", ".csv"),
    ]}[fmt]
    target = Path(output) if output else (
        Path(batch_dir) / f"batch_report{ext}"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


__all__ = [
    "VALID_FORMATS",
    "export_batch",
    "export_run",
    "load_run",
    "load_batch",
]