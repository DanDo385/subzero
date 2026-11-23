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

## Step 3 · Route every session

Each model does the part it is good at. Jev reads every session cheaply and
decides where it goes; only then do the expensive or external layers see it.

| Layer | What it holds | Who fills it | From which sessions |
| --- | --- | --- | --- |
| **Vault** | PARA notes and `Permanent/` | Claude, from the full transcript | Worth 0.8 or more, and Jev at least 70% sure of the folder |
| **Structured** | Facts, events (decisions), instructions (preferences), tasks | Claude, into Subzero's memories, decisions and lists | Every vault session, plus any worthwhile session with a decision, procedure or preference |
| **Honcho** | A model of you that tracks how your preferences change | Honcho, from your own messages with their original dates | Sessions that lean towards a preference or working pattern (60% or more) |
| **Hot memory** | `MEMORY.md`, `USER.md`, skills | You, weekly, from a ranked candidates list | Decisions on active projects, preferences, repeated procedures |
| **Inbox** | Drafts that need a person | Jev triage | Only valuable sessions whose folder is unclear or that contradict what you know |

```bash
uv run subzero memory-lab recover            # dry run: counts only, writes nothing
uv run subzero memory-lab recover --apply    # records the routing, writes Inbox drafts and reports
```

The output is the recovery report:

```json
{ "sessions_seen": 3191, "vault_notes_planned": 1681, "parked_in_inbox": 9,
  "logged_only": 1370, "discarded": 131,
  "layers": { "vault": 1681, "structured": 1787, "honcho": 354, "hot": 855, "inbox": 9 } }
```

How each session's place in the vault is decided (first rule that matches wins):

| Jev said | Outcome |
| --- | --- |
| Jev was unreachable | log only, asked again later |
| `worth_processing` under 0.6, or bucket `discard` | discard if Jev was sure, otherwise log only |
| `confidence_gate` low, or it duplicates something you have | log only |
| worth 0.8 or more, Jev 70% sure of the folder, relation `new` or `extends` | **vault note** in that folder |
| the same, but Jev torn between project, area and resource | **vault note**: in the project if the session belongs to a known Subzero project, otherwise in Resources (code decides, not you) |
| a clear resource that is one idea (a comparison, decision or research finding, not a procedure) and Jev is 95% sure stands alone | **`Permanent/` note**, written short and atomic |
| worth 0.8 or more but torn towards `discard` | log only |
| worth 0.8 or more, but Jev itself chose "inbox", or it contradicts or relates unclearly to what you have | **draft in `00-Inbox/`** |
| anything else worth processing | log only (it can still feed the structured, Honcho and hot layers) |

Sessions marked private or highly sensitive never reach the vault, Honcho or a
candidate list; they are counted, never listed.

Running `recover --apply` again only decides sessions that have new answers.
`subzero memory-lab report` shows the stored decisions.

## Step 4 · Let Claude write the notes and structured memory

```bash
uv run subzero memory-lab extract                       # estimate: sessions, tokens, dollars
uv run subzero memory-lab extract --limit 25 --apply    # do 25, best first
uv run subzero memory-lab structured                    # render Experiments/Structured/
```

Claude reads the whole transcript once per session. A vault session gets its
note written in the folder Jev chose (notes on the same subject merge into one);
a structured-only session gets its decisions, facts and tasks and no note.
**This costs money.** Look at the estimate first, and run it in batches.

## Step 5 · Feed Honcho (optional)

Set `SUBZERO_HONCHO_WORKSPACE`, `SUBZERO_HONCHO_PEER` and `SUBZERO_HONCHO_API_KEY`
to the same workspace and peer your agent already uses (see
`vault-kit/Experiments/Analysis/honcho.md`), install the extra, then:

```bash
uv sync --extra honcho
uv run subzero memory-lab honcho-sync                 # dry run: sessions, messages, characters
uv run subzero memory-lab honcho-sync --apply         # send, oldest first
uv run subzero memory-lab honcho-snapshot             # weekly: Honcho's conclusions into the vault
```

Only your own messages are sent, redacted, with their original dates. Each
session is sent once.

## Step 6 · Every week

1. Capture a few sessions with the `session-capture` skill (`skills/session-capture`).
   Run `memory-lab recover --apply`, `extract`, `honcho-sync` and `honcho-snapshot`
   so every layer has this week's sessions.
2. Run one row of `Experiments/Scoring/retrieval-test-template.md` per layer.
3. Paste `Experiments/Analysis/weekly-analysis-prompt.md` into an AI that can read
   your vault, and save its answer as `Experiments/Analysis/weekly-YYYY-MM-DD.md`.
4. Act on its keep, drop and merge list. This is the only way anything leaves
   the Inbox automatically.

## Undo

Every change is reversible:

- Every routing decision is a row in `memory_triage`; the reverse SQL is at the
  top of `migrations/0015_memory_lab.sql` and `0016_memory_lab_layers.sql`.
- Notes Claude wrote are marked `author = deep-extraction` with their source
  session; Honcho sessions are named `subzero-<app>-<session id>`.
- The vault files are plain Markdown. Delete the folders above to remove them.

## When something goes wrong

**`recover` says `sessions_seen: 0`.** No conversation has answers under the
current question set yet. Run step 2.

**Everything lands in `log only` with `no_triage_answers`.** The answers came from
an older question set. Run `reclassify-conversations --limit 10000`; it picks up every conversation classified under an older set.

**Notes do not appear in Obsidian.** The Obsidian helper is not running or is on an
older version that does not know the new folders. Update and restart it (guide 08).
