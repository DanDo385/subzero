---
name: use-subzero
description: >
  How to use the Subzero connector. Use for past history, preferences,
  projects, decisions, workflows, the vault, or a job for another agent.
  Also use it when you would otherwise say you have no memory of earlier chats.
---

# Use Subzero

Subzero is the memory shared by ChatGPT, Claude, Gemini, Grok, Muse, and the
coding agents. This app's own memory is supplemental. A connector named
personal-context-mcp is retired. Do not use it.

## When to call it

Use Subzero before you answer when the request depends on any of these:

- past history, "remember", "last time", "we decided", "continue"
- a preference, a person, or a way of working you would only know from before
- a project, a lesson, or an existing workflow
- a note that should land in the vault
- work that another agent should do

Skip it for a question that stands on its own (a calculation, a rewrite of text
in the message, a definition that needs no personal context).

## Start with one call

Call `context_bootstrap` once.

- `query`: a few key terms you choose (names, projects, topics). Never
  the whole message.
- `project`: the slug or name, when you know it. Otherwise leave it empty and
  read which project the server picked, and why.

The package is, in order: the project's handoff (recent summaries, open
questions, open tasks, active decisions), then items that match the query,
then raw transcript lines only if nothing else matched. `layers` says which
of those ran.

If that package does not answer the question, call `context_search` with a
sharper query. Use `project_get` when you only need one project's record.
Use `memory_search` for a saved fact. Leave message text out unless you need
someone's exact words.

Say what you found, including which assistant it came from. If nothing is
recorded, say so and answer from this conversation. If the tool errors, say
Subzero is unreachable and continue. Do not retry in a loop, and do not invent
a memory.

## Homes

Use the project name or slug for a project. Use the explicit `home` field
for a lesson or setup, such as `lesson/cfa2` or `setup/neovim`.

## Where the result goes

| The user wants | Do this |
| --- | --- |
| An answer from past work | Read the bootstrap package. Do not save. |
| This chat remembered later | `session_checkpoint` once: summary, decisions, facts, tasks (the project's next steps), open questions, and the project. |
| A note to reread in Obsidian | Vault notes come from memory-lab triage of your sessions, not from a chat tool. Something you cannot place goes to `inbox_add`: it lands in `00-Inbox/` and waits for a person to file it with `review_reclassify`. |
| Another agent to do the work | `dispatch` with their words and the agent as `target` if they named one. Say it is queued, not started. `agents_status` when they ask who is working or waiting. |
| A file kept | Store the bytes with the artifact tools. `artifact_register_reference` records that a file existed. Say "archived" only when the result says the bytes were captured. |
| A list | Stop. Shopping lists, errands and personal todos live in my own apps through their own connectors. There are no list tools here, and a markdown list in a reply is not a saved list. |
| Scratch, `/tmp`, or a local note | Not memory. Do not treat it as saved. |

Checkpoint when the thing is settled, not at the end of a long chat. Skip
small talk, a lookup that is finished in this message, and anything you
already checkpointed.

## Provenance

- `user_statement`: the user said it. Treat it as fact.
- `assistant_generated`: a model concluded it. Call it a hypothesis.
- `external_source`: research. Cite it as such.
- `imported_metadata`: filled in by an import. Weakest.

If two items conflict, show both with their dates. A decision marked
`superseded` has been replaced.

## Named guides

When the task is one of these, read that guide. The resource
`subzero://guide/<name>` serves every skill. A prompt exists for `search`,
`save`, `email`, `vault`, `use_subzero`, `recall_context`,
`checkpoint_session`, `capture_artifacts`, and `manage_lists` (underscores).
Skill names use hyphens.

| Guide | Task |
| --- | --- |
| `search` | "search my memory for X" |
| `save` | "save this" / "remember this session" |
| `vault` | "put this in Obsidian" |
| `email` | draft a message and send only after they confirm |
| `recall-context` | the retrieval steps in full |
| `checkpoint-session` | what belongs in one checkpoint |
| `capture-artifacts` | how to store a file honestly |
| `project-artifacts` | several files as one versioned artifact |
| `research-to-obsidian` | research worth rereading becomes a vault note |
| `update-project` | status, goals, decisions, and the activity log |
| `daily-review` | open tasks, unresolved questions, vault sync |
| `manage-lists` | say a list app manages lists; do not call the old list tools |
| `run-command` | run one exact command on this Mac with `run_cli_on_mbp`; report host, directory and exit code |
| `open-session` | open a terminal session there; the first command is typed for review, not run |
| `dispatch-work` | a described job for a coding agent, with a space or worktree |
