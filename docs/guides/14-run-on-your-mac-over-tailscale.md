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
```

`your-mac.tail1234.ts.net` will be your own Mac's name and tailnet. Settings come from
`deploy/production.env.op` (through 1Password) if you have one, otherwise `.env.local`.

## 3. Allow sign-in at the new address

Open your Google (or GitHub) OAuth client from [guide 3](03-google-sign-in.md) and add the
**Sign-in** line the script printed as an extra redirect URI. Keep the old one if Vercel still runs.

## 4. Connect

- **Terminal tools** (on any device in your tailnet):
  `claude mcp add --transport http subzero https://your-mac.tail1234.ts.net/mcp`
- **Apps** (needs `--public`): add the **MCP URL** as a custom connector, exactly as in
  [guide 6](06-connect-your-ai-apps.md).
- **Obsidian helper on the same Mac:** it can keep its current address, or point at
  `http://127.0.0.1:8080` to skip the network entirely.

Check it from your phone with Tailscale on: open the address followed by `/healthz` in a browser.
It should say `ok`.

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
| An app says the connector is unavailable | The Mac is asleep, or you installed without `--public`. | Wake the Mac, or re-run with `--public`. |
| `redirect_uri_mismatch` when signing in | Step 3 was skipped or has a typo. | Copy the **Sign-in** line exactly. |
