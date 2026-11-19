"""Replay one orchestrator turn against the router, out of band.

The replay sends ``[SHADOW] <user message>`` to a model running the
router's instructions. The ``[SHADOW]`` prefix tells the router to compute
its routing decision without spawning anything. The model picks the
branch; :mod:`hugin.router.decision` turns its reply into a
``branch:N:specialist`` string.

The provider is any object with a ``generate(*, prompt, system, model)``
method that returns ``(body, usage)``. The system prompt and both model
names are required arguments: this module knows nothing about where the
router's instructions live or which models a deployment uses.

Each call runs in its own worker process, so a timeout can end it. Two
bounds apply: ``startup_timeout_seconds`` covers the time from when the
launch call (``Process.start``) returns to the worker's ready message; the
launch call itself is not bounded. ``timeout_seconds`` covers the provider
call itself, counted from the moment the provider starts. A slow interpreter start therefore
never eats into the provider's time. On POSIX the worker starts its own
process group before the provider runs and reports the group id, and when the call ends the
whole group is killed by that saved id, so processes the provider started (a CLI it runs, for example)
end with it; the worker itself is then reaped. A process that leaves the
group (one that starts its own session, for example) is not reached, and
the provider is responsible for cleaning it up. On other platforms only
the worker is killed, and a provider that starts processes must clean
them up itself.

The worker uses the platform's default multiprocessing context unless
``mp_context`` is given. When that context is ``spawn`` (the default on
macOS and Windows) or ``forkserver``, the provider object must be
picklable, so define its class at module level in an importable module.
"""

from __future__ import annotations

import contextlib
import multiprocessing
import multiprocessing.connection
import os
import re
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from hugin.router.decision import (
    MALFORMED_DECISION,
    ParseStatus,
    decision_from_envelope,
    extract_envelope,
)
from hugin.router.envelope import HuginResultEnvelope

DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_STARTUP_TIMEOUT_SECONDS = 60
SHADOW_PREFIX = "[SHADOW] "

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


class Provider(Protocol):
    """The model call the replay needs."""

    def generate(self, *, prompt: str, system: str, model: str) -> tuple[str, dict[str, Any]]: ...


@dataclass(frozen=True)
class ReplayResult:
    """Outcome of one replay call.

    ``provider_failed`` separates a retryable transport failure (timeout,
    exception) from a completed reply that did not parse.
    """

    decision: str
    raw_envelope: dict[str, Any]
    parse_status: ParseStatus
    cost_usd: float
    provider_failed: bool = False


def strip_frontmatter(text: str) -> str:
    """Return the body after a leading ``---`` YAML block, else the input."""
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return text
    return match.group(2)


def load_system_prompt(path: Path) -> str:
    """Read an instructions file and return its body without frontmatter."""
    return strip_frontmatter(path.read_text(encoding="utf-8")).strip()


class HuginReplay:
    """One-call harness around the router's instructions."""

    def __init__(
        self,
        provider: Provider,
        *,
        system_prompt: str,
        default_model: str,
        escalation_model: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        mp_context: multiprocessing.context.BaseContext | None = None,
        startup_timeout_seconds: float = DEFAULT_STARTUP_TIMEOUT_SECONDS,
    ) -> None:
        if not system_prompt.strip():
            raise ValueError("system_prompt must not be empty")
        if not default_model.strip() or not escalation_model.strip():
            raise ValueError("model names must not be empty")
        self._provider = provider
        self._system_prompt = system_prompt
        self._default_model = default_model
        self._escalation_model = escalation_model
        self._timeout_seconds = timeout_seconds
        self._startup_timeout_seconds = startup_timeout_seconds
        self._mp_context = mp_context or multiprocessing.get_context()

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @property
    def mp_context(self) -> multiprocessing.context.BaseContext:
        return self._mp_context

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    @property
    def startup_timeout_seconds(self) -> float:
        return self._startup_timeout_seconds

    def run(self, user_message: str, *, escalate: bool = False) -> ReplayResult:
        """Compute the router's shadow decision for one user message.

        Any provider failure, parse failure or envelope that fails the
        :class:`HuginResultEnvelope` schema gives ``branch:1:malformed``;
        a schema failure has ``parse_status`` ``invalid``. Cost is zero for
        provider failures, since only a completed call reports usage.
        """
        prompt = f"{SHADOW_PREFIX}{user_message}"
        model = self._escalation_model if escalate else self._default_model

        body, usage, ok = self._call_with_timeout(prompt=prompt, model=model)
        if not ok:
            return ReplayResult(
                decision=MALFORMED_DECISION,
                raw_envelope={},
                parse_status="malformed",
                cost_usd=0.0,
                provider_failed=True,
            )

        envelope, status = extract_envelope(body)
        if status != "ok":
            return ReplayResult(
                decision=MALFORMED_DECISION,
                raw_envelope=envelope,
                parse_status=status,
                cost_usd=safe_cost(usage),
            )
        try:
            HuginResultEnvelope.model_validate(envelope)
        except ValidationError:
            return ReplayResult(
                decision=MALFORMED_DECISION,
                raw_envelope=envelope,
                parse_status="invalid",
                cost_usd=safe_cost(usage),
            )
        return ReplayResult(
            decision=decision_from_envelope(envelope),
            raw_envelope=envelope,
            parse_status="ok",
            cost_usd=safe_cost(usage),
        )

    def _call_with_timeout(self, *, prompt: str, model: str) -> tuple[str, dict[str, Any], bool]:
        """Call the provider in a worker process; return ``(body, usage, ok)``.

        The worker's process group is killed and the worker reaped when the
        call ends, so a hung provider and the processes it started cannot
        outlive the call. ``ok`` is false when either bound passes, on a
        provider exception and on a worker that exits without a reply.
        """
        receiver, sender = self._mp_context.Pipe(duplex=False)
        worker = self._mp_context.Process(
            target=_worker,
            args=(sender, self._provider, prompt, self._system_prompt, model),
            daemon=True,
        )
        worker.start()
        sender.close()
        pgid: int | None = None
        try:
            if not receiver.poll(self._startup_timeout_seconds):
                return "", {}, False
            try:
                ready = receiver.recv()
            except (EOFError, OSError, ValueError):
                return "", {}, False
            if not (isinstance(ready, tuple) and len(ready) == 2 and ready[0] == "started"):
                return "", {}, False
            pgid = ready[1] if isinstance(ready[1], int) else None
            if not receiver.poll(self._timeout_seconds):
                return "", {}, False
            try:
                status, body, usage = receiver.recv()
            except (EOFError, OSError, ValueError):
                return "", {}, False
            if status != "ok":
                return "", {}, False
            return body, usage, True
        finally:
            _kill_group(
                pgid,
                worker.pid,
                worker_alive=worker.is_alive(),
                direct_child=self._mp_context.get_start_method() in ("fork", "spawn"),
            )
            if worker.is_alive():
                worker.kill()
            worker.join()
            receiver.close()


def _kill_group(
    pgid: int | None,
    worker_pid: int | None,
    *,
    worker_alive: bool,
    direct_child: bool,
) -> None:
    """Kill the worker's process group while the group id is still ours.

    ``pgid`` is the group id the worker reported before the provider
    started. A group id stays reserved while its leader's pid does. With
    ``fork`` and ``spawn`` the worker is our own child, and it is not reaped
    until after this call, so its pid, and the group id, cannot have been
    reused even if it has exited: the group is killed whether or not the
    worker is still running. With ``forkserver`` the worker is the server's
    child and may already be reaped, so the group is killed only while the
    worker is still running; a short window remains between that check and
    the kill. When the worker never reported a group id, the group is killed
    only if the worker is confirmed to lead its own group, never the
    caller's group. A group that no longer exists is not an error.
    """
    if not hasattr(os, "killpg"):
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        if pgid is not None:
            if pgid != os.getpgrp() and (direct_child or worker_alive):
                os.killpg(pgid, signal.SIGKILL)
        elif worker_pid is not None and worker_alive and os.getpgid(worker_pid) == worker_pid:
            os.killpg(worker_pid, signal.SIGKILL)


def _worker(
    conn: multiprocessing.connection.Connection,
    provider: Provider,
    prompt: str,
    system: str,
    model: str,
) -> None:
    """Report ready, run one provider call and send ``(status, body, usage)`` back.

    The process group is created before ready is reported, and its id is
    sent with it, so the group exists before the provider starts and before
    the call timeout can fire.
    """
    pgid = None
    if hasattr(os, "setpgid"):
        os.setpgid(0, 0)
        pgid = os.getpgrp()
    conn.send(("started", pgid))
    try:
        body, usage = provider.generate(prompt=prompt, system=system, model=model)
        conn.send(("ok", body, dict(usage)))
    except Exception:  # noqa: BLE001
        conn.send(("error", "", {}))
    finally:
        conn.close()


def safe_cost(usage: dict[str, Any]) -> float:
    """``cost_usd`` from a usage dict, or 0.0 when absent or not a number."""
    raw = usage.get("cost_usd")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    return 0.0
