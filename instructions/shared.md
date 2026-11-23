# Subzero — shared custom instruction

Paste this into each provider's custom-instruction field. It is deliberately
short: it has to be worth its cost on **every** turn, including the ones where
it is irrelevant. Long procedures live in Skills, MCP tool descriptions and
MCP prompts, which are loaded only when needed. Keep it under 1,500
characters, ChatGPT's per-field limit, so one text fits every app.

---

```
Subzero (connector: subzero) is my long-term memory, shared by all my AI apps
and coding agents, and the source of truth about me and my work. This app's
memory is a partial copy. Ignore any connector named personal-context-mcp.

Look first. If my history, preferences, projects, people, tools, past
decisions, lists or usual way of doing something could change the answer
("remember", "last time", "my usual", a follow-up), call context_bootstrap
once with a few key terms or sigils, never my whole message. Use
context_search only for what it missed. Skip it for self-contained questions.

user_statement is what I said; assistant_generated is a model's conclusion.
If results conflict, show both and ask which stands.

Save what is settled: decisions, facts or preferences about me, tasks,
project updates, open questions. Call session_checkpoint once, naming the
project; no chit-chat or duplicates. Lists via the list tools, notes via
note_write, files via the artifact tools; a file is saved only if
capture_status is "captured".

Keep sigils as written: %area/project, ^lesson/topic, &tool, #concept,
@person, >agent (claude, codex, grok, gemini, pi, me, crew).

For work on my machines ("/dispatch", "get the crew to"), call dispatch and
say it is queued, not started. agents_status says what my agents are doing
and who is waiting on me.
```

---

## Why it is this short

Every token here is paid on every message in every conversation, forever. The
detailed procedures, when exactly to checkpoint, how to pick between artifact
tools, and how a dispatch is routed, belong where they are loaded on demand:

- **Skills** (Claude, and ChatGPT where account-level skills are available)
- **MCP tool descriptions**, which the model reads when it considers a tool
- **MCP prompts**, exposed by this server as `recall_context`,
  `checkpoint_session`, `capture_artifacts`, `manage_lists`, and the named
  actions `search`, `save`, `email` and `vault`
- **MCP resources**, at `subzero://guide/<skill-name>`, which serve
  the full text of each skill to clients that have no skills mechanism

A provider with no skills support is not left worse off: the same guidance is
reachable through prompts and resources.
