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
ChatGPT should know". Paste this text:

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
| Tools | all 48 |
| Write approval | ChatGPT confirms write actions |
| Mobile (iOS) | connectors configured on web are available in the app |

**Known rough edges.** Occasionally a connector disappears from the list.
If your tools stop working: remove and re-add the connector (30 seconds).
Your server is almost certainly fine; check `/healthz` to confirm.

**Attachment limitation.** As with every provider, an uploaded file that is
not delivered to tools as bytes is recorded with
`artifact_register_reference` and status `pending_import`.
