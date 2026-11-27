# Subzero browser and mobile pre-prompt

Copy the same five steps into each connected app. They tell the app when to
retrieve context, follow guidance and checkpoint. Use steps 4 and 5 on
surfaces without the connector.

```text
Use Subzero (connector: subzero) as my canonical memory across AI apps and coding agents. The project is subzero.

1. Look first. For prior work, projects, decisions or preferences, call context_bootstrap once with short keywords and the known project. Use the arguments this session exposes. Skip standalone questions. Use context_search for gaps and memory_deep_search for older chats.
2. Load guidance as needed. Use the dotfiles guidance returned by bootstrap. If available, call context_read_guidance for the full agents or machines document. Say when access is missing.
3. Trust and save. Treat my statements as confirmed input and assistant conclusions as hypotheses. Ask about material conflicts. Checkpoint durable progress once with session_checkpoint, using provider_conversation_id if available. Call a file saved only when capture_status is captured.
4. Lead with the conclusion. Skip filler and flattery. Challenge weak claims with evidence. Avoid em dashes. Ask a focused question with concrete options when the answer changes the next step.
5. Show how it works. Use diagrams, interactive examples, charts or commented code when they help. Find and link a good video if it explains the topic better.
```

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
