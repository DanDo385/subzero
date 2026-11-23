"""A lease for the loops that must run in exactly one process.

Two servers share this database: a long-running one that is always up, and
serverless instances that come and go. Which of them ran the background loops
used to be a deployment convention, a setting that said "off" on one side, and
nothing noticed when the setting was wrong. A lease makes it a property of the
system: whoever holds the row does the work, and when that process dies the row
expires and another takes over.

This is a lease, not a guarantee. A holder that stalls for longer than the TTL,
a laptop going to sleep or a long garbage collection pause, can be joined
briefly by a second process. Real leader election needs a consensus system, and
this system does not have one or need one, because the work underneath stays
idempotent and is claimed row by row. The lease removes the ordinary case: two
healthy servers paying an embedding or classification provider twice for the
same rows on every tick.
"""

from __future__ import annotations

import os
import socket

from subzero.db.pool import Database
from subzero.logging_setup import get_logger

logger = get_logger(__name__)

#: This process. Stable across ticks so a lease is renewed rather than fought
#: over, and unique per process so a restart cannot inherit a lease its
#: predecessor might still be holding.
HOLDER = f"{socket.gethostname()}:{os.getpid()}"

#: How many loop intervals a lease covers. Three gives a holder that misses a
#: tick, or two, time to come back before another process takes the work.
TTL_TICKS = 3


async def hold_lease(db: Database, name: str, *, ttl_seconds: int) -> bool:
    """Become or stay the holder of `name`. False while somebody else holds it.

    One statement, so two processes asking at the same instant cannot both be
    told yes: the unique name serialises them and the guard in the update only
    lets an expired lease, or the holder's own renewal, through.
    """
    held = await db.fetchval(
        """
        INSERT INTO background_leases (name, holder, expires_at)
        VALUES ($1, $2, now() + make_interval(secs => $3))
        ON CONFLICT (name) DO UPDATE
            SET holder = EXCLUDED.holder, expires_at = EXCLUDED.expires_at
        WHERE background_leases.holder = EXCLUDED.holder
           OR background_leases.expires_at <= now()
        RETURNING holder
        """,
        name,
        HOLDER,
        ttl_seconds,
    )
    return held is not None


async def release_lease(db: Database, name: str) -> None:
    """Give the lease up on shutdown, so a restart does not wait for it."""
    await db.execute("DELETE FROM background_leases WHERE name = $1 AND holder = $2", name, HOLDER)
