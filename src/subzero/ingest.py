"""Browser extension ingest endpoint and device token auth.

POST /api/v1/ingest/conversation
    Idempotent upsert of a conversation with messages.
    Auth: Bearer <device-token>

DELETE /api/v1/auth/devices/{device_id}
    Revoke a device token.
"""

from __future__ import annotations

import secrets
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from subzero.config import Settings
from subzero.importers.base import parse_timestamp
from subzero.importers.sensitivity import screen_texts
from subzero.logging_setup import get_logger
from subzero.services import get_services

logger = get_logger(__name__)


def _bearer_token(request: Request) -> str | None:
    """Extract Bearer token from Authorization header, if present."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


#: How stale a device's recorded "last used" may be before the next request
#: refreshes it. Nothing in the codebase reads the column, so touching the row
#: on every request made the auth path a write for no consumer: 8,071 updates
#: against 12 live tokens in two months of production traffic, and a hot row
#: both app servers contend on.
TOKEN_TOUCH_SECONDS = 60

#: Ceiling on sync-state's ?recent=N. Without it a caller that omits the
#: parameter gets every conversation for the provider on every poll, which is a
#: full scan of the largest table by a client that only needs the newest few
#: hundred.
SYNC_STATE_MAX = 2000


async def _validate_device_token(db, token: str) -> dict[str, Any] | None:
    """The device a live token belongs to, or None.

    One round trip, which matters against a remote database, and a write only
    when the recorded use has gone stale. The data-modifying CTE runs either
    way and the outer select returns the token row regardless of whether it
    updated anything.
    """
    row = await db.fetchrow(
        """
        WITH token AS (
            SELECT id, device_id, device_name, scopes, last_used_at
            FROM device_tokens
            WHERE token = $1 AND revoked_at IS NULL
        ), touched AS (
            UPDATE device_tokens AS d
            SET last_used_at = now()
            FROM token
            WHERE d.id = token.id
              AND (token.last_used_at IS NULL
                   OR token.last_used_at < now() - make_interval(secs => $2::int))
            RETURNING d.id
        )
        SELECT token.id, token.device_id, token.device_name, token.scopes
        FROM token LEFT JOIN touched ON touched.id = token.id
        """,
        token,
        TOKEN_TOUCH_SECONDS,
    )
    return dict(row) if row is not None else None


#: Provider ids are fixed by migrations; read once per process, and again
#: only when a capture names one not seen yet.
_known_providers: set[str] = set()


async def _provider_exists(db, provider: str) -> bool:
    if provider not in _known_providers:
        _known_providers.update(r["id"] for r in await db.fetch("SELECT id FROM providers"))
    return provider in _known_providers


def _merge_attachments(known: list[Any], new: list[Any]) -> list[Any]:
    """Union of attachment references, keeping the first seen of each.

    Keyed by the provider's attachment id, else by file name and size.
    """
    merged: list[Any] = []
    seen: set[str] = set()
    for item in [*known, *new]:
        if not isinstance(item, dict):
            continue
        key = str(
            item.get("provider_attachment_id") or f"{item.get('file_name')}:{item.get('file_size')}"
        )
        if key not in seen:
            seen.add(key)
            merged.append(item)
    return merged


def _make_token() -> str:
    """Generate a cryptographically random device token."""
    return f"pcd_{secrets.token_urlsafe(32)}"


def build_ingest_routes(settings: Settings) -> list:
    """Build ingest API routes as closures over settings."""

    # ---- Auth routes ----

    async def revoke_device(request: Request) -> JSONResponse:
        """Revoke a device token by device_id."""
        device_id = request.path_params.get("device_id", "")
        if not device_id:
            return JSONResponse({"error": "missing device_id"}, status_code=400)

        token = _bearer_token(request)
        if not token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        device = await _validate_device_token(get_services().db, token)
        if device is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        # Only allow self-revocation.
        if device["device_id"] != device_id:
            return JSONResponse({"error": "can only revoke own device"}, status_code=403)

        await get_services().db.execute(
            "UPDATE device_tokens SET revoked_at = now() WHERE device_id = $1",
            device_id,
        )

        logger.info("device revoked", extra={"device_id": device_id})
        return JSONResponse({"ok": True, "device_id": device_id})

    async def pair_device(request: Request) -> JSONResponse:
        """Pair a browser using a one-time code from the server CLI."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid json"}, status_code=400)

        if not isinstance(body, dict):
            return JSONResponse({"error": "expected object"}, status_code=400)

        raw_code = body.get("pairing_code", "")
        device_name = body.get("device_name", "unknown")
        if not isinstance(raw_code, str) or not isinstance(device_name, str):
            return JSONResponse(
                {"error": "pairing_code and device_name must be strings"}, status_code=400
            )
        pairing_code = raw_code.strip().upper()
        if not pairing_code:
            return JSONResponse({"error": "pairing_code is required"}, status_code=400)

        db = get_services().db

        # Serialize attempts so a one-time code cannot issue two device tokens.
        async with db.transaction() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, device_name, scopes
                FROM pairing_codes
                WHERE code = $1 AND used_at IS NULL AND expires_at > now()
                FOR UPDATE
                """,
                pairing_code,
            )
            if row is None:
                return JSONResponse({"error": "invalid or expired pairing code"}, status_code=403)

            device_id = f"dev_{secrets.token_urlsafe(12)}"
            token = _make_token()
            await conn.execute(
                """
                INSERT INTO device_tokens (device_id, token, device_name, scopes)
                VALUES ($1, $2, $3, $4)
                """,
                device_id,
                token,
                device_name or row["device_name"] or "unknown",
                row["scopes"],
            )
            await conn.execute(
                "UPDATE pairing_codes SET used_at = now(), used_by_device_id = $1 WHERE id = $2",
                device_id,
                row["id"],
            )

        logger.info(
            "device paired",
            extra={"device_id": device_id, "device_name": device_name},
        )

        return JSONResponse(
            {
                "device_id": device_id,
                "device_token": token,
                "device_name": device_name,
            }
        )

    # ---- Ingest route ----

    async def ingest_conversation(request: Request) -> JSONResponse:
        """Idempotent upsert of a conversation with messages."""
        token = _bearer_token(request)
        if not token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        services = get_services()
        db = services.db
        device = await _validate_device_token(db, token)
        if device is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid json"}, status_code=400)

        if not isinstance(body, dict):
            return JSONResponse({"error": "expected object"}, status_code=400)

        provider = body.get("provider")
        provider_conversation_id = body.get("provider_conversation_id")
        if not provider or not provider_conversation_id:
            return JSONResponse(
                {"error": "provider and provider_conversation_id are required"},
                status_code=400,
            )

        title = body.get("title")
        model = body.get("model")
        updated_at = parse_timestamp(body.get("updated_at"))
        messages = body.get("messages", [])
        attachments = body.get("attachments", [])
        if not isinstance(messages, list) or not isinstance(attachments, list):
            return JSONResponse(
                {"error": "messages and attachments must be lists"}, status_code=400
            )
        if not isinstance(provider, str) or not isinstance(provider_conversation_id, str):
            return JSONResponse(
                {"error": "provider and provider_conversation_id must be strings"}, status_code=400
            )
        if not await _provider_exists(db, provider):
            return JSONResponse({"error": "unknown provider"}, status_code=400)

        normalized = []
        for message in messages:
            if not isinstance(message, dict) or not isinstance(message.get("content", ""), str):
                return JSONResponse(
                    {"error": "each message must contain text content"}, status_code=400
                )
            if not message.get("content", "").strip():
                continue
            normalized.append(
                {
                    **message,
                    "content": message["content"].replace("\x00", ""),
                    "created_at": parse_timestamp(message.get("created_at")),
                    "provenance": "imported_metadata",
                }
            )

        # Use the shared conversation and message writers so browser imports
        # follow the same validation and deduplication rules as checkpoints.
        async with db.transaction() as conn:
            conv_id, created = await services.sessions.ensure_conversation(
                provider=provider,
                provider_conversation_id=provider_conversation_id,
                title=title,
                model=model,
                imported=True,
                started_at=parse_timestamp(body.get("created_at")),
                conn=conn,
            )
            inserted = await services.sessions.add_messages(
                conv_id, normalized, provider=provider, conn=conn
            )
            skipped = len(normalized) - inserted
            # Attachment references accumulate: a later capture that shows
            # fewer of them must not drop the ones already recorded.
            merged: list[Any] = []
            if attachments:
                known = await conn.fetchval(
                    "SELECT metadata->'_attachments' FROM conversations WHERE id = $1", conv_id
                )
                merged = _merge_attachments(known if isinstance(known, list) else [], attachments)
            # The deterministic sensitivity screen file imports run, here on
            # every capture (local pattern matching, nothing leaves the
            # process). It only ever raises the stored level, and it runs
            # before anything (embeddings, Jev) could send the text out.
            screen = (
                screen_texts(
                    [title or "", *(m["content"] for m in normalized)],
                    default=settings.import_default_sensitivity,
                )
                if settings.import_sensitivity_screen
                else None
            )
            metadata: dict[str, Any] = {"_attachments": merged} if merged else {}
            if screen and screen.reasons:
                metadata["sensitivity_screen"] = screen.reasons
            await conn.execute(
                """
                UPDATE conversations
                SET provider_updated_at = greatest(provider_updated_at, $2::timestamptz),
                    sensitivity = greatest(sensitivity, $4::sz_sensitivity),
                    metadata = metadata || $3::jsonb
                WHERE id = $1
                """,
                conv_id,
                updated_at,
                metadata,
                screen.level if screen else settings.import_default_sensitivity,
            )
            # A capture is classified later, by the background loop, which
            # claims from this queue. Enqueueing inside the same transaction
            # makes "saved" and "will be classified" one fact instead of two
            # that can disagree: without it the loop had to rediscover the
            # work by scanning every imported conversation on every tick.
            await services.classification.queue_reclassification(
                object_type="checkpoint",
                object_id=conv_id,
                reason="never_classified",
                conn=conn,
            )

        status = "created" if created else "updated"
        if created:
            logger.info(
                "ingest: conversation created",
                extra={"provider": provider, "id": str(conv_id), "messages": inserted},
            )
        else:
            logger.info(
                "ingest: conversation updated",
                extra={
                    "provider": provider,
                    "id": str(conv_id),
                    "messages_inserted": inserted,
                    "messages_skipped": skipped,
                },
            )

        return JSONResponse(
            {
                "conversation_id": str(conv_id),
                "status": status,
                "messages_inserted": inserted,
                "messages_skipped": skipped,
            }
        )

    # ---- Sync state (what the server already has) ----

    async def sync_state(request: Request) -> JSONResponse:
        """Per provider: every conversation the server has, with the provider's
        last update time it has seen. The extension fetches only what is newer."""
        token = _bearer_token(request)
        if not token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        db = get_services().db
        if await _validate_device_token(db, token) is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        provider = request.query_params.get("provider", "")
        if not provider:
            return JSONResponse({"error": "provider is required"}, status_code=400)
        # ?recent=N: only the N most recently updated. A sync stops at the
        # first conversation that is up to date, which is always among the
        # newest, so it needs a few hundred rows, not the whole history.
        try:
            recent = int(request.query_params.get("recent", "0"))
        except ValueError:
            return JSONResponse({"error": "recent must be a number"}, status_code=400)
        limit = min(recent, SYNC_STATE_MAX) if recent > 0 else SYNC_STATE_MAX
        rows = await db.fetch(
            """
            SELECT provider_conversation_id, provider_updated_at
            FROM conversations
            WHERE provider = $1 AND provider_conversation_id IS NOT NULL
            ORDER BY provider_updated_at DESC NULLS LAST
            LIMIT $2
            """,
            provider,
            limit,
        )
        conversations = {
            row["provider_conversation_id"]: (
                row["provider_updated_at"].isoformat() if row["provider_updated_at"] else None
            )
            for row in rows
        }
        known = [v for v in conversations.values() if v]
        return JSONResponse(
            {
                "provider": provider,
                "count": len(conversations),
                "latest": max(known) if known else None,
                "conversations": conversations,
            }
        )

    # ---- Handoff route (for side panel) ----

    async def handoff(request: Request) -> JSONResponse:
        """Return the current project handoff for the side panel."""
        token = _bearer_token(request)
        if not token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        db = get_services().db
        device = await _validate_device_token(db, token)
        if device is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        # Return the most recent active project handoff.
        # Simplified: returns the last 3 session summaries.
        rows = await db.fetch(
            """
            SELECT ss.title, ss.summary, ss.open_questions, ss.important_context,
                   c.provider, c.updated_at
            FROM session_summaries ss
            JOIN conversations c ON c.id = ss.conversation_id
            WHERE ss.classification_status IN ('classified','needs_review')
            ORDER BY c.updated_at DESC
            LIMIT 3
            """
        )

        if not rows:
            return JSONResponse({"handoff": "No recent session handoffs available."})

        parts = []
        for row in rows:
            parts.append(f"## {row['title'] or 'Untitled'} ({row['provider']})")
            parts.append(row["summary"] or "")
            open_qs = row.get("open_questions") or []
            if isinstance(open_qs, list) and open_qs:
                parts.append("\nOpen questions:")
                for q in open_qs:
                    parts.append(f"- {q}")
            parts.append("")

        return JSONResponse({"handoff": "\n".join(parts)})

    from starlette.routing import Route

    return [
        Route("/api/v1/auth/pair-device", pair_device, methods=["POST"]),
        Route("/api/v1/auth/devices/{device_id}", revoke_device, methods=["DELETE"]),
        Route("/api/v1/ingest/conversation", ingest_conversation, methods=["POST"]),
        Route("/api/v1/ingest/sync-state", sync_state, methods=["GET"]),
        Route("/api/v1/handoff", handoff, methods=["GET"]),
    ]
