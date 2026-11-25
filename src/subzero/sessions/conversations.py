"""Conversation rows and raw messages (layer 1)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg

from subzero.db.pool import Database
from subzero.homes import home_metadata
from subzero.util import sha256_text, utcnow

_INSERT_MESSAGES_SQL = """
WITH incoming AS (
    SELECT *
    FROM unnest($3::text[], $4::text[], $5::text[], $6::text[], $7::text[],
                $8::text[], $9::timestamptz[], $10::text[])
         WITH ORDINALITY AS m(provider_message_id, model, role, content, content_sha256,
                              provenance, created_at, metadata, ord)
),
fresh AS (
    SELECT i.*
    FROM incoming i
    WHERE i.provider_message_id IS NULL
       OR NOT EXISTS (
            SELECT 1 FROM messages e
            WHERE e.conversation_id = $1 AND e.provider_message_id = i.provider_message_id)
),
next_free AS (
    SELECT coalesce(max(sequence), -1) + 1 AS n FROM messages WHERE conversation_id = $1
)
INSERT INTO messages (
    conversation_id, provider, provider_message_id, model, role,
    content, content_sha256, provenance, sequence, created_at, metadata
)
SELECT $1, $2, f.provider_message_id, f.model, f.role,
       f.content, f.content_sha256, f.provenance::sz_provenance,
       next_free.n + row_number() OVER (ORDER BY f.ord) - 1,
       coalesce(f.created_at, now()), f.metadata::jsonb
FROM fresh f, next_free
ORDER BY f.ord
ON CONFLICT (conversation_id, provider_message_id)
    WHERE provider_message_id IS NOT NULL
DO NOTHING
RETURNING id
"""


def _message_rows(messages: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    seen: set[str] = set()
    for message in messages:
        content = (message.get("content") or "").strip()
        if not content:
            continue
        message_id = message.get("provider_message_id")
        if message_id is not None:
            message_id = str(message_id)
            if message_id in seen:
                continue
            seen.add(message_id)
        role = message.get("role") or "user"
        provenance = message.get("provenance") or (
            "user_statement"
            if role == "user"
            else "tool_result"
            if role == "tool"
            else "assistant_generated"
        )
        rows.append(
            (
                message_id,
                message.get("model"),
                role,
                content,
                sha256_text(content),
                provenance,
                message.get("created_at"),
                json.dumps(message.get("metadata") or {}),
            )
        )
    return rows


class ConversationStore:
    """Ensure conversations, append messages, and read transcripts."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def ensure_conversation(
        self,
        *,
        provider: str,
        provider_conversation_id: str | None,
        title: str | None = None,
        model: str | None = None,
        project_id: UUID | None = None,
        imported: bool = False,
        started_at: Any = None,
        conn: asyncpg.Connection | None = None,
    ) -> tuple[UUID, bool]:
        executor = conn or self._db
        external_id = provider_conversation_id or (
            f"synthetic:{sha256_text(f'{provider}:{title or ""}:{utcnow():%Y-%m-%d}')[:32]}"
        )
        row = await executor.fetchrow(
            """
            INSERT INTO conversations (
                provider, provider_conversation_id, title, model, project_id,
                imported, started_at, import_key
            ) VALUES ($1,$2,$3,$4,$5,$6,coalesce($7, now()),$8)
            ON CONFLICT (provider, provider_conversation_id)
                WHERE provider_conversation_id IS NOT NULL
            DO UPDATE SET
                title = coalesce(nullif(btrim(EXCLUDED.title), ''), conversations.title),
                model = coalesce(nullif(btrim(EXCLUDED.model), ''), conversations.model),
                project_id = coalesce(EXCLUDED.project_id, conversations.project_id),
                updated_at = now()
            RETURNING id, project_id, metadata, (xmax = 0) AS created
            """,
            provider,
            external_id,
            title,
            model,
            project_id,
            imported,
            started_at,
            f"{provider}:{external_id}",
        )
        if row is None:  # pragma: no cover
            raise RuntimeError("conversation upsert returned no row")
        if not (row["metadata"] or {}).get("home"):
            slug = await executor.fetchval(
                "SELECT slug FROM projects WHERE id = $1", row["project_id"]
            )
            await executor.execute(
                "UPDATE conversations SET metadata = metadata || $2::jsonb WHERE id = $1",
                row["id"],
                home_metadata(None, slug),
            )
        return row["id"], bool(row["created"])

    async def add_messages(
        self,
        conversation_id: UUID,
        messages: list[dict[str, Any]],
        *,
        provider: str,
        conn: asyncpg.Connection | None = None,
    ) -> int:
        rows = _message_rows(messages)
        if not rows:
            return 0
        if conn is None:
            async with self._db.transaction() as own:
                return await self.add_messages(
                    conversation_id, messages, provider=provider, conn=own
                )
        await conn.execute(
            "SELECT 1 FROM conversations WHERE id = $1 FOR NO KEY UPDATE", conversation_id
        )
        inserted = await conn.fetch(
            _INSERT_MESSAGES_SQL,
            conversation_id,
            provider,
            *(list(column) for column in zip(*rows, strict=True)),
        )
        return len(inserted)

    async def recent_summaries(
        self, *, limit: int = 10, provider: str | None = None, project_id: UUID | None = None
    ) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            """
            SELECT s.id, s.title, s.summary, s.provider, s.updated_at,
                   s.open_questions, c.provider_conversation_id, p.slug AS project_slug
            FROM session_summaries s
            JOIN conversations c ON c.id = s.conversation_id
            LEFT JOIN projects p ON p.id = s.project_id
            WHERE ($1::text IS NULL OR s.provider = $1)
              AND ($2::uuid IS NULL OR s.project_id = $2)
            ORDER BY s.updated_at DESC LIMIT $3
            """,
            provider,
            project_id,
            limit,
        )
        return [dict(r) for r in rows]

    async def conversation_messages(
        self, conversation_id: UUID, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            """
            SELECT role, content, provenance, sequence, created_at, model
            FROM messages WHERE conversation_id = $1
            ORDER BY sequence LIMIT $2
            """,
            conversation_id,
            limit,
        )
        return [dict(r) for r in rows]