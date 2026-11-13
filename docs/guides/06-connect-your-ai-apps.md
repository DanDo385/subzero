# 6. Connect ChatGPT, Claude, Gemini and Grok

Each app is connected **once, on a computer, in its website**. After that the connection also works in
the app's phone version. Every app follows the same three steps:

1. **Add a connector** with your server's address followed by `/mcp`, for example
   `https://subzero.vercel.app/mcp`.
2. **Sign in with Google** in the window that opens, choosing the account whose email you gave the
   setup wizard.
3. **Paste the custom instruction** so the app knows when to use its new memory.

## Check the server first (1 minute)

Before adding it to any app, let your computer connect to the server the same way the apps do:

```bash
uv run subzero probe https://<your-address>/mcp
```

✅ It prints what the server answered at each step and ends with `All 7 checks passed.` That means
the apps can find the sign-in page and register themselves. If a line starts with `FAIL`, it says
which step broke; fix that before connecting any app.

## The custom instruction (same for every app)

Copy the text below and paste it into each app's custom instruction field:

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

This is the same text as [instructions/shared.md](../../instructions/shared.md). It's deliberately
short: the app reads it with every message. The detailed procedures live in Skills, tool descriptions
and MCP prompts, which are loaded only when needed.

## Moving from personal-context-mcp

If an app still shows a connector named **personal-context-mcp** (or **Personal Context**), remove it
and add a new one named **subzero** with the same server address. Then paste the instruction above
over the old one. Old saved memories stay: they live in your database, not in the connector.

## App by app

| App | Where to add it | Where the instruction goes | Needs | Full guide |
| --- | --- | --- | --- | --- |
| **Claude** | claude.ai → **Settings → Connectors → Add custom connector** | **Settings → Profile** → "What personal preferences should Claude consider" | any plan that offers custom connectors | [instructions/claude.md](../../instructions/claude.md) |
| **ChatGPT** | chatgpt.com → **Settings → Security and login → Developer mode** on, then **Settings → Plugins → +** | **Settings → Personalization → Custom instructions** | a plan with developer mode (Plus, Pro, Business, ...) | [instructions/chatgpt.md](../../instructions/chatgpt.md) |
| **Gemini** | gemini.google.com **on a computer** → **Settings & help → Connected Apps → Custom apps for Spark → Add a custom app** | **Settings & help → Saved info** | **Google AI Pro or Ultra** (not Plus), a *personal* Google account, Keep Activity on | [instructions/gemini.md](../../instructions/gemini.md) |
| **Grok** | **grok.com/connectors** (not your accounts.x.ai page) → **New Connector → Custom** | **Settings → Customize** | a paid Grok plan | [instructions/grok.md](../../instructions/grok.md) |
| **Muse** | **Settings → Connectors → Add custom connector**, or ask Muse in a chat to add one with your server address | its own settings, or the start of a chat | US or Canada; connects from Meta's cloud, so the server must be public | none yet |

The menus in these apps move around every few months. If a menu name doesn't match, look for
"Connectors", "Apps" or "Integrations" in the settings, and check the full guide for the latest notes.

> **Same account everywhere.** Step 2's sign-in works with any Google account, but only the email(s)
> in `SUBZERO_ALLOWED_SUBJECTS` get in, and Gemini additionally refuses custom apps for work/school
> (Workspace) accounts. Use one **personal** Google account for all four apps, and make sure it's
> the one you gave the setup wizard.

## Recommended: turn off each app's built-in memory

Each app also keeps its own private memory of you. Left on, it competes with Subzero: an
app may answer from its own stale notes instead of checking the shared record, and the four apps drift
apart. With it off, every app gets its memory from the same place.

**First, download your history** from each app ([guide 9](09-import-your-history.md), step 1), so
nothing is lost. Turning memory off doesn't delete your chats, but the export is what gets your past
conversations into Subzero.

| App | Where | Turn **off** | Keep **on** |
| --- | --- | --- | --- |
| **Claude** | **Settings → Capabilities** (or **Settings → Memory**) | *Generate memory from chat history* and *Search and reference chats* | the Subzero connector |
| **ChatGPT** | **Settings → Personalization → Memory** | *Reference saved memories* and *Reference chat history* | Custom instructions |
| **Gemini** | **Settings → Personal Intelligence** (older versions: **Personal context** or **Past chats**) | *Your past chats with Gemini* | **Gemini Apps Activity / Keep Activity**: custom apps stop working without it. Also keep Saved info, where your instruction lives. |
| **Grok** | **Settings → Customize** or **Data controls** | *Memory* / *Personalize with memories* | Custom instructions |

Optional: each app also has a switch that allows your chats to be used to train its models (Claude:
**Privacy → Help improve Claude**; ChatGPT: **Data controls → Improve the model for everyone**;
Grok: **Data controls → Improve the model**; Gemini: tied to Keep Activity, so leave it on). Turning
it off doesn't affect Subzero.

Menu names change often. If one doesn't match, search the app's settings for "memory" or "past chats".

## Check that it worked

In each app, start a new chat and try:

> Remember that my test phrase is "purple giraffe".

The app should say it saved something, and some apps ask you to approve the first save. Then, in a
**different** app:

> What's my test phrase? Check subzero.

✅ It answers "purple giraffe" and says where it found it. That's the whole point working: one app
learned it, another one knew it.

## What to expect day to day

- **Approvals.** Claude asks before the first use of each saving tool; ChatGPT confirms saves; Gemini
  asks every time it saves. You can usually choose "always allow" for the saving tools you trust.
- **Files you attach** in the apps are *seen* by the AI, but most apps don't hand the actual file to
  connectors. The app records that the file existed; import your chat export later
  ([guide 9](09-import-your-history.md)) to fill in the real file. Files the AI *creates*, like charts,
  code or documents, can be saved directly: just ask it to "save this to subzero".
- **Skills (Claude, and ChatGPT where available)** are optional add-ons with more detailed
  instructions. Upload each folder under [skills/](../../skills/) in the app's skills settings. The
  apps work without them.

For the four named actions, upload the `search`, `save`, `email` and `vault` skill
folders. The same names appear in the server's MCP prompt list; clients that show
prompts as slash commands can invoke `/search`, `/save`, `/email` and `/vault`.
You can always use the ordinary phrases in [daily use](12-daily-use.md).
Real email needs the optional AgentMail setup in that guide and the `email:send`
client scope. Enabling other saving tools does not grant that scope automatically,
unless your policy already grants `*` (all scopes).

## Common problems

| You see | Fix |
| --- | --- |
| The sign-in window says `redirect_uri_mismatch` | Your Google client's redirect URI doesn't match your address + `/auth/callback` ([guide 3](03-google-sign-in.md)). |
| Sign-in works, then *access denied* / *not allowed* | You chose a Google account that isn't in `SUBZERO_ALLOWED_SUBJECTS`. |
| ChatGPT has no *Apps & Connectors*, only *Plugins* | That's the newer layout. Turn on **Settings → Security and login → Developer mode**, then add the server under **Settings → Plugins → +**. |
| The connector was added but no tools appear | Run the check in [Check the server first](#check-the-server-first-1-minute). If it passes, remove the connector and add it again, or reload the page. ChatGPT especially sometimes needs this. |
| One app is missing some tools the others have (for example Gemini has no `note_write` or `review_list`) | `SUBZERO_CLIENT_SCOPE_POLICY` gives that app fewer permissions. An app that matches no entry gets read-only tools. Use `{"*":["*"]}` to give every app everything, push the settings, redeploy, then remove and re-add the connector. |
| Grok won't accept the address | Grok needs a paid plan; also make sure the address ends in `/mcp`. |
| Gemini has no *Custom apps for Spark* section | Custom apps need **Google AI Pro or Ultra**; Plus and free don't include them. Use [guide 11](11-gemini-without-custom-apps.md) instead. Also a personal (not work or school) Google account, Keep Activity on, a US English account, and a computer browser. |
| The app never uses it on its own | Check the custom instruction is saved, or just ask explicitly: "check subzero". |

**Next:** [7. Keep your secrets in 1Password](07-keep-secrets-in-1password.md) (optional), or
[8. Set up your Obsidian notebook](08-obsidian.md).
