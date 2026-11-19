"""Tests for hugin.shadow.compare."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hugin.router.decision import DECISION_RE
from hugin.shadow.compare import (
    DelegationEvent,
    branch_of,
    branches_match,
    compose_shadow_record,
    decisions_match,
    derive_mimir_decision,
    parse_iso,
    write_zero_input_marker,
)
from hugin.shadow.recorder import ZERO_INPUT_KIND, zero_input_days
from hugin.shadow.replay import ReplayResult
from hugin.shadow.staging import TurnStagingRecord

SID = "example-session-0002"
SINCE = datetime(2026, 5, 7, 14, 0, 0, tzinfo=UTC)
UNTIL = datetime(2026, 5, 7, 15, 0, 0, tzinfo=UTC)


def _ev(ts: str, name: str = "code-reviewer", **kw: object) -> DelegationEvent:
    when = parse_iso(ts)
    assert when is not None
    return DelegationEvent(timestamp=when, session_id=str(kw.pop("session_id", SID)), subagent_type=name, **kw)  # type: ignore[arg-type]


def _derive(events: list[DelegationEvent]) -> str:
    return derive_mimir_decision(events, session_id=SID, since=SINCE, until=UNTIL)


def test_no_events_is_branch_1() -> None:
    assert _derive([]) == "branch:1:none"


def test_one_event_is_branch_2() -> None:
    assert _derive([_ev("2026-05-07T14:30:00Z")]) == "branch:2:code-reviewer"


def test_two_events_is_branch_4() -> None:
    events = [_ev("2026-05-07T14:30:00Z"), _ev("2026-05-07T14:30:05Z", "researcher")]
    assert _derive(events) == "branch:4:multiple"


def test_window_bounds() -> None:
    assert _derive([_ev("2026-05-07T13:59:00Z")]) == "branch:1:none"
    assert _derive([_ev("2026-05-07T15:00:01Z")]) == "branch:1:none"
    edges = [_ev("2026-05-07T14:00:00Z", "researcher"), _ev("2026-05-07T15:00:00Z", "writer")]
    assert _derive(edges) == "branch:4:multiple"


def test_since_after_until_raises() -> None:
    with pytest.raises(ValueError):
        derive_mimir_decision([], session_id=SID, since=SINCE, until=SINCE - timedelta(seconds=1))


def test_other_session_excluded() -> None:
    assert _derive([_ev("2026-05-07T14:30:00Z", session_id="example-other")]) == "branch:1:none"


def test_router_origin_and_nested_depth_excluded() -> None:
    assert _derive([_ev("2026-05-07T14:30:00Z", origin="hugin", depth=1)]) == "branch:1:none"
    assert _derive([_ev("2026-05-07T14:30:00Z", depth=1)]) == "branch:1:none"


def test_name_normalisation() -> None:
    assert _derive([_ev("2026-05-07T14:30:00Z", "WRITER")]) == "branch:2:writer"
    assert _derive([_ev("2026-05-07T14:30:00Z", "Code_Reviewer")]) == "branch:2:unnamed"


def test_output_always_matches_grammar() -> None:
    cases = [
        [],
        [_ev("2026-05-07T14:30:00Z", "researcher")],
        [_ev("2026-05-07T14:30:00Z", "researcher"), _ev("2026-05-07T14:30:05Z", "writer")],
        [_ev("2026-05-07T14:30:00Z", "!@#$%")],
    ]
    for events in cases:
        assert DECISION_RE.match(_derive(events))


def test_parse_iso() -> None:
    assert parse_iso("2026-05-07T14:30:00Z") == datetime(2026, 5, 7, 14, 30, tzinfo=UTC)
    assert parse_iso("not-a-timestamp") is None


def test_label_and_branch_matching() -> None:
    assert decisions_match("branch:2:writer", "branch:2:writer")
    assert not decisions_match("branch:2:writer", "branch:2:researcher")
    assert branches_match("branch:2:writer", "branch:2:researcher")
    assert not branches_match("branch:2:writer", "branch:1:none")
    assert branch_of("branch:4:multiple") == "4"


def _staged(decision: str, message: str = "Summarise the release notes") -> TurnStagingRecord:
    return TurnStagingRecord(
        session_id=SID,
        turn_id="0f0e0d0c-0b0a-4908-8706-0504030201ff",
        captured_at="2026-05-07T14:32:00Z",
        user_message=message,
        mimir_decision=decision,
        transcript_path="/tmp/example/transcript.jsonl",
        transcript_offset=0,
    )


def _result(decision: str) -> ReplayResult:
    return ReplayResult(decision=decision, raw_envelope={}, parse_status="ok", cost_usd=0.0)


def test_compose_shadow_record_matched_and_not() -> None:
    rec = compose_shadow_record(_staged("branch:2:writer"), _result("branch:2:writer"), timestamp="2026-05-07T14:33:00Z")
    assert rec.matched is True
    assert rec.session_id == SID
    rec = compose_shadow_record(_staged("branch:2:writer"), _result("branch:1:none"), timestamp="2026-05-07T14:33:00Z")
    assert rec.matched is False
    assert rec.hugin_decision == "branch:1:none"


def test_compose_shadow_record_truncates_summary() -> None:
    rec = compose_shadow_record(
        _staged("branch:1:none", "a" * 450), _result("branch:1:none"), timestamp="2026-05-07T14:33:00Z"
    )
    assert len(rec.user_message_summary) == 200


def test_zero_input_marker_once_per_day(tmp_path: Path) -> None:
    log = tmp_path / "logs" / "turns.jsonl"
    assert write_zero_input_marker(log, today="2026-05-07", timestamp="2026-05-07T01:00:00Z") is True
    assert write_zero_input_marker(log, today="2026-05-07", timestamp="2026-05-07T02:00:00Z") is False
    assert write_zero_input_marker(log, today="2026-05-08", timestamp="2026-05-08T01:00:00Z") is True
    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert [m["date"] for m in lines] == ["2026-05-07", "2026-05-08"]
    assert all(m["kind"] == ZERO_INPUT_KIND for m in lines)
    days = zero_input_days(since=datetime(2026, 5, 1, tzinfo=UTC), until=datetime(2026, 5, 9, tzinfo=UTC), log_path=log)
    assert days == 2


def test_zero_input_marker_survives_bad_sidecar(tmp_path: Path) -> None:
    log = tmp_path / "turns.jsonl"
    sidecar = tmp_path / "marker-date"
    sidecar.write_bytes(b"\xff\xfe")
    assert write_zero_input_marker(log, today="2026-05-07", timestamp="2026-05-07T01:00:00Z", sidecar=sidecar)
    assert sidecar.read_text(encoding="utf-8") == "2026-05-07"


def test_zero_input_marker_unwritable_log(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert write_zero_input_marker(blocker / "turns.jsonl", today="2026-05-07", timestamp="2026-05-07T01:00:00Z") is False


def test_default_sidecar_is_per_log(tmp_path: Path) -> None:
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    assert write_zero_input_marker(first, today="2026-05-07", timestamp="2026-05-07T01:00:00Z") is True
    assert write_zero_input_marker(second, today="2026-05-07", timestamp="2026-05-07T01:00:00Z") is True
    assert (tmp_path / ".first.jsonl.zero-input-marker-date").read_text(encoding="utf-8") == "2026-05-07"


def test_marker_sidecar_does_not_follow_a_planted_symlink(tmp_path: Path) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    log = tmp_path / "turns.jsonl"
    (tmp_path / ".turns.jsonl.zero-input-marker-date").symlink_to(victim)
    assert write_zero_input_marker(log, today="2026-05-07", timestamp="2026-05-07T01:00:00Z") is True
    assert victim.read_text() == "keep me"
    sidecar = tmp_path / ".turns.jsonl.zero-input-marker-date"
    assert not sidecar.is_symlink()
    assert sidecar.read_text(encoding="utf-8") == "2026-05-07"


def test_marker_tightens_an_existing_permissive_log(tmp_path: Path) -> None:
    import os

    log = tmp_path / "turns.jsonl"
    log.touch()
    os.chmod(log, 0o644)
    write_zero_input_marker(log, today="2026-05-07", timestamp="2026-05-07T01:00:00Z")
    assert os.stat(log).st_mode & 0o777 == 0o600


def test_marker_permission_failure_is_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    def refuse(*a: object, **k: object) -> None:
        raise PermissionError("simulated")

    monkeypatch.setattr(os, "chmod", refuse)
    monkeypatch.setattr(os, "fchmod", refuse)
    log = tmp_path / "turns.jsonl"
    with pytest.raises(PermissionError):
        write_zero_input_marker(log, today="2026-05-07", timestamp="2026-05-07T01:00:00Z")
    assert not log.exists() or log.read_text(encoding="utf-8") == ""


def test_failed_reply_is_never_branch_agreement() -> None:
    assert not branches_match("branch:1:none", "branch:1:malformed")
    assert not branches_match("branch:2:writer", "branch:1:malformed")


def test_branch_counts_report_failures_separately() -> None:
    from hugin.shadow.compare import branch_counts

    pairs = [
        ("branch:1:none", "branch:1:none"),
        ("branch:2:writer", "branch:2:researcher"),
        ("branch:2:writer", "branch:4:multiple"),
        ("branch:1:none", "branch:1:malformed"),
        ("branch:2:writer", "branch:1:malformed"),
    ]
    assert branch_counts(pairs) == {"total": 5, "failed": 2, "compared": 3, "branch_mismatched": 1}
