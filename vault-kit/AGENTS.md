# Vault rules

Read this before you write anything in this vault. It applies to every AI and
every script, and to the person who owns the vault.

## Folders

| Folder | Holds | Who writes |
| --- | --- | --- |
| `00-Inbox/` | Valuable items whose home is unclear or that contradict a note. The only holding area. | Jev triage, session-capture, `inbox_add` from any connected app, any agent that is unsure |
| `20-Projects/` | Work with a goal and an end, one sub-folder per project | Subzero, the person |
| `25-Areas/` | Ongoing responsibilities with no end date | Claude (`memory-lab extract`) in the folder Jev chose, the person |
| `60-Resources/` | Topics and reference material | Claude (`memory-lab extract`) in the folder Jev chose, the person |
| `99-Archive/` | Finished or inactive projects and areas | The person, the weekly analysis |
| `Permanent/` | One idea per note. Flat: never make sub-folders here | Claude (resources Jev rated ready to stand alone), the person |
| `Experiments/Memory-Logs/` | One log per captured session or recovery run | session-capture, `subzero memory-lab recover` |
| `Experiments/Scoring/` | Retrieval tests | The person |
| `Experiments/Analysis/` | Recovery reports, memory candidates, weekly analyses | Subzero, the weekly analysis |
| `Experiments/Structured/` | Facts, instructions, events, tasks (generated) | `subzero memory-lab structured` |

`10-Daily`, `30-Research`, `40-Wiki`, `50-Concepts` and `70-Atlas` are Subzero's
existing rendered folders. Leave their layout alone.

## Where things go

Each layer gets what it is good at. Jev decides the route; nobody files by guess.

| What it is | Where it goes | Who writes it |
| --- | --- | --- |
| Used often and useful almost every session | `MEMORY.md`, or a skill if it is a procedure | The person, from `Experiments/Analysis/hot-memory-candidates.md` |
| A preference that drifts or a pattern over time | Honcho (`subzero memory-lab honcho-sync`) | Honcho, from the person's own messages |
| An explicit decision with a date and context | Structured memory (decisions), shown in `Experiments/Structured/` | Claude (`subzero memory-lab extract`) |
| A valuable session with a clear home | A note in its PARA folder | Claude, in the folder Jev chose |
| A one-off insight that stands alone | A `Permanent/` note, linked from its project or area | Claude or the person |
| Valuable, but the home is unclear or it contradicts a note | `00-Inbox/` only | Jev triage, `inbox_add` |

The Inbox is for decisions only a person can make, not for everything
uncertain. The way out is a person filing the item with `review_reclassify`,
or the weekly analysis, which is the only automatic way out.

## Classification

Jev (TypeSafe System One) is the classifier. Do not invent a free-text
classification when a Jev question can answer it. The questions and every
threshold are in Subzero's `src/subzero/classifier/questions.py`; the decision
policy is in `src/subzero/memorylab/triage.py`.

## Hot memory

- `MEMORY.md` stays at or under 2,200 characters. Sections: Current Projects,
  Conventions, Hard-won Lessons, Preferences. To add a line, remove or merge one.
- `USER.md` holds communication style, tools and working patterns.
- Promote into either only from `Experiments/Analysis/hot-memory-candidates.md` or a
  weekly analysis, never straight from a single session.

## Writing rules

- Plain Markdown. Frontmatter for anything a script reads.
- Notes Subzero writes carry `pc_id` in their frontmatter. Edit their body
  freely; Subzero picks up the edit. Do not change `pc_id`.
- Never delete a note to "clean up". Move it to `99-Archive/` or leave it in
  the Inbox with a comment.
- Nothing sensitive (health, money, legal, credentials) goes in the vault
  without the person asking.
