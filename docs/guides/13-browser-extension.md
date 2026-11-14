# 13. The browser extension

The extension is the piece that lives in your browser. It does three jobs:

1. **Live capture** — while you chat in Claude, ChatGPT, Gemini, Grok or Muse, it saves the
   conversation into Subzero as you go, so nothing is lost when you close the tab.
2. **Import** — Claude and ChatGPT import your whole history through the same internal
   interface their own web pages use. Gemini, Grok and Muse can only save the conversation that
   is open on screen (see Step 4).
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

The extension talks to **one** Subzero server: the one you pair it with. A pairing code only
works on the server that shares its database, so the code has to come from the same settings
the server runs with.

1. Make sure the server is running. For the Mac setup in
   [guide 14](14-run-on-your-mac-over-tailscale.md), open its address followed by `/healthz` in
   the browser; it should say `ok`.
2. On the computer that runs the server, in the project folder, make a code with the server's
   own settings:
   - **Mac over Tailscale (guide 14):** the install script printed the exact command on its
     `Browser:` line. Run that.
   - **Vercel (guide 5):** `op run --env-file=deploy/admin.env.op -- uv run subzero pair-device`
3. The command prints three lines. Check them before you go on:

   ```text
     Pairing code: ABCD2345
     Server:       https://your-mac.your-tailnet.ts.net
     Database:     subzero on this computer (/Users/you/subzero/.data/pg)
   ```

   **Database** must be the database your server uses. If it names a different one (for
   example a cloud database when your server runs on your Mac), the code will be refused.
4. Click the Subzero icon in the browser toolbar. Enter the **Server** address and the code,
   then press **Pair this browser**. The browser asks once for permission to talk to that
   address; choose **Allow**.

The code expires after five minutes; if it does, run the command again.

**Success looks like:** the popup shows *Saving to https://your-…* at the top and your five
providers below it.

## Step 3: Live capture

Open any of the five apps and chat as usual. The extension notices when a reply finishes and
saves the conversation by itself. You do not have to do anything per message.

Each provider row in the popup shows the last conversation **the server confirmed**: its title,
how many messages it has, how many were new, and the time. That line only appears after the
server answers, so it is proof the conversation reached your database. Under the list:

- **Save Now** — save the conversation in the tab you are looking at, right now.
- **Pause / Resume** — stop or restart automatic capture everywhere.

Conversations travel through a small queue inside the browser and are sent straight away. If
the server is asleep or offline, they wait and are retried with growing gaps (30 seconds, then
a minute, up to an hour). The popup shows how many are waiting, and the error the server gave.
Anything the server refuses outright, or that keeps failing, is kept under *could not be sent*
with a **Retry** button: nothing is thrown away.

## Step 4: Import your history, then stay in sync

Import brings in the conversations from before the extension was installed. After that,
the extension keeps your database current by itself.

1. In the popup, click **Open All Tabs**. One background tab opens per provider. Sign in to any
   you are not signed in to.
2. Click **Import All**, or **Import** on a single provider's row to do just that one.
3. The row counts up while it works: *Import running: 120 checked, 95 saved, 25 already up to
   date*. **Keep that provider's tab open until it says *Import at 14:05*.**
4. To stop an import, press **Stop** on that row (the Import button turns into Stop while
   one runs). It finishes the conversation it is on, then stops. Nothing saved is lost, and
   pressing **Import** later carries on where the database left off.

How it decides what to fetch: before starting, the extension asks your server which
conversations it already has and when each was last updated. It walks your history newest
first and fetches only what is new or changed, so a second import is quick.

**Staying in sync:** every two minutes, each open Claude or ChatGPT tab checks for
conversations that changed since the server last saw them, newest first, and stops at the
first one that is already up to date. When nothing changed, that is a single small request.
The row then shows *Sync at 14:07: 3 checked, 1 saved, 2 already up to date*. Keep one
Claude and one ChatGPT tab open (pinned tabs are handy) and new chats reach your database
within a few minutes.

Notes:

- Import fetches about one conversation per second, and slows down by itself if the app asks
  it to (rate limiting). A history of a few thousand conversations takes an hour or more; that
  is normal. You can keep using the browser meanwhile.
- **Claude and ChatGPT** import and sync every conversation in your account.
- **Gemini, Grok and Muse** have no history import. **Import** (and **Save Now**) save only the
  conversation open on screen, and an open tab is re-saved every two minutes if it changed.
  These three are read from the page itself, so a redesign of their site can break them until
  the extension is updated.

**If you'd rather stop everything at once:** press **Pause** in the popup (stops automatic
saving and sending), or close or reload the provider tab (ends an import running in it).

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
| *Could not reach https://… (Failed to fetch). Is the server running?* | Nothing answered at that address. Open the address followed by `/healthz`; start the server (guide 14: `deploy/install-tailnet-server.sh`) and check the Mac is awake and on Tailscale. Waiting conversations are sent when it is back. |
| *… does not know that code* | The code was made with different settings from the server's, or it expired. Run the command from Step 2 again and check its **Database** line. |
| *Subzero needs permission to talk to …* | You chose *Deny* in the permission prompt. Press **Pair** again and choose **Allow**. |
| *… did not accept this browser's token. Pair it again.* | The server does not know this browser (it was revoked, or the server now uses a different database). Pair again; waiting conversations are kept and sent afterward. |
| *Open claude in a tab and sign in first* (or another provider) | Import needs a signed-in tab for that app. Click **Open All Tabs**, sign in, then retry. |
| *Subzero cannot reach the … tab* | Refresh that tab. If it persists, open the extension's **Details** and check *Site access* allows that site. |
| *No conversation ID found* | Open a saved conversation (one with its own address) and try again. |
| *No messages found* on Gemini, Grok or Muse | The page layout changed and the extension no longer recognises it. Please open an issue. |
| *Import stopped (tab closed?)* | The provider tab was closed or slept. Reopen it and press **Import**; what the database has is skipped. |
| *N could not be sent (…)* | The server refused those conversations; the reason is in brackets. Fix it, then press **Retry**. |
| *zsh: command not found: subzero* | Use the full command from Step 2, run inside the project folder. |

**Next:** [12. Daily use](12-daily-use.md) is where the captured conversations become useful.
