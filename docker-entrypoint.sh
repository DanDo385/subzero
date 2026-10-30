#!/bin/sh
# Migrate only when the container is starting the server. probe, check,
# migrate, and other one-off commands run unchanged.
#
# A second server starting while the first is still migrating is not
# serialized. apply_migrations does not take a lock between reading
# schema_migrations and writing the next version.
set -eu

if [ "${1:-}" = "subzero" ] && [ "${2:-}" = "serve" ]; then
  subzero migrate
fi
exec "$@"
