"""Tests for hugin.router.envelope."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hugin.router.envelope import (
    AuditEntry,
    HuginResultEnvelope,
    RequestHistory,
    ReviewRecord,
)


def _entry(**overrides: object) -> AuditEntry:
    base: dict[str, object] = {
        "subagent_type": "researcher",
        "prompt_summary": "look up the release notes",
        "response_size_bytes": 100,
        "outcome": "success",
    }
    base.update(overrides)
    return AuditEntry.model_validate(base)


def test_minimal_envelope_round_trip() -> None:
    env = HuginResultEnvelope(relay="Drafted the note. Review passed.", audit_entries=[_entry()])
    assert env.relay.startswith("Drafted")
    assert HuginResultEnvelope.model_validate_json(env.model_dump_json()) == env


def test_shadow_outcome_accepted() -> None:
    assert _entry(outcome="shadow").outcome == "shadow"


def test_other_outcome_rejected() -> None:
    with pytest.raises(ValidationError):
        _entry(outcome="maybe")


def test_audit_entry_rejects_blank_fields() -> None:
    with pytest.raises(ValidationError):
        _entry(subagent_type="   ")
    with pytest.raises(ValidationError):
        _entry(prompt_summary="   ")


def test_audit_entry_summary_capped() -> None:
    _entry(prompt_summary="x" * 120)
    with pytest.raises(ValidationError):
        _entry(prompt_summary="x" * 121)


def test_audit_entry_size_non_negative() -> None:
    with pytest.raises(ValidationError):
        _entry(response_size_bytes=-1)


def test_relay_at_exactly_100_words_accepted() -> None:
    HuginResultEnvelope(relay=" ".join(["word"] * 100))
    with pytest.raises(ValidationError, match="100 words"):
        HuginResultEnvelope(relay=" ".join(["word"] * 101))


def test_relay_must_not_be_empty_or_whitespace() -> None:
    with pytest.raises(ValidationError):
        HuginResultEnvelope(relay="")
    with pytest.raises(ValidationError):
        HuginResultEnvelope(relay="   ")


def test_request_history_envelope() -> None:
    env = HuginResultEnvelope(
        relay="Need more context.",
        request_history=RequestHistory(
            range="turns-3-through-7",
            reason="an earlier turn names a file I cannot find",
            expected_token_size=800,
        ),
    )
    assert env.request_history is not None
    assert env.request_history.range == "turns-3-through-7"


@pytest.mark.parametrize("value", ["turns-3-through-7", "turns-1-through-1", "turns-100-through-200"])
def test_request_history_range_valid(value: str) -> None:
    RequestHistory(range=value, reason="x", expected_token_size=10)


@pytest.mark.parametrize("value", ["turns 3 through 7", "3-7", "turns-3-thru-7", "", "turns--3"])
def test_request_history_range_invalid(value: str) -> None:
    with pytest.raises(ValidationError):
        RequestHistory(range=value, reason="x", expected_token_size=10)


def test_request_history_reason_rules() -> None:
    RequestHistory(range="turns-1-through-1", reason="x" * 80, expected_token_size=10)
    with pytest.raises(ValidationError):
        RequestHistory(range="turns-1-through-1", reason="x" * 81, expected_token_size=10)
    with pytest.raises(ValidationError):
        RequestHistory(range="turns-1-through-1", reason="  ", expected_token_size=10)


def test_request_history_token_size_non_negative() -> None:
    with pytest.raises(ValidationError):
        RequestHistory(range="turns-1-through-1", reason="x", expected_token_size=-1)


def test_review_records_field() -> None:
    env = HuginResultEnvelope(
        relay="Reviewed.",
        review_records=[ReviewRecord(artifact_path="notes/x.md", record_path="reviews/r-1.json")],
    )
    assert len(env.review_records) == 1


def test_review_record_paths_non_empty() -> None:
    with pytest.raises(ValidationError):
        ReviewRecord(artifact_path="", record_path="reviews/x.json")
    with pytest.raises(ValidationError):
        ReviewRecord(artifact_path="notes/x.md", record_path=" ")


def test_munin_update_optional() -> None:
    env = HuginResultEnvelope(relay="ok")
    assert env.munin_update is None
    assert env.audit_entries == []


def test_full_envelope_round_trip() -> None:
    env = HuginResultEnvelope(
        relay="Drafted the note. Review passed.",
        audit_entries=[
            _entry(subagent_type="writer", prompt_summary="draft note", response_size_bytes=2500),
            _entry(subagent_type="reviewer", prompt_summary="review", outcome="pass"),
        ],
        review_records=[ReviewRecord(artifact_path="notes/x.md", record_path="reviews/r-1.json")],
        munin_update={"open_thread": "Continue the note tomorrow."},
    )
    assert HuginResultEnvelope.model_validate_json(env.model_dump_json()) == env


def test_extra_keys_are_ignored() -> None:
    env = HuginResultEnvelope.model_validate({"relay": "ok", "extra_field": {"id": "ignored"}})
    assert "extra_field" not in env.model_dump()
