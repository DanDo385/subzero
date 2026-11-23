# Weekly analysis prompt

Paste everything below the line into an AI that can read this vault (Claude Code
or Codex opened in the vault folder, or a chat app connected to Subzero). Save
its answer as `Experiments/Analysis/weekly-YYYY-MM-DD.md`.

---

You are reviewing a four-layer memory experiment. Read, in this order:

1. `AGENTS.md` (the rules; follow them).
2. Every file in `Experiments/Memory-Logs/` dated in the last 7 days.
3. `MEMORY.md` and `USER.md`.
4. Honcho's conclusions about me, if you can reach Honcho (otherwise the newest
   `Experiments/Analysis/honcho-snapshot-*.md`).
5. Everything in `00-Inbox/`, including each draft's `jev_*` and `triage_*` frontmatter.
6. `Experiments/Analysis/hot-memory-candidates.md` and `Experiments/Structured/structured-memories.md`.
7. `Permanent/` notes changed in the last 7 days.
8. `Experiments/Scoring/` files from this week.

Then answer with exactly these sections. Cite the file for every claim.

## Patterns useful in two or more layers
What showed up in at least two layers and actually helped a session.

## Where one layer clearly won
Situations, with the query or session, where one layer beat the others. Use the
retrieval scores when there are any.

## Worth promoting
Unique insights to turn into a `Permanent/` note, a `MEMORY.md` line or a skill.
For each, give the exact text and where it goes. Respect the 2,200-character
limit on `MEMORY.md`: say what to remove to make room.

## Inbox decisions
For every draft in `00-Inbox/`: keep (and where to), merge (with what), or
delete. A draft can only leave the Inbox through this list.

## Contradictions and stale items
Anything that disagrees between layers, or is out of date. Say which version
looks right and why, and ask me where you cannot tell. Do not pick for me.

## Keep, drop, merge
One line per layer: keep, drop or merge, and why, based on this week's evidence.

Do not edit any file. I will apply the changes.
