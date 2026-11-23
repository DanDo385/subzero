"""Re-run pass-1 classification over conversations already in the database.

Import classifies each conversation once, when it is first seen. This module
revisits them later - after ``QUESTIONS_VERSION`` changes, after a Jev outage
left results degraded, or after projects were created that old conversations
belong to - working from the stored messages, never from the export again.
The raw archive is never touched.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any
from uuid import UUID

from subzero.classifier.policy import Verdict
from subzero.classifier.questions import QUESTIONS_VERSION
from subzero.importers.base import (
    ImportReport,
    NormalizedConversation,
    NormalizedMessage,
)
from subzero.importers.runner import (
    classification_transcript,
    derive_summary,
    route_conversation,
    screen_conversation,
    transcript_budget,
)
from subzero.logging_setup import get_logger
from subzero.services import Services

logger = get_logger(__name__)


async def reclassify_live_checkpoint(services: Services, conversation_id: UUID) -> bool:
    """Re-run classification for a non-imported live checkpoint from stored layer-2 rows."""
    conv = await services.db.fetchrow(
        """
        SELECT c.id, c.provider, c.title, c.sensitivity::text AS sensitivity,
               p.slug AS project_slug,
               s.id AS summary_id, s.summary, s.open_questions, s.important_context
        FROM conversations c
        LEFT JOIN projects p ON p.id = c.project_id
        LEFT JOIN session_summaries s ON s.conversation_id = c.id
        WHERE c.id = $1
        """,
        conversation_id,
    )
    if conv is None:
        return True
    decisions = [
        r["statement"]
        for r in await services.db.fetch(
            "SELECT statement FROM decisions WHERE source_conversation_id = $1 LIMIT 20",
            conversation_id,
        )
    ]
    facts = [
        r["content"]
        for r in await services.db.fetch(
            "SELECT content FROM memories WHERE source_conversation_id = $1 LIMIT 30",
            conversation_id,
        )
    ]
    tasks = [
        r["text"]
        for r in await services.db.fetch(
            "SELECT text FROM list_items WHERE source_conversation_id = $1 LIMIT 20",
            conversation_id,
        )
    ]
    state = {
        "title": conv["title"] or "",
        "provider": conv["provider"],
        "summary": conv["summary"] or "",
        "decisions": decisions,
        "facts": facts,
        "tasks": tasks,
        "open_questions": list(conv["open_questions"] or [])[:10],
        "important_context": list(conv["important_context"] or [])[:10],
        "sensitivity": conv["sensitivity"] or "normal",
        "stated_project": conv["project_slug"],
    }
    result, routing, event_id = await services.classification.classify_checkpoint(
        state, object_id=conversation_id, historical_import=False
    )
    if result.degraded:
        return False
    project_id = (
        await services.projects.resolve(routing.project_slug) if routing.project_slug else None
    )
    if project_id:
        await services.db.execute(
            "UPDATE conversations SET project_id = coalesce(project_id, $2) WHERE id = $1",
            conversation_id,
            project_id,
        )
    if conv["summary_id"]:
        await services.db.execute(
            """
            UPDATE session_summaries
            SET project_id = coalesce($2, project_id),
                classification_status = $3::pc_classification_status,
                updated_at = now()
            WHERE id = $1
            """,
            conv["summary_id"],
            project_id,
            "needs_review" if routing.needs_review else "classified",
        )
    if routing.needs_review:
        await services.sessions._queue_reviews(
            routing, conversation_id=conversation_id, event_id=event_id
        )
    return True


async def drain_reclassification_queue(services: Services, *, limit: int = 50) -> tuple[int, int]:
    """Process due rows in reclassification_queue. Returns (reclassified, examined)."""
    rows = await services.db.fetch(
        """
        SELECT object_type, object_id, reason FROM reclassification_queue
        WHERE next_attempt_at <= now()
        ORDER BY next_attempt_at LIMIT $1
        """,
        limit,
    )
    if not rows:
        return 0, 0

    done = 0
    for row in rows:
        object_id = row["object_id"]
        object_type = row["object_type"]
        if object_type == "checkpoint":
            conv_row = await services.db.fetchrow(
                "SELECT imported FROM conversations WHERE id = $1", object_id
            )
            if conv_row is None:
                await services.db.execute(
                    "DELETE FROM reclassification_queue WHERE object_id = $1", object_id
                )
                continue
            if conv_row["imported"]:
                report = await reclassify_conversations(services, [object_id])
                ok = not report.errors
            else:
                ok = await reclassify_live_checkpoint(services, object_id)
            if ok:
                await services.db.execute(
                    "DELETE FROM reclassification_queue WHERE object_id = $1", object_id
                )
                done += 1
            else:
                await services.db.execute(
                    """
                    UPDATE reclassification_queue
                    SET attempts = attempts + 1,
                        next_attempt_at = now() + interval '15 minutes'
                    WHERE object_id = $1
                    """,
                    object_id,
                )
            continue

        if object_type == "artifact":
            record = await services.artifacts.get(object_id)
            if record is None:
                await services.db.execute(
                    "DELETE FROM reclassification_queue WHERE object_id = $1", object_id
                )
                continue
            sensitivity = (
                await services.db.fetchval(
                    "SELECT sensitivity::text FROM artifacts WHERE id = $1", record.id
                )
                or "normal"
            )
            result, routing, _ = await services.classification.classify_artifact(
                {
                    "title": record.title,
                    "description": record.description or "",
                    "artifact_type": record.artifact_type,
                    "source_type": record.source_type,
                    "source_provider": record.source_provider or "",
                    "file_paths": [f.relative_path for f in record.files],
                    "sensitivity": sensitivity,
                },
                object_id=record.id,
            )
            if not result.degraded:
                policy = services.classification.policy()
                project_slug, _ = policy.artifact_project(result)
                semantic_category, _ = result.choice("semantic_category")
                project_id = await services.projects.resolve(project_slug) if project_slug else None
                if project_id:
                    await services.artifacts.attach_to_project(record.id, project_id)
                await services.artifacts.record_classification(
                    record.id,
                    semantic_category=semantic_category,
                    project_id=project_id,
                    promote_to_vault=routing.promote_to_vault is Verdict.YES,
                    needs_human_review=routing.needs_review,
                    long_term_value=routing.importance,
                    confidence=routing.confidence,
                    classifier=(
                        f"{result.classifier}:{result.model}" if result.model else result.classifier
                    ),
                    answers=result.answers_json(),
                )
                await services.db.execute(
                    "DELETE FROM reclassification_queue WHERE object_id = $1", record.id
                )
                done += 1
            else:
                await services.db.execute(
                    """
                    UPDATE reclassification_queue
                    SET attempts = attempts + 1,
                        next_attempt_at = now() + interval '15 minutes'
                    WHERE object_id = $1
                    """,
                    record.id,
                )
            continue

        # Unknown object_type: remove so it never blocks the queue head.
        await services.db.execute(
            "DELETE FROM reclassification_queue WHERE object_id = $1", object_id
        )
    return done, len(rows)


async def stale_conversations(
    services: Services,
    *,
    limit: int,
    provider: str | None = None,
    include_current: bool = False,
) -> list[UUID]:
    """Imported conversations with no usable classification under the
    current question set: never classified, classified by an older version,
    or classified only while Jev was unavailable."""
    rows = await services.db.fetch(
        """
        SELECT c.id
        FROM conversations c
        WHERE c.imported
          AND ($2::text IS NULL OR c.provider = $2)
          AND ($3 OR NOT EXISTS (
                SELECT 1 FROM classification_events e
                WHERE e.object_type = 'checkpoint'
                  AND e.object_id = c.id
                  AND e.questions_version = $4
                  AND NOT e.degraded))
        ORDER BY c.started_at DESC NULLS LAST
        LIMIT $1
        """,
        limit,
        provider,
        include_current,
        QUESTIONS_VERSION,
    )
    return [row["id"] for row in rows]


async def load_conversation(services: Services, conversation_id: UUID) -> NormalizedConversation:
    head = await services.db.fetchrow(
        """
        SELECT provider, provider_conversation_id, title, model, started_at, updated_at
        FROM conversations WHERE id = $1
        """,
        conversation_id,
    )
    if head is None:
        raise LookupError(f"conversation {conversation_id} not found")
    rows = await services.db.fetch(
        """
        SELECT role, content, provenance::text AS provenance, created_at
        FROM messages WHERE conversation_id = $1 ORDER BY sequence
        """,
        conversation_id,
    )
    return NormalizedConversation(
        provider=head["provider"],
        provider_conversation_id=head["provider_conversation_id"] or "",
        title=head["title"],
        created_at=head["started_at"],
        updated_at=head["updated_at"],
        model=head["model"],
        messages=[
            NormalizedMessage(
                role=row["role"],
                content=row["content"],
                provenance=row["provenance"],
                created_at=row["created_at"],
            )
            for row in rows
        ],
    )


async def reclassify_conversation(
    services: Services,
    conversation_id: UUID,
    report: ImportReport,
    *,
    use_cache: bool = True,
) -> None:
    conversation = await load_conversation(services, conversation_id)
    # Re-screen too: the patterns may have improved since the import, and the
    # screen only ever raises the stored level.
    sensitivity = await screen_conversation(
        services,
        conversation_id,
        [conversation.title or "", *(m.content for m in conversation.messages)],
    )
    if sensitivity != "normal":
        report.conversations_screened_sensitive += 1
    # Classify what the archive says the conversation is about now: the
    # stored summary when one exists (deep extraction writes a far better one
    # than the mechanical excerpt), otherwise a fresh excerpt.
    stored_summary = await services.db.fetchval(
        "SELECT summary FROM session_summaries WHERE conversation_id = $1", conversation_id
    )
    await route_conversation(
        services,
        conversation_id=conversation_id,
        provider=conversation.provider,
        title=conversation.title,
        summary=stored_summary or derive_summary(conversation),
        message_count=len(conversation.messages),
        sensitivity=sensitivity,
        report=report,
        use_cache=use_cache,
        transcript=classification_transcript(conversation, transcript_budget(services)),
    )


async def reclassify_conversations(
    services: Services,
    conversation_ids: list[UUID],
    *,
    concurrency: int | None = None,
    use_cache: bool = True,
) -> ImportReport:
    report = ImportReport(provider="*", archive_sha256="")
    semaphore = asyncio.Semaphore(max(1, concurrency or services.settings.import_concurrency))

    async def one(conversation_id: UUID) -> None:
        async with semaphore:
            report.conversations_seen += 1
            try:
                await reclassify_conversation(
                    services, conversation_id, report, use_cache=use_cache
                )
            except Exception as exc:
                logger.exception("failed to reclassify conversation")
                report.errors.append(f"{conversation_id}: {type(exc).__name__}: {exc}")

    await asyncio.gather(*(one(cid) for cid in conversation_ids))
    return report


def summarise(report: ImportReport) -> dict[str, Any]:
    data = report.as_dict()
    return {
        "conversations": data["conversations"]["seen"],
        "screened_sensitive": data["conversations"]["screened_sensitive"],
        "summaries_refreshed": data["summaries_created"],
        "memories_created": data["memories_created"],
        "reviews_queued": data["reviews_queued"],
        "deep_extraction_candidates": data["deep_extraction_candidates"],
        "errors": data["errors"],
    }


# ------------------------------------------------------- background intake


async def unclassified_conversations(services: Services, *, limit: int) -> list[UUID]:
    """Imported conversations with messages and no usable classification
    under any question set: what the browser extension saved since the last
    run. Newest first."""
    rows = await services.db.fetch(
        """
        SELECT c.id
        FROM conversations c
        WHERE c.imported
          AND EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = c.id)
          AND NOT EXISTS (
                SELECT 1 FROM classification_events e
                WHERE e.object_type = 'checkpoint' AND e.object_id = c.id AND NOT e.degraded)
        ORDER BY c.updated_at DESC
        LIMIT $1
        """,
        limit,
    )
    return [row["id"] for row in rows]


#: A conversation whose classification just failed waits this long before
#: the next try, so one that always fails cannot starve the rest.
RETRY_AFTER_S = 6 * 3600


async def classify_forever(services: Services) -> None:
    """The server's background classification loop. Never raises."""
    settings = services.settings
    failed_at: dict[UUID, float] = {}
    while True:
        try:
            now = time.monotonic()
            ids = [
                cid
                for cid in await unclassified_conversations(
                    services, limit=settings.auto_classify_batch * 3
                )
                if now - failed_at.get(cid, -RETRY_AFTER_S) >= RETRY_AFTER_S
            ][: settings.auto_classify_batch]
            if ids:
                report = await reclassify_conversations(services, ids, concurrency=2)
                for error in report.errors:
                    with contextlib.suppress(ValueError):
                        failed_at[UUID(error.split(":", 1)[0])] = time.monotonic()
                logger.info(
                    "conversations classified",
                    extra={
                        "classified": len(ids) - len(report.errors),
                        "failed": len(report.errors),
                    },
                )
            await drain_reclassification_queue(services, limit=settings.auto_classify_batch)
        except Exception:
            logger.exception("background classification failed; retrying next interval")
        await asyncio.sleep(settings.auto_classify_seconds)


@contextlib.asynccontextmanager
async def background_classify(services: Services):  # type: ignore[no-untyped-def]
    """Run the loop for the server's lifetime, when turned on."""
    if services.settings.auto_classify_seconds <= 0:
        yield
        return
    task = asyncio.create_task(classify_forever(services), name="auto-classify")
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
