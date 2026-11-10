# 14. Agents, messages and the board

Every coding session you run (Claude Code in a worktree, a firstmate crew member, Codex) can
register with subzero, start with one shared master prompt, message the others, take jobs you
dispatch from your phone, and show up as a little figure on a live board on your Mac.

**Time:** about 15 minutes. **You need:** a running server ([guide 5](05-put-it-online.md) or
[guide 13](13-run-on-your-mac-over-tailscale.md)) and a terminal on each machine whose agents
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
| A session starts | registers it, then adds the **master prompt**: subzero's rules and sigils, your MEMORY.md and MACHINES.md, where you left off in this project or lesson, and any messages waiting |
| You send a prompt | marks it working and adds any new messages from other sessions |
| A turn ends | marks it idle (a lesson's "your turn" stays up) |
| The session ends | marks it done; on the board it walks to the vault |

A hook never blocks Claude Code: if the server can't be reached it prints nothing and writes the
reason to `~/.cache/subzero/hook-errors.log`. These hooks follow Claude Code's documented hook
format (JSON on stdin; text printed at session start or prompt time is added to the context).

**See the master prompt yourself:** `subzero prompt` (add `--query "^cfa2"` for a lesson).

## 3. Tell a folder what it is

Put a one-line `.subzero` file at the top of a repo or worktree with its sigils:

```bash
echo "^zig #comptime" > ~/Code/zig-lessons/.subzero
echo "%Code/portfolio-site" > ~/Code/portfolio-site/.subzero
```

Sessions started there land in that lesson or project, get its handoff, and receive messages sent
to `^zig` or `%Code/portfolio-site`.

## 4. Sigils, everywhere

| Type | Means | Goes to |
| --- | --- | --- |
| `%Code/portfolio-site` | project | that project (must already exist; it is never created for you) |
| `^cfa2/fixed-income` | lesson | home `lesson/cfa2`, concept `fixed-income` |
| `&tailscale` | setup or tool | home `setup/tailscale` |
| `#comptime` | concept | tag `concept/comptime` |
| `@handle` | person | tag `person/handle` |
| `>claude` `>codex` `>grok` `>gemini` `>pi` `>me` `>crew` | who does a job | that agent |

They work in chat apps, in Claude Code, in the CLI and on the board. When there's no sigil, Jev
decides; when Jev isn't sure, things go to the inbox (saves) or to `crew` (jobs).

## 5. Dispatch work, send messages

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

To feed firstmate, run a small loop on the Hetzner box that claims a job and passes its goal to
the first mate's herdr pane with `herdr pane send-text <pane> "<goal>"`.

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
was chosen (`sigil`, `jev 0.82`, or `default`).

| Key | Does |
| --- | --- |
| `/` | focus the command bar |
| `d` | start a dispatch (`/d goal`) |
| `m` | start a message (`/m to text`); click an agent to fill its id |
| `/ship goal` | dispatch and make the change |
| `?` | help |

The board runs only on your machine (127.0.0.1) and never shows your device token to the page.

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
