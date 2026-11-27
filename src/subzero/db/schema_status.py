"""Whether the database has every migration this server's code expects.

On 2026-11-24 the MBP server restarted on code that reads the ``tasks`` table
from migration 0020 while production still had the old ``tasks`` view. For a
day every ``context_bootstrap`` and ``context_search`` failed with
``column x.provenance does not exist``, every client saw only "Error calling
tool", and ``/readyz`` kept answering ready because ``SELECT 1`` still worked.

This module makes that state visible: the server checks at startup and logs
one clear error, ``/readyz`` reports not ready while migrations are pending,
and a tool that fails on a missing column or table says the schema is behind
instead of failing silently. The check is read only (see
:func:`subzero.db.migrate.pending_migrations`) and cached briefly, so a
readiness probe costs at most one small query every ``TTL_SECONDS``; once
someone runs ``subzero migrate`` the server reports ready again without a
restart.

A deployment without ``migrations/`` on disk (a slim image) cannot compare,
so it reports ``None`` (unknown) and changes nothing.
"""

from __future__ import annotations

import time
from typing import Any

from subzero.db.migrate import pending_migrations
from subzero.logging_setup import get_logger

logger = get_logger(__name__)

#: How long one answer is reused. Short enough that a fresh ``migrate`` is
#: noticed within a probe or two, long enough that probes cost nothing.
TTL_SECONDS = 30.0

_pending: list[str] | None = None
_checked_at: float = 0.0
_known: bool = False


async def check(db: Any, *, force: bool = False) -> list[str] | None:
    """Pending migration versions, ``[]`` when current, ``None`` when unknown."""
    global _pending, _checked_at, _known
    now = time.monotonic()
    if not force and _known and now - _checked_at < TTL_SECONDS:
        return _pending
    try:
        pending: list[str] | None = await pending_migrations(db)
    except FileNotFoundError:
        pending = None
    previous = _pending if _known else None
    _pending, _checked_at, _known = pending, now, True
    if pending and pending != previous:
        logger.error(
            "database schema is behind this server: run `subzero migrate`",
            extra={"pending_migrations": pending},
        )
    elif previous and not pending:
        logger.info("database schema is current again")
    return pending


def cached() -> list[str] | None:
    """The last answer from :func:`check`, without touching the database."""
    return _pending if _known else None


def reset() -> None:
    """Forget the cached answer (tests, and a server shutting down)."""
    global _pending, _checked_at, _known
    _pending, _checked_at, _known = None, 0.0, False
