"""Secret redaction for agent output (read-only, no I/O).

Phase 3 P1: minimal surface area to redact 4 common secret formats
(Bearer, OpenAI sk-, Anthropic sk-ant-, GitHub gho_) before any
agent finding surfaces in JSON / stderr / --write-report.

Why this exists: api_key_value 永远不出现在 disk / log / stderr.
Even when run_summary.json / fallback_reason / prompt string happens
to contain a leaked key (shouldn't happen given model_config redaction
contract, but defense in depth), this module ensures the agent's
report surface stays clean.

Regex source: planner/providers/openai_compatible_adapter.py:177-188
The 4 patterns are intentionally aligned with the provider-side
redaction so the agent and the provider agree on what counts as a
secret. Any new token format MUST be added in BOTH places.

Hard rule: never echo raw api_key_value into findings / summary /
tool_invocations. Always run redact_secrets_text first.
"""

from __future__ import annotations

import re
from typing import Final

_BEARER_RE: Final[re.Pattern[str]] = re.compile(r"(Bearer\s+)[A-Za-z0-9_\-]{8,}")
_OPENAI_KEY_RE: Final[re.Pattern[str]] = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")
_ANTHROPIC_KEY_RE: Final[re.Pattern[str]] = re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}")
_GITHUB_KEY_RE: Final[re.Pattern[str]] = re.compile(r"gho_[A-Za-z0-9_\-]{8,}")

_REDACTED: Final[str] = "<redacted>"


def redact_secrets_text(text: str) -> str:
    """Replace any matched secret prefix + body with ``<redacted>``.

    The 4 regexes are non-overlapping by construction:
      * ``sk-ant-...`` is matched by the anthropic regex BEFORE the
        generic openai ``sk-...`` regex (anthropic first because it
        is a strict prefix match and would otherwise be partially
        matched by the openai one).
      * ``gho_...`` is GitHub; orthogonal to sk-/sk-ant-.
      * ``Bearer <token>`` is matched independently and keeps the
        ``Bearer `` literal so the resulting string is still
        syntactically a Bearer header (helpful for log readers).

    Numbers, UUIDs, and short strings are NOT matched because every
    regex requires the documented prefix and at least 8 chars after.

    Empty / None inputs are returned unchanged.
    """
    if not text:
        return text
    # Order matters: anthropic before openai (since sk-ant- starts
    # with sk-). Bearer handled separately to preserve the literal.
    text = _BEARER_RE.sub(r"\1" + _REDACTED, text)
    text = _ANTHROPIC_KEY_RE.sub(_REDACTED, text)
    text = _OPENAI_KEY_RE.sub(_REDACTED, text)
    text = _GITHUB_KEY_RE.sub(_REDACTED, text)
    return text
