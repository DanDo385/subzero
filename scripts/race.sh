#!/bin/sh
# Resolve the two provider keys with the 1Password CLI, then start the race.
# Do not echo the key values.
#
# `op run` sits on a pipe so it can mask secrets. That pipe is not a terminal,
# and the scoreboard would never draw. `op read` leaves this process attached
# to the terminal you started.
set -eu
cd "$(dirname "$0")/.."

if [ ! -x build/derby ]; then
  echo "Build first: make" >&2
  exit 1
fi

if [ ! -f deploy/keys.op ]; then
  echo "Missing deploy/keys.op" >&2
  echo "Copy deploy/keys.op.example to deploy/keys.op and set your op:// references." >&2
  exit 1
fi

ref_of() {
  awk -F= -v key="$1" '
    $1 == key {
      print substr($0, index($0, "=") + 1)
      exit
    }
  ' deploy/keys.op
}

alchemy_ref=$(ref_of ALCHEMY_API_KEY)
infura_ref=$(ref_of INFURA_API_KEY)
if [ -z "$alchemy_ref" ] || [ -z "$infura_ref" ]; then
  echo "deploy/keys.op needs ALCHEMY_API_KEY and INFURA_API_KEY op:// references." >&2
  exit 1
fi

ALCHEMY_API_KEY=$(op read "$alchemy_ref")
INFURA_API_KEY=$(op read "$infura_ref")
export ALCHEMY_API_KEY INFURA_API_KEY
exec ./build/derby "$@"
