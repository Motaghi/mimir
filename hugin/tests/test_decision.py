"""Tests for hugin.router.decision."""

from __future__ import annotations

import pytest

from hugin.router import decision
from hugin.router.decision import (
    DECISION_RE,
    FALLBACK_NAME,
    MALFORMED_DECISION,
    decision_from_envelope,
    decision_from_reply,
    extract_envelope,
    is_valid_decision,
    safe_name,
)

# --- grammar -----------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "branch:1:none",
        "branch:2:code-reviewer",
        "branch:3:writer",
        "branch:4:multiple",
        "branch:5:queue-handoff",
        "branch:6:clarify",
        "branch:1:a",
    ],
)
def test_valid_decisions_accepted(value: str) -> None:
    assert is_valid_decision(value)


@pytest.mark.parametrize(
    "value",
    [
        "branch:0:foo",
        "branch:7:foo",
        "branch:1:Foo",
        "branch:1:1foo",
        "branch:1:",
        "branch:1",
        "Branch:1:foo",
        "",
    ],
)
def test_invalid_decisions_rejected(value: str) -> None:
    assert not is_valid_decision(value)


def test_decision_pattern_is_pinned() -> None:
    assert DECISION_RE.pattern == r"^branch:[1-6]:[a-z][a-z0-9-]*$"
    assert decision.MALFORMED_DECISION == "branch:1:malformed"


# --- safe_name ---------------------------------------------------------------


def test_safe_name_passes_kebab_case() -> None:
    assert safe_name("code-reviewer") == "code-reviewer"


def test_safe_name_lowercases_plain_letters() -> None:
    assert safe_name("WRITER") == "writer"


@pytest.mark.parametrize("value", ["Code_Reviewer", "!@#$%", "", "   ", "9lives"])
def test_safe_name_falls_back(value: str) -> None:
    assert safe_name(value) == FALLBACK_NAME
    assert is_valid_decision(f"branch:2:{safe_name(value)}")


# --- extract_envelope --------------------------------------------------------


def test_extract_envelope_plain_json() -> None:
    env, status = extract_envelope('{"relay": "ok", "audit_entries": []}')
    assert status == "ok"
    assert env["relay"] == "ok"


def test_extract_envelope_fenced_json() -> None:
    body = 'Here is the answer:\n```json\n{"relay": "x", "audit_entries": []}\n```\n'
    env, status = extract_envelope(body)
    assert status == "ok"
    assert env["relay"] == "x"


def test_extract_envelope_unfenced_with_prose() -> None:
    body = 'My decision is: {"relay": "x", "audit_entries": []} and nothing else.'
    env, status = extract_envelope(body)
    assert status == "ok"
    assert env["audit_entries"] == []


@pytest.mark.parametrize("body", ["", "   \n  \t  "])
def test_extract_envelope_empty(body: str) -> None:
    assert extract_envelope(body) == ({}, "empty")


@pytest.mark.parametrize("body", ["just prose, no JSON here", "{not actually json}", "[1, 2, 3]"])
def test_extract_envelope_malformed(body: str) -> None:
    assert extract_envelope(body) == ({}, "malformed")


# --- decision_from_envelope --------------------------------------------------


def test_no_audit_entries_field() -> None:
    assert decision_from_envelope({"relay": "x"}) == "branch:1:none"


def test_empty_audit_entries() -> None:
    assert decision_from_envelope({"audit_entries": []}) == "branch:1:none"


def test_audit_entries_not_a_list() -> None:
    assert decision_from_envelope({"audit_entries": "writer"}) == "branch:1:none"


def test_single_entry() -> None:
    env = {"audit_entries": [{"subagent_type": "code-reviewer", "outcome": "shadow"}]}
    assert decision_from_envelope(env) == "branch:2:code-reviewer"


def test_multiple_entries() -> None:
    env = {
        "audit_entries": [
            {"subagent_type": "code-reviewer", "outcome": "shadow"},
            {"subagent_type": "researcher", "outcome": "shadow"},
        ]
    }
    assert decision_from_envelope(env) == "branch:4:multiple"


@pytest.mark.parametrize(
    "entry",
    [{"subagent_type": "Code_Reviewer"}, {"outcome": "shadow"}, "not a dict"],
)
def test_single_entry_without_usable_name(entry: object) -> None:
    assert decision_from_envelope({"audit_entries": [entry]}) == f"branch:2:{FALLBACK_NAME}"


# --- decision_from_reply -----------------------------------------------------


def test_reply_maps_in_one_step() -> None:
    d, env, status = decision_from_reply('{"audit_entries": [{"subagent_type": "writer"}]}')
    assert (d, status) == ("branch:2:writer", "ok")
    assert env["audit_entries"][0]["subagent_type"] == "writer"


@pytest.mark.parametrize("body,status", [("", "empty"), ("prose only", "malformed")])
def test_reply_failure_is_malformed(body: str, status: str) -> None:
    assert decision_from_reply(body) == (MALFORMED_DECISION, {}, status)


def test_decision_with_trailing_newline_rejected() -> None:
    assert not is_valid_decision("branch:1:none\n")
