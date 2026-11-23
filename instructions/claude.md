# Claude setup

## 1. Connect the MCP server

Claude web/desktop → **Settings → Connectors → Add custom connector**.

- **Name:** Subzero
- **URL:** `https://<your-host>/mcp` (or `/mcp/<server-id>` if you set one)

Claude discovers the OAuth metadata from
`/.well-known/oauth-protected-resource`, registers itself dynamically, and
opens a browser window for you to sign in with the Google account on your
allowlist. Approve the consent screen once.

Claude supports Dynamic Client Registration, so no client id needs to be
configured by hand.

## 2. Custom instructions

Settings → **Profile → What personal preferences should Claude consider**.
Paste this text:

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

> ⚠️ **Also paste this extra line after the shared instruction:**
>
> When you use Subzero, say briefly what you retrieved or saved, so I can tell memory from inference.

## 3. Skills

Claude supports Agent Skills. Install the workflow skills from `skills/`:

- Claude.ai → **Settings → Capabilities → Skills → Upload skill**, once per
  directory in `skills/`, or
- for Claude Code in this repository, they are already on disk under `skills/`

## Turn off built-in memory (recommended)

Export your history first ([docs/guides/09-import-your-history.md](../docs/guides/09-import-your-history.md)), then:
Settings → **Capabilities** (or **Memory**): turn off *Generate memory from chat history* and *Search and reference chats*. Keep the Subzero connector enabled.
Subzero then becomes this app's only long-term memory, shared with the other three.

## 4. Surfaces and limitations

| | |
| --- | --- |
| Transport | Streamable HTTP |
| Auth | OAuth 2.1 + PKCE, Dynamic Client Registration |
| Tools | all 48 |
| Prompts | supported, surfaced as slash-style commands |
| Resources | supported |
| Write approval | Claude asks before the first call to each write tool |
| Mobile (iOS) | connectors configured on web are available in the app; file bytes from the iOS share sheet are **not** passed to tools |

**Attachment limitation.** Files you attach in the Claude apps are visible to
the model but are not delivered to MCP tools as bytes. Claude will call
`artifact_register_reference` for them, which records that they existed with
status `pending_import`. Import your Claude export to fill in the bytes.

## For developers only

Skip this unless you're working on the subzero code itself.

Keep these separate from TypeSafe's official SDK skill, which is a development
aid for this repository, not a runtime workflow:

```bash
claude plugin marketplace add typesafe-ai/skills
claude plugin install typesafe@typesafe-ai
```
