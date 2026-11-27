# Troubleshooting

Start here: **`uv run subzero check`** tests your settings, database, storage and
classifier, and lists **every** problem it finds at once, in plain words. Most answers below begin
with it.

Find what you're seeing below. Each guide also ends with a table for its own step.

## Named actions: email and vault

Browser capture returning HTTP 400 means its payload was rejected. An unknown
provider, non-text message content, or non-list `messages`/`attachments` must be
corrected by updating the extension. Captures and their original message times
are stored through the same canonical writers as imported conversations.

| You see | What to do |
| --- | --- |
| `AgentMail is not configured` | Leave email in `dry_run=true`, or configure the two `SUBZERO_AGENTMAIL_*` settings using [daily use](guides/12-daily-use.md#optional-email-setup). Restart the server. |
| No `email` tool appears | Grant the client `email:send` in `SUBZERO_CLIENT_SCOPE_POLICY`, then reconnect it. Read-only clients cannot send. |
| No `run_cli_on_mbp` tool appears | `cli:run` is not included in `"*"`. Add `"cli:run"` to that client's entry in `SUBZERO_CLIENT_SCOPE_POLICY`, restart the server, then remove and re-add the connector so it refreshes the tool list. |
| `Resolve recipients explicitly` | Supply the full address confirmed by the user, not a name or display-name address. |
| `send_at must be in the future with an explicit timezone` | Ask for a timezone and choose a future time. Do not silently send immediately. |
| `Confirmation does not match the preview` | Read the existing draft, show it and get confirmation again. |
| `That email key already names a different draft` | For an intentional content change, create a new draft and review it. For a retry, use the original ID and confirmation. |
| `AgentMail did not return a usable receipt` | Retry the same draft. Do not invent a new key or assume the email failed to send. |
| `Email delivery is uncertain` | The safe retry window has expired. Reconcile the sending inbox in AgentMail before taking further action. |
| `The approved send time has passed` | Check the original schedule in AgentMail if a send was attempted. For an unsent draft, review and confirm a new time. |
| `This sensitivity level cannot be sent to AgentMail` | The content's privacy level is blocked by `SUBZERO_NO_EXTERNAL_SENSITIVITY`. Keep it local; do not lower the level just to send it. |
| `Home must be projects/{slug}, lesson/{category}, setup/{tool}, or other` | Use a home in that format for CLI options, API calls, or the first line of a `.subzero` file. |
| `A project home must match an existing project` | Use `project_search` to find the existing project slug or select `other`. |
| `This object already has a different vault path` | No second copy was queued. Keep the original home, or relocate the managed file and update its tracked path before retrying. Files are not auto-moved. |
| Vault result is `busy`, `failed` or `conflict` | The note is not confirmed written. Inspect `sync_status` and the helper's log; preserve human edits when resolving conflicts. |

## Installing

| You see | What it means | Fix |
| --- | --- | --- |
| `command not found: uv` | The Terminal hasn't noticed the new program. | Close the Terminal window, open a new one, try again. |
| `command not found: subzero` | You ran it without `uv run`, or outside the project folder. | `cd ~/subzero`, then `uv run subzero ...`. |
| `Cannot connect to the Docker daemon` | Docker Desktop isn't running. | Open the Docker Desktop app, wait for it to say *running*, try again. |

## Settings

| You see | What it means | Fix |
| --- | --- | --- |
| `.env.local already exists` from `init` | The wizard won't overwrite your settings by accident. | Rename the old file (`mv .env.local .env.old`) or run `init --force`. |
| `Refusing to start. Fix the configuration problems above` | A setting is missing or unsafe. | The lines above it say exactly which; `check` shows them too. |
| `SUBZERO_AUTH_MODE=static is refused when SUBZERO_ENVIRONMENT=prod` | Test settings are being used online. | Run `init` again and choose `online`. |
| `SUBZERO_ALLOWED_SUBJECTS must list the account(s)...` | No email is allowed in. | Add your email to `SUBZERO_ALLOWED_SUBJECTS` in `.env.local`. |
| `PC_ environment variables are no longer read` from `check`, or the server refuses to start | This project was called personal-context-mcp and its settings started with `PC_`. That prefix is gone rather than silently ignored, so a stale name cannot quietly drop a setting. | Rename each `PC_NAME` to `SUBZERO_NAME` in `.env.local`, your 1Password Environment, `deploy/*.env.op` and Vercel. The message names every variable it found. |
| `command not found: personal-context-mcp` | The command is now `subzero`. | Use `uv run subzero ...`. Re-run `deploy/install-worker.sh` and `deploy/install-gemini-sync.sh` so background jobs use the new name. |
| `{"error": "not found"}` from the extension or `subzero agent` on a Mac server | The device reached the Mac through its public (Funnel) address without Tailscale. Those APIs are tailnet-only. | Turn Tailscale on for that device ([guide 14](guides/14-run-on-your-mac-over-tailscale.md)). |
| `SUBZERO_HARNESS_API_KEY is required for bridge commands` | The CLI has no capability token for the browser bridge. | Add `SUBZERO_HARNESS_API_KEY` to `.env.local`, restart the server, and run the bridge command again. |

## Browser session bridge

| You see | What it means | Fix |
| --- | --- | --- |
| `Bridge request failed with status 401` | The harness token is missing or does not match the server. | Check that `SUBZERO_HARNESS_API_KEY` is identical in the CLI and server environment. |
| The Bridge tab says `Not paired` | The browser has no device token. | Pair the extension with `uv run subzero pair-device`, then paste the code into the extension popup. |
| The Bridge tab stays empty | The session has no messages, or the server has not applied the bridge migration. | Run `uv run subzero migrate`, then append a message with `bridge-message`. |

## Database

| You see | What it means | Fix |
| --- | --- | --- |
| `database: cannot connect (InvalidPasswordError)` | The password in the connection string is wrong. | Reset it in Supabase (**Project Settings → Database**) to letters and numbers only, update `SUBZERO_DATABASE_URL`. |
| `database: cannot connect (gaierror / timeout)` | The address can't be reached. | Use Supabase's **Transaction pooler** string, not *Direct connection*. |
| `N pending migrations` | New database changes haven't been applied. | Run `uv run subzero export-lists --out DIR` first: migration 0020 drops the old `lists` and `list_items` tables and only task items are carried over. Then `uv run subzero migrate`. |
| Everything stopped working after a week of not using it | Supabase paused the free project. | Resume it in the Supabase dashboard. |
| An app says *Subzero's database schema is behind this server's code* (Gemini may show only `INVALID_REQUEST`, ChatGPT `INVALID_ARGUMENT`) | The server runs newer code than the database: a migration was not applied before the restart. Your arguments are fine. | Run `subzero migrate` with the server's settings. The server notices within half a minute; `/readyz` turns back to `{"ready":true}`. See [guide 14, Update it](guides/14-run-on-your-mac-over-tailscale.md#update-it). |
| `/readyz` answers `{"ready": false, "reason": "migrations_pending", "pending": N}` | N migrations in the code are not in the database. The log line `database schema is behind this server` names them. | Same: `subzero migrate`. |

## Online (Vercel)

| You see | What it means | Fix |
| --- | --- | --- |
| A Vercel login page at `/healthz` | Vercel's own protection is on. | `vercel project protection disable subzero --sso` |
| `500` / *Serverless Function has crashed* | The server refused its settings. | `check` locally; fix `.env.local`; `deploy/vercel-env.sh .env.local`; `vercel deploy --prod`. The **Logs** tab on vercel.com shows the reason. |
| Build error mentioning `README.md` | An old copy of the project. | `git pull`, then deploy again. |

## Connecting the AI apps

| You see | What it means | Fix |
| --- | --- | --- |
| `redirect_uri_mismatch` | Google doesn't know your server's address. | Add `https://<your-address>/auth/callback` to the Google client ([guide 3](guides/03-google-sign-in.md)). |
| *has not completed the Google verification process* | The Google sign-in app is still in Testing. | Publish it ([guide 3](guides/03-google-sign-in.md), step 3). |
| Sign-in succeeds, then *access denied* | That Google account isn't allowed. | Add its email to `SUBZERO_ALLOWED_SUBJECTS`, push settings, redeploy. |
| Connected, but no tools show up | The app didn't finish setting up the connector, or the server isn't answering the way apps expect. | Run `uv run subzero probe https://<your-address>/mcp`: any `FAIL` line names the broken step. If all checks pass, remove and re-add the connector and reload the page. |
| One app has fewer tools than the others | `SUBZERO_CLIENT_SCOPE_POLICY` grants that app fewer permissions; an app that matches no entry gets read-only tools. | Grant it more (`{"*":["*"]}` gives every app everything), push settings, redeploy, then remove and re-add the connector. |
| *Invalid or expired consent token*, or Approve leaves you on the `/consent` page | The approval page is single-use: it was already submitted, left open over 15 minutes, or (before the fix in this version) the browser blocked the hand-off to Google. | Close the sign-in window, then click **Connect** in the app again for a fresh page; click Approve once. If it persists, run `git pull` and redeploy. |
| Worked for a week, then asks to sign in again | The Google app is in Testing (7-day limit). | Publish it. |
| Gemini: *Account linking is required to use this custom app. Try again.* | Google finished your sign-in but the linking handshake did not complete. Most often you signed in with a Google Workspace (work/school) account, which Gemini refuses to link custom apps on, or one that isn't in `SUBZERO_ALLOWED_SUBJECTS`. | Remove the custom app in Gemini (Settings & help → Connected Apps → More details → Remove app), sign in to gemini.google.com as your **personal** Google account (the same one that's in `SUBZERO_ALLOWED_SUBJECTS` and that you used for the other apps), and add the custom app again with only the URL. Retry the whole flow; Google's side can also fail once transiently. |
| Gemini: *Client ID ... is not registered with this server* | Manual credentials were entered under Advanced features, and they aren't an MCP registration of this server. | Leave the credential fields empty (this server supports Dynamic Client Registration); remove the custom app in Gemini and add it again with only the URL. |
| Gemini's *Custom apps* option or the connector disappeared | *Gemini Apps Activity / Keep Activity* was turned off. | Turn it back on; custom apps need it. You can still turn off *past chats*. |
| The app never uses its memory unprompted | The custom instruction is missing. | Paste [instructions/shared.md](../instructions/shared.md) into the app's settings. |
| The app pastes your whole message into `context_bootstrap` | It is following an older instruction. | Paste the current [instructions/shared.md](../instructions/shared.md) again. Old calls still work: the server keeps only the key words and adds a note saying `user_message` is deprecated. |
| `context_read_guidance` returns `unavailable` | The server could not read the configured document. | On the Mac, check the checkout path and file permissions. On Vercel, confirm the source ref contains the file and the token can read that repository's contents. See [guide 14](guides/14-run-on-your-mac-over-tailscale.md#share-agent-guidance). |
| A chat's work doesn't show up when you start a new chat about that project | That session was saved without a project. | Ask the app to name the project when it checkpoints (`project` in `session_checkpoint`). Untagged sessions that mention the project by name still appear, marked as possibly belonging to it. |
| `No project matches '...'; the session was saved without one` | The app named a project that doesn't exist, or misspelled it badly. | Ask the app to call `project_list` and checkpoint again with the real name. Nothing is created automatically. |
| A package lists `conflicts` | A newer item on the same subject may overrule an older decision, and the server can't tell which one stands. | Tell the app which one is current; it can record that as a new decision. |
| An app says *Internal error in Subzero. Reference* followed by a code | Something failed inside the server that it does not explain to apps on purpose (details could include internal names). | Search the server log for that code: `grep <code> ~/Library/Logs/subzero/server.out.log` shows the full error and the tool. |
| An app says *Subzero's database took too long to answer* | A query timed out, usually a slow or overloaded database connection. | Retry. If it keeps happening, ask for fewer results (`limit`, `token_budget`) and check the database's health in Supabase. |
| An app says *Unexpected keyword argument* for `title`, `opening_message` or `conversation_id` on `context_bootstrap` | Your saved app instructions describe parameters this server does not have. | Remove that text from the app's custom instructions and use the current [instructions/shared.md](../instructions/shared.md): `context_bootstrap` takes `query` and `project`; `session_checkpoint` records the conversation. |

## Obsidian

| You see | What it means | Fix |
| --- | --- | --- |
| `Vault directory does not exist` | The folder in `OBSIDIAN_SYNC_VAULT_PATH` isn't there. | Create it or correct the path. |
| `worker rejected: check OBSIDIAN_SYNC_API_KEY` | Helper and server disagree on the shared key. | Make `OBSIDIAN_SYNC_API_KEY` equal `SUBZERO_WORKER_API_KEY`; push settings and redeploy if you changed the server's. |
| An edit of mine didn't show up in the apps | The helper wasn't running when you edited. | Start it; changed notes are pushed on its next pass. If the server also changed that note, the server's update is held back as a *conflict* and your edit stays in the vault. |
| An edit I made before upgrading the helper never came back | The helper's record of what it wrote started empty with this version. | Nothing is lost; that note is tracked from the next time the server rewrites it. |
| `vault write not queued; another job for this path won the race` in the server log | Two writes asked for the same file at the same instant; one of them is queued and will render the current content. | Nothing to do. If the file still looks stale afterwards, `uv run subzero wiki render` queues it again. |

## Gemini digest

See the table at the end of [guide 11](guides/11-gemini-without-custom-apps.md#common-problems).

## Memory lab

**`memory-lab recover` reports `sessions_seen: 0`.** No conversation has Jev answers
under the current question set. Live checkpoints carry answers from the moment they
are saved; for imported history run `subzero reclassify-conversations --limit 10000`.

**Every session is `log` with reason `no_triage_answers`.** Same cause: the stored
answers predate the triage questions.

**Recovered notes never reach Obsidian.** The Obsidian helper rejects folders it does
not know. Update it to this version and restart it, so it accepts `25-Areas`,
`Permanent` and `Experiments` ([guide 17](guides/17-memory-lab.md)).

**`memory-lab extract` reports sessions under `failed` and `errors`.** Claude answered
for those sessions but writing the result to the database did not finish, usually a
`TimeoutError` on a slow connection. The rest of the batch still landed. Those sessions
are not marked as extracted, so the next run offers them again, which means paying for
them a second time. If several fail in one batch, run a smaller `--limit` or set
`SUBZERO_EXTRACTION_CONCURRENCY=1` before retrying.

## Importing

| You see | What it means | Fix |
| --- | --- | --- |
| `Could not identify which provider produced this archive` | Unknown file layout. | Name the app: `import gemini file.zip`. For Gemini, export **JSON**, not HTML. |
| Many `classifier_degraded` | TypeSafe was unreachable. | Nothing is lost. `uv run subzero reclassify` later. |

## Saving files

| You see | What it means | Fix |
| --- | --- | --- |
| `The uploaded bytes do not match that SHA-256` | What arrived in storage is not the file the app described, usually an interrupted or repeated upload. | Ask the app to upload the file again. The artifact stays `pending_import` until it matches, so it is never reported as saved. |
| `Stored bytes at ... did not match the expected hash` | Storage kept something different from what Subzero sent. | Save again. The artifact is marked `failed`; check the storage service if it keeps happening. |
| An artifact with status `failed` | A save stopped part-way, for example storage went away between two files. | Save it again; the new save gets the next version. Files from the failed one stay listed, so you can see how far it got. |

## Background loops

Two loops run inside the server: the embedding refresh and the classifier intake. Each holds a row in
`background_leases` while it works, so if two servers share the database only one of them runs each
loop. A server that dies stops renewing and another takes over within a few intervals.

| You see | What it means | Fix |
| --- | --- | --- |
| `embedding refresh has failed N ticks running; backing off` | The embedding provider is refusing the calls: out of credit, a revoked key, or a rate limit. The loop has stopped asking every interval and is backing off, to an hour at most. | Check the key and the provider's billing. Nothing is lost: rows keep `embedding IS NULL` and are picked up once the calls succeed. Full-text search works throughout; `memory_deep_search` says it is running full-text only. |
| `embeddings refreshed` with `"failed": 64` but no warning | The first couple of failures, before the warning threshold. | Same fix as above if it keeps repeating. |
| Captures from the browser extension are never classified | `SUBZERO_AUTO_CLASSIFY_SECONDS` is `0` on every server that shares this database, so no loop is running. The captures are not lost: they are waiting in `reclassification_queue`. | Set it to `300` on the one server that should do the work, or run `uv run subzero reclassify-conversations` once by hand. |
| Classification stops after a Jev outage and resumes slowly | Each failed conversation gets an exponential `next_attempt_at`, up to an hour. | Expected. `uv run subzero reclassify-conversations` retries immediately if you do not want to wait. |

## Still stuck?

Open an issue using the **Setup help** template on GitHub. Include what you ran, what you expected and
what you saw, plus the output of `check`. **Never include your `.env.local` or any secret**; `check` doesn't
print any.

## Agents, hooks and the board

See [guide 15](guides/15-agents-messages-and-the-board.md#when-something-goes-wrong) for the full
table. The common ones:

| You see | What it means | Fix |
| --- | --- | --- |
| `Not connected. Run: subzero connect ...` | This machine has no device token. | `subzero pair-device` on the server, then `subzero connect <address>` here. |
| `The server refused this device` | The device token was revoked. | Connect again with a new pairing code. |
| `this device is not paired for agents` | The token can only save chats (the extension's kind). | `subzero pair-device --agents` on the server, then `subzero connect` again. |
| No master prompt in Claude Code | Hooks not installed, or the session predates them. | `subzero hooks install`; start a new session; read `~/.cache/subzero/hook-errors.log`. |
| `... is not valid JSON; fix it first` from `hooks install` | Your Claude Code settings file is broken. | Fix the JSON (nothing was changed), then install again. |
| `Started as scout: it touches the network ...` | A risky job was asked to ship. | Read the plan it reports, then dispatch again with `--ship`. |

## Browser extension

The full table is in [guide 13](guides/13-browser-extension.md#when-it-goes-wrong). The common ones:

| You see | What it means | Fix |
| --- | --- | --- |
| `Could not establish connection. Receiving end does not exist` (older versions) | The chat tab had no Subzero script, usually because it was open before the extension was installed or reloaded. | Update the extension (0.2 reconnects tabs by itself), or refresh the tab. |
| `Could not reach https://… Is the server running?` | Nothing answers at the paired address. The extension never falls back to another server. | Open the address plus `/healthz`; start the server. Waiting conversations are sent when it is back. |
| `… does not know that code` | The pairing code was made against a different database than the server uses. | Run `pair-device` with the server's settings (guide 14's installer prints the command) and check its `Database:` line. |
| `… did not accept this browser's token. Pair it again.` | The server does not know this browser. | Pair again; queued conversations are kept. |
| `No messages found` on Gemini, Grok or Muse | Their page layout changed. | Open an issue; Claude and ChatGPT are unaffected. |
