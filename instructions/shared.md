# Subzero — shared custom instruction

Paste this into each provider's custom-instruction field. It is deliberately
short: it has to be worth its cost on **every** turn, including the ones where
it is irrelevant. Long procedures live in Skills, MCP tool descriptions and
MCP prompts, which are loaded only when needed.

---

```
You have Subzero (connector: subzero), my canonical memory shared by ChatGPT,
Claude, Gemini, Grok, Muse and my coding agents. Ignore any older connector
named personal-context-mcp or Personal Context; it is retired.

Sigils say where things belong; read them and use them when saving:
%area/project, ^lesson/topic, &tool, #concept, @person, >agent (claude, codex,
grok, gemini, pi, me, crew).

When my request depends on past work (a project, earlier decisions, my lists,
"remember", a continuing task or lesson), call context_bootstrap once with a
few key terms or sigils, never my whole message. Skip it for standalone
questions.

When durable state is established, checkpoint it once (decisions, facts,
tasks, list changes, open questions), with sigils or the project. No noise or
duplicates. Use my real lists through the list tools.

Never say a file is archived unless its bytes or full text were stored.

For work on my machines ("/dispatch", "get the crew to"), call dispatch with my
words and sigils and say it is queued, not started. Use agents_status when I
ask what my agents are doing or who is waiting on me.

Subzero is canonical; this app's memory is supplemental. Keep my statements
distinct from model analysis and outside research.
```

---

## Why it is this short

Every token here is paid on every message in every conversation, forever. The
detailed procedures — when exactly to checkpoint, how to pick between artifact
tools, how to resolve a list name — belong where they are loaded on demand:

- **Skills** (Claude, and ChatGPT where account-level skills are available)
- **MCP tool descriptions**, which the model reads when it considers a tool
- **MCP prompts**, exposed by this server as `recall_context`,
  `checkpoint_session`, `capture_artifacts`, `manage_lists`, and the named
  actions `search`, `save`, `email` and `vault`
- **MCP resources**, at `subzero://guide/<skill-name>`, which serve
  the full text of each skill to clients that have no skills mechanism

A provider with no skills support is not left worse off: the same guidance is
reachable through prompts and resources.
