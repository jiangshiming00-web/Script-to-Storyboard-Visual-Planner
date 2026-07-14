"""Read-only prompt-bible consistency review engine for the planner agent.

Phase 3 P2: ``review-run`` checks a single run's image / video prompts
against the bibles (character / location / prop) to catch reference
mismatches that :func:`planner.validate.validate_run`'s loose substring
check misses. This is **single-run** consistency; cross-episode
consistency is ``review-batch``'s job.

4 rules:

* ``rv1_image_prompt_bible_ref_mismatch`` (warning): parse the prompt
  header (``场景：`` / ``人物：`` / ``道具：``) and cross-check against
  the shot's bible ID refs. Bidirectional: *missing* (shot references a
  bible entry whose name is absent from the header) + *phantom* (header
  names a character / location / prop the shot does not reference).
  Unlike :func:`planner.validate.validate_run` (single-direction
  ``name in prompt`` substring check), rv1 parses the structured header
  and avoids substring-collision false negatives.
* ``rv2_video_prompt_missing_field`` (warning): video prompt
  ``motion`` / ``camera`` / ``avoid`` must be non-empty.
* ``rv3_unresolved_placeholder`` (error): no ``{word}`` / ``<WORD>`` /
  ``[[TBD]]`` template placeholders in any prompt text field.
* ``rv4_shot_id_misaligned`` (warning): shot_list / image_prompts /
  video_prompts shot_id sets must agree.

Hard rules (same as :mod:`planner.agent.diagnose`):

* Never write files (read-only contract).
* Never call subprocess or shell.
* Never import LLM SDKs (rules are pure-data).
* Never echo ``api_key_value`` into any finding / summary. Run
  :func:`redact_secrets_text` on every string that flows into the
  report (defense in depth - prompt text could embed a leaked token).
* Graceful degradation: missing / corrupted ``run_summary.json`` ->
  error finding + minimal report; missing / corrupted artifact ->
  warning + skip dependent rules.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from .diagnose import (
    DiagnoseFinding,
    EvidenceRef,
    ImplementationStatus,
    ReportStatus,
    Severity,
    ToolInvocation,
)
from .redact import redact_secrets_text
from .tools import list_artifacts as _tools_list_artifacts, read_artifact, read_run_summary

# ---------- Pydantic model ----------


class ReviewRunReport(BaseModel):
    """Top-level review report for one run.

    Mirrors :class:`~planner.agent.diagnose.DiagnoseReport` shape but
    drops ``provider`` / ``validation`` fields (review-run does not
    audit provider health and does not delegate to ``validate_run`` -
    see module docstring). ``review_version`` is the canonical version
    tag so JSON consumers can branch on report type.
    """

    run_dir: str
    run_id: Optional[str] = None
    env: Optional[str] = None
    expected_env: Optional[str] = None
    status: ReportStatus = "ok"
    implementation_status: ImplementationStatus = "full"
    review_version: Literal["1.0"] = "1.0"
    summary: str = ""
    counts: Dict[str, int] = Field(default_factory=dict)
    findings: List[DiagnoseFinding] = Field(default_factory=list)
    tool_invocations: List[ToolInvocation] = Field(default_factory=list)
    generated_at: str = ""

    def derive_status(self) -> "ReviewRunReport":
        """Recompute ``status`` from findings. Returns self."""
        if any(f.severity == "error" for f in self.findings):
            self.status = "errors"
        elif any(f.severity == "warning" for f in self.findings):
            self.status = "warnings"
        else:
            self.status = "ok"
        return self


# ---------- Helpers ----------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(value: Any) -> str:
    """Stringify + redact. ``None`` / empty -> empty string.

    Mirrors ``diagnose._safe_text``; duplicated here to avoid importing
    a private name across modules. Both copies MUST stay in sync with
    :func:`redact_secrets_text`.
    """
    if value is None:
        return ""
    return redact_secrets_text(str(value))


def _add_finding(
    findings: List[DiagnoseFinding],
    severity: Severity,
    code: str,
    message: str,
    evidence: List[EvidenceRef],
) -> None:
    """Append a finding with message run through ``_safe_text``.

    Centralizes the redact-on-exit contract so no rule can forget to
    redact prompt / bible text that may embed a leaked token.
    """
    findings.append(
        DiagnoseFinding(
            severity=severity,
            code=code,
            message=_safe_text(message),
            evidence=evidence,
        )
    )


# Artifacts review-run needs (subset of KNOWN_ARTIFACTS).
_REVIEW_ARTIFACTS = (
    "character_bible",
    "location_bible",
    "prop_bible",
    "shot_list",
    "image_prompts",
    "video_prompts",
)


def _read_artifact_safe(
    run_dir: Path,
    name: str,
    findings: List[DiagnoseFinding],
    tool_invocations: List[ToolInvocation],
) -> Optional[Dict[str, Any]]:
    """Read one artifact, record a ToolInvocation, emit a finding on failure.

    Returns ``None`` when the artifact is missing / corrupted / unreadable;
    callers skip rules that depend on it. The finding ``code``
    (``artifact_unreadable`` vs ``artifact_corrupted``) distinguishes the
    failure mode so the operator can act.
    """
    path = run_dir / f"{name}.json"
    try:
        # read_artifact delegates to readers.load_artifact which
        # requires the KNOWN_ARTIFACTS entry (with the .json suffix).
        payload = read_artifact(run_dir, f"{name}.json")
    except FileNotFoundError:
        _add_finding(
            findings,
            "warning",
            "artifact_unreadable",
            f"{name}.json 不存在；依赖该产物的规则已跳过。",
            [EvidenceRef(artifact=f"{name}.json", path=str(path), locator="$")],
        )
        tool_invocations.append(
            ToolInvocation(tool="read_artifact", ok=False, artifact_refs=[f"{name}.json"], bytes_read=0)
        )
        return None
    except (ValueError, json.JSONDecodeError) as exc:
        _add_finding(
            findings,
            "warning",
            "artifact_corrupted",
            f"{name}.json 损坏（{_safe_text(exc)}）；依赖该产物的规则已跳过。",
            [EvidenceRef(artifact=f"{name}.json", path=str(path), locator="$")],
        )
        tool_invocations.append(
            ToolInvocation(tool="read_artifact", ok=False, artifact_refs=[f"{name}.json"], bytes_read=0)
        )
        return None
    except OSError as exc:
        _add_finding(
            findings,
            "warning",
            "artifact_unreadable",
            f"{name}.json 读取失败（{_safe_text(exc)}）；依赖该产物的规则已跳过。",
            [EvidenceRef(artifact=f"{name}.json", path=str(path), locator="$")],
        )
        tool_invocations.append(
            ToolInvocation(tool="read_artifact", ok=False, artifact_refs=[f"{name}.json"], bytes_read=0)
        )
        return None
    # Legitimate JSON but the top level is not a JSON object (e.g. a
    # bare list / string / int / bool). review-run indexes every
    # artifact as a dict, so a non-dict top level would raise
    # ``AttributeError`` on the downstream ``.get(...)`` calls and leak
    # a traceback through the CLI (the ``except KeyError`` /
    # ``PlannerError`` guards do not catch ``AttributeError``). Treat
    # it as corrupted so the run degrades gracefully and dependent
    # rules skip. See Codex Phase 3 P2 review (P1).
    if not isinstance(payload, dict):
        _add_finding(
            findings,
            "warning",
            "artifact_corrupted",
            f"{name}.json 顶层不是 JSON 对象（{type(payload).__name__}）；依赖该产物的规则已跳过。",
            [EvidenceRef(artifact=f"{name}.json", path=str(path), locator="$")],
        )
        tool_invocations.append(
            ToolInvocation(tool="read_artifact", ok=False, artifact_refs=[f"{name}.json"], bytes_read=0)
        )
        return None
    bytes_read = path.stat().st_size if path.is_file() else 0
    tool_invocations.append(
        ToolInvocation(tool="read_artifact", ok=True, artifact_refs=[f"{name}.json"], bytes_read=bytes_read)
    )
    return payload


# ---------- Placeholder + header parsing ----------

# Identifier-style placeholders only; avoids matching legitimate braces
# or angle brackets in prose. {location_name} / <TBD> / [[TBD: ...]].
_PLACEHOLDER_PATTERNS = [
    re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}"),
    re.compile(r"<[A-Z_][A-Z0-9_]*>"),
    re.compile(r"\[\[(?:TBD|PLACEHOLDER|TODO)[^\]]*\]\]", re.IGNORECASE),
]


def _find_placeholders(text: str) -> List[str]:
    if not text:
        return []
    hits: List[str] = []
    for pat in _PLACEHOLDER_PATTERNS:
        hits.extend(pat.findall(text))
    return hits


# Header format (planner/prompts.py:51-57): the generator emits
#   场景：{loc.name}。人物：{ch.name}。道具：{pr.name}。
# in fixed 场景 -> 人物 -> 道具 order, one segment per bible entry
# (one 场景 if the shot has a location, one 人物 per character, one
# 道具 per prop). Header and body share the ``。`` separator with no
# newline, so rv1 cannot rely on a structural boundary.
# ``_parse_prompt_header`` consumes exactly the expected count of each
# label from the start (``consumed``) and collects any further
# 场景：/人物：/道具： segments into ``extra``. ``_rule_rv1`` then flags
# an extra segment as a phantom only when its name hits a known bible
# entry the shot does not reference; body prose whose label-prefixed
# name matches no bible entry (e.g. "人物：背景群众只是画面描述") is
# ignored. This catches real phantoms (人物：张楠 / 道具：文件夹 where
# the name is in the bible) without flagging body prose. See Codex
# Phase 3 P2 round-3 review (P2 direction E).
_SCENE_RE = re.compile(r"场景：([^。]*)")
_CHAR_RE = re.compile(r"人物：([^。]*)")
_PROP_RE = re.compile(r"道具：([^。]*)")


def _split_names(raw: List[str]) -> List[str]:
    out: List[str] = []
    for n in raw:
        for part in re.split(r"[、,]", n):
            part = part.strip()
            if part:
                out.append(part)
    return out


def _parse_prompt_header(
    prompt: str,
    *,
    n_scene: int,
    n_char: int,
    n_prop: int,
) -> tuple:
    """Parse a prompt header into consumed + extra name buckets.

    Consumes exactly ``n_scene`` ``场景：`` segments, then ``n_char``
    ``人物：`` segments, then ``n_prop`` ``道具：`` segments, from the
    start of the prompt (matching the generator emit order). Each label
    is consumed independently: a missing segment for one label does not
    block consuming the next label.

    Segments past the expected count are split:

    * those still starting with a header label (``场景：`` / ``人物：`` /
      ``道具：``) are collected into ``extra`` - candidates for the
      bible-name phantom check. A name that hits a known bible entry
      but is not in the shot's refs is a phantom; a name that matches
      no bible entry is body prose and ignored.
    * non-label segments are body prose and ignored.

    Returns ``(consumed, extra)``. See Codex Phase 3 P2 round-3 review.
    """
    empty = {"scene": [], "character": [], "prop": []}
    if not prompt:
        return empty, empty
    segments = [s.strip() for s in prompt.split("。") if s.strip()]
    consumed: Dict[str, List[str]] = {"scene": [], "character": [], "prop": []}
    extra: Dict[str, List[str]] = {"scene": [], "character": [], "prop": []}
    idx = 0
    while len(consumed["scene"]) < n_scene and idx < len(segments) and segments[idx].startswith("场景："):
        consumed["scene"].extend(_split_names(_SCENE_RE.findall(segments[idx])))
        idx += 1
    while len(consumed["character"]) < n_char and idx < len(segments) and segments[idx].startswith("人物："):
        consumed["character"].extend(_split_names(_CHAR_RE.findall(segments[idx])))
        idx += 1
    while len(consumed["prop"]) < n_prop and idx < len(segments) and segments[idx].startswith("道具："):
        consumed["prop"].extend(_split_names(_PROP_RE.findall(segments[idx])))
        idx += 1
    for seg in segments[idx:]:
        if seg.startswith("场景："):
            extra["scene"].extend(_split_names(_SCENE_RE.findall(seg)))
        elif seg.startswith("人物："):
            extra["character"].extend(_split_names(_CHAR_RE.findall(seg)))
        elif seg.startswith("道具："):
            extra["prop"].extend(_split_names(_PROP_RE.findall(seg)))
        # else: body prose (no header label) - ignore
    return consumed, extra


# ---------- Rules ----------


def _rule_rv1_image_prompt_bible_ref(
    shots: List[Dict[str, Any]],
    image_prompts: Dict[str, Dict[str, Any]],
    char_index: Dict[str, Dict[str, Any]],
    loc_index: Dict[str, Dict[str, Any]],
    prop_index: Dict[str, Dict[str, Any]],
    findings: List[DiagnoseFinding],
    run_dir: Path,
) -> None:
    """rv1: image prompt header names must match the shot's bible refs.

    Bidirectional: missing (shot references a bible entry whose name is
    absent from the header) + phantom (header names an entry the shot
    does not reference).
    """
    # All known bible names (for the extra-segment phantom check):
    # a header-label segment past the expected count whose name hits a
    # known bible entry but is not in the shot's refs is a phantom; a
    # name that matches no bible entry is body prose and ignored.
    char_names_all = {c.get("name") for c in char_index.values() if c.get("name")}
    loc_names_all = {l.get("name") for l in loc_index.values() if l.get("name")}
    prop_names_all = {p.get("name") for p in prop_index.values() if p.get("name")}

    for shot in shots:
        sid = shot.get("id")
        if sid is None:
            continue
        ip = image_prompts.get(sid)
        if not ip:
            continue  # rv4 handles shot_id misalignment
        prompt = ip.get("prompt") or ""

        loc_id = shot.get("location_id")
        expected_loc = [loc_index[loc_id]["name"] for loc_id in [loc_id] if loc_id and loc_id in loc_index and loc_index[loc_id].get("name")]
        expected_char = [char_index[c]["name"] for c in (shot.get("character_ids") or []) if c in char_index and char_index[c].get("name")]
        expected_prop = [prop_index[p]["name"] for p in (shot.get("prop_ids") or []) if p in prop_index and prop_index[p].get("name")]

        # Parse the header into (consumed, extra): consumed = the first
        # expected-count segments in 场景->人物->道具 order; extra =
        # remaining segments that still start with a header label
        # (candidates for the bible-name phantom check). Non-label
        # segments are body prose. See Codex Phase 3 P2 round-3 review.
        consumed, extra = _parse_prompt_header(
            prompt,
            n_scene=1 if expected_loc else 0,
            n_char=len(expected_char),
            n_prop=len(expected_prop),
        )

        ev = [
            EvidenceRef(artifact="image_prompts.json", path=str(run_dir / "image_prompts.json"), locator=f"$.image_prompts[?(@.shot_id=='{sid}')].prompt"),
            EvidenceRef(artifact="shot_list.json", path=str(run_dir / "shot_list.json"), locator=f"$.shots[?(@.id=='{sid}')]"),
        ]

        # Missing refs (shot references a bible entry not in consumed header)
        if expected_loc:
            missing = [n for n in expected_loc if n not in consumed["scene"]]
            if missing:
                _add_finding(findings, "warning", "rv1_image_prompt_bible_ref_mismatch",
                             f"shot {sid} 的 image prompt header 缺少场景引用：{missing}（shot 引用 location 但 prompt 未声明）。",
                             ev)
        if expected_char:
            missing = [n for n in expected_char if n not in consumed["character"]]
            if missing:
                _add_finding(findings, "warning", "rv1_image_prompt_bible_ref_mismatch",
                             f"shot {sid} 的 image prompt header 缺少人物引用：{missing}（shot 引用角色但 prompt 未声明）。",
                             ev)
        if expected_prop:
            missing = [n for n in expected_prop if n not in consumed["prop"]]
            if missing:
                _add_finding(findings, "warning", "rv1_image_prompt_bible_ref_mismatch",
                             f"shot {sid} 的 image prompt header 缺少道具引用：{missing}（shot 引用道具但 prompt 未声明）。",
                             ev)

        # Phantom refs - consumed header names an entry the shot does not reference
        phantom_scene = [n for n in consumed["scene"] if n not in expected_loc]
        if phantom_scene:
            _add_finding(findings, "warning", "rv1_image_prompt_bible_ref_mismatch",
                         f"shot {sid} 的 image prompt header 声明了场景 {phantom_scene}，但 shot_list 未引用对应 location。",
                         ev)
        phantom_char = [n for n in consumed["character"] if n not in expected_char]
        if phantom_char:
            _add_finding(findings, "warning", "rv1_image_prompt_bible_ref_mismatch",
                         f"shot {sid} 的 image prompt header 声明了人物 {phantom_char}，但 shot_list 未引用对应角色。",
                         ev)
        phantom_prop = [n for n in consumed["prop"] if n not in expected_prop]
        if phantom_prop:
            _add_finding(findings, "warning", "rv1_image_prompt_bible_ref_mismatch",
                         f"shot {sid} 的 image prompt header 声明了道具 {phantom_prop}，但 shot_list 未引用对应道具。",
                         ev)

        # Extra phantom: header-label segments past the expected count
        # whose name hits a known bible entry but is not in the shot's
        # refs. Body prose that starts with a header label but names no
        # bible entry (e.g. "人物：背景群众只是画面描述") is ignored,
        # so body prose cannot trigger a false phantom while a real
        # phantom (header names a known bible entry the shot does not
        # reference) is still caught. See Codex round-3 review.
        for name in extra["scene"]:
            if name in loc_names_all and name not in expected_loc:
                _add_finding(findings, "warning", "rv1_image_prompt_bible_ref_mismatch",
                             f"shot {sid} 的 image prompt header 声明了场景 [{name}]，但 shot_list 未引用对应 location。",
                             ev)
        for name in extra["character"]:
            if name in char_names_all and name not in expected_char:
                _add_finding(findings, "warning", "rv1_image_prompt_bible_ref_mismatch",
                             f"shot {sid} 的 image prompt header 声明了人物 [{name}]，但 shot_list 未引用对应角色。",
                             ev)
        for name in extra["prop"]:
            if name in prop_names_all and name not in expected_prop:
                _add_finding(findings, "warning", "rv1_image_prompt_bible_ref_mismatch",
                             f"shot {sid} 的 image prompt header 声明了道具 [{name}]，但 shot_list 未引用对应道具。",
                             ev)

        # No reference at all
        if not expected_loc and not expected_char and not expected_prop:
            if not consumed["scene"] and not consumed["character"] and not consumed["prop"]:
                _add_finding(findings, "warning", "rv1_image_prompt_bible_ref_mismatch",
                             f"shot {sid} 的 image prompt 无任何 场景/人物/道具 引用，且 shot_list 也无 bible 引用。",
                             ev)


def _rule_rv2_video_prompt_fields(
    video_prompts: Dict[str, Dict[str, Any]],
    findings: List[DiagnoseFinding],
    run_dir: Path,
) -> None:
    """rv2: video prompt motion / camera / avoid must be non-empty."""
    for sid, vp in video_prompts.items():
        ev = [EvidenceRef(artifact="video_prompts.json", path=str(run_dir / "video_prompts.json"), locator=f"$.video_prompts[?(@.shot_id=='{sid}')]")]
        for field in ("motion", "camera", "avoid"):
            val = vp.get(field)
            if not val or (isinstance(val, str) and not val.strip()):
                _add_finding(findings, "warning", "rv2_video_prompt_missing_field",
                             f"shot {sid} 的 video prompt 缺少 {field} 字段（空或缺失）。",
                             ev)


def _rule_rv3_unresolved_placeholder(
    image_prompts: Dict[str, Dict[str, Any]],
    video_prompts: Dict[str, Dict[str, Any]],
    findings: List[DiagnoseFinding],
    run_dir: Path,
) -> None:
    """rv3: no template placeholders in any prompt text field."""
    for sid, ip in image_prompts.items():
        ev = [EvidenceRef(artifact="image_prompts.json", path=str(run_dir / "image_prompts.json"), locator=f"$.image_prompts[?(@.shot_id=='{sid}')]")]
        for field in ("prompt", "negative_prompt"):
            hits = _find_placeholders(ip.get(field) or "")
            if hits:
                _add_finding(findings, "error", "rv3_unresolved_placeholder",
                             f"shot {sid} 的 image prompt {field} 含未解析占位符：{hits}。",
                             ev)
    for sid, vp in video_prompts.items():
        ev = [EvidenceRef(artifact="video_prompts.json", path=str(run_dir / "video_prompts.json"), locator=f"$.video_prompts[?(@.shot_id=='{sid}')]")]
        for field in ("prompt", "avoid"):
            hits = _find_placeholders(vp.get(field) or "")
            if hits:
                _add_finding(findings, "error", "rv3_unresolved_placeholder",
                             f"shot {sid} 的 video prompt {field} 含未解析占位符：{hits}。",
                             ev)


def _rule_rv4_shot_id_alignment(
    shots: List[Dict[str, Any]],
    image_prompts: Dict[str, Dict[str, Any]],
    video_prompts: Dict[str, Dict[str, Any]],
    findings: List[DiagnoseFinding],
    run_dir: Path,
) -> None:
    """rv4: shot_list / image_prompts / video_prompts shot_id sets agree."""
    shot_ids = {s.get("id") for s in shots if s.get("id")}
    image_ids = set(image_prompts.keys())
    video_ids = set(video_prompts.keys())
    ev = [
        EvidenceRef(artifact="shot_list.json", path=str(run_dir / "shot_list.json"), locator="$.shots[*].id"),
        EvidenceRef(artifact="image_prompts.json", path=str(run_dir / "image_prompts.json"), locator="$.image_prompts[*].shot_id"),
        EvidenceRef(artifact="video_prompts.json", path=str(run_dir / "video_prompts.json"), locator="$.video_prompts[*].shot_id"),
    ]
    if shot_ids != image_ids:
        diff = sorted(shot_ids.symmetric_difference(image_ids))
        _add_finding(findings, "warning", "rv4_shot_id_misaligned",
                     f"shot_list 与 image_prompts 的 shot_id 不一致：{diff}。",
                     ev)
    if shot_ids != video_ids:
        diff = sorted(shot_ids.symmetric_difference(video_ids))
        _add_finding(findings, "warning", "rv4_shot_id_misaligned",
                     f"shot_list 与 video_prompts 的 shot_id 不一致：{diff}。",
                     ev)


# ---------- Chinese summary ----------


def _build_summary_zh(report: ReviewRunReport) -> str:
    """Build a 1-3 sentence Chinese summary. Factual, no emojis."""
    if report.run_id is None and report.env is None:
        return (
            "未能读取 run_summary.json；"
            f"review 只报告了 {len(report.findings)} 条 finding（见 findings 列表）。"
        )
    env_label = report.env or "未知"
    rid = report.run_id or "?"
    counts_str = ""
    if report.counts:
        shots = report.counts.get("shots")
        if shots is not None:
            counts_str = f"，共 {shots} 个镜头"
    lines: List[str] = [f"run {rid}（env={env_label}）{counts_str}。"]

    findings_summary: List[str] = []
    n_err = sum(1 for f in report.findings if f.severity == "error")
    n_warn = sum(1 for f in report.findings if f.severity == "warning")
    if n_err:
        findings_summary.append(f"{n_err} 条 error")
    if n_warn:
        findings_summary.append(f"{n_warn} 条 warning")
    if findings_summary:
        lines.append("本次 review 发现 " + " 和 ".join(findings_summary) + "。")
    elif report.status == "ok":
        lines.append("未发现 prompt-bible 一致性问题。")

    for f in report.findings:
        if f.code == "rv3_unresolved_placeholder":
            lines.append("[RED LINE] prompt 含未解析占位符；不应进入 executor。")
            break
    return "".join(lines)


# ---------- Engine entry ----------


def review_run_dir(
    run_dir: Path,
    *,
    expected_env: Optional[str] = None,
) -> ReviewRunReport:
    """Review a single run's prompt-bible consistency.

    Read-only: never writes files, never calls LLM / shell. Graceful
    degradation: missing / corrupted ``run_summary.json`` -> error +
    minimal report; missing / corrupted artifact -> warning + skip
    dependent rules.
    """
    run_dir = Path(run_dir)
    report = ReviewRunReport(
        run_dir=str(run_dir),
        expected_env=expected_env,
        generated_at=_now_iso(),
        tool_invocations=[],
    )

    # ----- Step 0: load run_summary.json (missing/corrupted -> error) -----
    try:
        summary = read_run_summary(run_dir)
    except KeyError as exc:
        msg = str(exc)
        if "not found" in msg or "file not found" in msg:
            code = "missing_run_summary"
            message = "run_summary.json 不存在；agent 无法 review 本次 run。请确认 run_dir 路径正确，且 pipeline.run() 已成功完成。"
        else:
            code = "corrupted_run_summary"
            message = f"run_summary.json 损坏（{_safe_text(msg)}）；agent 将输出最小化报告。"
        report.findings.append(
            DiagnoseFinding(
                severity="error",
                code=code,
                message=_safe_text(message),
                evidence=[EvidenceRef(artifact="run_summary.json", path=str(run_dir / "run_summary.json"), locator="$")],
            )
        )
        report.tool_invocations.append(
            ToolInvocation(tool="read_run_summary", ok=False, artifact_refs=["run_summary.json"], bytes_read=0)
        )
        report.summary = _build_summary_zh(report)
        return report.derive_status()

    # Legitimate JSON but top level not a dict (e.g. a bare list /
    # string / int). ``read_run_summary`` only guards ``data is None``,
    # so a non-dict top level reaches here and the ``summary.get(...)``
    # calls below would raise ``AttributeError`` (not caught by
    # ``except KeyError``) and leak a traceback. Treat as corrupted.
    # See Codex Phase 3 P2 review (P1).
    if not isinstance(summary, dict):
        report.findings.append(
            DiagnoseFinding(
                severity="error",
                code="corrupted_run_summary",
                message=_safe_text(
                    f"run_summary.json 顶层不是 JSON 对象（{type(summary).__name__}）；agent 将输出最小化报告。"
                ),
                evidence=[EvidenceRef(artifact="run_summary.json", path=str(run_dir / "run_summary.json"), locator="$")],
            )
        )
        report.tool_invocations.append(
            ToolInvocation(tool="read_run_summary", ok=False, artifact_refs=["run_summary.json"], bytes_read=0)
        )
        report.summary = _build_summary_zh(report)
        return report.derive_status()

    report.tool_invocations.append(
        ToolInvocation(tool="read_run_summary", ok=True, artifact_refs=["run_summary.json"], bytes_read=0)
    )
    report.run_id = summary.get("run_id")
    report.env = summary.get("env")

    # expected_env mismatch (warning, mirrors diagnose R6)
    if expected_env and report.env and expected_env != report.env:
        _add_finding(
            report.findings,
            "warning",
            "env_mismatch",
            f"run_summary.env={report.env!r} 与 --expected-env={expected_env!r} 不一致。",
            [EvidenceRef(artifact="run_summary.json", path=str(run_dir / "run_summary.json"), locator="$.env")],
        )

    # ----- Step 1: list_artifacts (record tool call) -----
    _tools_list_artifacts(run_dir)  # existence probe
    report.tool_invocations.append(
        ToolInvocation(tool="list_artifacts", ok=True, artifact_refs=["run_summary.json"], bytes_read=0)
    )

    # ----- Step 2: load bibles + shot_list + prompts -----
    artifacts: Dict[str, Optional[Dict[str, Any]]] = {}
    for name in _REVIEW_ARTIFACTS:
        artifacts[name] = _read_artifact_safe(run_dir, name, report.findings, report.tool_invocations)

    char_bible = artifacts.get("character_bible") or {}
    loc_bible = artifacts.get("location_bible") or {}
    prop_bible = artifacts.get("prop_bible") or {}
    shot_list = artifacts.get("shot_list") or {}
    image_prompts_raw = artifacts.get("image_prompts") or {}
    video_prompts_raw = artifacts.get("video_prompts") or {}

    char_index = {c.get("id"): c for c in (char_bible.get("characters") or []) if isinstance(c, dict)}
    loc_index = {l.get("id"): l for l in (loc_bible.get("locations") or []) if isinstance(l, dict)}
    prop_index = {p.get("id"): p for p in (prop_bible.get("props") or []) if isinstance(p, dict)}
    shots = shot_list.get("shots") or []
    image_prompts = {p.get("shot_id"): p for p in (image_prompts_raw.get("image_prompts") or []) if isinstance(p, dict) and p.get("shot_id")}
    video_prompts = {p.get("shot_id"): p for p in (video_prompts_raw.get("video_prompts") or []) if isinstance(p, dict) and p.get("shot_id")}

    report.counts = {
        "shots": len(shots),
        "characters": len(char_index),
        "locations": len(loc_index),
        "props": len(prop_index),
        "image_prompts": len(image_prompts),
        "video_prompts": len(video_prompts),
    }

    # ----- Step 3: rules (only when dependencies present) -----
    have = {name: artifacts.get(name) is not None for name in _REVIEW_ARTIFACTS}
    if have["shot_list"] and (have["image_prompts"] or have["video_prompts"]):
        _rule_rv4_shot_id_alignment(shots, image_prompts, video_prompts, report.findings, run_dir)
    if have["shot_list"] and have["image_prompts"] and have["character_bible"] and have["location_bible"] and have["prop_bible"]:
        _rule_rv1_image_prompt_bible_ref(shots, image_prompts, char_index, loc_index, prop_index, report.findings, run_dir)
    if have["video_prompts"]:
        _rule_rv2_video_prompt_fields(video_prompts, report.findings, run_dir)
    if have["image_prompts"] or have["video_prompts"]:
        _rule_rv3_unresolved_placeholder(image_prompts, video_prompts, report.findings, run_dir)

    # ----- Step 4: derive status + Chinese summary -----
    report.summary = _build_summary_zh(report)
    return report.derive_status()


__all__ = ["ReviewRunReport", "review_run_dir"]
