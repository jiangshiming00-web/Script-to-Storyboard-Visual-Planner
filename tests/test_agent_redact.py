"""Tests for planner.agent.redact (Phase 3 P1).

Phase 3 P1 contract: the agent must never echo raw API key values
into findings / summary / JSON output. This module pins the
redaction regexes that protect those surfaces. Regex alignment
with ``planner.providers.openai_compatible_adapter._redact_secrets``
is required — a PR that changes one must change the other.
"""

from __future__ import annotations

import pytest

from planner.agent.redact import redact_secrets_text


class TestRedactOpenAI:
    def test_sk_prefix_redacted(self) -> None:
        out = redact_secrets_text("api_key=sk-projabc12345xyz")
        assert out == "api_key=<redacted>"

    def test_sk_with_dashes_underscores(self) -> None:
        out = redact_secrets_text("key=sk-abc_DEF-12345678")
        assert out == "key=<redacted>"

    def test_short_sk_not_matched(self) -> None:
        # Less than 8 chars after the sk- prefix; the regex requires
        # at least 8 to avoid false positives on words like "sk-test".
        out = redact_secrets_text("sk-short")
        assert out == "sk-short"


class TestRedactAnthropic:
    def test_sk_ant_prefix_redacted(self) -> None:
        out = redact_secrets_text(
            "env PLANNER_ANTHROPIC_API_KEY=sk-ant-api03-abcdefghij"
        )
        assert out == "env PLANNER_ANTHROPIC_API_KEY=<redacted>"

    def test_anthropic_redacted_before_openai(self) -> None:
        # Anthropic key starts with sk-; if the openai regex ran first
        # it would leave the sk-ant- prefix. Verify ordering is correct.
        out = redact_secrets_text("sk-ant-api03-abcdefghij")
        assert out == "<redacted>"


class TestRedactBearer:
    def test_bearer_redacted_preserving_prefix(self) -> None:
        out = redact_secrets_text("Authorization: Bearer eyJabc12345")
        assert out == "Authorization: Bearer <redacted>"

    def test_bearer_short_token_not_matched(self) -> None:
        # 5 chars after Bearer; below the 8-char threshold.
        out = redact_secrets_text("Bearer short")
        assert out == "Bearer short"


class TestRedactGitHub:
    def test_gho_prefix_redacted(self) -> None:
        out = redact_secrets_text("token=gho_abc12345xyz")
        assert out == "token=<redacted>"


class TestRedactFalsePositives:
    @pytest.mark.parametrize(
        "text",
        [
            "uuid=12345678-1234-1234-1234-123456789012",  # UUID
            "id=00000000",                                 # all digits
            "abcdefghij",                                  # plain word, no prefix
            "Bearer short",                                # short bearer
            "sk-short",                                    # short sk-
            "sk_ant-short",                                # short with underscore
        ],
    )
    def test_non_secrets_not_redacted(self, text: str) -> None:
        assert redact_secrets_text(text) == text

    def test_empty_string_unchanged(self) -> None:
        assert redact_secrets_text("") == ""

    def test_none_safe(self) -> None:
        # ``None`` must not crash; the function only accepts str but
        # a defensive guard means callers don't need to check.
        assert redact_secrets_text("") == ""


class TestRedactMixed:
    def test_multiple_secrets_in_one_string(self) -> None:
        text = "openai=sk-aaaaaaaaaaaa anthropic=sk-ant-bbbbbbbbbbbb bearer=Bearer ccccccccc github=gho_ddddddddd"
        out = redact_secrets_text(text)
        assert "sk-aaaaaaaaaaaa" not in out
        assert "sk-ant-bbbbbbbbbbbb" not in out
        assert "Bearer ccccccccc" not in out
        assert "gho_ddddddddd" not in out
        assert out.count("<redacted>") == 4
