# 13. The browser extension

The extension is the piece that lives in your browser. It does three jobs:

1. **Live capture** — while you chat in Claude, ChatGPT, Gemini, Grok or Muse, it saves the
   conversation into Subzero as you go, so nothing is lost when you close the tab.
2. **Backfill** — Claude and ChatGPT import history through their internal APIs. Gemini, Grok
   and Muse capture the open conversation. Their page selectors need live verification as sites change.
3. **Session bridge** — it shows a live coding session from your terminal (any CLI harness that
   checkpoints to Subzero) in a side panel, and lets you type replies back from the browser.

**Nothing here changes or deletes anything in the apps themselves.** The extension only reads
what your browser already shows you, and sending the same conversation twice never creates
duplicates.

## Supported browsers

Any Chromium browser: **Brave** (recommended), Chrome, Chromium, Arc. Firefox is not supported
in this version.

## Step 1: Load the extension

The extension ships inside this project, in the `extension/` folder. It is loaded "unpacked",
straight from that folder — there is nothing to build and nothing to install from a store.

1. Open your browser's extensions page: `brave://extensions/` (or `chrome://extensions/`).
2. Turn on **Developer mode** (the toggle in the top-right corner).
3. Click **Load unpacked**.
4. Choose the `extension/` folder inside this project.

**Success looks like:** a "Subzero" card appears in the list, switched on, with no red *Errors*
button on it.

> If you change the extension's files later (or pull an update), click the small **reload**
> arrow on its card to pick the changes up. Refresh open chat tabs afterward. Import and Save
> Now also try reconnecting the capture script when a tab was open before installation.

## Step 2: Pair your browser

The extension talks to your Subzero server, and the server only accepts browsers you have
approved. Pairing is a one-time, eight-character code.

On the machine that runs Subzero, generate a code:

The server must be running first — `subzero serve`, or the `op run … serve` form below. If
nothing is listening, the popup reports *Failed to fetch* instead of a pairing error.

```bash
subzero pair-device
```

If you run Subzero from this folder instead of an installed command, use:

```bash
op run --env-file=deploy/admin.env.op -- uv run subzero pair-device
```

Then click the Subzero icon in your browser toolbar, paste the code, and press
**Pair this browser**. The code expires after five minutes; if it does, run the command again.

**Success looks like:** the popup stops showing the pairing box and shows your five providers
instead.

## Step 3: Live capture

Open any of the five apps and chat as usual. The extension notices when a reply finishes and
queues the conversation by itself. You do not have to do anything per-message.

The popup shows each provider's last confirmed server delivery, including the server address.
Older capture timestamps do not count as confirmed delivery. Two buttons
sit under the list:

- **Save Now** — capture the conversation in the tab you are looking at, right now.
- **Pause / Resume** — stop or restart automatic capture everywhere.

Captured conversations travel through a small queue inside the browser. If your server is
offline for a while, the queue holds them and retries; the popup shows how many are waiting.

## Step 4: Backfill your history

Backfill imports the conversations that happened *before* the extension was installed.

1. In the popup, click **Open All Tabs**. One background tab opens per provider. Sign in to any
   you are not signed in to.
2. Click **Backfill All**, or press **Import** on a single provider's row to do just that one.
3. Leave the tabs open while it works. Each row shows what it is doing: *Listing
   conversations…*, then *Queued N conversations*.

**Success looks like:** reopen the popup after the queue processes, normally within a minute
for one conversation. The provider row shows *Saved to server* and the intended server address.
Check the conversation in Subzero search. A queued count only confirms browser storage.
An ingest error appears on the provider row and remains visible in the service worker console.

Notes:

- Backfill is gentle on purpose: about one conversation every two seconds, so the apps do not
  rate-limit you. A large history can take a while; that is normal.
- Claude and ChatGPT list every conversation through their own internal interface, so backfill
  can walk available history. Gemini, Grok and Muse capture only the currently open conversation.
  Open each saved conversation and use Save Now. A missing conversation ID or unrecognized
  page layout produces an error that needs provider-specific investigation.
- Running a backfill twice is safe — already-saved messages are skipped.

## Step 5: The session bridge

The side panel connects a browser tab to a live coding session.

1. Right-click the Subzero toolbar icon and choose **Open side panel** (or open it from the
   extensions page).
2. Switch to the **Bridge** tab.
3. Paste the session link or code your coding harness gave you, and press **Connect**.
4. The conversation appears newest-last and refreshes every few seconds. Type in the box at the
   bottom and press **Send** to reply back into the session.

The **Handoff** tab next to it shows your current project handoff text, with a copy button —
the same text your coding sessions read when they start.

## What the extension can see

- It reads pages on `claude.ai`, `chatgpt.com`, `gemini.google.com`, `grok.com` and `muse.ai`
  only. It does not touch any other site.
- It stores its queue and settings inside the browser's own extension storage, and sends
  conversations only to your Subzero server.
- It never downloads your chats to files on disk.

## When it goes wrong

| What you see | What it means, and what to do |
| --- | --- |
| *Could not load icon … Could not load manifest* | The `extension/icons/` folder is missing. Pull the latest version of this project and reload the extension. |
| The popup only shows the pairing box | The browser is not paired yet, or its pairing was revoked. Run `subzero pair-device` again and re-enter the code. |
| *Device token expired or revoked. Re-pair the browser.* | The server forgot this browser. Pair again. |
| *No claude tab open* (or another provider) | Backfill needs a signed-in tab for that app. Click **Open All Tabs**, sign in, then retry. |
| *Receiving end does not exist* or *Refresh the … tab and allow Subzero site access* | Reload the extension and refresh the provider tab. In extension Details, allow access to that provider site. The extension attempts script recovery on Import and Save Now. |
| *No conversation ID found* | Open a saved conversation with its own URL and try again. If the error persists, the provider extractor needs an update. |
| *No confirmed delivery yet* | Capture has not received a server acknowledgement. Check the queue count, the Pause setting, and any delivery error. |
| A row stuck on *Importing…* | The tab went to sleep or was closed. Reopen the provider tab and press **Import** again; already-saved messages are skipped. |
| *zsh: command not found: subzero* | You ran the pairing command outside the project. Use the `op run --env-file=…` form from Step 2, or install the command first. |
| Pairing command fails with a database connection error | The database settings are not loaded. Use the `op run --env-file=deploy/admin.env.op -- …` form, which supplies them. |

**Next:** [12. Daily use](12-daily-use.md) is where the captured conversations become useful.
