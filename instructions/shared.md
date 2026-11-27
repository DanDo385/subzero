# Subzero browser and mobile pre-prompt

Paste the same three lines into each connected app. They only tell the app to
call `context_bootstrap` at the start of every chat: your rules, voice,
memory pointer and machines come from the server, at the top of that result
(`subzero/CONTEXT.md`, `SOUL.md`, `AGENTS.md`, `MEMORY.md` and `MACHINES.md`
from your dotfiles; see
[guide 14](../docs/guides/14-run-on-your-mac-over-tailscale.md#share-agent-guidance)).
So there is one copy of the rules, and editing it in dotfiles changes every
app. The same text lives in dotfiles as `subzero/PRE-PROMPT.md`.

```text
Subzero (connector: subzero) is my shared memory; ignore personal-context-mcp.
At the start of every chat, call context_bootstrap once (add key terms when the request depends on past work). Its text begins with my context header, SOUL.md, AGENTS.md, MEMORY.md and MACHINES.md from my dotfiles: follow them.
If Subzero is not connected, say so in your first reply instead of guessing my preferences.
```

For a surface without the connector, paste your dotfiles `subzero/SOUL.md`
there instead: it is the voice part, and works without Subzero.

## Where to paste it

Field names move between releases. If a path below does not match, search the
app's Settings for "instruction".

| App | Field (as of Nov 2026) | Length limit | Does not apply in |
| --- | --- | --- | --- |
| ChatGPT | Settings → Personalization → **Custom instructions** | 1,500 chars on Free/Go, 5,000 on Plus and up | Projects (project instructions win), temporary chats unless personalization is switched on |
| Claude | your initials, bottom left → Settings → **Instructions for Claude** (older UIs: Profile → "What personal preferences should Claude consider") | not published | conflicts with organization instructions on Team/Enterprise |
| Gemini | Settings & help → **Personal Intelligence → Instructions for Gemini** (older UIs: Saved info) | not published | Gems, work/school/supervised accounts |
| Grok | Settings → **Customize** | not published | free tier (connectors are paid) |
| Muse | its connector settings, or the start of a chat | not published | - |

Use project-level instructions when the app gives them priority over account settings.

## Why it is this short

The detailed procedures (when exactly to checkpoint, how to pick between
artifact tools, how a dispatch is routed, how to capture a file honestly)
belong where they load on demand:

- **Skills** (Claude, and ChatGPT where account-level skills are available):
  the directories under [`skills/`](../skills)
- **MCP tool descriptions**, which the model reads when it considers a tool
- **MCP prompts**, exposed by this server as `recall_context`,
  `checkpoint_session`, `capture_artifacts` and `use_subzero`, and the named
  actions `search`, `save` and `email`
- **MCP resources**, at `subzero://guide/<skill-name>`, which serve the full
  text of each skill to clients that have no skills mechanism

Where the app supports MCP prompts and resources, it can use them to load
the same detailed procedures on demand.
