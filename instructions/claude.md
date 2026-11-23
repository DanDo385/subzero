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
Paste this text (the same as `instructions/shared.md`):

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
| Tools | all 53 |
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
