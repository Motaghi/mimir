"""The provider-choice decision tree.

Pure functions: given a user message, the chosen specialist's profile and
the state directory, compute which provider chain should run the
specialist. Nothing here calls a provider; the caller spawns and logs.

Five steps:

1. Detect routing intents in the message with
   :func:`hugin.router.sensitivity.classify`.
2. Read the session flag: a file named ``local-only`` in ``state_dir``.
3. Effective mode: local-only when the session flag is set or the message
   asks for it.
4. Eligibility: when the turn must run locally (local-only mode, or a
   ``local-only`` provider preference) a specialist that is not
   local-capable is refused; ``degraded`` warns and proceeds in local-only
   mode.
5. Pick the chain:

   - local mode or ``provider_preference == "local-only"`` -> ``ollama``
   - ``codex-only``  -> ``codex_with_claude_fallback``
   - ``claude-only`` -> ``claude_pinned``
   - ``gemini-only`` -> ``gemini_pinned``
   - otherwise ``any_chain``, or ``codex_with_claude_fallback`` when the
     message asks for the second provider.

The ``parallel`` intent (for example "second opinion") is detected and
recorded in :class:`AuditMeta`, but :func:`route` does not act on it: the
choice for one spawn is the same with or without it. This is inherited
from the code this package was extracted from, where running a second
provider was left to the caller as a second :func:`route` call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from hugin.router.sensitivity import (
    EXAMPLE_PHRASES,
    IntentResult,
    PhraseTable,
    classify,
)

ProviderChoice = Literal[
    "ollama",
    "codex_with_claude_fallback",
    "claude_pinned",
    "gemini_pinned",
    "any_chain",
    "refused",
]

LocalCapable = bool | Literal["degraded"]
Mode = Literal["normal", "local_only"]

LOCAL_ONLY_FLAG = "local-only"


class SpecialistProfile(Protocol):
    """What the router needs to know about a specialist."""

    @property
    def name(self) -> str: ...

    @property
    def local_capable(self) -> LocalCapable: ...

    @property
    def local_capable_reason(self) -> str | None: ...

    @property
    def provider_preference(self) -> str: ...


@dataclass(frozen=True)
class Specialist:
    """A plain :class:`SpecialistProfile`.

    The defaults are the conservative ones: a specialist that declares
    nothing is not local-capable and has no provider preference.
    """

    name: str
    local_capable: LocalCapable = False
    local_capable_reason: str | None = "no local capability declared"
    provider_preference: str = "any"


@dataclass(frozen=True)
class AuditMeta:
    """What the router decided and why, for the caller's audit record."""

    specialist_name: str
    provider_choice: ProviderChoice
    mode: Mode
    intent_phrases: tuple[tuple[str, str], ...] = ()
    explicit_prefixes: frozenset[str] = field(default_factory=lambda: frozenset[str]())
    frontmatter_preference: str = "any"
    local_capable: str = "true"


@dataclass(frozen=True)
class RoutingDecision:
    """Result of the decision tree."""

    decision: ProviderChoice
    mode: Mode
    block_reason: str | None = None
    warnings: tuple[str, ...] = ()
    audit: AuditMeta | None = None


@dataclass(frozen=True)
class SessionToggle:
    """A session-level local-mode toggle found in the message.

    The caller acts on it before routing the same turn. When both fields
    are true the message contradicts itself and the caller decides.
    """

    turn_on: bool = False
    turn_off: bool = False


def detect_session_toggle(message: str, phrases: PhraseTable = EXAMPLE_PHRASES) -> SessionToggle:
    """Detect whether the message starts or ends a local-only session."""
    r = classify(message, phrases)
    return SessionToggle(
        turn_on="session_local_on" in r.intents,
        turn_off="session_local_off" in r.intents,
    )


def local_only_session_active(state_dir: Path) -> bool:
    """True when ``state_dir`` holds the ``local-only`` flag file."""
    return (state_dir / LOCAL_ONLY_FLAG).exists()


def route(
    *,
    message: str,
    specialist: SpecialistProfile,
    state_dir: Path,
    phrases: PhraseTable = EXAMPLE_PHRASES,
) -> RoutingDecision:
    """Run the decision tree and return a :class:`RoutingDecision`."""
    intent_result = classify(message, phrases)

    session_local_only = local_only_session_active(state_dir)
    intent_local_only = "local_only_escalate" in intent_result.intents
    mode_local_only = session_local_only or intent_local_only
    mode: Mode = "local_only" if mode_local_only else "normal"

    local_capable = specialist.local_capable
    reason = specialist.local_capable_reason
    preference = specialist.provider_preference

    needs_local = mode_local_only or preference == "local-only"
    if needs_local and local_capable is False:
        return RoutingDecision(
            decision="refused",
            mode=mode,
            block_reason=(
                f"specialist {specialist.name!r} cannot run on a local "
                f"provider: {reason or 'no reason on record'}"
            ),
            audit=_audit(
                specialist=specialist,
                provider_choice="refused",
                mode=mode,
                intent_result=intent_result,
            ),
        )

    warnings: list[str] = []
    if mode_local_only and local_capable == "degraded":
        warnings.append(
            f"specialist {specialist.name!r} is local_capable: degraded "
            f"({reason or 'see profile'}); proceeding with the local provider "
            "but quality may be below the cloud baseline."
        )

    decision = select_provider(
        mode_local_only=mode_local_only,
        provider_preference=preference,
        codex_intent="codex_prefer" in intent_result.intents,
    )

    return RoutingDecision(
        decision=decision,
        mode=mode,
        warnings=tuple(warnings),
        audit=_audit(
            specialist=specialist,
            provider_choice=decision,
            mode=mode,
            intent_result=intent_result,
        ),
    )


def select_provider(
    *,
    mode_local_only: bool,
    provider_preference: str,
    codex_intent: bool,
) -> ProviderChoice:
    """Pick a provider chain from mode, preference and intent.

    A per-message request for the second provider upgrades the ``any``
    preference only; it never overrides a pinned preference.
    """
    if mode_local_only or provider_preference == "local-only":
        return "ollama"
    if provider_preference == "codex-only":
        return "codex_with_claude_fallback"
    if provider_preference == "claude-only":
        return "claude_pinned"
    if provider_preference == "gemini-only":
        return "gemini_pinned"
    if codex_intent:
        return "codex_with_claude_fallback"
    return "any_chain"


def _audit(
    *,
    specialist: SpecialistProfile,
    provider_choice: ProviderChoice,
    mode: Mode,
    intent_result: IntentResult,
) -> AuditMeta:
    return AuditMeta(
        specialist_name=specialist.name,
        provider_choice=provider_choice,
        mode=mode,
        intent_phrases=tuple(
            (str(intent), phrase) for intent, phrase in intent_result.matched_phrases
        ),
        explicit_prefixes=intent_result.explicit_prefixes,
        frontmatter_preference=specialist.provider_preference,
        local_capable=str(specialist.local_capable).lower(),
    )
