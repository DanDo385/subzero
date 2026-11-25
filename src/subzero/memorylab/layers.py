"""Feed the memory layers that need a model: Claude (vault, structured) and Honcho.

Jev has already routed every session (memory_triage.layers). This module only
moves sessions to the layer that does the work:

* ``extraction_targets``: sessions on the ``vault`` or ``structured`` layer,
  best first, for Claude to read in full. A vault session gets its note
  written in the PARA bucket Jev chose; a structured-only session gets its
  decisions, facts and tasks extracted with no note (deep.NO_NOTE).
* ``honcho_sync``: the person's own messages from sessions on the ``honcho``
  layer, sent with their original dates so Honcho sees preferences change.
* ``honcho_snapshot``: Honcho's current card and newest conclusions, written
  to Experiments/Analysis for the weekly comparison.

Privacy: recover.py already kept sensitive sessions off the vault and Honcho
layers; the queries here check sensitivity again, and text sent to Honcho is
redacted first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from subzero.embeddings.base import redact
from subzero.extraction.deep import NO_NOTE, Candidate
from subzero.logging_setup import get_logger
from subzero.memorylab.triage import BUCKET_NOTE_CLASS
from subzero.services import Services
from subzero.util import truncate_for_budget

logger = get_logger(__name__)

#: Longest single message sent to Honcho.
HONCHO_MESSAGE_MAX_CHARS = 25_000
#: Messages per add_messages call.
HONCHO_BATCH = 50


async def extraction_targets(
    services: Services, *, limit: int, redo: bool = False
) -> list[Candidate]:
    # Pick first, then size only the picked sessions: summing message lengths
    # for every triaged session times out on a full history.
    rows = await services.db.fetch(
        """
        WITH picked AS (
            SELECT t.conversation_id, t.outcome, t.bucket, c.title, c.started_at,
                   (t.scores->>'worth_processing')::float AS worth,
                   ('vault' = ANY(t.layers)) AS vault
            FROM memory_triage t JOIN conversations c ON c.id = t.conversation_id
            WHERE t.layers && ARRAY['vault', 'structured']
              AND ($2 OR (t.extracted_at IS NULL AND NOT (c.metadata ? 'extraction')))
              AND c.sensitivity::text = ANY($3::text[])
            ORDER BY vault DESC, worth DESC NULLS LAST, c.started_at DESC NULLS LAST
            LIMIT $1
        ),
        sized AS (
            SELECT m.conversation_id, sum(length(m.content)) AS chars
            FROM messages m WHERE m.conversation_id IN (SELECT conversation_id FROM picked)
            GROUP BY m.conversation_id
        )
        SELECT p.*, coalesce(s.chars, 0) AS chars
        FROM picked p LEFT JOIN sized s USING (conversation_id)
        ORDER BY p.vault DESC, p.worth DESC NULLS LAST, p.started_at DESC NULLS LAST
        """,
        limit,
        redo,
        _sendable_levels(services),
    )
    return [
        Candidate(
            conversation_id=row["conversation_id"],
            title=row["title"],
            chars=int(row["chars"]),
            importance=row["worth"],
            destination=(
                BUCKET_NOTE_CLASS.get(row["bucket"] or "", "inbox")
                if row["outcome"] == "note"
                else NO_NOTE
            ),
        )
        for row in rows
    ]


async def mark_extracted(services: Services, conversation_ids: list[UUID]) -> int:
    """Stamp the sessions extraction actually finished, so they are not paid for twice."""
    result = await services.db.execute(
        """
        UPDATE memory_triage t SET extracted_at = now()
        FROM conversations c
        WHERE c.id = t.conversation_id AND t.conversation_id = ANY($1::uuid[])
          AND c.metadata->'extraction'->>'status' = 'extracted'
        """,
        conversation_ids,
    )
    return int(str(result).rsplit(" ", 1)[-1] or 0)


def _sendable_levels(services: Services) -> list[str]:
    blocked = set(services.settings.no_external_sensitivity)
    return [level for level in ("normal", "private", "highly_sensitive") if level not in blocked]


# -------------------------------------------------------------------- Honcho


@dataclass
class HonchoReport:
    dry_run: bool
    sessions: int = 0
    messages: int = 0
    chars: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "sessions": self.sessions,
            "messages": self.messages,
            "chars": self.chars,
            "errors": self.errors,
        }


def _client(services: Services) -> Any:
    settings = services.settings
    if not settings.honcho_workspace or not settings.honcho_peer:
        raise RuntimeError(
            "Set SUBZERO_HONCHO_WORKSPACE, SUBZERO_HONCHO_PEER and SUBZERO_HONCHO_API_KEY."
        )
    try:
        from honcho import Honcho
    except ImportError as exc:  # pragma: no cover - depends on the extra
        raise RuntimeError("Install the Honcho extra: uv sync --extra honcho") from exc
    kwargs: dict[str, Any] = {
        "api_key": settings.honcho_api_key.get_secret_value() or None,
        "workspace_id": settings.honcho_workspace,
    }
    if settings.honcho_base_url:
        kwargs["base_url"] = settings.honcho_base_url
    return Honcho(**kwargs)


async def honcho_targets(services: Services, *, limit: int) -> list[Any]:
    return await services.db.fetch(
        """
        SELECT t.conversation_id, c.provider, c.title, c.started_at
        FROM memory_triage t JOIN conversations c ON c.id = t.conversation_id
        WHERE 'honcho' = ANY(t.layers) AND t.honcho_synced_at IS NULL
          AND c.sensitivity = 'normal'
        ORDER BY c.started_at NULLS FIRST
        LIMIT $1
        """,
        limit,
    )


async def _user_messages(services: Services, conversation_id: UUID) -> list[Any]:
    return await services.db.fetch(
        """
        SELECT content, created_at FROM messages
        WHERE conversation_id = $1 AND role = 'user' AND length(content) > 0
        ORDER BY sequence
        """,
        conversation_id,
    )


async def honcho_sync(
    services: Services, *, dry_run: bool = True, limit: int = 100, client: Any = None
) -> HonchoReport:
    """Oldest first, so Honcho reads the history in the order it happened."""
    report = HonchoReport(dry_run=dry_run)
    targets = await honcho_targets(services, limit=limit)
    honcho = None if dry_run else (client or _client(services))
    peer = honcho.peer(services.settings.honcho_peer) if honcho else None
    for row in targets:
        messages = await _user_messages(services, row["conversation_id"])
        if not messages:
            continue
        texts = [
            (truncate_for_budget(redact(m["content"]), HONCHO_MESSAGE_MAX_CHARS), m["created_at"])
            for m in messages
        ]
        report.sessions += 1
        report.messages += len(texts)
        report.chars += sum(len(t) for t, _ in texts)
        if dry_run or honcho is None or peer is None:
            continue
        try:
            session = honcho.session(
                f"subzero-{row['provider']}-{row['conversation_id']}",
                metadata={
                    "source": "subzero",
                    "provider": row["provider"],
                    "title": row["title"] or "",
                },
                peers=[peer],
            )
            when = row["started_at"]
            params = [
                peer.message(text, created_at=(created or when or datetime.now(UTC)))
                for text, created in texts
            ]
            for start in range(0, len(params), HONCHO_BATCH):
                session.add_messages(params[start : start + HONCHO_BATCH])
        except Exception as exc:
            report.errors.append(f"{row['conversation_id']}: {type(exc).__name__}")
            logger.warning("honcho sync failed", extra={"error": type(exc).__name__})
            continue
        await services.db.execute(
            "UPDATE memory_triage SET honcho_synced_at = now() WHERE conversation_id = $1",
            row["conversation_id"],
        )
    return report


async def honcho_snapshot(services: Services, *, client: Any = None, size: int = 100) -> str:
    """Write Honcho's view of the person into the vault; returns the vault path."""
    from subzero.memorylab.recover import _write_note

    honcho = client or _client(services)
    peer = honcho.peer(services.settings.honcho_peer)
    card = peer.get_card() or []
    page = peer.conclusions.list(size=size, reverse=True)
    items = list(getattr(page, "items", None) or page)
    today = datetime.now(UTC).date().isoformat()
    lines = [
        f"Honcho's model of `{services.settings.honcho_peer}` on {today} "
        f"(workspace `{services.settings.honcho_workspace}`). Read-only evidence for",
        "the weekly analysis: promote nothing from here directly.",
        "",
        "## Card",
        "",
        *([f"- {line}" for line in card] or ["- (empty)"]),
        "",
        f"## Newest conclusions ({len(items)})",
        "",
    ]
    for item in items:
        content = str(getattr(item, "content", item)).replace("\n", " ")
        created = getattr(item, "created_at", None)
        stamp = created.date().isoformat() if isinstance(created, datetime) else ""
        lines.append(f"- {stamp + ' · ' if stamp else ''}{truncate_for_budget(content, 300)}")
    title = f"Honcho snapshot {today}"
    path = services.vault_paths.experiment("Analysis", title)
    await _write_note(
        services,
        title=title,
        content="\n".join(lines),
        note_class="experiment",
        vault_path=path,
        metadata={
            "tags": ["memory-lab", "type/experiment", "layer/honcho"],
            "source_date": today,
        },
    )
    return path
