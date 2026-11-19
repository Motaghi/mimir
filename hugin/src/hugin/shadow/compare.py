"""Compare the orchestrator's decision with the router's.

The orchestrator's own decision for a turn is derived from the delegations
it made during that turn. The caller reads them from wherever it logs
them and passes them in as :class:`DelegationEvent` records; this module
reads no file.

* no top-level delegation in the window  -> ``branch:1:none``
* exactly one                            -> ``branch:2:<name>``
* two or more                           -> ``branch:4:multiple``

Only branches 1, 2 and 4 can be inferred, on either side, because the
branch is read from the number of delegations. A sequential handoff (3)
with two delegations and a parallel fan-out (4) with two delegations both
become ``branch:4:multiple``; a handoff to a background layer (5) and a
clarifying question (6) make no delegation and become ``branch:1:none``.
The router's side goes through
:func:`hugin.router.decision.decision_from_envelope`, which has the same
limit, because the envelope carries no branch field. So a branch-level
comparison (:func:`branches_match`) can only see disagreements among
branches 1, 2 and 4; sequential against parallel work with the same
number of delegations is invisible.

A failed replay (``branch:1:malformed``) is not a routing decision. It
never counts as agreement in :func:`branches_match`, and
:func:`branch_counts` reports failures separately.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from hugin.router.decision import MALFORMED_DECISION, safe_name
from hugin.shadow.recorder import (
    ZERO_INPUT_KIND,
    LogModeError,
    ShadowRecord,
    append_private_line,
)
from hugin.shadow.replay import ReplayResult
from hugin.shadow.staging import TurnStagingRecord

SUMMARY_MAX_LEN = 200


@dataclass(frozen=True)
class DelegationEvent:
    """One delegation the orchestrator made.

    ``origin`` names who delegated (``"mimir"`` for the orchestrator,
    ``"hugin"`` for the router); ``depth`` is 0 for a top-level call.
    """

    timestamp: datetime
    session_id: str
    subagent_type: str
    origin: str = "mimir"
    depth: int = 0


def parse_iso(ts: str) -> datetime | None:
    """Parse an ISO 8601 UTC string such as ``2026-05-07T14:30:00Z``."""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def derive_mimir_decision(
    events: Iterable[DelegationEvent],
    *,
    session_id: str,
    since: datetime,
    until: datetime,
) -> str:
    """The orchestrator's decision string for one turn.

    Counts top-level delegations by the orchestrator in ``session_id``
    whose timestamp falls in ``[since, until]``, both ends inclusive.
    """
    if since > until:
        raise ValueError(f"since ({since}) must be <= until ({until})")

    matches = [
        e
        for e in events
        if e.session_id == session_id
        and e.origin == "mimir"
        and e.depth == 0
        and since <= e.timestamp <= until
    ]
    if not matches:
        return "branch:1:none"
    if len(matches) == 1:
        return f"branch:2:{safe_name(matches[0].subagent_type)}"
    return "branch:4:multiple"


def decisions_match(mimir_decision: str, hugin_decision: str) -> bool:
    """Label-level match: branch and specialist name both equal."""
    return mimir_decision == hugin_decision


def branch_of(decision: str) -> str:
    """The branch number of a decision string, as text."""
    return decision.split(":", 2)[1]


def is_failed(decision: str) -> bool:
    """True for a replay that produced no usable decision."""
    return decision == MALFORMED_DECISION


def branches_match(mimir_decision: str, hugin_decision: str) -> bool:
    """Branch-level match: only the routing branch has to agree.

    A failed decision on either side is never a match.
    """
    if is_failed(mimir_decision) or is_failed(hugin_decision):
        return False
    return branch_of(mimir_decision) == branch_of(hugin_decision)


def branch_counts(pairs: Iterable[tuple[str, str]]) -> dict[str, int]:
    """Branch-level counts over ``(mimir_decision, hugin_decision)`` pairs.

    Failed replays are counted in ``failed`` and left out of the
    comparison, so ``compared + failed == total`` and
    ``branch_mismatched`` counts only real branch disagreements.
    """
    total = failed = mismatched = 0
    for mimir_decision, hugin_decision in pairs:
        total += 1
        if is_failed(mimir_decision) or is_failed(hugin_decision):
            failed += 1
        elif not branches_match(mimir_decision, hugin_decision):
            mismatched += 1
    return {"total": total, "failed": failed, "compared": total - failed, "branch_mismatched": mismatched}


def compose_shadow_record(
    record: TurnStagingRecord,
    result: ReplayResult,
    *,
    timestamp: str,
) -> ShadowRecord:
    """Turn a staged turn and its replay into one shadow record."""
    return ShadowRecord(
        timestamp=timestamp,
        session_id=record.session_id,
        turn_id=record.turn_id,
        user_message_summary=record.user_message[:SUMMARY_MAX_LEN],
        mimir_decision=record.mimir_decision,
        hugin_decision=result.decision,
        matched=decisions_match(record.mimir_decision, result.decision),
        fixture_id=None,
    )


def write_zero_input_marker(
    log_path: Path,
    *,
    today: str,
    timestamp: str,
    sidecar: Path | None = None,
) -> bool:
    """Append one zero-input marker for ``today`` to the log, at most once a day.

    Without the marker, a day when the replay found nothing to replay looks
    the same as a day when the producer was dead. A sidecar file next to
    the log (``.<log file name>.zero-input-marker-date`` unless given)
    remembers the last marked day, so two logs in one directory keep
    separate sidecars.

    The marker is appended first and the sidecar written second, so a
    failure leans toward a duplicate marker (harmless: days are counted
    once) and never toward a missing one. Returns ``True`` when a marker
    line was written and ``False`` when the log could not be written.

    The log is written as in :func:`hugin.shadow.recorder.append_private_line`:
    if its permissions cannot be restricted, :class:`LogModeError` is
    raised and nothing is written. The sidecar is replaced through a
    randomly named temporary file, so a symlink planted at its name is
    never written through.
    """
    side = sidecar if sidecar is not None else log_path.with_name(f".{log_path.name}.zero-input-marker-date")
    try:
        if not side.is_symlink() and side.exists() and side.read_text(encoding="utf-8").strip() == today:
            return False
    except (OSError, ValueError):
        pass

    marker = {
        "kind": ZERO_INPUT_KIND,
        "date": today,
        "timestamp": timestamp,
        "source": "hugin-replay",
        "schema_version": 1,
    }
    try:
        append_private_line(log_path, json.dumps(marker))
    except LogModeError:
        raise
    except OSError:
        return False

    with contextlib.suppress(OSError):
        _replace_private(side, today)
    return True


def _replace_private(path: Path, text: str) -> None:
    """Replace ``path`` with ``text`` through an exclusive 0o600 temporary file."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
