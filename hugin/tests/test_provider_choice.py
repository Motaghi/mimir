"""Tests for hugin.router.provider_choice."""

from __future__ import annotations

from pathlib import Path

import pytest

from hugin.router.provider_choice import (
    LOCAL_ONLY_FLAG,
    SessionToggle,
    Specialist,
    detect_session_toggle,
    local_only_session_active,
    route,
    select_provider,
)


@pytest.fixture
def normal_dir(tmp_path: Path) -> Path:
    d = tmp_path / "normal-state"
    d.mkdir()
    return d


@pytest.fixture
def local_dir(tmp_path: Path) -> Path:
    d = tmp_path / "local-state"
    d.mkdir()
    (d / LOCAL_ONLY_FLAG).touch()
    return d


def _spec(**overrides: object) -> Specialist:
    base: dict[str, object] = {
        "name": "example-specialist",
        "local_capable": True,
        "local_capable_reason": None,
        "provider_preference": "any",
    }
    base.update(overrides)
    return Specialist(**base)  # type: ignore[arg-type]


# --- normal mode -------------------------------------------------------------


def test_any_preference_returns_any_chain(normal_dir: Path) -> None:
    r = route(message="do the thing", specialist=_spec(), state_dir=normal_dir)
    assert (r.decision, r.mode, r.block_reason) == ("any_chain", "normal", None)


def test_codex_intent_promotes_any(normal_dir: Path) -> None:
    r = route(message="use Codex for this pass", specialist=_spec(), state_dir=normal_dir)
    assert r.decision == "codex_with_claude_fallback"


def test_claude_only_wins_over_intent(normal_dir: Path) -> None:
    s = _spec(provider_preference="claude-only")
    r = route(message="use Codex for this", specialist=s, state_dir=normal_dir)
    assert r.decision == "claude_pinned"


@pytest.mark.parametrize(
    "preference,expected",
    [
        ("codex-only", "codex_with_claude_fallback"),
        ("local-only", "ollama"),
        ("gemini-only", "gemini_pinned"),
    ],
)
def test_pinned_preferences(normal_dir: Path, preference: str, expected: str) -> None:
    r = route(message="x", specialist=_spec(provider_preference=preference), state_dir=normal_dir)
    assert r.decision == expected


# --- local-only mode ---------------------------------------------------------


def test_local_session_capable_routes_local(local_dir: Path) -> None:
    r = route(message="do thing", specialist=_spec(), state_dir=local_dir)
    assert (r.decision, r.mode) == ("ollama", "local_only")


def test_local_mode_beats_gemini_only(local_dir: Path) -> None:
    r = route(message="x", specialist=_spec(provider_preference="gemini-only"), state_dir=local_dir)
    assert r.decision == "ollama"


def test_local_session_not_capable_refuses(local_dir: Path) -> None:
    s = _spec(local_capable=False, local_capable_reason="Needs web access.")
    r = route(message="x", specialist=s, state_dir=local_dir)
    assert r.decision == "refused"
    assert r.block_reason is not None
    assert "Needs web access." in r.block_reason


def test_local_session_degraded_warns_but_proceeds(local_dir: Path) -> None:
    s = _spec(local_capable="degraded", local_capable_reason="Long reasoning suffers.")
    r = route(message="x", specialist=s, state_dir=local_dir)
    assert r.decision == "ollama"
    assert any("degraded" in w for w in r.warnings)


def test_intent_promotes_one_turn_to_local(normal_dir: Path) -> None:
    r = route(message="Stay local for this review", specialist=_spec(), state_dir=normal_dir)
    assert (r.mode, r.decision) == ("local_only", "ollama")


def test_default_specialist_is_refused_in_local_mode(local_dir: Path) -> None:
    r = route(message="x", specialist=Specialist(name="plain"), state_dir=local_dir)
    assert r.decision == "refused"
    assert r.block_reason is not None
    assert "no local capability declared" in r.block_reason


def test_default_specialist_normal_mode(normal_dir: Path) -> None:
    r = route(message="x", specialist=Specialist(name="plain"), state_dir=normal_dir)
    assert r.decision == "any_chain"


# --- state directory ---------------------------------------------------------


def test_state_dir_flag_is_read(local_dir: Path, normal_dir: Path) -> None:
    assert local_only_session_active(local_dir) is True
    assert local_only_session_active(normal_dir) is False


def test_missing_state_dir_means_normal(tmp_path: Path) -> None:
    r = route(message="x", specialist=_spec(), state_dir=tmp_path / "absent")
    assert r.mode == "normal"


def test_custom_phrase_table_is_used(normal_dir: Path) -> None:
    table = {"local_only_escalate": (r"offline please",)}
    r = route(message="offline please", specialist=_spec(), state_dir=normal_dir, phrases=table)
    assert r.mode == "local_only"


# --- audit metadata ----------------------------------------------------------


def test_audit_records_intent_phrases(normal_dir: Path) -> None:
    r = route(message="Stay local for this review", specialist=_spec(), state_dir=normal_dir)
    assert r.audit is not None
    assert any(intent == "local_only_escalate" for intent, _ in r.audit.intent_phrases)


def test_audit_records_explicit_prefix(normal_dir: Path) -> None:
    r = route(message="[local] a task", specialist=_spec(), state_dir=normal_dir)
    assert r.audit is not None
    assert "local" in r.audit.explicit_prefixes


def test_audit_records_preference_and_capability(normal_dir: Path) -> None:
    s = _spec(provider_preference="codex-only", local_capable="degraded", local_capable_reason="x")
    r = route(message="x", specialist=s, state_dir=normal_dir)
    assert r.audit is not None
    assert r.audit.frontmatter_preference == "codex-only"
    assert r.audit.local_capable == "degraded"
    assert r.audit.specialist_name == "example-specialist"


# --- session toggle and select_provider --------------------------------------


def test_session_toggle_on_off_neither() -> None:
    assert detect_session_toggle("Switch to local mode for the session") == SessionToggle(True, False)
    assert detect_session_toggle("Back to normal mode") == SessionToggle(False, True)
    assert detect_session_toggle("Just a normal task") == SessionToggle(False, False)


def test_select_provider_any_without_intent() -> None:
    assert select_provider(mode_local_only=False, provider_preference="any", codex_intent=False) == (
        "any_chain"
    )


def test_local_only_preference_on_incapable_specialist_refuses(normal_dir: Path) -> None:
    s = _spec(provider_preference="local-only", local_capable=False, local_capable_reason="Needs web access.")
    r = route(message="x", specialist=s, state_dir=normal_dir)
    assert r.decision == "refused"
    assert r.block_reason is not None
    assert "Needs web access." in r.block_reason


@pytest.mark.parametrize("msg", ["[parallel] [local] inspect the report", "[codex] [local] inspect the report"])
def test_later_local_prefix_keeps_the_turn_local(normal_dir: Path, msg: str) -> None:
    r = route(message=msg, specialist=_spec(), state_dir=normal_dir)
    assert (r.mode, r.decision) == ("local_only", "ollama")
