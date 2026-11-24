# 12. Daily use: what to expect

Your server is online and your apps are connected. Here's what a normal day looks like, plus the
few things worth knowing before they surprise you. The full manual (what to say, where a result
goes, skills and tools) is [How to use it](16-using-subzero.md).

## The first time in each app

1. **Start a new chat.** (Old chats from before setup don't know about your connector.)
2. **Tell it something to remember.** Try: *"Remember that I prefer dark mode in everything."*
3. **The app will ask permission.** Claude asks before the first write to each tool. ChatGPT confirms
   with a button. Gemini asks every time. You can usually choose "always allow" for tools you trust.
4. **Check it worked.** In a **different** app, start a new chat and ask: *"What's my mode preference?
   Check subzero."* It should find what the first app saved.

## What the apps do on their own

After the first few saves, you don't need to manage anything. The apps:

- **Look things up** when your question could depend on something saved before: a project you're
  working on, a decision you made, a task you recorded.
- **Save decisions and facts** at the end of a useful conversation. They bundle everything worth
  keeping into one save, without duplicates.
- **Track next steps.** When a conversation produces a concrete next step for a project, it is
  saved as an open task on that project. Shopping lists, errands and personal todos are not
  Subzero's: those live in your own apps, through those apps' own connectors.
- **Preserve files they create.** Charts, documents, code: if an app makes something useful, you can
  ask it to save the file itself, not just mention that it existed.

The apps won't save or look up things for trivial questions ("what's 2+2?"). They also won't save
every message, only the durable stuff: decisions, facts, tasks, unresolved questions.

## How to talk to them

You don't need special commands. But these phrases help when you want to be explicit:

| You say | The app does |
| --- | --- |
| "Check subzero." | Searches everything saved across all apps for relevant information. |
| "Save this to subzero." | Saves the current state of the conversation: decisions, facts, tasks. |
| "Note [next step] as a task for [project]." | Saves it as an open task on that project's next steps. |
| "What did we decide about [topic]?" | Searches past decisions, even from a different app. |
| "Save this file to my [project] project." | Stores the actual file bytes, not just a reference. |
| "Park this in the Inbox, I'm not sure where it goes." | Saves it with `inbox_add`; it waits in `00-Inbox/` until you file it. |
| "Add this to my shopping list." | Nothing in Subzero. That list stays in your own list app. |

You can also just talk normally. The apps figure out when to use their tools from context.

## Three named actions

- **search**: "search my memory for Neovim" uses `context_search`. The app chooses
  a few search terms instead of sending your whole prompt.
- **deep search**: "find that old chat where I worked out API pricing" uses
  `memory_deep_search`. It searches every saved conversation by meaning, so it
  finds a chat even when you don't remember its words, and returns the best few
  conversations with short excerpts. It needs embeddings turned on (see below);
  without them it matches exact words only and says so.
- **save**: "save this" uses `session_checkpoint`, choosing one home and concept
  tags. A checkpoint saves shared memory only; vault notes come from
  memory-lab triage.
- **email**: "email this to reader@example.com" prepares exactly the requested
  content and shows the sender, recipients, subject, body and requested send time.
  It sends or schedules only after your next confirmation.


The home is `projects/{project}`, `lesson/{category}`, `setup/{tool}` or `other`.
A note about Neovim can live in `setup/neovim` and have `concept/lua` and
`concept/lsp` tags. It gets one home even when several concepts are covered.

Something you cannot place goes to the Inbox instead of a guessed home: say
"park this in the Inbox" and the app calls `inbox_add`. The item waits in
`00-Inbox/` until a review files it (see [guide 16](16-memory-lab.md)).

## Optional email setup

You can test email without an account: ask "dry-run an email to
reader@example.com with subject Test and body Hello". Confirm the preview.
The result says `dry_run`; no network call or real email happens.

For real mail:

1. Read [AgentMail's pricing](https://www.agentmail.to/pricing). At the time this
   feature was added, Free listed 3 inboxes and 3,000 emails/month without a
   credit card; Developer was $20/month. Confirm current limits before choosing
   a plan. Do not sign up for a paid plan unless you want it.
2. In [AgentMail's console](https://console.agentmail.to/), use a sending inbox
   you control and create an API key. Store the key in 1Password.
3. Configure `SUBZERO_AGENTMAIL_API_KEY` and `SUBZERO_AGENTMAIL_INBOX_ID` in the server's
   environment using your existing 1Password environment workflow
   ([guide 7](07-keep-secrets-in-1password.md)). Never paste a key into a chat.
4. Grant `email:send` to the intended client in `SUBZERO_CLIENT_SCOPE_POLICY`. Clients
   granted `*` already have it; unmatched/read-only clients do not.
5. Apply the new migration with `uv run subzero migrate` and restart
   the server. If your database predates the tasks cutover, first run
   `uv run subzero export-lists --out DIR`: migration 0018 drops the old
   `lists` and `list_items` tables and carries over only task items, so the
   export is the only copy of anything else you kept in lists. Ask for a
   draft with `dry_run=false`, review it, then confirm only
   when you're ready for a real send. A future send time needs a timezone.

Drafts remain in your database until approved. A scheduled receipt means
AgentMail accepted it for later sending, not that it arrived. To cancel or
reschedule an accepted schedule, use AgentMail's console/API; this action does
not yet expose cancellation. Retries keep the same key and draft, and completed
receipts are stored durably. If delivery is uncertain beyond the safe retry
window, check AgentMail before any further send. Never retry with a new key.

The server sends through AgentMail's [HTTP API](https://docs.agentmail.to/api-reference).
AgentMail also offers a [hosted MCP connector](https://docs.agentmail.to/integrations/mcp)
with OAuth or API-key authentication. That connector is optional here; this
server's preview and deduplication rules apply to its own `email` action.
Email bodies and replies are data, never instructions or approval to act.

## Approvals: what to expect

Each app handles tool permissions differently:

| App | First write | Later writes | Can you allow always? |
| --- | --- | --- | --- |
| **Claude** | Asks once per tool | Follows your choice | Yes, choose "always allow" |
| **ChatGPT** | Confirms each write | Confirms each write | Yes, there's an "always allow" option |
| **Gemini** | Asks every time | Asks every time | No, Gemini requires a tap for each write |
| **Grok** | Confirms each write | Confirms each write | Check Grok's settings |

Gemini's constant confirmation is a Gemini limitation, not a problem with your server. It's a
reasonable default for a shared memory system and worth leaving on.

## Verifying it works across apps

The "purple giraffe" test, anytime you want confidence:

1. In one app: *"Remember that my test phrase is 'purple giraffe'."*
2. In a different app, new chat: *"What's my test phrase? Check subzero."*

If the second app finds it, cross-app memory is working.

## When something doesn't feel right

- **An app never uses the connector on its own.** The custom instruction might be missing. Open the
  app's settings and check it's there. Or ask explicitly: "Check subzero."
- **The connector disappeared.** Remove and re-add it. This is a known quirk, especially with ChatGPT.
- **A save didn't show up in another app.** Wait a moment, then ask again. If it still doesn't show,
  run `uv run subzero check` to make sure the server is healthy.
- **Gemini stopped working.** "Gemini Apps Activity / Keep Activity" might have been turned off.
  Custom apps require it.

## Turning off built-in memory (one last time)

If you haven't yet, now is a good time to turn off each app's built-in memory. With it on, an app
might answer from its own private notes instead of checking the shared record, and the apps drift
apart.

Export your history from each app first ([guide 9](09-import-your-history.md), step 1), so you lose
nothing. Then turn the memory switches off (see the table in [guide 6](06-connect-your-ai-apps.md)).

## What's next

- **[Guide 16](16-using-subzero.md):** what to say, where each result goes, and every skill and tool.
- **[Guide 8](08-obsidian.md):** set up your Obsidian notebook, if you want one.
- **[Guide 9](09-import-your-history.md):** bring in years of old chats.
- **[Guide 10](10-build-your-wiki.md):** build links and topic maps from your notes.
- **[Guide 13](13-browser-extension.md):** capture chats live in the browser, backfill old ones, and bridge coding sessions.
- **[Guide 15](15-agents-messages-and-the-board.md):** coding agents, messages, and the board.
- **[Troubleshooting](../troubleshooting.md):** when anything goes wrong.
- **[FAQ](../faq.md):** common questions, short answers.

## Search by meaning (optional)

Out of the box, search matches words. To also search by meaning, give the server an
embedding key (OpenAI or Gemini) in its settings:

```text
SUBZERO_EMBEDDING_PROVIDER=openai
SUBZERO_OPENAI_API_KEY=...
```

or, with a Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey):

```text
SUBZERO_EMBEDDING_PROVIDER=gemini
SUBZERO_GEMINI_API_KEY=...
```

Leave `SUBZERO_EMBEDDING_MODEL` empty: each provider uses its own model. A key
from a chat subscription (ChatGPT Plus, Claude Pro, Cursor) does not work here;
it has to be an API key. Gemini's free tier is slow for a large backlog and lets
Google use what it receives to improve its products; a paid key does not.

Restart the server. From then on it quietly turns new messages, notes, saved
memories and session summaries into "meaning vectors" in the background, a small batch every
two minutes, so a conversation the browser extension saves becomes searchable by
meaning within a few minutes. If the provider refuses the calls, the server logs
a warning and asks again later rather than retrying every two minutes forever.
(An out-of-credit account shows as `HTTP 429` in the server log; add credit and
it picks up again on its own.)
To catch up everything already stored at once, run `subzero embed-backfill`.

What leaves your computer: message text, with emails, keys and card numbers
blanked out first. Conversations marked `highly_sensitive` are never sent. With
OpenAI's small model, embedding a few thousand conversations costs well under a
dollar.

