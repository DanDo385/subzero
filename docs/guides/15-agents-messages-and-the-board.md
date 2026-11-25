# 15. Agents, messages and the board

Every coding session you run (Claude Code in a worktree, a firstmate crew member, Codex) can
register with subzero, start with one shared master prompt, message the others, take jobs you
dispatch from your phone, and show up as a little figure on a live board on your Mac.

**Time:** about 15 minutes. **You need:** a running server ([guide 5](05-put-it-online.md) or
[guide 14](14-run-on-your-mac-over-tailscale.md)) and a terminal on each machine whose agents
should join.

## 1. Connect each machine (once)

On the machine running the **server**, make a one-time code. `--agents` matters: a code made
without it (like the browser extension's) can save chats but can't drive agents.

```bash
uv run subzero pair-device --agents --device-name mbp
```

On the machine whose agents should join (it can be the same one):

```bash
subzero connect https://<your-server-address> --machine mbp
```

Paste the code when asked. **Success:** `Connected as mbp.` It also creates two files for you to
fill in, because every session starts with them:

| File | What to write | Example |
| --- | --- | --- |
| `~/.config/subzero/MEMORY.md` | Who you are, what you're learning, rules for agents | "Lessons: leave the key lines as TODO(you)." |
| `~/.config/subzero/MACHINES.md` | Your machines, their tailnet names and roles | `hetzner` runs long jobs; `mbp` has Obsidian |

Keep both short (about 40 lines). Never put passwords or tokens in them.

## 2. Install the hooks into Claude Code

```bash
subzero hooks install
```

This adds four hooks to `~/.claude/settings.json` (your other settings and hooks are kept, and the
original is saved as `settings.json.before-subzero`) plus three slash commands: `/dispatch`,
`/msg` and `/agents`. Start a new Claude Code session to load them. To undo:
`subzero hooks uninstall`. Use `--scope project` to install for one repo only.

What each hook does:

| When | It |
| --- | --- |
| A session starts | registers it, then adds the **master prompt**: subzero's rules, your MEMORY.md and MACHINES.md, where you left off in this project or lesson, and any messages waiting |
| You send a prompt | marks it working and adds any new messages from other sessions |
| A turn ends | marks it idle (a lesson's "your turn" stays up) |
| The session ends | marks it done; on the board it walks to the vault |

A hook never blocks Claude Code: if the server can't be reached it prints nothing and writes the
reason to `~/.cache/subzero/hook-errors.log`. These hooks follow Claude Code's documented hook
format (JSON on stdin; text printed at session start or prompt time is added to the context).

**See the master prompt yourself:** `subzero prompt` (add `--query "cfa2"` to focus a lesson).

## 3. Homes, everywhere

Every session and note is filed under one home: `projects/<slug>`,
`lesson/<name>`, `setup/<tool>`, or `other`. Say the project, lesson, or tool
by name in chat or in a checkpoint's `home` field and the server matches it.
When nothing matches, Jev decides; when Jev isn't sure, things go to the
inbox (saves) or to `crew` (jobs).

## 4. Dispatch work, send messages

```bash
subzero dispatch "%Code/portfolio-site add a dark mode toggle >claude"
subzero dispatch "^zig scaffold the arena allocator lesson"      # Jev picks the agent
subzero dispatch "&tailscale tighten the ACL" --ship             # still starts as scout: it's risky
subzero msg ^zig "use 0.15 syntax"                               # every session in that lesson
subzero msg me "PR #14 is ready"                                 # shows on your board and phone
subzero agents                                                   # who's running, open jobs
subzero inbox                                                    # messages for you
```

From your phone, ask any connected app: "/dispatch ^zig write the allocator test >claude". It calls
the `dispatch` tool and tells you which agent got it.

**Scout or ship.** Jobs are `scout` (read, plan, report) unless you say `--ship`. Anything that
touches the network, credentials, deletion or deploys always starts as scout, so a bad ACL can't
lock you out of a machine.

**Picking up jobs.** A runner registers once and claims jobs for the agents it can run:

```bash
id=$(subzero agent start --harness firstmate --name crew)
subzero claim --as claude --session "$id"     # prints the job as JSON, or nothing
subzero done <job id> --result "PR #15"       # or --failed
```

A job is never left hanging. If a session ends before `subzero done`, its job is marked
**failed** with the reason. If a session stops reporting for a whole day (it crashed, or the
machine went to sleep for good), its job is marked failed the same way, and you can dispatch it
again.

To feed firstmate, run a small loop on the Hetzner box that claims a job and passes its goal to
the first mate's herdr pane with `herdr pane send-text <pane> "<goal>"`.

**Queuing a command (agents:exec).** A chat client holding the `agents:exec` scope can queue one
command for your machine (`agent_run "uv run pytest -q"` with a `worktree` like
`subzero/cli-exec`) or an interactive session (`agent_open`). The server only stores the job: the
runner on your machine claims it and is the only piece that touches a shell. The worktree must
exist under one of your `SUBZERO_EXEC_ROOTS` (by default `~/Code` and `~/Code/worktrees`);
anything else is refused before it is queued. Grant `agents:exec` by name in
`SUBZERO_CLIENT_SCOPE_POLICY`, only to your own CLI and harness clients: the `"*"` shorthand
deliberately excludes it, so a wildcard policy can never hand a chat app a shell. Results come
back through the same finish path as any job: an exit code plus the last 16 KB of output, and a
non-zero exit is a failure even when the output looks fine.

**Running the queued jobs (the runner).** `agent_run` and `agent_open` jobs are executed by
the runner on the target machine:

```bash
subzero agent run --as crew --once                 # claim at most one job, then exit
subzero agent run --as crew --loop --interval 2    # keep claiming until Ctrl-C
subzero agent run --as crew --pane w5W:p2          # where goal jobs with a space go
subzero agent run --as crew --open                 # goal jobs also open the repo via herdr-axi
subzero agent run --as crew --dry-run              # show the plan for queued jobs; claim nothing
```

The machine needs `tmux-axi` and `herdr-axi` on its PATH: every shell interaction goes
through those two CLIs, and the runner itself never invokes `tmux`, `herdr` or a raw shell.
It refuses to claim without `tmux-axi` installed, refuses to run as root, and needs a
registered session (`subzero agent start`) to claim with.

What each job kind does:

| Kind | The runner |
| --- | --- |
| `command` | Runs the command with `tmux-axi run` in the job's `cwd` and finishes with the real exit code plus the labelled output tail. A non-zero exit is `done` with that code: it is data, not a runner failure. Only a command that could not be run at all (tool missing, refused folder, timeout, unparseable output) is `failed`. |
| `session` | Opens `tmux-axi open` in a terminal window, session `subzero-<first 8 of the job id>`; when no window could be launched the detached session stays and the result says so. `first_command` is *typed* into the session for the human to review: Enter is sent only when the text ends with a newline, and the runner never executes it. |
| `goal` | Never executed. Without a `space` the runner prints the job for a human (and with `--open` opens the repo with `herdr-axi open`). With a `space` it submits the goal text to the one herdr pane holding an agent (`--pane` wins; an ambiguous pane list fails naming the panes rather than guessing). An `FM_TASK_ID` refusal from herdr-axi is surfaced verbatim. |

**Local re-validation.** The server resolved the `worktree` into a `cwd` under its own
`SUBZERO_EXEC_ROOTS`; the runner does not trust that word and re-validates the `cwd` on
arrival against its own `SUBZERO_EXEC_ROOTS` (set them on the runner machine too): it must
exist, be a directory, resolve inside a root, and not escape through a symlink. Cloud and
laptop do not trust each other's validation, the same discipline the Obsidian sync worker
applies to vault paths. A job the runner cannot place is finished `failed` with the reason,
and nothing runs.

## 6. Lessons where you type the important part

In a lesson folder (`^zig`, `^cfa2`, ...) the master prompt tells the agent to write the
scaffolding but leave the lines that teach the idea as `TODO(you): ...`, then run:

```bash
subzero agent status waiting "Type the comptime List(T) fn in src/list.zig:30"
```

(The prompt gives each session its id; it adds `--session <id>` so two sessions in one folder
never answer for each other.) The board shows a **Your turn** banner. Open the file in Neovim, type it, then tell the agent
"done". It checks your code, explains it, and carries on.

## 7. The board

```bash
subzero board
```

It opens `http://127.0.0.1:7777`: agents arrive at the dock, walk to their project or lesson room,
show **Your turn** when a lesson waits on you, and walk to the vault when their session ends, where
new notes land on the shelf. Queued jobs sit in their room with the agent they're for and how it
was chosen (`target`, `jev 0.82`, or `default`).

| Key | Does |
| --- | --- |
| `/` | focus the command bar |
| `d` | start a dispatch (`/d goal`) |
| `m` | start a message (`/m to text`); click an agent to fill its id |
| `/ship goal` | dispatch and make the change |
| `?` | help |

The board runs only on your machine (127.0.0.1) and never shows your device token to the page.

## 8. Where a note goes

| Kind of note | Where it goes |
| --- | --- |
| Something you or Claude should find later | The vault, through Subzero. The conversation is already in the database. |
| A job or a message for another agent | The board (`subzero dispatch`, `subzero msg`) |
| A list | A list app. Subzero does not keep lists. |
| Scratch from an agent run | The temporary run folder `scratch-run` creates under `~/Code/.scratch`. It is not memory. |

Herdr (tabs, panes, worktrees) is driven from the terminal. The browser extension captures chats
and can open a bridge session with a CLI when you ask it to. It does not click herdr on its own.
A worker on the always-on machine can pass a claimed job into a herdr pane with
`herdr pane send-text`.

## When something goes wrong

| You see | What it means | Fix |
| --- | --- | --- |
| `Not connected. Run: subzero connect ...` | This machine has no device token. | Step 1. |
| `this device is not paired for agents` | The code was made without `--agents` (or it's the extension's token). | `subzero pair-device --agents`, then `subzero connect` again. |
| `That pairing code is wrong or expired` | Codes last a few minutes and work once. | Make a new one with `subzero pair-device`. |
| No master prompt in Claude Code | The hooks aren't installed, or the session started before you installed them. | `subzero hooks install`, then a new session. Check `~/.cache/subzero/hook-errors.log`. |
| `No session for this folder` | `agent status` couldn't tell which session you mean. | Run it inside the folder the session started in, or start one with `subzero agent start`. |
| `Unknown target 'x'` | `>x` isn't an agent subzero knows. | Use one of `claude codex grok gemini pi me crew`. |
| The board says `can't reach subzero` | The server is down or the Mac is asleep. | Wake the Mac or start the server; the board reconnects on its own. |
| `Port 7777 is busy` | Another board (or app) is using it. | `subzero board --port 7778`. |
