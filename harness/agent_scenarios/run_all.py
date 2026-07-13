"""Harness: agent scenario runner (read-only).

Validates every JSON scenario in ``harness/agent_scenarios/`` against
two checks:

1. **Static validation** — the scenario JSON is well-formed, has the
   required top-level keys, ``expected_tool_calls`` and
   ``forbidden_tool_calls`` do not overlap, every ``expected_tool_call``
   has a non-empty ``tool`` name, and the approval-gate scenarios
   declare an ``expected_approval_request`` shape.

2. **Static + live cross-check** — for each scenario that needs a run
   directory (diagnose / review), the runner generates a fresh
   development run via ``planner run`` and verifies that the scenario's
   declared ``expected_tool_calls`` are individually applicable
   against the produced artifacts. We do NOT call into an agent
   (the product-side agent is out of scope for v1.0); the runner
   only checks that **if** the agent followed the scenario, the
   artifacts it would touch exist.

The runner does NOT execute the agent and does NOT trigger any
write actions. It is purely a static + live cross-check harness so
the scenario files stay accurate as the artifact schema evolves.

Run as::

    python3 harness/agent_scenarios/run_all.py

Exit code 0 on full success, non-zero on first failed check.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Set

HARNESS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = HARNESS_DIR.parent.parent
PYTHON = sys.executable
SAMPLE_SCRIPT = PROJECT_ROOT / "samples" / "v1" / "EP01.txt"

REQUIRED_TOP_KEYS = {
    "scenario_id",
    "version",
    "description",
    "category",
    "risk_level",
    "expected_outcome",
    "input",
    "expected_tool_calls",
    "forbidden_tool_calls",
}
VALID_RISK_LEVELS = {"read_only", "requires_approval"}
VALID_CATEGORIES = {"diagnose", "review", "approval_gate"}


def _log(msg: str) -> None:
    print(f"[agent_scenarios] {msg}", flush=True)


def _scrubbed_env() -> Dict[str, str]:
    """Return a copy of the environment with PLANNER_* cleared."""

    return {k: v for k, v in os.environ.items() if not k.startswith("PLANNER_")}


def load_scenarios() -> Dict[str, dict]:
    """Load every ``*.json`` in this directory into a name → dict map."""

    scenarios: Dict[str, dict] = {}
    for path in sorted(HARNESS_DIR.glob("*.json")):
        try:
            scenarios[path.name] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(
                f"[agent_scenarios] {path.name} is not valid JSON: {exc}"
            )
    if not scenarios:
        raise SystemExit(
            f"[agent_scenarios] no scenario files found under "
            f"{HARNESS_DIR}"
        )
    return scenarios


def validate_static_shape(name: str, scenario: dict) -> None:
    """Step 1: scenario JSON has the required shape."""

    missing = REQUIRED_TOP_KEYS - set(scenario.keys())
    if missing:
        raise SystemExit(
            f"[agent_scenarios] {name}: missing top-level keys: "
            f"{sorted(missing)}"
        )
    if scenario["risk_level"] not in VALID_RISK_LEVELS:
        raise SystemExit(
            f"[agent_scenarios] {name}: unknown risk_level "
            f"{scenario['risk_level']!r}"
        )
    if scenario["category"] not in VALID_CATEGORIES:
        raise SystemExit(
            f"[agent_scenarios] {name}: unknown category "
            f"{scenario['category']!r}"
        )
    expected: List[dict] = scenario["expected_tool_calls"]
    forbidden: List[str] = scenario["forbidden_tool_calls"]
    expected_names: Set[str] = {t.get("tool") for t in expected}
    if any(not n for n in expected_names):
        raise SystemExit(
            f"[agent_scenarios] {name}: expected_tool_calls contains "
            f"an entry without a 'tool' field"
        )
    overlap = expected_names & set(forbidden)
    if overlap:
        raise SystemExit(
            f"[agent_scenarios] {name}: expected and forbidden tool "
            f"calls overlap: {sorted(overlap)}"
        )
    _log(
        f"{name}: shape ok (category={scenario['category']!r}, "
        f"risk_level={scenario['risk_level']!r}, "
        f"{len(expected)} expected tools, {len(forbidden)} forbidden)"
    )


def ensure_sample_run(tmp_root: Path) -> Path:
    """Generate a deterministic development run for the live checks."""

    out_dir = tmp_root / "agent_scenario_run"
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
            f"[agent_scenarios] failed to generate sample run: "
            f"rc={proc.returncode} stderr={proc.stderr}"
        )
    return out_dir


def ensure_sample_batch(tmp_root: Path) -> Path:
    """Generate a deterministic batch for the cross-episode live check."""

    out_dir = tmp_root / "agent_scenario_batch"
    proc = subprocess.run(
        [
            PYTHON, "-m", "planner",
            "batch",
            "--env", "development",
            "--scripts", str(PROJECT_ROOT / "samples" / "v1"),
            "--out", str(out_dir),
            "--skip-validation",
        ],
        capture_output=True,
        text=True,
        env=_scrubbed_env(),
        cwd=str(PROJECT_ROOT),
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"[agent_scenarios] failed to generate sample batch: "
            f"rc={proc.returncode} stderr={proc.stderr}"
        )
    return out_dir


# Mapping from scenario-declared tool names to the artifacts that
# must exist for that tool to be applicable. This is a *static*
# check — we are not invoking the tool, just verifying the
# preconditions the agent would face.
_TOOL_ARTIFACT_MAP = {
    "read_run_summary": ["run_summary.json"],
    "validate_run": ["run_summary.json", "script_parse.json"],
    "list_artifacts": ["run_summary.json"],
    "read_artifact": [],  # depends on the artifact name; checked per call
    "read_batch_summary": ["batch_summary.json"],
    "list_runs_in_batch": ["batch_summary.json"],
}


def validate_live_cross_check(
    name: str,
    scenario: dict,
    sample_run_dir: Path,
    sample_batch_dir: Path,
) -> None:
    """Step 2: the scenario's expected_tool_calls apply to real artifacts."""

    input_block = scenario.get("input", {}) or {}
    run_dir_value = input_block.get("run_dir")
    batch_dir_value = input_block.get("batch_dir")
    # Decide which fixture to cross-check against. If both
    # ``run_dir`` and ``batch_dir`` are template placeholders, fall
    # back to the scenario's primary fixture (batch_continuity uses
    # batch_dir; diagnose / review_prompt_refs use run_dir).
    if batch_dir_value in (None, "<batch_dir>") and run_dir_value in (
        None, "<run_dir>", "",
    ):
        # batch_continuity primarily targets a batch; diagnose /
        # review_prompt_refs target a single run. Use the field that
        # is present + non-empty.
        if "batch_dir" in input_block and "run_dir" not in input_block:
            target_dir = sample_batch_dir
            target_label = "batch"
        elif "run_dir" in input_block and "batch_dir" not in input_block:
            target_dir = sample_run_dir
            target_label = "run"
        else:
            # Both placeholders; pick the most relevant by scenario
            # id (batch_continuity → batch; the rest → run).
            if "batch_continuity" in name:
                target_dir = sample_batch_dir
                target_label = "batch"
            else:
                target_dir = sample_run_dir
                target_label = "run"
    elif run_dir_value in (None, "<run_dir>", ""):
        target_dir = sample_run_dir
        target_label = "run"
    elif batch_dir_value in (None, "<batch_dir>", ""):
        target_dir = sample_batch_dir
        target_label = "batch"
    else:
        target_dir = None
        target_label = "unknown"

    for call in scenario["expected_tool_calls"]:
        tool = call.get("tool")
        required_artifacts = _TOOL_ARTIFACT_MAP.get(tool, [])
        for art in required_artifacts:
            if target_dir is None:
                continue
            candidate = target_dir / art
            # batch_summary.json lives at the batch root, but
            # run_summary.json lives inside each episode dir.
            if target_label == "batch" and art == "run_summary.json":
                # Find any per-episode run_summary.json
                per_episode = list(target_dir.glob("EP*/run_summary.json"))
                if not per_episode:
                    raise SystemExit(
                        f"[agent_scenarios] {name}: scenario expects "
                        f"{tool!r} which requires per-episode "
                        f"run_summary.json under {target_dir}, but "
                        f"none was produced"
                    )
                continue
            if not candidate.exists():
                raise SystemExit(
                    f"[agent_scenarios] {name}: scenario expects "
                    f"{tool!r} which requires {art!r} at {candidate}, "
                    f"but the file does not exist"
                )
        # ``must_contain_keys`` is the agent-side schema. We just
        # sanity-check that the scenario author didn't leave it empty
        # by mistake.
        must_keys = call.get("must_contain_keys") or []
        if tool in {"read_artifact"} and not must_keys:
            raise SystemExit(
                f"[agent_scenarios] {name}: {tool!r} call has empty "
                f"must_contain_keys - likely author error"
            )
    _log(
        f"{name}: live cross-check ok ({target_label}="
        f"{target_dir if target_dir else 'n/a'})"
    )


def validate_approval_gate_shape(name: str, scenario: dict) -> None:
    """Step 3: approval-gate scenarios declare the right shape."""

    if scenario["risk_level"] != "requires_approval":
        return
    approvals = scenario.get("expected_approval_requests") or []
    if not approvals:
        raise SystemExit(
            f"[agent_scenarios] {name}: risk_level=requires_approval "
            f"but expected_approval_requests is empty"
        )
    for req in approvals:
        for must_key in ("action", "must_list_side_effects",
                         "must_list_revert_path"):
            if not req.get(must_key):
                raise SystemExit(
                    f"[agent_scenarios] {name}: approval request "
                    f"missing {must_key!r}"
                )
    # The agent MUST surface at least one forbidden tool call that
    # the approval gate is supposed to block.
    forbidden = set(scenario.get("forbidden_tool_calls") or [])
    if not forbidden:
        raise SystemExit(
            f"[agent_scenarios] {name}: approval-gate scenario has no "
            f"forbidden_tool_calls - gate is decorative"
        )
    _log(
        f"{name}: approval-gate shape ok ({len(approvals)} approval "
        f"request shape(s), {len(forbidden)} forbidden tools)"
    )


def main() -> int:
    scenarios = load_scenarios()
    _log(f"loaded {len(scenarios)} scenario file(s)")

    tmp_root = Path(tempfile.mkdtemp(prefix="agent_scenarios_"))
    try:
        # Validate static shape first (no I/O needed).
        for name, scenario in scenarios.items():
            validate_static_shape(name, scenario)

        # Approval-gate scenarios: shape only, no live fixtures needed.
        for name, scenario in scenarios.items():
            if scenario["category"] == "approval_gate":
                validate_approval_gate_shape(name, scenario)

        # Generate fixtures once; reused by all live cross-checks.
        any_live = any(
            s["category"] in {"diagnose", "review"} for s in scenarios.values()
        )
        if any_live:
            sample_run_dir = ensure_sample_run(tmp_root)
            sample_batch_dir = ensure_sample_batch(tmp_root)
        else:
            sample_run_dir = sample_batch_dir = tmp_root

        # Live cross-check.
        for name, scenario in scenarios.items():
            if scenario["category"] in {"diagnose", "review"}:
                validate_live_cross_check(
                    name, scenario, sample_run_dir, sample_batch_dir,
                )
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[agent_scenarios] unexpected error: {exc}", file=sys.stderr)
        return 3
    finally:
        _log(f"work dir kept at {tmp_root} for inspection")
    _log("ALL AGENT SCENARIO STEPS PASSED ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())