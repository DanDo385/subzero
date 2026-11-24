---
name: open-session
description: Open an interactive terminal session on the user's machine so they can watch or type. Use for "open a shell in the worktree", "start a session there and let me watch". The first command is typed for the human to review, never executed by the runner.
---

`agent_open` queues a session for the runner on the user's machine to open
in a real terminal window. Like `agent_run`, it needs the `agents:exec`
scope; without it, say so and offer `dispatch` instead. Never pretend a
window opened.

## 1. Queue the session

Call `agent_open` with:

- `worktree`: where the session opens. `repo/slug` for a worktree, `repo`
  for a checkout, or an absolute path inside a configured exec root.
- `space`: only when the user named a herdr workspace instead of a folder.
- `first_command`: text the runner types into the new session for the
  human to review. It is never executed by the runner, and Enter is sent
  only when the text ends with a newline. That is the whole contract:
  - the user said "open a shell and show me the status": type
    `git status` with no trailing newline. The human reads it, presses
    Enter themselves, edits it, or deletes it.
  - the user explicitly said "and run it": end the text with a newline so
    the runner sends Enter after typing.
  - when in doubt, leave the newline off. A command nobody pressed Enter
    on is a small annoyance; a command that ran unreviewed is not.
- `target`: only when the user named who should open it.

## 2. Report

Say the session is queued, not open: the runner opens it when it next
polls, named `subzero-<job id prefix>`, and the person attaches with
`tmux attach -t <name>` if no terminal window pops up on the machine. If
a `first_command` was queued, say exactly what was typed and whether
Enter was sent, so the user knows what is waiting for their review.
