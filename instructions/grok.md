# Grok setup

## 1. Connect the MCP server

Open **https://grok.com/connectors** (the Grok chat site, not your xAI account page at
accounts.x.ai, which only lists sign-ins and apps authorized to your account). Click
**New Connector → Custom**, then paste `https://<your-host>/mcp`.

Grok opens a browser window for OAuth. Sign in with the Google account on your
allowlist and approve the consent screen. Grok then discovers the tools the
server exposes.

**Grok handles the sign-in automatically through your server.** You don't need to enter any client ID or secret. Just paste the URL and sign in with Google.

Connectors and the custom MCP option are on Grok's paid tiers.

## 2. Persistent instructions

Grok's custom-instruction field (**Settings → Customize**). Paste this text (the same as `instructions/shared.md`):

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

## 3. Skills

Grok doesn't support skills files. The instruction you pasted in step 2 is enough: it tells Grok when to use its new tools.

## Turn off built-in memory (recommended)

Export your history first ([docs/guides/09-import-your-history.md](../docs/guides/09-import-your-history.md)), then:
Settings → **Customize** or **Data controls**: turn off *Memory* / *Personalize with memories*.
Subzero then becomes this app's only long-term memory, shared with the other three.

## 4. Surfaces and limitations

| | |
| --- | --- |
| Transport | Streamable HTTP |
| Auth | OAuth (your server handles sign-in automatically) |
| Tools | all 53 |
| Tier | paid tiers only |

**Three separate products.** You only need the Grok web app connector (grok.com). The API and developer tools are separate and not covered here. xAI has more than one surface that connects to MCP servers: the Grok web app's Connectors, and the xAI API's remote MCP
tools. Their setup does not carry over; adding the connector in the web app
does not make it available to API calls, and vice versa. This document covers
the web app, which is what shares context with the other three assistants.

**Attachment limitation.** Grok's export does not reliably include generated
images. Artifacts that matter should be captured during the conversation;
anything only named is recorded as `pending_import`.
