---
name: run-command
description: Run one exact command on this Mac and report its exit code and output in the same chat. Use when they say "run the tests", "check the build on my Mac", or name any specific command for a specific folder.
---

One command, one folder, one call. You do not run anything yourself.

## 1. Run it on this Mac and return the result

When the user wants the result in this chat, call `run_cli_on_mbp` with:

- `command`: the exact command and nothing chained onto it. No `sudo`, no
  `&& rm -rf`, one command per call. If the user described work rather than
  a command, this is the wrong skill: use `dispatch-work`.
- `cwd`: where it runs. `repo/slug` for a worktree under `~/Code/worktrees`,
  `repo` for a checkout under `~/Code`, or an absolute path inside a
  configured exec root. The server refuses a path that does not exist.
- `timeout_s`: only when the command needs longer than the 30 second default.
  The cap is 600. If the result says `running`, call `cli_job_on_mbp` with
  the job id until it is `done` or `timed_out`.
- `retain`: true only when the user asked to reattach to the session.

Report `exit_code`, `host`, `cwd`, and the stdout and stderr. A non-zero exit
is a failure even when the output looks fine. `timed_out` means the command
was stopped. Do not use `agent_run` or `dispatch` for this: those queue a
coding harness and do not return the command's output here.

## 2. If the call is refused

`run_cli_on_mbp` needs the `cli:run` scope, which is granted by name only:
the read-only default and the `"*"` policy shorthand both exclude it. If the
tool is missing or refuses:

- say plainly that this client cannot run commands on the Mac;
- do not fall back to pretending you ran it;
- `agent_run` is a different path (it queues for a harness runner) and is
  also excluded from `"*"`. Offer it only when the user asked to queue a
  command for a coding agent rather than see the output in this chat.

## 3. Queue a command for a harness runner

Call `agent_run` only when the user asked to queue a command for a coding
agent's runner, not when they want the output in this chat:

- `command`: the exact command and nothing chained onto it. No `&& rm -rf`,
  no force-push tail, one command per job.
- `worktree`: where it runs. `repo/slug` for a worktree under
  `~/Code/worktrees`, `repo` for a checkout under `~/Code`, or an absolute
  path inside a configured exec root.
- `timeout_s`: only when the command needs longer than the 120 second
  default. The cap is 1800.
- `target`: only when the user named the machine or agent that should run
  it. Otherwise any free runner takes the job.

`agent_run` needs `agents:exec`, also granted by name only. The job is
queued, not started: say so, and name the target. When you can see the
finished job, report its `exit_code` first. A non-zero exit is a failure
even when the output looks fine. Quote a `failed` job's reason instead of
guessing.
