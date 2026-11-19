"""Tests for hugin.shadow.replay. A fake provider stands in for the model.

The provider runs in a worker process, so the fakes record their calls to a
file rather than to an attribute.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from hugin.router.decision import DECISION_RE
from hugin.shadow.replay import (
    DEFAULT_TIMEOUT_SECONDS,
    SHADOW_PREFIX,
    HuginReplay,
    ReplayResult,
    load_system_prompt,
    safe_cost,
    strip_frontmatter,
)

DEFAULT_MODEL = "example-small-model"
ESCALATION_MODEL = "example-large-model"


def _envelope(*names: str) -> str:
    entries = [
        {"subagent_type": n, "prompt_summary": "would spawn", "response_size_bytes": 0, "outcome": "shadow"}
        for n in names
    ]
    return json.dumps({"relay": "shadow decision", "audit_entries": entries})


@dataclass
class FakeProvider:
    body: str = ""
    usage: dict[str, Any] | None = None
    raises: bool = False
    calls: Path | None = None

    def generate(self, *, prompt: str, system: str, model: str) -> tuple[str, dict[str, Any]]:
        if self.calls is not None:
            self.calls.write_text(json.dumps({"prompt": prompt, "system": system, "model": model}))
        if self.raises:
            raise RuntimeError("simulated provider failure")
        return self.body, dict(self.usage or {})


@dataclass
class BlockedProvider:
    """Never returns. Writes the pid of the process it runs in first."""

    pid_file: Path

    def generate(self, *, prompt: str, system: str, model: str) -> tuple[str, dict[str, Any]]:
        del prompt, system, model
        self.pid_file.write_text(str(os.getpid()))
        while True:
            time.sleep(60)


@dataclass
class ForkingBlockedProvider:
    """Starts a child process of its own, records its pid, then never returns."""

    pid_file: Path

    def generate(self, *, prompt: str, system: str, model: str) -> tuple[str, dict[str, Any]]:
        del prompt, system, model
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        self.pid_file.write_text(str(child.pid))
        while True:
            time.sleep(60)


@dataclass
class SlowToStartProvider:
    """Delays the worker's ready message by sleeping while it is unpickled."""

    delay: float

    def __setstate__(self, state: dict[str, Any]) -> None:
        time.sleep(state["delay"])
        self.__dict__.update(state)

    def generate(self, *, prompt: str, system: str, model: str) -> tuple[str, dict[str, Any]]:
        del prompt, system, model
        return _envelope(), {}


@dataclass
class ExitingParentProvider:
    """Starts a child process, records its pid, then ends the worker at once.

    The worker exits without replying, so it has already exited (and only
    its child is left in the group) by the time the caller cleans up.
    """

    pid_file: Path

    def generate(self, *, prompt: str, system: str, model: str) -> tuple[str, dict[str, Any]]:
        del prompt, system, model
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        self.pid_file.write_text(str(child.pid))
        os._exit(0)


def _gone(pid: int, wait: float = 5.0) -> bool:
    deadline = time.monotonic() + wait
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _replay(provider: Any, *, system_prompt: str = "you route requests", **kw: Any) -> HuginReplay:
    return HuginReplay(
        provider,
        system_prompt=system_prompt,
        default_model=DEFAULT_MODEL,
        escalation_model=ESCALATION_MODEL,
        **kw,
    )


def _last_call(calls: Path) -> dict[str, str]:
    return json.loads(calls.read_text())


# --- frontmatter and prompt loading ------------------------------------------


def test_strip_frontmatter_removes_yaml_block() -> None:
    assert strip_frontmatter("---\nname: router\n---\nbody text\n") == "body text\n"


def test_strip_frontmatter_without_block_returns_input() -> None:
    raw = "no frontmatter here\nbody\n"
    assert strip_frontmatter(raw) == raw


def test_strip_frontmatter_multiline_yaml() -> None:
    raw = "---\nname: router\ntools:\n- Read\n---\n\nThe body.\n"
    assert strip_frontmatter(raw).strip() == "The body."


def test_load_system_prompt(tmp_path: Path) -> None:
    spec = tmp_path / "router.md"
    spec.write_text("---\nname: router\n---\n\nLive body here.\n", encoding="utf-8")
    assert load_system_prompt(spec) == "Live body here."


# --- construction ------------------------------------------------------------


def test_required_arguments_must_be_non_empty() -> None:
    with pytest.raises(ValueError):
        _replay(FakeProvider(), system_prompt="  ")
    with pytest.raises(ValueError):
        HuginReplay(FakeProvider(), system_prompt="x", default_model="", escalation_model="y")


def test_timeout_default_and_override() -> None:
    assert DEFAULT_TIMEOUT_SECONDS == 120
    assert _replay(FakeProvider()).timeout_seconds == 120
    assert _replay(FakeProvider(), timeout_seconds=42.5).timeout_seconds == 42.5


# --- run ---------------------------------------------------------------------


def test_run_sends_shadow_prefix_and_returns_decision(tmp_path: Path) -> None:
    calls = tmp_path / "call.json"
    provider = FakeProvider(body=_envelope("writer"), usage={"cost_usd": 0.0123}, calls=calls)
    result = _replay(provider).run("Draft chapter 3 of the guide")
    assert isinstance(result, ReplayResult)
    assert (result.decision, result.parse_status, result.cost_usd) == ("branch:2:writer", "ok", 0.0123)
    call = _last_call(calls)
    assert call["prompt"] == f"{SHADOW_PREFIX}Draft chapter 3 of the guide"
    assert call["model"] == DEFAULT_MODEL


def test_run_maps_empty_and_multiple_entries() -> None:
    assert _replay(FakeProvider(body=_envelope())).run("x").decision == "branch:1:none"
    assert _replay(FakeProvider(body=_envelope("a", "b"))).run("x").decision == "branch:4:multiple"


def test_run_escalates_when_asked(tmp_path: Path) -> None:
    calls = tmp_path / "call.json"
    _replay(FakeProvider(body=_envelope(), calls=calls)).run("hard question", escalate=True)
    assert _last_call(calls)["model"] == ESCALATION_MODEL


def test_run_uses_the_given_system_prompt(tmp_path: Path) -> None:
    calls = tmp_path / "call.json"
    _replay(FakeProvider(body=_envelope(), calls=calls), system_prompt="You are a routing model.").run("any")
    assert _last_call(calls)["system"] == "You are a routing model."


def test_run_handles_provider_exception() -> None:
    result = _replay(FakeProvider(raises=True)).run("anything")
    assert (result.decision, result.parse_status, result.cost_usd) == ("branch:1:malformed", "malformed", 0.0)
    assert result.provider_failed is True


def test_run_handles_empty_reply() -> None:
    result = _replay(FakeProvider(body="", usage={"cost_usd": 0.001})).run("hi")
    assert (result.decision, result.parse_status, result.cost_usd) == ("branch:1:malformed", "empty", 0.001)
    assert result.provider_failed is False


def test_run_handles_prose_reply() -> None:
    result = _replay(FakeProvider(body="prose only", usage={"cost_usd": 0.01})).run("hi")
    assert (result.decision, result.parse_status) == ("branch:1:malformed", "malformed")
    assert result.provider_failed is False


@pytest.mark.parametrize(
    "body",
    [
        "{}",
        '{"audit_entries": "bad"}',
        '{"relay": "ok", "audit_entries": "bad"}',
        '{"relay": "ok", "audit_entries": [{"subagent_type": "writer"}]}',
    ],
)
def test_run_rejects_envelope_that_fails_the_schema(body: str) -> None:
    result = _replay(FakeProvider(body=body, usage={"cost_usd": 0.02})).run("hi")
    assert (result.decision, result.parse_status) == ("branch:1:malformed", "invalid")
    assert result.provider_failed is False
    assert result.cost_usd == 0.02


def test_timeout_kills_the_provider_process(tmp_path: Path) -> None:
    # The timeout counts from the moment the provider starts, so the pid is
    # always written before it can fire, however slow the worker's startup.
    pid_file = tmp_path / "pid"
    started = time.monotonic()
    result = _replay(BlockedProvider(pid_file), timeout_seconds=2).run("anything")
    assert time.monotonic() - started < 120
    assert (result.decision, result.parse_status, result.cost_usd) == ("branch:1:malformed", "malformed", 0.0)
    assert result.provider_failed is True
    pid = int(pid_file.read_text())
    assert pid != os.getpid()
    assert _gone(pid, wait=10)


def test_startup_bound_is_separate_from_the_call_timeout() -> None:
    # Under spawn the provider is unpickled in the worker before it reports
    # ready, so this provider holds readiness back for its whole delay.
    provider = SlowToStartProvider(delay=20.0)
    replay = _replay(
        provider,
        timeout_seconds=60,
        startup_timeout_seconds=1,
        mp_context=multiprocessing.get_context("spawn"),
    )
    assert replay.startup_timeout_seconds == 1
    started = time.monotonic()
    result = replay.run("anything")
    assert time.monotonic() - started < provider.delay
    assert (result.decision, result.provider_failed) == ("branch:1:malformed", True)
    assert _replay(FakeProvider()).startup_timeout_seconds == 60


@pytest.mark.parametrize(
    "alive,direct_child,expect_kill",
    [(True, False, True), (False, True, True), (False, False, False)],
)
def test_saved_group_is_killed_only_while_it_is_still_ours(
    monkeypatch: pytest.MonkeyPatch, alive: bool, direct_child: bool, expect_kill: bool
) -> None:
    from hugin.shadow import replay as replay_module

    killed: list[int] = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killed.append(pgid))
    stale = os.getpgrp() + 100000
    replay_module._kill_group(stale, stale, worker_alive=alive, direct_child=direct_child)
    assert killed == ([stale] if expect_kill else [])


def test_run_output_always_matches_grammar() -> None:
    providers = [
        FakeProvider(body=""),
        FakeProvider(body="nothing useful"),
        FakeProvider(body="{}"),
        FakeProvider(body=_envelope()),
        FakeProvider(body=_envelope("code-reviewer")),
        FakeProvider(body=_envelope("x", "y")),
        FakeProvider(body=_envelope("Capital_Case")),
        FakeProvider(raises=True),
    ]
    for provider in providers:
        assert DECISION_RE.match(_replay(provider).run("test").decision), provider


@pytest.mark.parametrize("usage,expected", [({}, 0.0), ({"cost_usd": "1"}, 0.0), ({"cost_usd": 2}, 2.0), ({"cost_usd": True}, 0.0)])
def test_safe_cost(usage: dict[str, Any], expected: float) -> None:
    assert safe_cost(usage) == expected


def test_timeout_kills_processes_the_provider_started(tmp_path: Path) -> None:
    pid_file = tmp_path / "child-pid"
    result = _replay(ForkingBlockedProvider(pid_file), timeout_seconds=3).run("anything")
    assert result.provider_failed is True
    pid = int(pid_file.read_text())
    try:
        assert _gone(pid, wait=10)
    finally:
        if not _gone(pid, wait=0):
            os.kill(pid, signal.SIGKILL)


def test_default_context_is_the_platform_default() -> None:
    replay = _replay(FakeProvider())
    assert replay.mp_context.get_start_method() == multiprocessing.get_context().get_start_method()
    forked = _replay(FakeProvider(), mp_context=multiprocessing.get_context("spawn"))
    assert forked.mp_context.get_start_method() == "spawn"


def test_child_outliving_an_exited_worker_is_killed(tmp_path: Path) -> None:
    pid_file = tmp_path / "child-pid"
    result = _replay(ExitingParentProvider(pid_file), timeout_seconds=30).run("anything")
    assert result.provider_failed is True
    pid = int(pid_file.read_text())
    try:
        assert _gone(pid, wait=10)
    finally:
        if not _gone(pid, wait=0):
            os.kill(pid, signal.SIGKILL)
