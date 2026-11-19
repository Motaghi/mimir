# Hugin

No shadow turns recorded since 11 June 2026.

Hugin is a shadow router. For each of the 755 turns recorded from 8 May to 11 June 2026, Hugin was asked out of band which routing branch and which specialist it would pick, and its answer was compared with what the orchestrator (Mimir) actually did. Trivial turns, such as very short messages, were skipped by design and never recorded, so the 755 are not every turn the orchestrator handled in that period. Nothing Hugin decided was acted on. The comparison is the measurement, and the measurement is what would decide whether Hugin ever takes over routing.

Hugin is not routing live.

## The numbers

Both figures describe the same 755 turns, recorded between 8 May and 11 June 2026. The last recorded turn is dated 2026-06-11. A query over any later window finds no turns at all.

Figure A: 755 turns, 84.9% divergence, window 2026-05-08 to 2026-06-11 (queried to 2026-11-18), measured 2026-11-18.
Definition: a turn counts as divergent when Hugin's decision label (routing branch plus specialist name) differs from the orchestrator's. Figure A was measured with the private `claude-shadow divergence` command over the private shadow log, aggregate numbers only, with a query running to 18 November 2026, past the last recorded turn.

Figure B: about 33% divergence (251 of 755 turns), window 2026-05-08 to 2026-06-12, measured 2026-06-12.
Definition: a turn counts as divergent only when the routing branch differs, whatever the specialist name. This is an audit estimate, not a built metric: a one-off routing audit on 12 June 2026 split the 641 label-level mismatches into 390 that share a branch and differ only in specialist label or a parse failure, and 251 true branch-level divergences. Both decisions were inferred as branch 1, 2 or 4 (see below), so Figure B can only see disagreements among those three branches. A failed or unparsable reply was recorded as branch 1, and the audit grouped parse failures with the branch agreements; how many of the 755 replies failed was not counted, so the size and direction of their effect on Figure B are not known.

## Why Hugin is not live

Three things are missing, and none of them is a tuning problem. Nothing consumes either number to switch Hugin on. The aggregator that would summarise divergence over time is not wired to any decision. The comparator redesign that the audit proposed, which would compute Figure B continuously instead of once by hand, is not built. On top of that, no turns have been recorded since 11 June 2026, so there is no recent evidence either way. The replay job found nothing to replay on 139 days in Figure A's query window. Why recording stopped was not investigated.

## How the routing decision is made

The model picks the routing branch, following its instructions. No code in this package chooses a branch. The code converts the model's reply into a comparable decision string, `branch:N:specialist`, where the specialist is a lowercase kebab-case name (`none` when the orchestrator handles a turn itself, `multiple` for two or more delegations). The orchestrator's own decision is derived the same way from the delegations it made during the turn, and the two strings are compared.

The orchestrator's contract has six routing branches, but neither side records which branch it took. The branch is inferred from the number of delegations: none is branch 1, one is branch 2, two or more is branch 4. Branches 3, 5 and 6 never appear in a decision string. A sequential handoff (3) and a parallel fan-out (4) that each make two delegations both become branch 4, so that disagreement cannot be seen; a handoff to a background layer (5) and a clarifying question (6) make no delegation and both look like branch 1.

A failed replay is not a routing decision. In this package it never counts as branch agreement: `branches_match` returns false when either side failed, and `branch_counts` reports failed replies separately from the turns it compares.

Replay depends on a small contract with the router's instructions. The user message is sent with a `[SHADOW] ` prefix, which tells the router to decide without spawning anything. The router must answer with a JSON envelope holding `relay` and `audit_entries`, one entry per specialist it would have spawned, each with `subagent_type`, `prompt_summary`, `response_size_bytes` and `outcome` set to `shadow`. A reply that does not parse, or that fails the envelope schema, is recorded as `branch:1:malformed`.

## What is in this folder

This folder is the measurement harness around the router, refactored for publication from the private code that produced the figures. The router itself is a model following written instructions, and those instructions (the router prompt) are not included. In the form published here, the modules have run only against the synthetic tests.

- `src/hugin/router/decision.py`: the decision-string grammar and the mapping from a model reply to a decision.
- `src/hugin/router/envelope.py`: the result envelope a router returns each turn.
- `src/hugin/router/sensitivity.py`: the phrase classifier that runs before a specialist is spawned. It is a routing hint, not a data-loss control: it reads phrases that ask for local handling, not the material itself. It ships with a short, neutral example phrase list; pass your own table.
- `src/hugin/router/provider_choice.py`: the provider-choice decision tree (local-only mode, pinned preferences, refusals). The state directory is a required argument.
- `src/hugin/shadow/staging.py`: per-turn staging records waiting for replay.
- `src/hugin/shadow/replay.py`: replays one turn against the router through a provider object (see below). Each call runs in a worker process whose process group is killed when the call ends. The system prompt and model names are required arguments.
- `src/hugin/shadow/compare.py`: derives the orchestrator's decision from delegation events, compares the two decisions and writes the zero-input marker for days with nothing to replay.
- `src/hugin/shadow/recorder.py`: appends shadow records to a log you name, readable only by its owner, and counts divergence over a window.
- `tests/`: one test file per module, synthetic fixtures only.

Four helpers were added for the public package and have never run on the real data: `branch_of`, `branches_match` and `branch_counts` in `compare.py`, and `latest_turn_date` in `recorder.py`. Figure B came from the by-hand audit, not from `branches_match`.

The private hooks, the audit and delegation logging, the provider layer and the scheduled replay job stay private; every place the code touched them is now a small interface or a required parameter.

## Using the replay

A provider is any object with a method `generate(*, prompt, system, model)` that returns a tuple `(body, usage)`: `body` is the model's reply as text, and `usage` is a dict whose optional `cost_usd` is a number. `timeout_seconds` bounds the provider call and counts from the moment the provider starts; a separate `startup_timeout_seconds` (60 by default) bounds the time from when the worker process has been launched to its ready message (the launch call itself is not bounded). When the call ends, the worker's whole process group is killed, so processes the provider started end with it. A process that detaches into its own session leaves that group, and the provider is responsible for cleaning it up. With the `forkserver` start method the group is killed only while the worker is still running. The call runs in a separate process, so on macOS and Windows the provider must be picklable: define its class at module level in an importable module, and start the replay under `if __name__ == "__main__":`.

```python
# my_provider.py
class FixedProvider:
    """Stands in for a real model call and returns one canned reply."""

    def generate(self, *, prompt: str, system: str, model: str) -> tuple[str, dict]:
        return '{"relay": "handled directly", "audit_entries": []}', {"cost_usd": 0.0}
```

```python
# run_replay.py
from hugin.shadow.replay import HuginReplay
from my_provider import FixedProvider

if __name__ == "__main__":
    replay = HuginReplay(
        FixedProvider(),
        system_prompt="You are the router. ...",
        default_model="your-default-model",
        escalation_model="your-larger-model",
        timeout_seconds=60,
    )
    result = replay.run("Summarise the release notes")
    print(result.decision, result.parse_status)  # branch:1:none ok
```

## Platforms

The package is written for POSIX systems and tested on macOS. Two behaviours depend on POSIX: logs and staging files are created readable only by their owner (mode 0o600), and the replay kills the worker's whole process group. On Windows the files get the platform's default permissions and only the worker process is killed, so a provider that starts processes must clean them up itself.

## Running the tests

Requires Python 3.11 or newer; `python` in the first command must be that interpreter.

```
python -m venv .venv
.venv/bin/python -m pip install "./hugin[test]"
.venv/bin/python -m pytest hugin/tests -q
```

Run these from the folder that contains `hugin/`. The tests need no network and no state outside a temporary directory.

## Licence

MIT. See the repository's `LICENSE`.
