"""The sensitivity classifier that runs before a specialist is spawned.

It reads the user's message for phrases that should change where the work
runs, for example "keep this on this machine" or "get a second opinion".
Matching is deterministic, case-insensitive and word-bounded. There is no
model judgement and no embedding similarity: a false negative here can send
sensitive material to a cloud provider, so the matcher must be auditable by
reading it.

This is a routing hint, not a data-loss control. It does not look at what
the material is; it only reads phrases in which the user asks for local
handling or another provider. Sensitive content sent without such a phrase
is not caught.

Five intents:

* ``local_only_escalate``: run this turn on a local model only.
* ``parallel``: run on two providers and show both answers.
* ``codex_prefer``: prefer the second cloud provider for this turn.
* ``session_local_on``: switch the whole session to local-only mode.
* ``session_local_off``: switch the session back to normal.

:data:`EXAMPLE_PHRASES` is a short, neutral example list. It is a starting
point, not a tuned list. Pass your own table to :func:`classify` to match
how you and your users actually write.

Explicit prefixes always work as a fallback, whatever the phrase table:
``[local]``, ``[parallel]`` and ``[codex]`` set the matching intent;
``[local-paste-ok]`` is recorded but sets no intent. Every prefix at the
start of the message is read, in any order, so ``[parallel] [local]``
sets both intents and the local restriction still applies.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

Intent = Literal[
    "local_only_escalate",
    "parallel",
    "codex_prefer",
    "session_local_on",
    "session_local_off",
]

INTENTS: tuple[Intent, ...] = (
    "local_only_escalate",
    "parallel",
    "codex_prefer",
    "session_local_on",
    "session_local_off",
)

PhraseTable = Mapping[Intent, Sequence[str]]

# Example phrases only. Each entry is a regex fragment; the classifier wraps
# it in a group inside word boundaries, so an alternation such as
# "private|secret" stays whole words, and joins the entries of one intent
# with "|".
EXAMPLE_PHRASES: dict[Intent, tuple[str, ...]] = {
    "local_only_escalate": (
        r"stay local",
        r"keep (?:this|it) (?:private|local)",
        r"do not send (?:this )?to (?:the )?cloud",
        r"don't send (?:this )?to (?:the )?cloud",
        r"local only",
    ),
    "parallel": (
        r"second opinion",
        r"have another model (?:check|review|look at)",
        r"run (?:this )?on both",
    ),
    "codex_prefer": (
        r"use codex",
        r"send (?:this )?to codex",
        r"ask codex",
    ),
    "session_local_on": (
        r"switch to local mode",
        r"local mode on",
        r"start (?:a )?local(?:[- ]only)? session",
    ),
    "session_local_off": (
        r"back to normal(?: mode)?",
        r"local mode off",
        r"end (?:the )?local session",
    ),
}

_PREFIX_RE = re.compile(
    r"\s*\[(local|parallel|codex|local-paste-ok)\]\s*",
    re.IGNORECASE,
)

_PREFIX_INTENT: dict[str, Intent] = {
    "local": "local_only_escalate",
    "parallel": "parallel",
    "codex": "codex_prefer",
}


@dataclass(frozen=True)
class IntentResult:
    """Intents detected in one user message.

    Several intents can fire at once. ``matched_phrases`` carries the
    substring that triggered each intent, for the audit record.
    """

    intents: frozenset[Intent] = field(default_factory=lambda: frozenset[Intent]())
    matched_phrases: tuple[tuple[Intent, str], ...] = ()
    explicit_prefixes: frozenset[str] = field(default_factory=lambda: frozenset[str]())


def compile_pattern_list(patterns: Sequence[str]) -> re.Pattern[str] | None:
    """Build one word-bounded, case-insensitive regex; ``None`` if empty.

    Each fragment is grouped before the boundaries are added, so every
    alternative inside a fragment is bounded on both sides.
    """
    if not patterns:
        return None
    bounded = [rf"\b(?:{p})\b" for p in patterns]
    return re.compile("|".join(bounded), re.IGNORECASE)


def classify(message: str, phrases: PhraseTable = EXAMPLE_PHRASES) -> IntentResult:
    """Classify ``message`` against ``phrases``.

    Returns every intent that matched, the substring behind each, and any
    explicit bracket prefix. Empty or whitespace input gives an empty
    result. Intents missing from ``phrases`` simply never match by phrase.
    """
    if not message or not message.strip():
        return IntentResult()

    intents: set[Intent] = set()
    matched: list[tuple[Intent, str]] = []
    prefixes: set[str] = set()

    pos = 0
    while (pre := _PREFIX_RE.match(message, pos)) is not None:
        pos = pre.end()
        prefix = pre.group(1).lower()
        prefixes.add(prefix)
        intent = _PREFIX_INTENT.get(prefix)
        if intent is not None and intent not in intents:
            intents.add(intent)
            matched.append((intent, f"[{prefix}]"))

    for intent in INTENTS:
        regex = compile_pattern_list(phrases.get(intent, ()))
        if regex is None:
            continue
        m = regex.search(message)
        if m:
            intents.add(intent)
            matched.append((intent, m.group(0)))

    return IntentResult(
        intents=frozenset(intents),
        matched_phrases=tuple(matched),
        explicit_prefixes=frozenset(prefixes),
    )


def phrase_categories(phrases: PhraseTable = EXAMPLE_PHRASES) -> dict[str, list[str]]:
    """Return the phrase table as plain lists keyed by intent."""
    return {intent: list(phrases.get(intent, ())) for intent in INTENTS}
