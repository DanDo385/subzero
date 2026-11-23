# ChatGPT setup

## 1. Enable developer mode

**Settings → Security and login → Developer mode**, switch it on. ChatGPT labels it an elevated-risk
setting because it allows connectors OpenAI hasn't reviewed; this one is your own server.

(Older ChatGPT versions had it under **Apps & Connectors → Advanced settings**. The location moves
from time to time; if neither matches, search Settings for "Developer mode".)

## 2. Add the server as a plugin

**Settings → Plugins → +** (the plus button), then supply:

- **Name:** Subzero
- **Description:** My shared memory, projects, lists and artifacts
- **Connection:** the MCP server URL, `https://<your-host>/mcp` (the `/mcp` path is required)
- **Authentication:** OAuth, if asked

Create it, sign in (Approve, then Google), and check the list of discovered tools.

To use it, start a new chat and add **Subzero** from the tools menu (**+** under the message
box). ChatGPT only uses a custom plugin in chats where it's switched on.

ChatGPT connects over public HTTPS only. There is no stdio option, and a
server on your laptop will not work without OpenAI's own tunnel, which is why
the server is put online (docs/guides/05-put-it-online.md) rather than run locally.

## 3. Custom instructions

**Settings → Personalization → Custom instructions**, into "Anything else
ChatGPT should know". Paste this text (the same as `instructions/shared.md`):

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

## Turn off built-in memory (recommended)

Export your history first ([docs/guides/09-import-your-history.md](../docs/guides/09-import-your-history.md)), then:
Settings → **Personalization → Memory**: turn off *Reference saved memories* and *Reference chat history*. Custom instructions stay on.
Subzero then becomes this app's only long-term memory, shared with the other three.

## 4. Skills / plugin packaging

If your account has account-level Skills, upload the directories under
`skills/`: at minimum `recall-context`, `checkpoint-session`, `manage-lists`,
`capture-artifacts`, `project-artifacts` and `research-to-obsidian`.
To check: go to Settings. If you see a "Skills" section, upload the folders
listed above. If you don't see it, skip this section: your custom instruction
covers the same ground.

## 5. Surfaces and limitations

| | |
| --- | --- |
| Transport | standard web (works automatically) |
| Auth | OAuth (DCR supported) |
| Tools | all 53 |
| Write approval | ChatGPT confirms write actions |
| Mobile (iOS) | connectors configured on web are available in the app |

**Known rough edges.** Occasionally a connector disappears from the list.
If your tools stop working: remove and re-add the connector (30 seconds).
Your server is almost certainly fine; check `/healthz` to confirm.

**Attachment limitation.** As with every provider, an uploaded file that is
not delivered to tools as bytes is recorded with
`artifact_register_reference` and status `pending_import`.
