"""The shadow recorder and the divergence count.

Each orchestrator turn is replayed against the router with no side
effects. Both decisions go into one JSON line, so the divergence between
them can be counted over any window.

The decision strings follow :mod:`hugin.router.decision`. The log path is
always given by the caller; nothing here has a default location. Logs are opened with
mode 0o600 and their permissions are restricted before anything is
written, one record per line.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field, field_validator, model_validator

from hugin.router.decision import DECISION_RE

ISO8601_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
ZERO_INPUT_KIND = "zero-input"


def check_utc_timestamp(v: str, field_name: str) -> str:
    """Accept ISO 8601 UTC that is also a real calendar time (no 30 February)."""
    if not ISO8601_UTC_RE.fullmatch(v):
        raise ValueError(f"{field_name} must be ISO 8601 UTC (e.g. '2026-04-27T14:32:00Z')")
    try:
        datetime.strptime(v[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError as exc:
        raise ValueError(f"{field_name} is not a real calendar time: {v!r}") from exc
    return v


class ShadowRecord(BaseModel):
    """One shadow turn: the orchestrator's decision next to the router's."""

    timestamp: str
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    user_message_summary: str = Field(min_length=1, max_length=200)
    mimir_decision: str
    hugin_decision: str
    matched: bool
    fixture_id: str | None = None
    schema_version: int = 1

    @field_validator("timestamp")
    @classmethod
    def _iso8601_utc(cls, v: str) -> str:
        return check_utc_timestamp(v, "timestamp")

    @field_validator("mimir_decision", "hugin_decision")
    @classmethod
    def _validate_decision(cls, v: str) -> str:
        if not DECISION_RE.fullmatch(v):
            raise ValueError(
                "decision must match 'branch:N:specialist' where N in 1-6 "
                "and specialist is lowercase kebab-case"
            )
        return v

    @model_validator(mode="after")
    def _matched_agrees(self) -> ShadowRecord:
        if self.matched != (self.mimir_decision == self.hugin_decision):
            raise ValueError("matched must be true exactly when the two decisions are equal")
        return self


class LogModeError(PermissionError):
    """A log's permissions could not be restricted, so nothing was written."""


def append_private_line(log_path: Path, line: str) -> None:
    """Append ``line`` and a newline to a log readable only by its owner.

    The file is created with mode 0o600, and the open descriptor is set to
    0o600 before writing, which also tightens an existing log that was
    more open. A failure to set the mode raises :class:`LogModeError`, and
    nothing is written. The descriptor is closed on every path.

    File modes are a POSIX feature. Where ``os.fchmod`` does not exist
    (Windows), the mode step is skipped and the log gets the platform's
    default permissions.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        if hasattr(os, "fchmod"):
            try:
                os.fchmod(fd, 0o600)
            except OSError as exc:
                raise LogModeError(f"cannot restrict permissions of {log_path}: {exc}") from exc
        f = os.fdopen(fd, "a", encoding="utf-8")
    except BaseException:
        os.close(fd)
        raise
    with f:
        f.write(line + "\n")


def record_turn(record: ShadowRecord, *, log_path: Path) -> None:
    """Append one record to the log at ``log_path``.

    The summary must already be scrubbed by the caller; the recorder does
    not scrub. A single short write per line keeps the append atomic on
    POSIX. See :func:`append_private_line` for the file mode.
    """
    append_private_line(log_path, record.model_dump_json())


def read_records(log_path: Path) -> list[ShadowRecord]:
    """Every valid shadow record in the log, in file order.

    Malformed lines, schema violations and marker lines are skipped. A
    missing log gives an empty list.
    """
    if not log_path.exists():
        return []
    out: list[ShadowRecord] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(ShadowRecord.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValueError):
            continue
    return out


def divergence_pct(
    *,
    since: datetime,
    until: datetime,
    log_path: Path,
) -> tuple[float, int, int]:
    """``(mismatched / total) * 100`` over the inclusive window.

    Returns ``(pct, total, mismatched)``. An empty or missing log gives
    ``(0.0, 0, 0)``. A turn counts as mismatched when its two decision
    strings differ. Lines whose ``matched`` flag contradicts the strings,
    and zero-input marker lines, fail record validation and never count; :func:`zero_input_days` reports them separately.
    """
    total = 0
    mismatched = 0
    for record in read_records(log_path):
        when = datetime.fromisoformat(record.timestamp.replace("Z", "+00:00"))
        if since <= when <= until:
            total += 1
            if record.mimir_decision != record.hugin_decision:
                mismatched += 1
    if total == 0:
        return 0.0, 0, 0
    return (mismatched / total) * 100, total, mismatched


def latest_turn_date(log_path: Path) -> str | None:
    """Date part (``YYYY-MM-DD``) of the newest shadow record, or ``None``."""
    stamps = [r.timestamp for r in read_records(log_path)]
    if not stamps:
        return None
    return max(stamps)[:10]


def zero_input_days(
    *,
    since: datetime,
    until: datetime,
    log_path: Path,
) -> int:
    """Count distinct UTC dates that carry a zero-input marker.

    A marker line (``kind == "zero-input"`` with a day-level ``date``) is
    written when a replay run finds nothing to replay, so an idle producer
    can be told apart from a dead one. Dates compare by calendar day,
    inclusive of the days of ``since`` and ``until``: this is wider than
    the instant window of :func:`divergence_pct` on purpose, since markers
    are day-level facts. Dates are counted after parsing, so two spellings
    of one day (``2026-04-21`` and ``2026-4-21``) count once.
    """
    if not log_path.exists():
        return 0

    since_date = since.date()
    until_date = until.date()
    dates: set[date] = set()
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            parsed: Any = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        data = cast("dict[str, Any]", parsed)
        if data.get("kind") != ZERO_INPUT_KIND:
            continue
        date_str = data.get("date")
        if not isinstance(date_str, str):
            continue
        try:
            day = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if since_date <= day <= until_date:
            dates.add(day)
    return len(dates)
