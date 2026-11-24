---
name: save
description: Save durable context from this chat when the user says "save this", "remember this session", or asks to preserve decisions, facts or tasks for future chats.
---

Call `session_checkpoint` once with a concise summary and any durable decisions,
facts, or tasks: a project's next steps, not personal errands. Shopping and
errand lists live in my own list apps and have no parameter here. Use your
actual app or harness as `provider` and reuse this conversation's ID when
available. Preserve accurate fact provenance. Find the existing project before
naming or creating one.

Choose exactly one `home`: `projects/{existing-project-slug}`, `lesson/{category}`,
`setup/{tool}` (for example `setup/neovim`), or `other`. Use the main subject as
the home and pass each additional concept as a name in `concepts`; the server
stores `concept/{name}` tags. Supply `project` for a project home. If uncertain,
use `other` rather than inventing a project. Keep an existing session's home
unless the user changes its subject.

A checkpoint saves shared memory only; vault notes come from memory-lab
triage (`subzero memory-lab recover`), not from a checkpoint. Report the
checkpoint result, including any degraded classification or unresolved
project match.
