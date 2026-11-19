"""Tests for hugin.shadow.staging."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import pytest

from hugin.router.decision import DECISION_RE
from hugin.shadow.staging import (
    USER_MESSAGE_MAX_LEN,
    TurnStagingRecord,
    list_staging,
    new_turn_id,
    read_staging_file,
    staging_filename,
    write_staging_file,
)

SESSION = "example-session-0001"
TURN = "0f0e0d0c-0b0a-4908-8706-050403020100"


def _valid(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schema_version": 1,
        "session_id": SESSION,
        "turn_id": TURN,
        "captured_at": "2026-05-07T14:32:00Z",
        "user_message": "Summarise the attached release notes",
        "mimir_decision": "branch:1:none",
        "transcript_path": "/tmp/example/transcript.jsonl",
        "transcript_offset": 12345,
    }
    base.update(overrides)
    return base


def test_valid_record_constructs() -> None:
    assert TurnStagingRecord.model_validate(_valid()).session_id == SESSION


@pytest.mark.parametrize(
    "field,bad",
    [
        ("session_id", "short"),
        ("session_id", "../../etc/passwd"),
        ("session_id", "with/slash"),
        ("session_id", "x" * 65),
        ("turn_id", "12345"),
        ("turn_id", TURN.upper()),
        ("captured_at", "2026-05-07 14:32:00"),
        ("captured_at", "2026-05-07T14:32:00+02:00"),
        ("mimir_decision", "branch:7:foo"),
        ("mimir_decision", "branch:1:With_Caps"),
        ("mimir_decision", "branch:1"),
        ("mimir_decision", ""),
        ("transcript_offset", -1),
        ("user_message", "a" * (USER_MESSAGE_MAX_LEN + 1)),
    ],
)
def test_invalid_field_rejected(field: str, bad: object) -> None:
    with pytest.raises(ValueError):
        TurnStagingRecord.model_validate(_valid(**{field: bad}))


@pytest.mark.parametrize(
    "session_id",
    ["11111111-2222-4333-8444-555555555555", "01EXAMPLEULID000001", "ABC123_DEF456", "abc-def-ghi-12345678"],
)
def test_path_safe_session_shapes_accepted(session_id: str) -> None:
    assert TurnStagingRecord.model_validate(_valid(session_id=session_id)).session_id == session_id


def test_user_message_at_cap_accepted() -> None:
    rec = TurnStagingRecord.model_validate(_valid(user_message="a" * USER_MESSAGE_MAX_LEN))
    assert len(rec.user_message) == USER_MESSAGE_MAX_LEN


def test_decision_grammar_is_shared() -> None:
    for value in ("branch:1:none", "branch:2:code-reviewer", "branch:4:multiple", "branch:6:clarify"):
        assert DECISION_RE.match(value)
        assert TurnStagingRecord.model_validate(_valid(mimir_decision=value)).mimir_decision == value


def test_staging_filename_pattern() -> None:
    name = staging_filename(SESSION, TURN)
    assert name == f"{SESSION}-{TURN}.json"
    assert "/" not in name and "\\" not in name


@pytest.mark.parametrize("bad", ["..", "a/b", "a\\b", ""])
def test_staging_filename_rejects_bad_ids(bad: str) -> None:
    with pytest.raises(ValueError):
        staging_filename(bad, TURN)
    with pytest.raises(ValueError):
        staging_filename(SESSION, bad)


def test_write_creates_file_with_private_mode(tmp_path: Path) -> None:
    path = write_staging_file(TurnStagingRecord.model_validate(_valid()), staging_dir=tmp_path)
    assert path.parent == tmp_path
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_write_creates_missing_directory(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "more"
    path = write_staging_file(TurnStagingRecord.model_validate(_valid()), staging_dir=target)
    assert path.parent == target and path.exists()


def test_round_trip(tmp_path: Path) -> None:
    rec = TurnStagingRecord.model_validate(_valid(user_message="hello world"))
    assert read_staging_file(write_staging_file(rec, staging_dir=tmp_path)) == rec


def test_read_rejects_bad_json_and_schema(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    with pytest.raises(ValueError):
        read_staging_file(bad)
    bad.write_text(json.dumps({"session_id": "x", "turn_id": "y"}))
    with pytest.raises(ValueError):
        read_staging_file(bad)


def test_no_temp_file_left_behind(tmp_path: Path) -> None:
    write_staging_file(TurnStagingRecord.model_validate(_valid()), staging_dir=tmp_path)
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []


def test_overwrite_replaces_in_place(tmp_path: Path) -> None:
    a = TurnStagingRecord.model_validate(_valid(user_message="first"))
    b = TurnStagingRecord.model_validate(_valid(user_message="second"))
    assert write_staging_file(a, staging_dir=tmp_path) == write_staging_file(b, staging_dir=tmp_path)
    assert read_staging_file(tmp_path / staging_filename(SESSION, TURN)).user_message == "second"


def test_list_staging_oldest_first_and_capped(tmp_path: Path) -> None:
    paths = []
    for i in range(3):
        rec = TurnStagingRecord.model_validate(_valid(turn_id=new_turn_id()))
        p = write_staging_file(rec, staging_dir=tmp_path)
        stamp = time.time() - 100 + i
        os.utime(p, (stamp, stamp))
        paths.append(p)
    (tmp_path / "notes.txt").write_text("ignored")
    assert list_staging(tmp_path, cap=2) == paths[:2]
    assert list_staging(tmp_path / "absent", cap=5) == []


def test_new_turn_id_is_uuid4_and_unique() -> None:
    tid = new_turn_id()
    assert uuid.UUID(tid).version == 4
    assert TurnStagingRecord.model_validate(_valid(turn_id=tid)).turn_id == tid
    assert len({new_turn_id() for _ in range(20)}) == 20


@pytest.mark.parametrize("bad", ["2026-02-30T14:32:00Z", "2026-04-31T00:00:00Z", "2026-05-07T14:61:00Z"])
def test_captured_at_must_be_a_real_calendar_time(bad: str) -> None:
    with pytest.raises(ValueError):
        TurnStagingRecord.model_validate(_valid(captured_at=bad))


def test_temp_file_is_created_private(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "chmod", lambda *a, **k: None)
    old = os.umask(0o022)
    try:
        path = write_staging_file(TurnStagingRecord.model_validate(_valid()), staging_dir=tmp_path)
    finally:
        os.umask(old)
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_captured_at_with_trailing_newline_rejected() -> None:
    with pytest.raises(ValueError):
        TurnStagingRecord.model_validate(_valid(captured_at="2026-05-07T14:32:00Z\n"))


def test_write_does_not_follow_a_planted_temp_symlink(tmp_path: Path) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    staging = tmp_path / "staging"
    staging.mkdir()
    name = staging_filename(SESSION, TURN)
    (staging / f".{name}.tmp").symlink_to(victim)
    path = write_staging_file(TurnStagingRecord.model_validate(_valid()), staging_dir=staging)
    assert victim.read_text() == "keep me"
    assert read_staging_file(path).session_id == SESSION


@pytest.mark.parametrize(
    "field,bad",
    [("session_id", SESSION + "\n"), ("turn_id", TURN + "\n"), ("mimir_decision", "branch:1:none\n")],
)
def test_trailing_newline_rejected(field: str, bad: str) -> None:
    with pytest.raises(ValueError):
        TurnStagingRecord.model_validate(_valid(**{field: bad}))


def test_staging_filename_rejects_trailing_newline() -> None:
    with pytest.raises(ValueError):
        staging_filename(SESSION + "\n", TURN)
    with pytest.raises(ValueError):
        staging_filename(SESSION, TURN + "\n")
