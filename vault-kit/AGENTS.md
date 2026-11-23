# Vault rules

Read this before you write anything in this vault. It applies to every AI and
every script, and to the person who owns the vault.

## Folders

| Folder | Holds | Who writes |
| --- | --- | --- |
| `00-Inbox/` | Everything not yet confidently classified. The only holding area. | Jev triage, session-capture, any agent that is unsure |
| `20-Projects/` | Work with a goal and an end, one sub-folder per project | Subzero, the person |
| `25-Areas/` | Ongoing responsibilities with no end date | Jev triage (high confidence), the person |
| `60-Resources/` | Topics and reference material | Jev triage (high confidence), the person |
| `99-Archive/` | Finished or inactive projects and areas | The person, the weekly analysis |
| `Permanent/` | One idea per note. Flat: never make sub-folders here | Jev triage (ready-permanent only), the person |
| `Experiments/Memory-Logs/` | One log per captured session or recovery run | session-capture, `subzero memory-lab recover` |
| `Experiments/Scoring/` | Retrieval tests | The person |
| `Experiments/Analysis/` | Recovery reports, memory candidates, weekly analyses | Subzero, the weekly analysis |
| `Experiments/Structured/` | Facts, instructions, events, tasks (generated) | `subzero memory-lab structured` |

`10-Daily`, `30-Research`, `40-Wiki`, `50-Concepts` and `70-Atlas` are Subzero's
existing rendered folders. Leave their layout alone.

## Where things go

| What it is | Where it goes |
| --- | --- |
| Used often and useful almost every session | `MEMORY.md`, or a skill if it is a procedure |
| A preference that drifts or a pattern over time | Honcho |
| An explicit decision with a date and context | A structured memory or a `Permanent/` note |
| A one-off insight worth keeping | A `Permanent/` note, linked from its project or area |
| Anything Jev rated medium or low, or whose relation to existing notes is unclear | `00-Inbox/` only |

The weekly analysis is the only automatic way out of `00-Inbox/`. Everything
else leaves it by hand.

## Classification

Jev (TypeSafe System One) is the classifier. Do not invent a free-text
classification when a Jev question can answer it. The questions and every
threshold are in Subzero's `src/subzero/classifier/questions.py`; the decision
policy is in `src/subzero/memorylab/triage.py`.

## Hot memory

- `MEMORY.md` stays at or under 2,200 characters. Sections: Current Projects,
  Conventions, Hard-won Lessons, Preferences. To add a line, remove or merge one.
- `USER.md` holds communication style, tools and working patterns.
- Promote into either only from `Experiments/Analysis/memory-candidates.md` or a
  weekly analysis, never straight from a single session.

## Writing rules

- Plain Markdown. Frontmatter for anything a script reads.
- Notes Subzero writes carry `pc_id` in their frontmatter. Edit their body
  freely; Subzero picks up the edit. Do not change `pc_id`.
- Never delete a note to "clean up". Move it to `99-Archive/` or leave it in
  the Inbox with a comment.
- Nothing sensitive (health, money, legal, credentials) goes in the vault
  without the person asking.
