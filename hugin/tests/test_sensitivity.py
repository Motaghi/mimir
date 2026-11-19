"""Tests for hugin.router.sensitivity."""

from __future__ import annotations

import pytest

from hugin.router.sensitivity import (
    EXAMPLE_PHRASES,
    INTENTS,
    classify,
    compile_pattern_list,
    phrase_categories,
)


def test_empty_string_returns_no_intents() -> None:
    r = classify("")
    assert r.intents == frozenset()
    assert r.matched_phrases == ()
    assert r.explicit_prefixes == frozenset()


def test_whitespace_only_returns_no_intents() -> None:
    assert classify("   \n  \t  ").intents == frozenset()


@pytest.mark.parametrize(
    "msg",
    [
        "Stay local for this one, it is a draft contract.",
        "Keep this private please.",
        "keep it local while we read the report",
        "Don't send this to the cloud.",
        "do not send this to cloud",
        "local only for the next task",
    ],
)
def test_local_only_escalate(msg: str) -> None:
    assert "local_only_escalate" in classify(msg).intents


def test_local_only_does_not_fire_on_unrelated_text() -> None:
    assert "local_only_escalate" not in classify("I like the local bakery").intents


@pytest.mark.parametrize(
    "msg",
    [
        "I want a second opinion on this draft.",
        "Have another model check this memo.",
        "Run this on both, please.",
    ],
)
def test_parallel(msg: str) -> None:
    assert "parallel" in classify(msg).intents


@pytest.mark.parametrize("msg", ["Use Codex for this pass.", "Send this to Codex.", "Ask Codex."])
def test_codex_prefer(msg: str) -> None:
    assert "codex_prefer" in classify(msg).intents


@pytest.mark.parametrize("msg", ["Switch to local mode now.", "local mode on", "Start a local session."])
def test_session_local_on(msg: str) -> None:
    assert "session_local_on" in classify(msg).intents


@pytest.mark.parametrize("msg", ["Back to normal mode.", "local mode off", "End the local session."])
def test_session_local_off(msg: str) -> None:
    assert "session_local_off" in classify(msg).intents


def test_word_boundaries_hold() -> None:
    assert classify("the outstay locality report").intents == frozenset()


@pytest.mark.parametrize(
    "msg,intent,prefix",
    [
        ("[local] read the attached report", "local_only_escalate", "local"),
        ("[parallel] draft the memo", "parallel", "parallel"),
        ("[codex] review this change", "codex_prefer", "codex"),
        ("[LOCAL] anything", "local_only_escalate", "local"),
    ],
)
def test_explicit_prefixes(msg: str, intent: str, prefix: str) -> None:
    r = classify(msg)
    assert intent in r.intents
    assert prefix in r.explicit_prefixes


def test_local_paste_ok_recorded_but_not_routing() -> None:
    r = classify("[local-paste-ok] here is the document body")
    assert "local-paste-ok" in r.explicit_prefixes
    assert r.intents == frozenset()


def test_several_intents_fire_together() -> None:
    r = classify("Stay local and give me a second opinion.")
    assert {"local_only_escalate", "parallel"} <= r.intents


def test_matched_phrases_carry_substring() -> None:
    r = classify("Stay local for this one")
    found = [m for intent, m in r.matched_phrases if intent == "local_only_escalate"]
    assert any(m.lower() == "stay local" for m in found)


def test_custom_phrase_table_replaces_examples() -> None:
    table = {"local_only_escalate": (r"offline please",)}
    assert "local_only_escalate" in classify("Offline please, this one is internal.", table).intents
    assert classify("Stay local", table).intents == frozenset()


def test_prefixes_work_with_an_empty_table() -> None:
    assert "parallel" in classify("[parallel] anything", {}).intents


def test_compile_pattern_list_empty_is_none() -> None:
    assert compile_pattern_list([]) is None
    regex = compile_pattern_list(["two words"])
    assert regex is not None
    assert regex.search("TWO WORDS here")


def test_phrase_categories_cover_every_intent() -> None:
    cats = phrase_categories()
    assert set(cats) == set(INTENTS)
    for name, phrases in cats.items():
        assert len(phrases) >= 3, name
        assert phrases == list(EXAMPLE_PHRASES[name])


@pytest.mark.parametrize(
    "fragment,text",
    [("private|secret", "I work privately"), ("local|offline", "run it locally"), ("a|b", "abc")],
)
def test_custom_alternatives_stay_inside_word_boundaries(fragment: str, text: str) -> None:
    assert classify(text, {"local_only_escalate": (fragment,)}).intents == frozenset()


@pytest.mark.parametrize("msg", ["[parallel] [local] inspect the report", "[codex][local] inspect the report"])
def test_every_leading_prefix_is_read(msg: str) -> None:
    r = classify(msg)
    assert "local_only_escalate" in r.intents
    assert "local" in r.explicit_prefixes
