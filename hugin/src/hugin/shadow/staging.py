"""Per-turn staging records for the shadow-replay loop.

At the end of each non-trivial orchestrator turn the host captures one
:class:`TurnStagingRecord` into ``<staging_dir>/<session>-<turn>.json``. A
replay job drains that directory, replays each turn against the router and
appends a shadow record to the log.

This module owns the schema and the read and write helpers only. It does
not scrub the message (the caller does that first) and it does not call
the router (:mod:`hugin.shadow.replay` does). The staging directory is
always given by the caller.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from contextlib import suppress
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from hugin.router.decision import DECISION_RE
from hugin.shadow.recorder import check_utc_timestamp

UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
# A session id is path-safe: letters, digits, dash and underscore, 8 to 64
# characters. UUIDs and ULID-like ids both pass, and neither can hold a
# separator, a leading dot or "..".
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

USER_MESSAGE_MAX_LEN = 500


class TurnStagingRecord(BaseModel):
    """One staged turn waiting for replay."""

    schema_version: int = 1
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    captured_at: str
    user_message: str = Field(min_length=1, max_length=USER_MESSAGE_MAX_LEN)
    mimir_decision: str
    transcript_path: str = Field(min_length=1)
    transcript_offset: int = Field(ge=0)

    @field_validator("session_id")
    @classmethod
    def _session_id_path_safe(cls, v: str) -> str:
        if not SESSION_ID_RE.fullmatch(v):
            raise ValueError(
                "session_id must be path-safe (letters, digits, dash, underscore; "
                "8 to 64 characters)"
            )
        return v

    @field_validator("turn_id")
    @classmethod
    def _turn_id_uuid4(cls, v: str) -> str:
        if not UUID4_RE.fullmatch(v):
            raise ValueError("turn_id must be a UUID4 (lowercase hex)")
        return v

    @field_validator("captured_at")
    @classmethod
    def _captured_at_iso(cls, v: str) -> str:
        return check_utc_timestamp(v, "captured_at")

    @field_validator("mimir_decision")
    @classmethod
    def _decision_shape(cls, v: str) -> str:
        if not DECISION_RE.fullmatch(v):
            raise ValueError(
                "mimir_decision must match 'branch:N:specialist' where N in 1-6 "
                "and specialist is lowercase kebab-case"
            )
        return v


def staging_filename(session_id: str, turn_id: str) -> str:
    """Compose ``<session>-<turn>.json``; raise ``ValueError`` on a bad id."""
    if not SESSION_ID_RE.fullmatch(session_id):
        raise ValueError(f"invalid session_id: {session_id!r}")
    if not UUID4_RE.fullmatch(turn_id):
        raise ValueError(f"invalid turn_id: {turn_id!r}")
    return f"{session_id}-{turn_id}.json"


def write_staging_file(record: TurnStagingRecord, *, staging_dir: Path) -> Path:
    """Write the record as JSON into ``staging_dir`` and return its path.

    The directory is created if missing. The record is written to a
    sibling temporary file with a random name, created exclusively with
    mode 0o600 and without following symlinks (``tempfile.mkstemp``), then
    moved into place with ``os.replace``. A link planted in the directory
    is never written through, and two writers never share a temporary file.
    """
    staging_dir.mkdir(parents=True, exist_ok=True)
    name = staging_filename(record.session_id, record.turn_id)
    path = staging_dir / name
    fd, tmp = tempfile.mkstemp(dir=staging_dir, prefix=f".{name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(record.model_dump_json())
        os.replace(tmp, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp)
        raise
    return path


def read_staging_file(path: Path) -> TurnStagingRecord:
    """Parse a staging record; ``ValueError`` on bad JSON or schema."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return TurnStagingRecord.model_validate(data)


def list_staging(staging_dir: Path, *, cap: int) -> list[Path]:
    """Up to ``cap`` staged files, oldest first by modification time."""
    if not staging_dir.exists():
        return []
    candidates = [p for p in staging_dir.glob("*.json") if p.is_file()]
    candidates.sort(key=lambda p: p.stat().st_mtime)
    return candidates[:cap]


def new_turn_id() -> str:
    """A fresh UUID4 turn id (lowercase hex)."""
    return str(uuid.uuid4())
