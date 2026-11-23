# Deploy scripts

Helpers for putting the server online and keeping the Obsidian helper running. New here? Follow
[guide 5](../docs/guides/05-put-it-online.md) instead; it uses these scripts step by step.

| File | What it does |
| --- | --- |
| `go-live.sh [FILE]` | One command from filled-in settings to a verified live server: preflight, migrate, check, push settings, deploy, then confirm `/healthz` says `ok` and an unauthenticated request gets 401. |
| `vercel-env.sh [FILE]` | Copies settings into Vercel's Production environment, one value at a time over stdin, marking secrets *sensitive*. `FILE` is a plain `.env.local`-style file, or an `op://` reference file resolved with the 1Password CLI. Default: `deploy/production.env.op` if present, else `.env.production`. Skips `OBSIDIAN_SYNC_*`. |
| `preflight.sh` | 1Password users: fails if any field of your deployment's item is empty or `REPLACE_ME`. Needs `SUBZERO_OP_ITEM` (and `SUBZERO_OP_VAULT`) in `deploy/local.env`; otherwise it's skipped. |
| `install-worker.sh` | macOS: installs the Obsidian helper as a LaunchAgent (starts at login, restarts on failure). Reads `deploy/worker.env.op` through `op run` if present, else `.env.local`. |
| `obsidian-sync.plist` | The LaunchAgent template `install-worker.sh` fills in. |
| `install-tailnet-server.sh [--public \| --remove]` | macOS: runs the server on this Mac as a LaunchAgent bound to 127.0.0.1 and publishes it at `https://<mac>.<tailnet>.ts.net` with Tailscale Serve (tailnet only) or Funnel (`--public`) ([guide 13](../docs/guides/14-run-on-your-mac-over-tailscale.md)). |
| `subzero-server.plist` | The LaunchAgent template `install-tailnet-server.sh` fills in. |
| `install-gemini-sync.sh`, `gemini-sync.plist` | macOS: pushes the Subzero digest to its Google Doc weekly for a Gemini Gem ([guide 11](../docs/guides/11-gemini-without-custom-apps.md)). |
| `docker-entrypoint.sh` *(repo root)* | Container entrypoint: runs `subzero migrate` only when the command is `subzero serve`, then execs that command. Other commands are not migrated. |
| `*.env.op.example` | Templates of `op://` reference files: `production` (what Vercel gets), `admin` (commands you run against production), `worker` (the Obsidian helper). Copy to the name without `.example`; the copies are Git-ignored. |
| `local.env` *(yours, Git-ignored)* | Personal defaults for the scripts. See below. |

## `deploy/local.env`

Optional. Sourced by every script; holds names and references, never secrets.

```bash
SUBZERO_OP_VAULT=Private                          # vault holding your deployment's item
SUBZERO_OP_ITEM=abcdefghijklmnopqrstuvwxyz        # the item's id (op item get "<title>" --format json)
SUBZERO_VERCEL_PROJECT=subzero       # Vercel project name
VERCEL_TOKEN_REF="op://Private/<vercel-token-item-id>/credential"   # optional; else `vercel login`
WORKER_ENV_FILE=deploy/worker.env.op         # optional; else auto-detected
WORKER_LABEL=com.subzero.obsidian-sync
VAULT="$HOME/Obsidian/Subzero"      # folder install-worker.sh creates
```

## The 1Password item layout the templates expect

One item (any title; referenced by id) with three sections:

| Section | Fields |
| --- | --- |
| `Server` | `base_url`, `worker_api_key`, `oauth_signing_key` |
| `Google OAuth` | `google_client_id`, `google_client_secret`, `gcp_project` |
| `Supabase` | `supabase_project_ref`, `supabase_url`, `supabase_service_key`, `database_url_pooler`, `database_url_session` |

plus separate items for the TypeSafe and Anthropic API keys (field `credential`). Generate the two
`Server` secrets with `openssl rand -hex 32`.

## Typical sequence

```bash
deploy/preflight.sh                                                         # 1Password users
op run --env-file=deploy/admin.env.op -- uv run subzero migrate  # or: uv run ... migrate
deploy/vercel-env.sh                                                        # or: deploy/vercel-env.sh .env.local
vercel deploy --prod
curl -s https://<your-address>/healthz                                      # -> ok
deploy/install-worker.sh
```

Vercel builds the app from `vercel.json` (framework `python`) and `pyproject.toml`
(`[tool.vercel] entrypoint = "main:app"`); `.vercelignore` keeps tests, docs and local data out of the
upload.
