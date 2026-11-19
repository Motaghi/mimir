"""Tests for hugin.shadow.recorder."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from hugin.shadow.recorder import (
    ZERO_INPUT_KIND,
    ShadowRecord,
    divergence_pct,
    latest_turn_date,
    read_records,
    record_turn,
    zero_input_days,
)

BASE = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make(
    *,
    matched: bool = True,
    when: datetime = BASE,
    mimir: str = "branch:2:code-reviewer",
    turn_id: str = "example-turn-1",
) -> ShadowRecord:
    return ShadowRecord(
        timestamp=_ts(when),
        session_id="example-session",
        turn_id=turn_id,
        user_message_summary="example user message",
        mimir_decision=mimir,
        hugin_decision=mimir if matched else "branch:1:none",
        matched=matched,
    )


def _write_marker(log: Path, *, date: str) -> None:
    marker = {"kind": ZERO_INPUT_KIND, "date": date, "timestamp": f"{date}T00:00:00Z"}
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(marker) + "\n")


def test_record_appends_jsonl_with_private_mode(tmp_path: Path) -> None:
    log = tmp_path / "logs" / "turns.jsonl"
    rec = _make()
    record_turn(rec, log_path=log)
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert ShadowRecord.model_validate(json.loads(lines[0])) == rec
    assert os.stat(log).st_mode & 0o777 == 0o600


def test_divergence_zero_when_all_match(tmp_path: Path) -> None:
    log = tmp_path / "turns.jsonl"
    for i in range(10):
        record_turn(_make(when=BASE + timedelta(minutes=i), turn_id=f"t-{i}"), log_path=log)
    got = divergence_pct(since=BASE - timedelta(days=1), until=BASE + timedelta(days=1), log_path=log)
    assert got == (0.0, 10, 0)


def test_divergence_half(tmp_path: Path) -> None:
    log = tmp_path / "turns.jsonl"
    for i in range(10):
        record_turn(
            _make(matched=i < 5, when=BASE + timedelta(minutes=i), turn_id=f"t-{i}"), log_path=log
        )
    got = divergence_pct(since=BASE - timedelta(days=1), until=BASE + timedelta(days=1), log_path=log)
    assert got == (50.0, 10, 5)


def test_window_filters_by_timestamp(tmp_path: Path) -> None:
    log = tmp_path / "turns.jsonl"
    for i in range(5):
        record_turn(_make(matched=False, when=BASE + timedelta(days=i), turn_id=f"t-{i}"), log_path=log)
    since = BASE + timedelta(days=1)
    until = BASE + timedelta(days=3, hours=1)
    assert divergence_pct(since=since, until=until, log_path=log) == (100.0, 3, 3)


def test_missing_or_empty_log_is_zero(tmp_path: Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.touch()
    for log in (tmp_path / "absent.jsonl", empty):
        assert divergence_pct(since=BASE, until=BASE, log_path=log) == (0.0, 0, 0)
        assert read_records(log) == []
        assert latest_turn_date(log) is None


def test_bad_lines_are_skipped(tmp_path: Path) -> None:
    log = tmp_path / "turns.jsonl"
    record_turn(_make(), log_path=log)
    with log.open("a", encoding="utf-8") as f:
        f.write("{not json\n\n")
        f.write(json.dumps({"timestamp": "x"}) + "\n")
    assert len(read_records(log)) == 1


def test_summary_passes_through_as_given(tmp_path: Path) -> None:
    log = tmp_path / "turns.jsonl"
    rec = _make().model_copy(update={"user_message_summary": "check the fee table [REDACTED:key]"})
    record_turn(rec, log_path=log)
    assert json.loads(log.read_text(encoding="utf-8"))["user_message_summary"].endswith("[REDACTED:key]")


def test_latest_turn_date_ignores_markers(tmp_path: Path) -> None:
    log = tmp_path / "turns.jsonl"
    record_turn(_make(when=datetime(2026, 5, 2, 9, 0, tzinfo=UTC)), log_path=log)
    record_turn(_make(when=datetime(2026, 5, 9, 23, 59, tzinfo=UTC), turn_id="t-2"), log_path=log)
    _write_marker(log, date="2026-07-01")
    assert latest_turn_date(log) == "2026-05-09"


def test_zero_input_days_counts_distinct_dates(tmp_path: Path) -> None:
    log = tmp_path / "turns.jsonl"
    _write_marker(log, date="2026-04-21")
    _write_marker(log, date="2026-04-21")
    _write_marker(log, date="2026-04-22")
    since = datetime(2026, 4, 20, tzinfo=UTC)
    until = datetime(2026, 4, 23, tzinfo=UTC)
    assert zero_input_days(since=since, until=until, log_path=log) == 2


def test_zero_input_days_ignores_out_of_window_and_bad_lines(tmp_path: Path) -> None:
    log = tmp_path / "turns.jsonl"
    _write_marker(log, date="2026-04-01")
    _write_marker(log, date="2026-04-21")
    with log.open("a", encoding="utf-8") as f:
        f.write("{not json\n")
        f.write("[1]\n")
        f.write(json.dumps({"kind": ZERO_INPUT_KIND}) + "\n")
        f.write(json.dumps({"kind": ZERO_INPUT_KIND, "date": "21 April"}) + "\n")
    since = datetime(2026, 4, 20, tzinfo=UTC)
    until = datetime(2026, 4, 23, tzinfo=UTC)
    assert zero_input_days(since=since, until=until, log_path=log) == 1


def test_zero_input_days_compares_calendar_days(tmp_path: Path) -> None:
    log = tmp_path / "turns.jsonl"
    _write_marker(log, date="2026-04-21")
    since = datetime(2026, 4, 21, 18, 0, tzinfo=UTC)
    until = datetime(2026, 4, 23, tzinfo=UTC)
    assert zero_input_days(since=since, until=until, log_path=log) == 1


def test_zero_input_days_missing_log(tmp_path: Path) -> None:
    assert zero_input_days(since=BASE, until=BASE, log_path=tmp_path / "absent.jsonl") == 0


def test_markers_do_not_change_divergence(tmp_path: Path) -> None:
    log = tmp_path / "turns.jsonl"
    for i in range(4):
        record_turn(_make(matched=i < 2, when=BASE + timedelta(minutes=i), turn_id=f"t-{i}"), log_path=log)
    _write_marker(log, date=BASE.date().isoformat())
    got = divergence_pct(since=BASE - timedelta(days=1), until=BASE + timedelta(days=1), log_path=log)
    assert got == (50.0, 4, 2)


@pytest.mark.parametrize("bad", ["branch:7:foo", "branch:0:foo", "branch:1:Foo"])
def test_schema_rejects_bad_decision(bad: str) -> None:
    with pytest.raises(ValidationError):
        _make(mimir=bad)


def test_schema_rejects_non_utc_timestamp() -> None:
    with pytest.raises(ValidationError):
        _make().model_validate({**_make().model_dump(), "timestamp": "2026-04-27T14:32:00+02:00"})


def test_matched_must_agree_with_decisions() -> None:
    with pytest.raises(ValidationError, match="matched"):
        ShadowRecord(
            timestamp="2026-04-27T14:32:00Z",
            session_id="example-session",
            turn_id="example-turn-9",
            user_message_summary="x",
            mimir_decision="branch:2:writer",
            hugin_decision="branch:1:none",
            matched=True,
        )
    with pytest.raises(ValidationError, match="matched"):
        _make(matched=True).model_validate({**_make().model_dump(), "matched": False})


def test_divergence_ignores_contradictory_lines(tmp_path: Path) -> None:
    log = tmp_path / "turns.jsonl"
    record_turn(_make(matched=False), log_path=log)
    bad = {**_make().model_dump(), "turn_id": "t-bad", "matched": False}
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(bad) + "\n")
    assert divergence_pct(since=BASE, until=BASE, log_path=log) == (100.0, 1, 1)


@pytest.mark.parametrize("bad", ["2026-02-30T00:00:00Z", "2026-13-01T00:00:00Z", "2026-04-27T25:00:00Z"])
def test_schema_rejects_impossible_calendar_time(bad: str) -> None:
    with pytest.raises(ValidationError):
        ShadowRecord.model_validate({**_make().model_dump(), "timestamp": bad})


def test_timestamp_with_trailing_newline_rejected() -> None:
    with pytest.raises(ValidationError):
        ShadowRecord.model_validate({**_make().model_dump(), "timestamp": "2026-04-27T14:32:00Z\n"})


def test_new_log_is_private_from_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "chmod", lambda *a, **k: None)
    monkeypatch.setattr(os, "fchmod", lambda *a, **k: None)
    log = tmp_path / "turns.jsonl"
    old = os.umask(0o022)
    try:
        record_turn(_make(), log_path=log)
    finally:
        os.umask(old)
    assert os.stat(log).st_mode & 0o777 == 0o600


def test_existing_permissive_log_is_tightened(tmp_path: Path) -> None:
    log = tmp_path / "turns.jsonl"
    log.touch()
    os.chmod(log, 0o644)
    record_turn(_make(), log_path=log)
    assert os.stat(log).st_mode & 0o777 == 0o600


def test_permission_failure_is_raised_before_writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*a: object, **k: object) -> None:
        raise PermissionError("simulated")

    monkeypatch.setattr(os, "chmod", refuse)
    monkeypatch.setattr(os, "fchmod", refuse)
    log = tmp_path / "turns.jsonl"
    with pytest.raises(PermissionError):
        record_turn(_make(), log_path=log)
    assert not log.exists() or log.read_text(encoding="utf-8") == ""


def test_zero_input_days_normalises_date_spellings(tmp_path: Path) -> None:
    log = tmp_path / "turns.jsonl"
    _write_marker(log, date="2026-04-21")
    _write_marker(log, date="2026-4-21")
    since = datetime(2026, 4, 20, tzinfo=UTC)
    until = datetime(2026, 4, 23, tzinfo=UTC)
    assert zero_input_days(since=since, until=until, log_path=log) == 1


def test_decision_with_trailing_newline_rejected() -> None:
    bad = "branch:2:code-reviewer\n"
    data = {**_make().model_dump(), "mimir_decision": bad, "hugin_decision": bad}
    with pytest.raises(ValidationError):
        ShadowRecord.model_validate(data)


def test_platform_without_fchmod_still_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(os, "fchmod")
    log = tmp_path / "turns.jsonl"
    record_turn(_make(), log_path=log)
    assert len(read_records(log)) == 1


def test_descriptor_closed_when_mode_cannot_be_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[int] = []
    real_close = os.close

    def refuse(*a: object, **k: object) -> None:
        raise PermissionError("simulated")

    def tracking_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(os, "fchmod", refuse)
    monkeypatch.setattr(os, "close", tracking_close)
    with pytest.raises(PermissionError):
        record_turn(_make(), log_path=tmp_path / "turns.jsonl")
    assert len(closed) == 1
