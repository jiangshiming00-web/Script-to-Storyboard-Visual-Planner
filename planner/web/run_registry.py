"""In-memory registry of pipeline runs.

The web UI needs to know which runs exist, their current status
(``running`` / ``done`` / ``failed``), and where they live on disk, so
it can poll progress and list history. We keep this state in memory
because:

- The registry is per-process; the web server is single-user for now
  (pywebview opens one window).
- Re-scanning the filesystem on every poll would be wasteful for
  hundreds of runs.
- The on-disk ``run_summary.json`` remains the source of truth for
  artifacts; this registry only tracks **process-local** state (status,
  started_at) that is not yet persisted.

The registry is thread-safe because the run loop updates it from a
background thread while the HTTP handler reads it from the main thread.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class RunRecord:
    run_id: str
    out_dir: Path
    env: str
    started_at: str
    status: str = "running"  # running | done | failed
    finished_at: Optional[str] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    counts: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["out_dir"] = str(self.out_dir)
        return d


class RunRegistry:
    """Thread-safe in-memory map of run_id → RunRecord."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: Dict[str, RunRecord] = {}

    def register(self, run_id: str, out_dir: Path, env: str) -> RunRecord:
        rec = RunRecord(
            run_id=run_id,
            out_dir=out_dir,
            env=env,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock:
            self._records[run_id] = rec
        return rec

    def mark_done(self, run_id: str, counts: Dict[str, int]) -> None:
        with self._lock:
            rec = self._records.get(run_id)
            if rec is None:
                return
            rec.status = "done"
            rec.finished_at = datetime.now(timezone.utc).isoformat()
            rec.counts = dict(counts)

    def mark_failed(self, run_id: str, err_type: str, err_message: str) -> None:
        with self._lock:
            rec = self._records.get(run_id)
            if rec is None:
                return
            rec.status = "failed"
            rec.finished_at = datetime.now(timezone.utc).isoformat()
            rec.error_type = err_type
            rec.error_message = err_message

    def get(self, run_id: str) -> Optional[RunRecord]:
        with self._lock:
            rec = self._records.get(run_id)
            if rec is None:
                return None
            # Return a copy so callers can mutate freely.
            return RunRecord(**asdict(rec))

    def list(self, env: Optional[str] = None) -> List[RunRecord]:
        with self._lock:
            records = list(self._records.values())
        if env is not None:
            records = [r for r in records if r.env == env]
        # Newest first.
        records.sort(key=lambda r: r.started_at, reverse=True)
        return [RunRecord(**asdict(r)) for r in records]

    def clear(self) -> None:
        """Test helper: drop all records."""

        with self._lock:
            self._records.clear()