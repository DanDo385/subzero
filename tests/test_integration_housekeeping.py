"""Rows that outlive their purpose are cleared by the writes that create them.

``oauth_kv.purge_expired`` had no caller, so expired OAuth state piled up
(727 rows in production). A coding session that crashed without ending kept
its job ``claimed`` for ever: the board stopped showing the session after a
day of silence, and nothing ever released or failed the job.
"""

from __future__ import annotations

from uuid import UUID

from conftest import requires_db
from subzero.auth.kv import PostgresKeyValue

pytestmark = requires_db


async def test_writing_oauth_state_purges_what_expired(services) -> None:
    await services.db.execute(
        """
        INSERT INTO oauth_kv (collection, key, value, expires_at)
        VALUES ('fastmcp:default', 'old', '{}'::jsonb, now() - interval '1 hour')
        """
    )
    kv = PostgresKeyValue(services.db)

    await kv.put("fresh", {"v": 1}, ttl=60)

    keys = await services.db.fetch("SELECT key FROM oauth_kv ORDER BY key")
    assert [r["key"] for r in keys] == ["fresh"]


async def test_a_job_held_by_a_silent_session_is_failed_visibly(services) -> None:
    session = await services.agents.start(
        harness="claude-code", external_id=None, machine="mbp", cwd="/tmp/x", home=None
    )
    job = await services.agents.dispatch("tidy the README >claude")
    await services.agents.claim(UUID(session["id"]), targets=["claude"])
    # The session crashed: no end, no heartbeat for more than a day.
    await services.db.execute(
        "UPDATE agent_sessions SET last_seen_at = now() - interval '25 hours' WHERE id = $1",
        UUID(session["id"]),
    )

    await services.agents.board()

    row = await services.db.fetchrow(
        "SELECT status, result FROM dispatch_jobs WHERE id = $1", UUID(job["id"])
    )
    assert row["status"] == "failed"
    assert "stopped reporting" in row["result"]
    assert (
        await services.db.fetchval(
            "SELECT job_id FROM agent_sessions WHERE id = $1", UUID(session["id"])
        )
        is None
    )
