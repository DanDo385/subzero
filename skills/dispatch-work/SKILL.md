---
name: dispatch-work
description: Hand a described piece of work to the user's coding agents as a goal job. Use for "have claude fix...", "get the crew to...", "/dispatch ..." where the user describes an outcome rather than one exact command. One exact command for their machine is run-command, not this.
---

`dispatch` queues a goal: a description of the work, in the user's words,
for a coding agent (Claude Code, a firstmate crew member, Codex) to pick
up. The job is queued, not started.

## 1. Write the goal

- `goal`: the work in the user's words. Keep their intent and scope.
- `target`: the agent they named, if they chose one.
- `home`: the named home if clear, such as `projects/site` or `lesson/zig`.
- `worktree`: where the work lives, when the user named a folder:
  `repo/slug` for a worktree, `repo` for a checkout. The server refuses a
  path that does not exist under a configured exec root.
- `space`: the herdr workspace, when the user named one. A goal with a
  space is submitted into that workspace: the runner types it into the one
  herdr pane holding an agent. With no space and no runner pane, the goal
  waits on the board for an agent or a human to claim it.

## 2. Scout or ship

- `mode="scout"` is the default: read, plan, report, change nothing.
- `mode="ship"` only when the user said to go ahead ("fix it", "make the
  change", "--ship").
- Anything touching the network, credentials, deletion or deployment
  always starts as scout, whatever the user said. The server enforces
  this; do not argue with it and do not rephrase the goal to sneak past
  it. A risky job that plans first is the point.

## 3. Report

Say plainly: queued, not started, and name the agent that got it
(`claude`, or `crew` when routing is still open) and the mode. The board
shows it being picked up; `agents_status` answers "who is working on it".
Never say work is done, in progress, or failed until a job record says so,
and never summarize a result you have not seen.
