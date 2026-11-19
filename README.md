# mimir

Mimir is the orchestrator of a working agent platform: the main session that reads each request, picks one of six routing branches and either answers directly or hands the work to a named specialist. This repository publishes its contract, three dated versions of the real thing, and the code of Hugin, the shadow router that was built to take over routing and never earned the switch. It is for engineers who run an orchestrating agent over a fleet of specialists and want a worked example, including the parts that did not pan out.

Start with [HISTORY.md](HISTORY.md) for how it got here: the April contract when the orchestrator was still called AM, the rename to Mimir, the Trinity redesign, Hugin and Munin, and the later changes, with dates.

## What is here

| Path | What it is |
|---|---|
| [template/AGENTS.md](template/AGENTS.md) | A generic orchestrator contract you can copy and fill in. It has the six routing branches, delegation to named specialists, verify-before-assert, a memory section and a hosts section. Owner-specific values are placeholders such as `{{OWNER}}`. |
| [snapshots/](snapshots/) | The real contract at three dates: [19 April 2026](snapshots/2026-04-19-v1-am.md) (v1, the orchestrator called AM), [22 April 2026](snapshots/2026-04-22-mimir-v2.md) (renamed to Mimir) and [30 June 2026](snapshots/2026-06-30-provider-neutral-brain.md) (the provider-neutral `AGENTS.md`). |
| [hugin/](hugin/) | Hugin's routing decision logic and its shadow-replay side (recorder, replay, comparison and divergence count), with tests that run on synthetic data. Hugin is not routing live; [hugin/README.md](hugin/README.md) leads with the gap in its evidence before giving any number. |
| [HISTORY.md](HISTORY.md) | The dated history for engineers. |

## Reading the snapshots

Each snapshot is the file as committed on its date, redacted and not rewritten. Absolute paths on the owner's machine, machine and backup details, business systems and named private files are redacted. Every line is either verbatim from the original or carries a marker where a private detail was removed:

- `[redacted: path]` for absolute paths on the owner's machine and paths to private documents,
- `[redacted: host]` for machine and backup details,
- `[redacted: client]` for client names and business systems,
- `[redacted: person]` and `[redacted: owner]` for people,
- `[redacted: private-file]` for named private files, such as the memory index, the lesson stores and internal audit and incident notes.

Everything else, including the rough edges, the references to files that are not published and the instructions that changed in later versions, is left as it was. The snapshots describe a private platform, so many paths and commands in them do not exist in this repository. Use the template if you want something to build on.

## Running the Hugin tests

Hugin is a small Python package with its own `pyproject.toml`. It needs Python 3.11 or newer. From the repository root:

```
python -m venv .venv
.venv/bin/python -m pip install "./hugin[test]"
.venv/bin/python -m pytest hugin/tests
```

The tests need no network and no state outside a temporary directory.

## Licence and contributions

MIT, see [LICENSE](LICENSE). Contributions need a Developer Certificate of Origin sign-off on every commit; see [CONTRIBUTING.md](CONTRIBUTING.md). If you spot a missed redaction or anything that looks private, report it privately as described in [SECURITY.md](SECURITY.md), never in a public issue.
