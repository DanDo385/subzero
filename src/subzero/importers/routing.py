"""Classify an imported conversation and act on the routing.

Shared by the importer and by the reclassification loop, so a conversation
retried after a classifier outage is routed exactly as a fresh one would be.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from subzero.classifier.policy import Routing, Verdict
from subzero.classifier.service import ClassificationService
from subzero.config import Settings
from subzero.db.pool import Database
from subzero.importers.base import ImportReport
from subzero.importers.sensitivity import ScreenResult, screen_texts
from subzero.memory.service import MemoryService
from subzero.models import CheckpointFact
from subzero.notes.service import NoteService
from subzero.projects.service import ProjectService
from subzero.sessions.service import SessionService
from subzero.util import truncate_for_budget


@dataclass(frozen=True)
class ConversationRouter:
    """What it takes to screen and route a conversation, passed in once."""

    classification: ClassificationService
    db: Database
    memories: MemoryService
    notes: NoteService
    projects: ProjectService
    sessions: SessionService
    settings: Settings

    def transcript_budget(self) -> int:
        """Leave room in Jev's state for the title, excerpt and other fields."""
        return max(4_000, self.settings.jev_max_state_chars - 4_000)

    async def screen(self, conversation_id: Any, texts: list[str]) -> str:
        """Run the deterministic sensitivity screen and record it on the row.

        Only ever raises the stored level (GREATEST over the ordered enum), so a
        re-import can never make a conversation less private than it was.
        """
        settings = self.settings
        default = settings.import_default_sensitivity
        result = (
            screen_texts(texts, default=default)
            if settings.import_sensitivity_screen
            else ScreenResult(level=default)
        )
        stored = await self.db.fetchval(
            """
            UPDATE conversations
            SET sensitivity = GREATEST(sensitivity, $2::sz_sensitivity),
                metadata = metadata || jsonb_build_object('sensitivity_screen', $3::jsonb)
            WHERE id = $1
            RETURNING sensitivity::text
            """,
            conversation_id,
            result.level,
            result.reasons,
        )
        return str(stored or result.level)

    async def route(
        self,
        *,
        conversation_id: Any,
        provider: str,
        title: str | None,
        summary: str,
        message_count: int,
        sensitivity: str,
        report: ImportReport,
        use_cache: bool = True,
        transcript: str = "",
    ) -> Routing | None:
        """Classify one stored conversation and act on the routing.

        Shared by the importer and by ``reclassify-conversations``. Every write is
        idempotent - the summary is upserted, memories deduplicate on content,
        review items on (object, reason) - so running it again on the same
        conversation only refreshes what changed.
        """
        if not summary:
            return None

        state = {
            "title": title or "",
            "provider": provider,
            "summary": summary,
            "decisions": [],
            "facts": [],
            "tasks": [],
            "message_count": message_count,
            "sensitivity": sensitivity,
            "stated_project": None,
        }
        if transcript:
            state["transcript"] = transcript
        # What is already known, so relation_to_existing has something to compare
        # against. Titles only: cheap, local, and enough to spot a duplicate.
        if title:
            # A memory-lab note made from this very session is not "existing".
            own = f"{title.strip()} ("
            state["existing_similar_notes"] = [
                n
                for n in await self.notes.similar_titles(title, limit=6)
                if not str(n.get("title", "")).startswith(own)
            ][:5]
        result, routing, event_id = await self.classification.classify_checkpoint(
            state, object_id=conversation_id, historical_import=True, use_cache=use_cache
        )

        project_id = (
            await self.projects.resolve(routing.project_slug) if routing.project_slug else None
        )
        async with self.db.transaction() as conn:
            # A conversation that already has a summary - from an earlier run, or
            # written by deep extraction - keeps its text, open questions and
            # context. Only the fields classification owns are refreshed.
            refreshed = await conn.fetchval(
                """
                UPDATE session_summaries
                SET project_id = coalesce($2, project_id),
                    sensitivity = GREATEST(sensitivity, $3::sz_sensitivity),
                    classification_status = $4::sz_classification_status,
                    updated_at = now()
                WHERE conversation_id = $1
                RETURNING id
                """,
                conversation_id,
                project_id,
                sensitivity,
                "needs_review" if routing.needs_review else "classified",
            )
            if refreshed is None:
                await self.sessions._upsert_summary(
                    conn,
                    conversation_id=conversation_id,
                    provider=provider,
                    project_id=project_id,
                    title=title,
                    summary=summary,
                    open_questions=[],
                    important_context=[],
                    sensitivity=sensitivity,
                    needs_review=routing.needs_review,
                )
            if project_id:
                await conn.execute(
                    "UPDATE conversations SET project_id = $2 WHERE id = $1",
                    conversation_id,
                    project_id,
                )
        report.summaries_created += 1

        # Historical imports create memories only when the classifier is
        # confident the conversation carries durable value. The alternative -
        # a memory per old chat - buries the useful ones.
        if routing.save_memory is Verdict.YES and routing.importance >= 0.6 and not result.degraded:
            fact = CheckpointFact(
                content=truncate_for_budget(f"{title or 'Earlier conversation'}: {summary}", 220),
                kind="knowledge",
                subject=routing.project_slug or (title or None),
                provenance="imported_metadata",
            )
            saved = await self.memories.save(
                content=fact.content,
                kind=fact.kind,
                subject=fact.subject,
                project_id=project_id,
                provenance="imported_metadata",
                sensitivity=sensitivity,
                importance=routing.importance,
                source_provider=provider,
                source_conversation_id=conversation_id,
            )
            if saved.outcome == "created":
                report.memories_created += 1

        if (
            not result.degraded
            and routing.deep_extract_p >= self.settings.extraction_min_probability
        ):
            report.deep_extraction_candidates += 1

        if routing.needs_review:
            queued = await self.classification.queue_review(
                object_type="conversation",
                object_id=conversation_id,
                reason=routing.review_reasons[0] if routing.review_reasons else "low_confidence",
                question=f"Imported {provider} conversation {title!r}: where does it belong?",
                suggested=routing.as_json(),
                confidence=routing.confidence,
                classification_event_id=event_id,
            )
            if queued:
                report.reviews_queued += 1
        return routing
