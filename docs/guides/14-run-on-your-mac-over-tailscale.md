# 14. Run it on your Mac over Tailscale

Instead of (or as well as) Vercel, your own Mac can run the server. Tailscale gives it a private
https address that only your devices can reach, and the server answers only while the Mac is awake.

**Time:** about 15 minutes. **You need:** a Mac where [guide 2](02-try-it-locally.md) already works,
your online settings from [guide 5](05-put-it-online.md), and the free
[Tailscale](https://tailscale.com/download) app signed in on the Mac and on your phone.

## Pick who can reach it

| Choice | Command | Who can connect | Use it for |
| --- | --- | --- | --- |
| Tailnet only | `deploy/install-tailnet-server.sh` | Only devices signed in to your Tailscale | Claude Code and other terminal tools, herdr crews, the Hetzner box |
| Public | `deploy/install-tailnet-server.sh --public` | Anyone on the internet, but every request still needs your sign-in | ChatGPT, Claude, Gemini and Grok in the browser or on your phone |

Why the apps need **Public**: ChatGPT, Claude, Gemini and Grok call your server from their own
computers, not from your phone. Their computers are not on your tailnet, so a tailnet-only address
looks like it doesn't exist to them. **Public** uses Tailscale Funnel, which forwards internet
traffic to the Mac without opening any port on your router.

**What Public shows the internet.** Only what an app needs to connect: the MCP URL, the sign-in
pages, and `/healthz`. Everything else (the browser extension, agents, the Obsidian helper and
file transfers) answers only to devices on your tailnet; from the internet those addresses look
like they don't exist. Even the MCP URL returns nothing until someone signs in with an account on
your allowlist. (`SUBZERO_FUNNEL_MCP_ONLY=true`, the default, does this. Leave it on.)

Either way, the address only answers while the Mac is awake and connected to Tailscale. When it
sleeps, the apps report that the connector is unavailable, and they carry on without it.

## 1. Turn on two Tailscale settings (once)

In the [Tailscale admin console](https://login.tailscale.com/admin/dns):

1. **DNS → MagicDNS:** on.
2. **DNS → HTTPS Certificates:** enable. This is what lets the address start with `https://`.
3. **Public only:** Funnel must be allowed for the Mac. Run `tailscale funnel status` on the Mac;
   if it says Funnel is not enabled, it prints a link that turns it on for you.

## 2. Install the server

In the project folder on the Mac:

```bash
deploy/install-tailnet-server.sh            # or add --public
```

It finds the Mac's Tailscale name, starts the server in the background (it restarts after a crash
or a reboot), waits until it answers, then publishes it. **Success looks like this:**

```text
subzero is running on this Mac.
  Address:     https://your-mac.tail1234.ts.net  (reachable by only devices signed in to your tailnet)
  MCP URL:     https://your-mac.tail1234.ts.net/mcp
  Sign-in:     add https://your-mac.tail1234.ts.net/auth/callback to your Google or GitHub OAuth client
  Browser:     pair the extension with a code from: SUBZERO_BASE_URL=https://your-mac.tail1234.ts.net op run --env-file=deploy/production.env.op -- .venv/bin/subzero pair-device
```

`your-mac.tail1234.ts.net` will be your own Mac's name and tailnet. Settings come from
`deploy/production.env.op` (through 1Password) if you have one, otherwise `.env.local`.

**Which database it uses** is whatever `SUBZERO_DATABASE_URL` says in those settings. If they
point at a cloud database (Supabase, for example), the Mac server reads and writes that one. To
keep everything on the Mac instead, make a copy of the settings file (say
`deploy/mac.env.op`) whose `SUBZERO_DATABASE_URL` names a Postgres on the Mac, set
`SUBZERO_STORAGE_BACKEND=local`, and run the script with `SERVER_ENV_FILE=deploy/mac.env.op`
in front (or put that line in `deploy/local.env`). Run `subzero migrate` against that database
once first.

## 3. Allow sign-in at the new address

Open your Google (or GitHub) OAuth client from [guide 3](03-google-sign-in.md) and add the
**Sign-in** line the script printed as an extra redirect URI. Keep the old one if Vercel still runs.

## 4. Connect

- **Terminal tools** (on any device in your tailnet):
  `claude mcp add --transport http subzero https://your-mac.tail1234.ts.net/mcp`
- **Apps** (needs `--public`): add the **MCP URL** as a custom connector, exactly as in
  [guide 6](06-connect-your-ai-apps.md).
- **Browser extension:** run the command on the **Browser** line, then enter the address and code
  in the extension popup ([guide 13](13-browser-extension.md#step-2-pair-your-browser)).
- **Obsidian helper on the same Mac:** it can keep its current address, or point at
  `http://127.0.0.1:8080` to skip the network entirely.

Check it from your phone with Tailscale on: open the address followed by `/healthz` in a browser.
It should say `ok`.

## Update it

When you pull a newer version, update the database **before** you restart the server. New code
can expect database changes (migrations) that are not there yet; until they are, the tools that
use them fail. On 2026-11-24 that broke `context_bootstrap` and `context_search` in every app for a
day.

```bash
cd ~/Code/subzero
git pull --ff-only
uv sync
op run --env-file=deploy/production.env.op -- .venv/bin/subzero migrate --status   # what is pending
op run --env-file=deploy/production.env.op -- .venv/bin/subzero migrate            # 1. the database
launchctl kickstart -k gui/$(id -u)/com.subzero.server                             # 2. the server
curl -s http://127.0.0.1:8080/readyz                                               # {"ready":true}
```

Use whichever settings file your server uses in place of `deploy/production.env.op`. If you forget
the order, the server still starts and tells you: `/readyz` answers
`{"ready": false, "reason": "migrations_pending", ...}`, the log says
`database schema is behind this server: run subzero migrate`, and a tool that needs the missing
change answers *Subzero's database schema is behind this server's code*. Run `subzero migrate`;
the server notices within half a minute, with no restart.

## Share agent guidance

Chat apps (ChatGPT, Claude, Gemini, Grok, Muse) have no files of their own, so
Subzero sends them your standing context from your dotfiles repository. Every
`context_bootstrap` text starts with three files, in this order:

| File | What it is for |
| --- | --- |
| `subzero/CONTEXT.md` | The header: which file is for which tool, and how a chat gets it |
| `subzero/SOUL.md` | Voice: how the AI should talk with you |
| `subzero/AGENTS.md` | What to do in a chat: memory, saving, machines |

Two more open on demand with `context_read_guidance`: `memory`
(`subzero/MEMORY.md`, where facts about you live) and `machines`
(`MACHINES.md`, your machines and their roles). Nothing else in the repository
can be read. The three standing files are sent on top of `token_budget`
(about 1k tokens; the total is capped at 8,000 characters).

On the Mac, set the server's `SUBZERO_DOTFILES_ROOT` to the absolute path of the
dotfiles checkout (for example, `~/dotfiles`, expanded for the account that
runs Subzero). It can also point to a local symlink to that checkout:

```bash
mkdir -p ~/.config/subzero
ln -s "$HOME/dotfiles" ~/.config/subzero/dotfiles
```

If that path already exists, inspect it before changing it. Put the link's
absolute path in the Mac's server settings, then restart the server once.
Changes on disk show up on the next call.

One settings file can serve both servers: when `SUBZERO_DOTFILES_ROOT` names a
folder that does not exist on the machine (as on Vercel), Subzero reads the
GitHub settings below instead.

Vercel runs on a separate filesystem. Set `SUBZERO_DOTFILES_GITHUB_REPO` to
`owner/dotfiles`, `SUBZERO_DOTFILES_GITHUB_REF` to the branch that holds the
files, and `SUBZERO_DOTFILES_GITHUB_TOKEN` to a sensitive deployment variable
with read-only Contents access to that one repository. Deploy once with these
settings. Vercel reads newly pushed versions at runtime, reusing a read for
`SUBZERO_DOTFILES_CACHE_SECONDS` (default 60), including while the Mac is
offline. Keep the files suitable for every signed-in client you grant
`context:read`.

Each app keeps only the short pointer in `subzero/PRE-PROMPT.md` (call
`context_bootstrap` at the start of every chat). The rules themselves come from
Subzero, so there is one copy to edit.

To test, start a new chat in each app and ask it to quote the ids on the
"Loaded:" line of its first `context_bootstrap` result. They are the first 12
hex digits of each file's SHA-256, so you can check them on the Mac:

```bash
cd ~/dotfiles && for f in subzero/CONTEXT.md subzero/SOUL.md subzero/AGENTS.md; do
  printf '%s %s\n' "$(shasum -a 256 "$f" | cut -c1-12)" "$f"; done
```

The Mac connector matches as soon as a file is saved; the Vercel connector
matches after you push and the cache expires.

## Stop or change it

```bash
deploy/install-tailnet-server.sh --remove     # stop the server and take the address down
deploy/install-tailnet-server.sh --public     # switch to public; run without it to go back
tail -f ~/Library/Logs/subzero/server.err.log # see what it's doing
```

## When something goes wrong

| You see | What it means | Fix |
| --- | --- | --- |
| `Tailscale is not connected` | The Tailscale app is closed or signed out. | Open it and sign in, then run the script again. |
| `Could not read this Mac's tailnet name` | MagicDNS or HTTPS certificates are off. | Do step 1. |
| `The server did not start` | A setting is missing or wrong. | Run `uv run subzero check`; it lists what to fix. |
| `/readyz` says `"reason": "migrations_pending"` | The code is newer than the database. | Run `subzero migrate` with the server's settings ([Update it](#update-it)). |
| An app says the connector is unavailable | The Mac is asleep, or you installed without `--public`. | Wake the Mac, or re-run with `--public`. |
| The extension or `subzero agent` says `not found` | It is reaching the Mac through the public address from a device that is not on your tailnet. | Turn Tailscale on for that device. These parts are tailnet-only on purpose. |
| `redirect_uri_mismatch` when signing in | Step 3 was skipped or has a typo. | Copy the **Sign-in** line exactly. |
