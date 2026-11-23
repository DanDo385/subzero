#!/usr/bin/env bash
# Copy your production settings into Vercel's "Production" environment.
#
#   deploy/vercel-env.sh [SETTINGS_FILE]
#
# SETTINGS_FILE is either
#   - a plain file of NAME=value lines (for example the .env.local written by
#     `subzero init`), or
#   - a file of op:// references (deploy/production.env.op), resolved by the
#     1Password CLI at run time so no secret is ever written to disk.
# Default: deploy/production.env.op if it exists, otherwise .env.local.
#
# Each value is handed to the Vercel CLI on stdin, so none shows up in your
# shell history or in the process list.
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f deploy/local.env ]] && source deploy/local.env

FILE="${1:-}"
if [[ -z "$FILE" ]]; then
  if [[ -f deploy/production.env.op ]]; then FILE=deploy/production.env.op; else FILE=.env.local; fi
fi
[[ -f "$FILE" ]] || { echo "No settings file at $FILE. See docs/guides/05-put-it-online.md." >&2; exit 1; }
# The Vercel project may still be named personal-context-mcp: set
# SUBZERO_VERCEL_PROJECT=personal-context-mcp in deploy/local.env until you rename it.
PROJECT="${SUBZERO_VERCEL_PROJECT:-subzero}"

if [[ -z "${VERCEL_TOKEN:-}" && -n "${VERCEL_TOKEN_REF:-}" ]]; then
  VERCEL_TOKEN="$(op read "$VERCEL_TOKEN_REF")"
fi
[[ -n "${VERCEL_TOKEN:-}" ]] && export VERCEL_TOKEN

PUSH=$(cat <<'PY'
import os, re, subprocess, sys, time
path, project, resolved = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
values = {}
for line in open(path, encoding="utf-8"):
    m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line.strip())
    if not m:
        continue
    name, raw = m.groups()
    raw = re.sub(r"\s+#.*$", "", raw).strip().strip('"').strip("'")
    values[name] = os.environ[name] if resolved else raw
secret = re.compile(r"(KEY|SECRET|TOKEN|DATABASE_URL|CLIENT_ID)")
# The token travels in the exported VERCEL_TOKEN environment variable only;
# passing it on argv would put it in process lists and in any traceback.
token = []
for name, value in values.items():
    if name.startswith("OBSIDIAN_SYNC_"):
        continue  # the worker's settings stay on your computer
    kind = "--sensitive" if secret.search(name) else "--no-sensitive"
    # The CLI fails intermittently while retrieving the project, and its real
    # error goes to the stdout this discards. One flaky call must not abort a
    # push of twenty-odd variables, so retry before giving up.
    for attempt in range(1, 6):
        proc = subprocess.run(
            ["vercel", "env", "add", name, "production", "--force", kind,
             "--project", project, "--yes", "--non-interactive", *token],
            input=value.encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if proc.returncode == 0:
            break
        # Never print argv: with a token flag it would leak the credential. The
        # value travels on stdin, so neither captured stream can contain it.
        detail = (proc.stdout + proc.stderr).decode(errors="replace").strip().splitlines()
        sys.stderr.write(
            f"vercel env add {name} failed ({proc.returncode}), attempt {attempt}/5: "
            f"{detail[-1] if detail else 'no output'}\n"
        )
        time.sleep(3 * attempt)
    else:
        sys.exit(1)
    print(f"  set {name} ({kind.lstrip('-')})")
PY
)

if grep -q "op://" "$FILE"; then
  command -v op >/dev/null || { echo "$FILE uses op:// references but the 1Password CLI (op) is not installed." >&2; exit 1; }
  deploy/preflight.sh
  op run --env-file="$FILE" -- python3 -c "$PUSH" "$FILE" "$PROJECT" 1
else
  python3 -c "$PUSH" "$FILE" "$PROJECT" 0
fi
echo "Done. Deploy with: vercel deploy --prod"
