"""The routing decision-string grammar and the mapping from a router reply.

The model picks the routing branch by following its instructions. This
module does not choose a branch. It converts what the model returned into
one comparable string, ``branch:N:specialist``, where N is a routing branch
and specialist is a lowercase kebab-case name such as ``code-reviewer``,
``none`` or ``multiple``. The grammar accepts branches 1 to 6, but the
mapping below can only produce 1, 2 and 4: the envelope has no branch
field, so the branch is inferred from the number of spawns.

Mapping rules for a router reply:

* No ``audit_entries`` (or an empty list)        -> ``branch:1:none``
* Exactly one entry with a usable subagent_type  -> ``branch:2:<subagent_type>``
* Exactly one entry without a usable name        -> ``branch:2:unnamed``
* Two or more entries                            -> ``branch:4:multiple``
* Empty body, non-JSON body or parse failure     -> ``branch:1:malformed``
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal, cast

# "invalid" is a reply that parsed as JSON but failed the envelope schema.
ParseStatus = Literal["ok", "malformed", "empty", "invalid"]

DECISION_RE = re.compile(r"^branch:[1-6]:[a-z][a-z0-9-]*$")
FALLBACK_NAME = "unnamed"
MALFORMED_DECISION = "branch:1:malformed"

_KEBAB_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def is_valid_decision(value: str) -> bool:
    """True when ``value`` follows the ``branch:N:specialist`` grammar."""
    return DECISION_RE.fullmatch(value) is not None


def safe_name(subagent_type: str) -> str:
    """Coerce a subagent_type into the decision-string suffix grammar.

    Lowercase kebab-case names pass through. Pure-letter uppercase names
    are lowercased. Anything else (underscores, symbols, empty after
    stripping) collapses to ``unnamed`` so the decision string still
    passes :data:`DECISION_RE`.
    """
    candidate = subagent_type.strip().lower()
    if _KEBAB_RE.match(candidate):
        return candidate
    return FALLBACK_NAME


def extract_envelope(body: str) -> tuple[dict[str, Any], ParseStatus]:
    """Find a JSON object in a model reply and parse it.

    Tries three strategies in order:

    1. Code-fence extraction (```` ```json ... ``` ```` or ```` ``` ... ``` ````).
    2. The substring from the first ``{`` to the last ``}``.
    3. The whole body.

    Returns ``({}, "empty")`` for an empty or whitespace body and
    ``({}, "malformed")`` when no strategy yields a JSON object.
    """
    if not body or not body.strip():
        return {}, "empty"

    for raw in _candidate_json_blobs(body):
        try:
            parsed: Any = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return cast(dict[str, Any], parsed), "ok"

    return {}, "malformed"


def _candidate_json_blobs(body: str) -> list[str]:
    """Candidate JSON substrings in priority order."""
    candidates: list[str] = []
    candidates.extend(_FENCED_JSON_RE.findall(body))
    first_brace = body.find("{")
    last_brace = body.rfind("}")
    if first_brace >= 0 and last_brace > first_brace:
        candidates.append(body[first_brace : last_brace + 1])
    candidates.append(body)
    return candidates


def decision_from_envelope(envelope: dict[str, Any]) -> str:
    """Map a parsed router envelope to a decision string."""
    entries_obj = envelope.get("audit_entries")
    if not isinstance(entries_obj, list):
        return "branch:1:none"
    entries = cast(list[Any], entries_obj)
    if not entries:
        return "branch:1:none"
    if len(entries) == 1:
        first = entries[0]
        if isinstance(first, dict):
            entry = cast(dict[str, Any], first)
            subagent_type = entry.get("subagent_type")
            if isinstance(subagent_type, str):
                return f"branch:2:{safe_name(subagent_type)}"
        return f"branch:2:{FALLBACK_NAME}"
    return "branch:4:multiple"


def decision_from_reply(body: str) -> tuple[str, dict[str, Any], ParseStatus]:
    """Parse a raw model reply and map it in one step.

    Returns ``(decision, envelope, parse_status)``. A reply that does not
    parse maps to ``branch:1:malformed``.
    """
    envelope, status = extract_envelope(body)
    if status != "ok":
        return MALFORMED_DECISION, envelope, status
    return decision_from_envelope(envelope), envelope, status
