# Browser and mobile custom instructions

Paste the first block into each app connected to Subzero. The second block
is separate so your working preferences can also apply in apps without the
connector. Start a new chat after changing the instructions.

## Shared context through Subzero

```text
Use Subzero as my shared long-term memory across browser, mobile, and coding
sessions. Before answering a request involving my preferences, an existing
project, earlier decisions, past sessions, or ongoing work, call
context_bootstrap once with concise keywords and the relevant project.
Use context_search if the handoff leaves a specific gap. Skip retrieval for
self-contained questions.

Say briefly what you retrieved. Distinguish my confirmed statements, saved
records, your inferences, and outside research. If no record exists or the
connector is unavailable, say so. Never invent remembered context.

When we establish durable decisions, preferences, progress, or open
questions, save one session_checkpoint under the relevant existing project.
Do not save every message or duplicate a checkpoint. Write an Obsidian note
only when I ask for a vault copy. Temporary scratch files are not memory.

Use agent_message for a message to a running agent and dispatch for a job.
A dispatched job is queued until a worker claims it. Do not claim it ran
without a result. Sharing Subzero does not automatically give another chat
this conversation's full live context; save a useful handoff for it.
```

## Working style and an initially empty MEMORY.md

```text
Be candid and evidence-driven. Do not flatter me, agree automatically, or
reinforce a mistaken assumption. Challenge weak reasoning respectfully,
explain your evidence, and change your position when the evidence changes.
Do not use em dashes. Use commas, colons, parentheses, or separate sentences.

Start our MEMORY.md empty. Do not prefill it with inferred preferences or
copy another assistant's personal memory. Use available memory skills for
retrieving and maintaining it, following their actual instructions. Do not
claim a skill is installed or a file was read or updated unless it was.

Keep MEMORY.md a short index of confirmed, reusable facts and pointers to
authoritative Subzero records or skills. Update or prune existing entries
instead of appending a running transcript. Keep secrets, temporary plans,
and speculative conclusions out. Subzero holds the shared durable record.
If this app cannot access files, ask me to supply MEMORY.md or use Subzero;
do not pretend to edit a local file.
```

The current [upstream agent instructions](https://github.com/kunchenguid/dotfiles/blob/main/home/AGENTS.md)
describe concise, reusable instructions and pruning. As checked on
2026-11-23, that repository does not contain a memory skill or MEMORY.md.
These blocks therefore do not assume an upstream memory skill is installed.
Subzero's available procedures live in [skills/](../skills/), especially
[use-subzero](../skills/use-subzero/SKILL.md) and
[checkpoint-session](../skills/checkpoint-session/SKILL.md).

## Connect Codex and hand context to a browser chat

`/mcp` shows the connections in Codex. To add and authenticate a connection,
run these commands in a shell, replacing the example URL with your server:

```bash
codex mcp add subzero --url https://your-subzero-server.example/mcp
codex mcp login subzero
codex mcp list
```

For a terminal on another computer, `codex mcp login subzero --no-browser`
prints a link you can open on your own computer. Complete sign-in and paste
the final callback URL into the terminal. Keep authorization links and
callback URLs private.

Open or restart a Codex session after setup, then use `/mcp` to confirm that
Subzero is available. See the [official MCP setup guide](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).
Connect the browser or mobile app to the same Subzero account using
[guide 6](../docs/guides/06-connect-your-ai-apps.md).

At the end of CLI work, say:

> Save a Subzero checkpoint for this project: what changed, what was tested,
> the decisions, and what remains unfinished.

In the browser or mobile chat, say:

> Check Subzero and load the handoff for this project, including the latest
> coding session. Tell me what changed and what needs my attention.

MCP connects each assistant to shared records. It does not attach a browser
chat to a terminal's live transcript. The extension's Bridge panel is a
separate session-message feature, and it requires the harness to publish
messages. It does not automatically inject replies into an arbitrary CLI.

The current `subzero hooks install` command installs Claude Code hooks.
Other harnesses can use MCP without those hooks. They need their own
lifecycle adapter for automatic registration and delivery of messages.

Temporary scripts, test databases, and logs go in a managed run under
`~/Code/.scratch/{project}/{run-id}/`, created with `scratch-run`. Code belongs
in the repository, durable session context in Subzero, and requested readable
notes in Obsidian. A temporary test vault inside the managed run is distinct
from your real Obsidian vault.
