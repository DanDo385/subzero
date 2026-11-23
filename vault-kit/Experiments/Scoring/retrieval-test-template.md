---
date: YYYY-MM-DD
week: 1
---

# Retrieval test

Ask each query once per layer, in a fresh session, and score what came back.

- **Relevance:** 1 (nothing useful) to 5 (exactly what was needed).
- **Latency/tokens:** seconds to an answer, or tokens of context it pulled in.
- **Clarification needed?** Did you have to explain something the layer should have known?
- **Actionable?** Could you act on the answer without looking anything else up?

Layers: `memory_md` (MEMORY.md + USER.md + skills), `honcho`, `structured`
(Subzero memories, `Experiments/Structured/`), `vault` (PARA and Permanent notes,
searched through Subzero or Obsidian).

| Query | Layer | Relevance (1–5) | Latency/tokens | Clarification needed? | Actionable? | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| What am I working on this week, and what is blocked? | memory_md | | | | | |
| What am I working on this week, and what is blocked? | honcho | | | | | |
| What am I working on this week, and what is blocked? | structured | | | | | |
| What am I working on this week, and what is blocked? | vault | | | | | |
| How do I restart the Subzero server and check it is healthy? | | | | | | |
| Which landing rules apply to a repo I own versus a fork? | | | | | | |
| What did I decide about where the canonical database lives, and when? | | | | | | |
| How do I prefer explanations: format, length, jargon? | | | | | | |
| Which secrets manager do I use, and how do scripts read a key? | | | | | | |
| What was the last hard-won lesson about SQL filters? | | | | | | |
| Which open tasks are on my lists right now? | | | | | | |
| What did I learn last month about AI agent memory systems? | | | | | | |
| Which machine runs which services, and how do I reach it? | | | | | | |

Copy the first query's four rows for each of the others.
