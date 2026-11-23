# 16 · Memory lab

**Time:** about 30 minutes to set up, then 20 minutes a week.
**You need:** guides 08 (Obsidian) and 09 (import your history) done, and Jev
turned on (`SUBZERO_CLASSIFIER=jev`).

The memory lab does two things:

1. **Recovers your old sessions.** Jev reads each imported conversation once and
   answers nine typed questions about it. Subzero turns those answers into one of
   four outcomes: a note, a draft in your Inbox, a line in a log, or nothing.
2. **Lets you compare memory layers.** For four to six weeks you capture
   sessions, run a short retrieval test, and do a weekly review. At the end you
   know which layers actually helped you.

Nothing here edits your existing notes, and nothing is deleted.

## The folders

Subzero creates these the first time it writes to them:

| Folder | What goes in it |
| --- | --- |
| `00-Inbox/` | The only place for drafts Jev was not sure about. You empty it. |
| `20-Projects/` | Work with a goal and an end. |
| `25-Areas/` | Ongoing responsibilities with no end date. |
| `60-Resources/` | Topics and reference material. |
| `99-Archive/` | Finished or inactive things. |
| `Permanent/` | One idea per note, flat, no sub-folders. |
| `Experiments/Memory-Logs/` | One log per capture or recovery run. |
| `Experiments/Scoring/` | Retrieval tests. |
| `Experiments/Analysis/` | Recovery reports, memory candidates, weekly reviews. |
| `Experiments/Structured/` | Facts, instructions, events and tasks, generated. |

These are the PARA folders (Projects, Areas, Resources, Archive) kept at
Subzero's existing numbers, so the notes you already have stay where they are.
To use other names, set `SUBZERO_OBSIDIAN_VAULT_ROOTS` (see `.env.example`)
**before** anything has been written to them.

## Step 1 · Install the starter files

The starter files are the rules, templates and prompts. Copy them into your vault;
`-n` never overwrites a file you already have:

```bash
cp -Rn vault-kit/ ~/Obsidian/YourVault/
```

You get `AGENTS.md` (the vault rules, read by any AI that opens the vault),
`MEMORY.md` and `USER.md` (empty starters), the log template, the retrieval test
and the weekly analysis prompt.

## Step 2 · Let Jev read your sessions

Jev asks its questions while classifying. After an upgrade, every conversation
is classified under the new question set the next time you run:

```bash
uv run subzero reclassify-conversations --dry-run      # how many
uv run subzero reclassify-conversations --limit 50     # try a few
uv run subzero reclassify-conversations --limit 10000   # the rest
```

**What success looks like:** a JSON summary with `"errors": []`.
**This costs Jev credits**, one request per conversation. Run the 50 first and
look at the result in step 3 before you run them all.

## Step 3 · Preview, then recover

```bash
uv run subzero memory-lab recover            # dry run: counts only, writes nothing
uv run subzero memory-lab recover --apply    # writes notes, drafts and the report
```

The output is the recovery report:

```json
{ "sessions_seen": 50, "notes_created_directly": 6, "parked_in_inbox": 14,
  "logged_only": 21, "discarded": 9, ... }
```

How each session is decided (first rule that matches wins):

| Jev said | Outcome |
| --- | --- |
| Jev was unreachable | log only, asked again later |
| `worth_processing` under 0.6, or bucket `discard` | discard if Jev was sure, otherwise log only |
| `confidence_gate` low | log only |
| gate high, Jev at least 70% sure of the bucket, not `unclear` or `duplicates` | **note** in that bucket; a resource Jev is 90% sure stands alone goes to `Permanent/` |
| bucket `project` but no matching Subzero project | **draft in `00-Inbox/`** (Subzero never creates a project from a guess) |
| anything else worth keeping | **draft in `00-Inbox/`**, with Jev's bucket as a suggestion |

Sessions marked private or highly sensitive never get a vault note. They are
counted in the report and never listed.

A session with a confident preference, procedure or decision is also listed in
`Experiments/Analysis/memory-candidates.md`. Being listed changes nothing by
itself: you (or the weekly review) decide what goes into `MEMORY.md`, `USER.md`
or a skill.

Running `recover --apply` again only decides sessions that have new answers.
`subzero memory-lab report` shows the stored decisions.

## Step 4 · Structured memories

```bash
uv run subzero memory-lab structured
```

Writes `Experiments/Structured/structured-memories.md` from what Subzero already
stores: facts, instructions (your preferences), events (decisions with dates)
and open tasks. No model is called.

## Step 5 · The four layers

| Layer | Where | Who writes it |
| --- | --- | --- |
| Hot memory | `MEMORY.md` (2,200 characters at most), `USER.md`, skills | You, from the candidates list |
| Honcho | Honcho, through an agent that has it (for example Hermes with `memory.provider: honcho`) | The agent, as it talks to you |
| Structured | `Experiments/Structured/` and Subzero's memories | Subzero and Jev |
| Vault | PARA folders and `Permanent/` | Jev triage, then you |

Honcho is optional. To connect it, see `vault-kit/Experiments/Analysis/honcho.md`.

## Step 6 · Every week

1. Capture a few sessions with the `session-capture` skill (`skills/session-capture`).
2. Run one row of `Experiments/Scoring/retrieval-test-template.md` per layer.
3. Paste `Experiments/Analysis/weekly-analysis-prompt.md` into an AI that can read
   your vault, and save its answer as `Experiments/Analysis/weekly-YYYY-MM-DD.md`.
4. Act on its keep, drop and merge list. This is the only way anything leaves
   the Inbox automatically.

## Undo

Every change is reversible:

- Notes the recovery wrote are listed in the `memory_triage` table; the reverse
  SQL is at the top of `migrations/0015_memory_lab.sql`.
- The vault files are plain Markdown. Delete the folders above to remove them.

## When something goes wrong

**`recover` says `sessions_seen: 0`.** No conversation has answers under the
current question set yet. Run step 2.

**Everything lands in `log only` with `no_triage_answers`.** The answers came from
an older question set. Run `reclassify-conversations --limit 10000`; it picks up every conversation classified under an older set.

**Notes do not appear in Obsidian.** The Obsidian helper is not running or is on an
older version that does not know the new folders. Update and restart it (guide 08).
