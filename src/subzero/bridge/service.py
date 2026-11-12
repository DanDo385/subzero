"""Persistence for terminal-to-browser bridge sessions."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from subzero.db.pool import Database
from subzero.models import (
    BridgeMessage,
    BridgeMessageCreated,
    BridgeRole,
    BridgeSession,
    BridgeSessionCreated,
)
from subzero.util import sha256_text


class BridgeService:
    """Create bridge sessions and append idempotent, ordered messages."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def create_session(
        self, *, provider: str, title: str | None, model: str | None
    ) -> BridgeSessionCreated:
        row = await self._db.fetchrow(
            """
            INSERT INTO bridge_sessions (provider, title, model)
            VALUES ($1, $2, $3)
            RETURNING id, provider, title, model
            """,
            provider,
            title,
            model,
        )
        assert row is not None
        session_id = row["id"]
        return BridgeSessionCreated(
            session_id=session_id,
            provider=row["provider"],
            title=row["title"],
            model=row["model"],
            link=f"subzero://session/{session_id}",
        )

    async def append_message(
        self,
        session_id: UUID,
        *,
        content: str,
        role: BridgeRole,
        model: str | None,
        message_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> BridgeMessageCreated | None:
        content_sha256 = sha256_text(content)
        async with self._db.transaction() as conn:
            session_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM bridge_sessions WHERE id = $1)",
                session_id,
            )
            if not session_exists:
                return None

            # Advisory locking makes sequence assignment deterministic even
            # when two harness processes append at the same time.
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))",
                str(session_id),
            )
            existing = await conn.fetchrow(
                """
                SELECT id, sequence
                FROM bridge_messages
                WHERE session_id = $1 AND content_sha256 = $2
                """,
                session_id,
                content_sha256,
            )
            if existing is not None:
                return BridgeMessageCreated(
                    message_id=existing["id"],
                    session_id=session_id,
                    status="duplicate",
                    sequence=existing["sequence"],
                )

            row = await conn.fetchrow(
                """
                INSERT INTO bridge_messages (
                    id, session_id, role, content, content_sha256, model,
                    sequence, created_at
                ) VALUES (
                    coalesce($1, gen_random_uuid()), $2, $3, $4, $5, $6,
                    coalesce((SELECT max(sequence) + 1 FROM bridge_messages WHERE session_id = $2), 0),
                    coalesce($7, now())
                )
                RETURNING id, sequence
                """,
                message_id,
                session_id,
                role,
                content,
                content_sha256,
                model,
                created_at,
            )
            await conn.execute(
                "UPDATE bridge_sessions SET updated_at = now() WHERE id = $1",
                session_id,
            )
            assert row is not None
            return BridgeMessageCreated(
                message_id=row["id"],
                session_id=session_id,
                status="created",
                sequence=row["sequence"],
            )

    async def get_session(self, session_id: UUID) -> BridgeSession | None:
        session = await self._db.fetchrow(
            """
            SELECT id, provider, title, model
            FROM bridge_sessions
            WHERE id = $1
            """,
            session_id,
        )
        if session is None:
            return None
        rows = await self._db.fetch(
            """
            SELECT id, role, content, model, sequence, created_at
            FROM bridge_messages
            WHERE session_id = $1
            ORDER BY sequence ASC
            """,
            session_id,
        )
        return BridgeSession(
            session_id=session["id"],
            provider=session["provider"],
            title=session["title"],
            model=session["model"],
            messages=[BridgeMessage(**dict(row)) for row in rows],
        )
