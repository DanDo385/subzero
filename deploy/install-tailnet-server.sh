#!/usr/bin/env bash
# Run the subzero server on this Mac and give it an https address on your
# Tailscale network. It answers only while this Mac is awake and connected to
# Tailscale; when the Mac sleeps or is off, the address simply stops answering.
#
#   deploy/install-tailnet-server.sh           your own devices only (tailnet):
#                                              CLI harnesses, herdr crews, phone
#   deploy/install-tailnet-server.sh --public  also on the public internet through
#                                              Tailscale Funnel. ChatGPT, Claude,
#                                              Gemini and Grok need this: they
#                                              connect from their own cloud, not
#                                              from your tailnet.
#   deploy/install-tailnet-server.sh --remove  stop the server, take the address down
#
# Settings come from, in order:
#   deploy/production.env.op   op:// references, read from 1Password at start
#   .env.local                 plain settings written by `subzero init`
# The address is set for you: SUBZERO_HOST=127.0.0.1 (nothing on your Wi-Fi can
# reach the port directly) and SUBZERO_BASE_URL=https://<this-mac>.<tailnet>.ts.net.
# Guide: docs/guides/14-run-on-your-mac-over-tailscale.md
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
[[ -f deploy/local.env ]] && source deploy/local.env

MODE=tailnet
case "${1:-}" in
  "") ;;
  --public) MODE=public ;;
  --remove) MODE=remove ;;
  *) echo "Usage: $0 [--public | --remove]" >&2; exit 2 ;;
esac

LABEL="${SERVER_LABEL:-com.subzero.server}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGS="$HOME/Library/Logs/subzero"
PORT="${SUBZERO_PORT:-${PC_PORT:-8080}}"

if command -v tailscale >/dev/null; then
  TS=tailscale
elif [[ -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ]]; then
  TS=/Applications/Tailscale.app/Contents/MacOS/Tailscale
else
  echo "Tailscale is not installed. Install it from https://tailscale.com/download and sign in." >&2
  exit 1
fi

if [[ "$MODE" == remove ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  "$TS" funnel --https=443 off 2>/dev/null || true
  "$TS" serve --https=443 off 2>/dev/null || true
  echo "Stopped $LABEL and removed its Tailscale address."
  exit 0
fi

"$TS" status >/dev/null 2>&1 || { echo "Tailscale is not connected. Open the Tailscale app and sign in." >&2; exit 1; }
DNS="$("$TS" status --json | python3 -c 'import json, sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
[[ "$DNS" == *.ts.net ]] || { echo "Could not read this Mac's tailnet name (got: ${DNS:-nothing}). Turn on MagicDNS and HTTPS in the Tailscale admin console." >&2; exit 1; }
URL="https://$DNS"

ENV_FILE="${SERVER_ENV_FILE:-}"
if [[ -z "$ENV_FILE" && -f deploy/production.env.op ]]; then ENV_FILE=deploy/production.env.op; fi
SERVE="env SUBZERO_HOST=127.0.0.1 SUBZERO_PORT=$PORT SUBZERO_BASE_URL=$URL .venv/bin/subzero serve"
if [[ -n "$ENV_FILE" ]]; then
  command -v op >/dev/null || { echo "$ENV_FILE needs the 1Password CLI (op)." >&2; exit 1; }
  COMMAND="exec op run --env-file=$ENV_FILE -- $SERVE"
elif [[ -f .env.local || -f .env ]]; then
  COMMAND="exec $SERVE"
else
  echo "No settings found. Run: uv run subzero init" >&2
  exit 1
fi

mkdir -p "$LOGS" "$(dirname "$PLIST")"
uv sync --quiet

python3 - "$REPO/deploy/subzero-server.plist" "$PLIST" "$LABEL" "$REPO" "$LOGS" "$COMMAND" <<'PY'
import sys
from xml.sax.saxutils import escape
template, target, label, repo, logs, command = sys.argv[1:]
text = open(template).read()
for key, value in {"__LABEL__": label, "__REPO__": repo, "__LOGS__": logs, "__COMMAND__": command}.items():
    text = text.replace(key, escape(value))
open(target, "w").write(text)
PY
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

printf "Waiting for the server"
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then break; fi
  printf "."; sleep 1
done
echo
curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 || {
  echo "The server did not start. See: tail -50 $LOGS/server.err.log" >&2
  exit 1
}

# Tailscale remembers serve and funnel settings across restarts, so this runs once.
if [[ "$MODE" == public ]]; then
  "$TS" serve --https=443 off 2>/dev/null || true
  "$TS" funnel --bg "http://127.0.0.1:$PORT"
  REACH="anyone on the internet (still behind your sign-in)"
else
  "$TS" funnel --https=443 off 2>/dev/null || true
  "$TS" serve --bg "http://127.0.0.1:$PORT"
  REACH="only devices signed in to your tailnet"
fi

echo
echo "subzero is running on this Mac."
echo "  Address:     $URL  (reachable by $REACH)"
echo "  MCP URL:     $URL/mcp  (or $URL/mcp/<server id> if you set SUBZERO_SERVER_ID)"
echo "  Sign-in:     add $URL/auth/callback to your Google or GitHub OAuth client"
echo "  Logs:        tail -f $LOGS/server.err.log"
echo "  Stop:        $0 --remove"
