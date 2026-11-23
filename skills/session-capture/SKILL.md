---
name: session-capture
description: >
  Close out a working session for the memory-lab experiment: save what was
  settled to Subzero (Jev triages it), append a structured log to
  Experiments/Memory-Logs/, optionally tell Honcho, and park anything
  uncertain in 00-Inbox/. Use when the person says "capture this session",
  "log this", or at the end of a substantial working session.
---

# Session capture

Three to five minutes, at the end of a session that produced something. Skip it
for small talk and one-off lookups.

## 1 · Save the settled state

Call `session_checkpoint` with what was settled: decisions, confirmed facts,
tasks, open questions, and the project if there is one. Subzero asks Jev the
memory-lab triage questions in the same request, and the answer comes back as
`classification.triage` (outcome, gate, candidates and `scores`). Copy
`worth_processing`, `primary_intent`, `para_bucket`, `relation_to_existing` and
`confidence_gate` from `scores` for step 2.

Do not classify the session yourself. If the tool is unavailable, leave the
`jev_scores` fields empty and say so in the log.

## 2 · Write the log

Copy `Experiments/Memory-Logs/_template.md` to
`Experiments/Memory-Logs/YYYY-MM-DD-<short-slug>.md` and fill it in:

- `session_id`: the app or harness and its session id (`claude-code:<id>`, `chatgpt:<id>`).
- `tools`: what was used (CLIs, MCP servers, apps).
- `decisions`: one line each, as decided.
- `pain_points`: what was slow, wrong or had to be explained again.
- `memory_layers_touched`: which of `memory_md`, `user_md`, `skills`, `honcho`,
  `structured`, `vault` the session read from or wrote to.
- `jev_scores`: from step 1.

Under "What memory helped", be specific: which file or tool supplied what. This
is the evidence the weekly analysis scores.

How to write the file:

- **Agent with the vault on disk** (Claude Code, Codex on the vault's machine):
  write the file directly.
- **Chat app connected to Subzero:** call `note_write` with `note_class` `inbox`,
  the title `Session log YYYY-MM-DD <slug>` and the filled template as the
  content. It lands in `00-Inbox/`; the weekly analysis moves it.

## 3 · Tell Honcho (optional)

If a Honcho tool is available, send it the decisions and pain points as one
message from the person's side. Never send anything sensitive.

## 4 · Park what is uncertain

Anything you are not sure how to file (an idea with no clear home, a fact that
may contradict an existing note) goes to `00-Inbox/` through `note_write` with
`note_class` `inbox`, one note per item, with a line saying why it is uncertain.
Never file it elsewhere "for now".

## Do not

- Edit `MEMORY.md` or `USER.md`. Add candidates to the log instead; the weekly
  analysis promotes.
- Create folders. The vault rules are in `AGENTS.md` at the vault root.
