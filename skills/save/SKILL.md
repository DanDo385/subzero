---
name: save
description: Save durable context from this chat when the user says "save this", "remember this session", or asks to preserve decisions, facts or tasks for future chats.
---

Call `session_checkpoint` once with a concise summary and any durable decisions,
facts, or tasks. Do not send list updates. A list app manages lists. Use your
actual app or harness as `provider` and reuse this conversation's ID when
available. Preserve accurate fact provenance. Find the existing project before
naming or creating one.

Choose exactly one `home`: `projects/{existing-project-slug}`, `lesson/{category}`,
`setup/{tool}` (for example `setup/neovim`), or `other`. Use the main subject as
the home and pass each additional concept as a name in `concepts`; the server
stores `concept/{name}` tags. Supply `project` for a project home. If uncertain,
use `other` rather than inventing a project. Keep an existing session's home
unless the user changes its subject.

Set `queue_for_vault=false` for shared-memory saving. An explicit vault request
uses `note_write` as described by the vault skill. Report the checkpoint result,
including any degraded classification or unresolved project match.
