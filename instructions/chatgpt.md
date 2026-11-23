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
