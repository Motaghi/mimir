# History

By Behzad Motaghi, who built and runs the platform this repository comes from.

This is the short, dated history of the orchestrator contract and the Norse names around it, written for engineers who run an orchestrating agent over a fleet of specialists. It sticks to what the commits and design documents of the private repository support. Where the record is silent, it says so.

## AM, the Agent Manager (April 2026)

The project started with a design spec on 18 April 2026 for the first version of an agent platform: one main session that routes each request, a small fleet of named specialists, a learning loop and a background layer for scheduled work. There was no brain file yet on that day. A stub contract was committed at 13:01 on 19 April, and the first full contract followed on 19 April 2026 at 13:58. In it the main session is called "AM", short for Agent Manager, and it already has the six routing branches that every later version keeps. That contract is [snapshots/2026-04-19-v1-am.md](snapshots/2026-04-19-v1-am.md).

## The rename to Mimir

A spec dated 21 April 2026 renamed the main session, together with two other changes: the data layer moved into an Obsidian vault, and the contract gained rules for saving context in long sessions. The rename from AM to Mimir landed on 22 April 2026, across the contract, the runbooks and the code. Later that day an audit of the whole setup produced a batch of fixes to the contract, and the version at the end of 22 April is [snapshots/2026-04-22-mimir-v2.md](snapshots/2026-04-22-mimir-v2.md).

The name Mimir fits both roles the main session plays: counsel, because in the myths Mimir is the adviser Odin consults, and memory, because of Mimir's well, the source of that wisdom. The first written reason is in the 27 April design, which calls Mimir the wisdom-keeper. The fuller reading, counsel and memory, is the author's own. Once the orchestrator was Mimir, the next two names followed from the same myths: Odin's two ravens, Hugin (thought) and Munin (memory).

## Trinity, Hugin and Munin (27 April 2026)

Trinity was the codename of the 27 April redesign, which made three interlocking changes: a new vault taxonomy, Mimir reduced to a thin shell that routes and delegates, and a review pipeline for the platform's own runs. No component is called Trinity, and no coiner is recorded: the word first appears in a commit on 27 April 2026, and nothing in the record says who came up with it.

The first draft of that design had Hugin, the routing raven, next to a store for session state that had no name of its own. In the design review of the same day, Hugin alone was flagged as mythologically incomplete, since a raven of thought without a raven of memory breaks the pair, so Munin was added: the session-state store took the name of the raven of memory.

Munin was built and never used. Its session-state package, its protocol and its command-line tool were written on 27 and 28 April, but nothing ever read from them or wrote to them, and the state directory they were meant to fill never existed. Only Munin's scheduled prune job runs today, and it now expires queued lesson candidates, which has nothing to do with session state.

## Hugin, the shadow router (April to June 2026)

The Hugin specialist landed on 28 April 2026, together with a shadow mode in which it makes routing decisions without acting on them. In May it gained provider routing and then a replay job: each recorded orchestrator turn is replayed against Hugin, and the two routing decisions are compared. That comparison is the whole point: the plan was to let Hugin route live only once its decisions agreed closely enough with the orchestrator's.

The evidence never got there. Between 8 May and 11 June 2026 the replay recorded 755 turns. Counted by decision label (branch plus specialist name), Hugin disagreed with the orchestrator on 84.9 percent of them. A one-off audit on 12 June split those disagreements by hand and found that about 33 percent of turns (251 of 755) differ in the routing branch itself; the rest differ only in the specialist label or failed to parse. That split counted replies that failed to parse together with the turns that share a branch, and no check of either side's decisions against human labels is recorded, so it is a rough estimate and the true branch-level figure may be higher. The audit proposed a comparator that would measure branch agreement directly, but that comparator was never built, and no shadow turn has been recorded since 11 June 2026. Nothing consumes either number to switch Hugin on.

That is why [hugin/README.md](hugin/README.md) opens with the gap in the evidence before it gives any figure. Hugin is published as a measured experiment, not as a working router.

## The provider-neutral brain (30 June 2026)

On 30 June 2026 the contract moved from a file that only one host reads to `AGENTS.md`, which several agent hosts load by convention, and the old file became a link to it. From then on the same contract could run on any host that reads `AGENTS.md`. That version is [snapshots/2026-06-30-provider-neutral-brain.md](snapshots/2026-06-30-provider-neutral-brain.md), and the generic [template/AGENTS.md](template/AGENTS.md) is derived from its structure.

## The firstmate and liaison model (16 September 2026)

On 16 September 2026 a new operating model was designed and approved: Mimir as the liaison the owner talks to, with dispatching moved to an always-on firstmate. We tried to adopt firstmate into our own workflow in supervised steps, but in the end built our own instead: Yggdrasil, which was inspired by firstmate. The contract in use still carries the six routing branches. This repository has no snapshot of that era; this section is its only record here.

## What is in this repository

The three snapshots are the real contract at the three dates above, redacted line by line: private paths, hosts, client names, people and private files are replaced with markers such as `[redacted: path]`, and nothing else is rewritten. The template is a generic contract for anyone to adapt. The `hugin/` directory holds the router and shadow-replay code, extracted from the private repository and cut loose from it.
