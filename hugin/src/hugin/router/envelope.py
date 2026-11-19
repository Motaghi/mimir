"""The result envelope a router returns to the orchestrator each turn.

The orchestrator records one log line per entry in ``audit_entries``.
``review_records``, ``request_history`` and ``munin_update`` are optional
control fields.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator

NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]
TurnsRangeStr = Annotated[str, StringConstraints(pattern=r"^turns-\d+-through-\d+$")]

# "shadow" is the outcome a router reports when it computed a decision in
# shadow mode and spawned nothing.
Outcome = Literal["success", "failure", "pass", "needs-revision", "fail", "shadow"]

RELAY_MAX_WORDS = 100


def _reject_blank(v: str) -> str:
    if not v.strip():
        raise ValueError("must not be whitespace-only")
    return v


class AuditEntry(BaseModel):
    """One entry in the envelope, one per downstream spawn."""

    subagent_type: NonEmptyStr
    prompt_summary: str = Field(..., min_length=1, max_length=120)
    response_size_bytes: int = Field(..., ge=0)
    outcome: Outcome

    @field_validator("subagent_type", "prompt_summary")
    @classmethod
    def _not_whitespace_only(cls, v: str) -> str:
        return _reject_blank(v)


class RequestHistory(BaseModel):
    """The router asks for a slice of the conversation. One retry per turn."""

    range: TurnsRangeStr  # for example "turns-3-through-7"
    reason: str = Field(..., min_length=1, max_length=80)
    expected_token_size: int = Field(ge=0)

    @field_validator("reason")
    @classmethod
    def _not_whitespace_only(cls, v: str) -> str:
        return _reject_blank(v)


class ReviewRecord(BaseModel):
    """Pointer to a stored review record."""

    artifact_path: NonEmptyStr
    record_path: NonEmptyStr

    @field_validator("artifact_path", "record_path")
    @classmethod
    def _not_whitespace_only(cls, v: str) -> str:
        return _reject_blank(v)


class HuginResultEnvelope(BaseModel):
    """Structured payload the router returns to the orchestrator."""

    relay: str = Field(..., min_length=1, max_length=1000)
    audit_entries: list[AuditEntry] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    review_records: list[ReviewRecord] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    request_history: RequestHistory | None = None
    munin_update: dict[str, Any] | None = None

    @field_validator("relay")
    @classmethod
    def _relay_constraints(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("relay must not be whitespace-only")
        if len(v.split()) > RELAY_MAX_WORDS:
            raise ValueError(f"relay must be at most {RELAY_MAX_WORDS} words")
        return v
