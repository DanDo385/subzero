# Subzero — shared custom instruction

Paste this into each provider's custom-instruction field. It is deliberately
short: it has to be worth its cost on **every** turn, including the ones where
it is irrelevant. Long procedures live in Skills, MCP tool descriptions and
MCP prompts, which are loaded only when needed.

---

```
Subzero (connector: subzero) is the memory shared by ChatGPT, Claude, Gemini,
Grok, Muse, and my coding agents. For past history, preferences, projects,
decisions, or an existing workflow, use Subzero before you answer. This app's
memory is supplemental. Ignore a connector named personal-context-mcp.

Call context_bootstrap once with a few key terms or sigils (%project, ^lesson,
&setup, #concept, @person, >agent). Do not paste my whole message. Skip
Subzero for a question that stands on its own.

When something should outlive this chat, checkpoint it once, with sigils or
the project. A note to reread goes to the vault with note_write. Work for
another agent is dispatch: say it is queued, not started. Lists live in a
list app. Scratch files are not memory.

The use-subzero guide is the procedure (prompt use_subzero, or resource
subzero://guide/use-subzero). Say what you found. My statements are facts.
Your conclusions are hypotheses. Never say a file was archived unless its
bytes were stored.
```

---

## Why it is this short

Every token here is paid on every message in every conversation, forever. The
detailed procedures, when exactly to checkpoint, how to pick between artifact
tools, and how a dispatch is routed, belong where they are loaded on demand:

- **Skills** (Claude, and ChatGPT where account-level skills are available)
- **MCP tool descriptions**, which the model reads when it considers a tool
- **MCP prompts**, exposed by this server as `use_subzero`, `recall_context`,
  `checkpoint_session`, `capture_artifacts`, `manage_lists`, and the named
  actions `search`, `save`, `email` and `vault`
- **MCP resources**, at `subzero://guide/<skill-name>`, which serve
  the full text of each skill to clients that have no skills mechanism

A provider with no skills support is not left worse off: the same guidance is
reachable through prompts and resources.
