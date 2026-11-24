---
name: run-command
description: Run one exact command on the user's own machine and report its exit code and output. Use when they say "run the tests", "check the build on my Mac", or name any specific command for a specific folder. The command is queued for the runner on their machine; it is never executed in this chat.
---

One command, one folder, one call. You do not run anything yourself: the
server queues the job and the runner on the user's machine executes it.

## 1. Queue the command

Call `agent_run` with:

- `command`: the exact command and nothing chained onto it. No `&& rm -rf`,
  no force-push tail, one command per job. If the user described work rather
  than a command, this is the wrong skill: use `dispatch-work`.
- `worktree`: where it runs. `repo/slug` for a worktree under
  `~/Code/worktrees`, `repo` for a checkout under `~/Code`, or an absolute
  path inside a configured exec root. The server refuses a worktree that
  does not exist, and the runner on the machine re-checks before it runs
  anything.
- `timeout_s`: only when the command needs longer than the 120 second
  default (a build, a full test suite). The cap is 1800.
- `target`: only when the user named the machine or agent that should run
  it. Otherwise any free runner takes the job.

## 2. If the call is refused

`agent_run` needs the `agents:exec` scope, which is granted by name only:
the read-only default and the `"*"` policy shorthand both exclude it, so a
chat app normally does not have it. If the tool is missing or refuses:

- say plainly that this client cannot queue commands for the machine;
- offer `dispatch` instead: an agent with a shell can run the command as
  part of a goal job;
- never pretend to have run anything, and never paste the command's
  expected output from memory.

## 3. Report the result honestly

The job is queued, not started: say so, and name the target it went to.
The runner picks it up when it next polls; the result comes back through
the job record.

When you can see the finished job:

- report the `exit_code` first. A non-zero exit is a failure even when the
  output looks fine. Never call a red build green because the log's last
  line reads well.
- report the `output` tail (the last 16 KB, stdout then stderr). If
  `output_truncated` is set, say the output was cut and what was kept.
- a job that is `failed` with no exit code means the command never ran
  (the runner refused the folder, the tool was missing, or it timed out):
  quote the reason instead of guessing.
