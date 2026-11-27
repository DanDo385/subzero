"""Conversations, messages and the session checkpoint.

``session_checkpoint`` is the write path every provider shares. It must be
**idempotent**: a model that checkpoints twice with the same content must not
produce two summaries, two decisions and two tasks. Idempotency comes from
deterministic keys - (provider, provider_conversation_id) for the
conversation, content hashes for decisions, memories and tasks - rather
than from the caller remembering what it already sent.

The whole checkpoint runs in one transaction, so a failure halfway through
leaves nothing behind.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from subzero.audit.service import AuditService
from subzero.auth.principal import Principal
from subzero.classifier.policy import Verdict
from subzero.classifier.service import ClassificationService
from subzero.db.pool import Database, vector_literal
from subzero.embeddings.service import EmbeddingService
from subzero.homes import home_metadata
from subzero.logging_setup import get_logger
from subzero.memory.service import MemoryService
from subzero.memorylab.triage import has_triage, triage
from subzero.models import (
    CheckpointArtifactRef,
    CheckpointDecision,
    CheckpointFact,
    CheckpointProjectUpdate,
    CheckpointResult,
    CheckpointTask,
)
from subzero.projects.service import ProjectService
from subzero.retrieval import inference
from subzero.sessions.conversations import ConversationStore
from subzero.tasks import upsert_task
from subzero.util import sha256_text, truncate_for_budget

logger = get_logger(__name__)

#: How much of a summary is worth sending to the classifier.
_CLASSIFIER_SUMMARY_TOKENS = 3000


class SessionService(ConversationStore):
    def __init__(
        self,
        db: Database,
        *,
        memories: MemoryService,
        projects: ProjectService,
        classification: ClassificationService,
        embeddings: EmbeddingService,
        audit: AuditService,
    ) -> None:
        super().__init__(db)
        self._memories = memories
        self._projects = projects
        self._classification = classification
        self._embeddings = embeddings
        self._audit = audit

    # -------------------------------------------------------- checkpoint

    async def _memory_vectors(
        self, items: list[tuple[str, str]]
    ) -> dict[tuple[str, str], list[float] | None]:
        """One vector per distinct ``(text, sensitivity)``, a batch per level."""
        by_level: dict[str, list[str]] = {}
        for text, level in dict.fromkeys(items):
            by_level.setdefault(level, []).append(text)
        vectors: dict[tuple[str, str], list[float] | None] = {}
        for level, texts in by_level.items():
            for text, vector in zip(
                texts, await self._embeddings.embed(texts, sensitivity=level), strict=True
            ):
                vectors[(text, level)] = vector
        return vectors

    async def checkpoint(
        self,
        *,
        provider: str,
        principal: Principal,
        provider_conversation_id: str | None = None,
        title: str | None = None,
        summary: str | None = None,
        project: str | None = None,
        decisions: list[CheckpointDecision] | None = None,
        facts: list[CheckpointFact] | None = None,
        tasks: list[CheckpointTask] | None = None,
        project_updates: list[CheckpointProjectUpdate] | None = None,
        open_questions: list[str] | None = None,
        resolved_questions: list[str] | None = None,
        artifacts: list[CheckpointArtifactRef] | None = None,
        important_context: list[str] | None = None,
        model: str | None = None,
        sensitivity: str = "normal",
        historical_import: bool = False,
        home: str | None = None,
        concepts: list[str] | None = None,
    ) -> CheckpointResult:
        decisions = decisions or []
        facts = facts or []
        tasks = tasks or []
        project_updates = project_updates or []
        open_questions = open_questions or []
        resolved_questions = [q for q in (resolved_questions or []) if q.strip()]
        artifacts = artifacts or []
        important_context = important_context or []

        created: dict[str, int] = {}
        updated: dict[str, int] = {}
        skipped: dict[str, int] = {}
        notes_out: list[str] = []

        def bump(bucket: dict[str, int], key: str, n: int = 1) -> None:
            if n:
                bucket[key] = bucket.get(key, 0) + n

        # ---- 1. classify first, so routing informs what we write ---------
        # A project the caller names outright decides where the session goes.
        # It is matched like a search filter (slug, name, alias, near
        # misspelling) and never creates a project.
        if home and home.startswith("projects/") and not project:
            project = home.removeprefix("projects/")
        explicit = await self._match_project(project) if project and project.strip() else None
        if project and project.strip() and explicit is None:
            notes_out.append(
                f"No project matches {project!r}; the session was saved without one. "
                "Call project_list to see the existing projects."
            )
        stated_reference = next(
            (u.project for u in project_updates if u.project),
            next((d.project for d in decisions if d.project), None),
        )
        stated = await self._match_project(stated_reference) if stated_reference else None
        stated_project = explicit.slug if explicit else stated.slug if stated else stated_reference
        state = self._checkpoint_state(
            provider=provider,
            title=title,
            summary=summary,
            decisions=decisions,
            facts=facts,
            tasks=tasks,
            open_questions=open_questions,
            important_context=important_context,
            sensitivity=sensitivity,
            stated_project=stated_project,
        )
        result, routing, event_id = await self._classification.classify_checkpoint(
            state, historical_import=historical_import
        )

        # The summary's vector is computed here, outside the transaction. An
        # embedding call is a network round trip that can take seconds, and
        # holding row locks and a pooled connection across it is how a slow
        # provider turns into a hung checkpoint.
        summary_text = summary.strip() if summary else ""
        summary_embedding = (
            await self._embeddings.embed_one(
                f"{title or ''}\n\n{summary_text}", sensitivity=sensitivity
            )
            if summary_text
            else None
        )
        # Every memory this checkpoint may write, vectorised the same way and
        # for the same reason: memories.save would otherwise embed inside it.
        memory_vectors = await self._memory_vectors(
            [(d.statement.strip(), sensitivity) for d in decisions]
            + [(f.content.strip(), f.sensitivity) for f in facts]
        )

        # ---- 2. everything else in one transaction ----------------------
        async with self._db.transaction() as conn:
            routed_project_id = explicit.id if explicit else None
            if routed_project_id is None and routing.project_slug:
                routed_project_id = await self._projects.resolve(routing.project_slug, conn=conn)
            conversation_id, conv_created = await self.ensure_conversation(
                provider=provider,
                provider_conversation_id=provider_conversation_id,
                title=title,
                model=model,
                project_id=routed_project_id,
                imported=historical_import,
                conn=conn,
            )
            bump(created if conv_created else updated, "conversations")

            # Classification ran before the conversation existed, so its
            # event carries no object id. Link it inside this transaction:
            # memory-lab recover only sees events bound to a conversation,
            # and triage is what routes this session to the vault. The NULL
            # guard keeps a re-checkpoint from repointing an event that an
            # import or a reclassification already linked.
            if event_id is not None:
                await conn.execute(
                    "UPDATE classification_events SET object_id = $2 "
                    "WHERE id = $1 AND object_id IS NULL",
                    event_id,
                    conversation_id,
                )

            # -- project updates (explicit beats inferred) ----------------
            for update in project_updates:
                project_id = await self._projects.resolve(update.project, conn=conn)
                if project_id is None:
                    project_id, _ = await self._projects.create(
                        name=update.project,
                        goals=update.goals,
                        source_provider=provider,
                        conn=conn,
                    )
                    bump(created, "projects")
                else:
                    await self._projects.update(
                        update.project, status=update.status, goals=update.goals, conn=conn
                    )
                    bump(updated, "projects")
                if update.event:
                    if await self._projects.add_event(
                        project_id,
                        summary=update.event,
                        source_provider=provider,
                        source_conversation_id=conversation_id,
                        conn=conn,
                    ):
                        bump(created, "project_events")
                    else:
                        bump(skipped, "project_events")
                routed_project_id = routed_project_id or project_id

            if routed_project_id:
                await conn.execute(
                    "UPDATE conversations SET project_id = coalesce(project_id, $2) WHERE id = $1"
                    if explicit is None
                    else "UPDATE conversations SET project_id = $2 WHERE id = $1",
                    conversation_id,
                    routed_project_id,
                )
                await self._projects.touch(routed_project_id, conn=conn)

            saved = await conn.fetchrow(
                "SELECT c.metadata, p.slug FROM conversations c LEFT JOIN projects p ON p.id = c.project_id WHERE c.id = $1 FOR UPDATE OF c",
                conversation_id,
            )
            assert saved is not None
            previous = saved["metadata"] or {}
            # An explicit project can replace the initial unassigned home.
            default_home = None if (explicit and not home) else previous.get("home")
            chosen_home = home or default_home
            metadata = home_metadata(
                chosen_home,
                saved["slug"],
                concepts
                if concepts is not None
                else [
                    t.removeprefix("concept/")
                    for t in previous.get("tags", [])
                    if t.startswith("concept/")
                ],
            )
            await conn.execute(
                "UPDATE conversations SET metadata = metadata || $2::jsonb WHERE id = $1",
                conversation_id,
                metadata,
            )

            # -- session summary (layer 2) --------------------------------
            summary_id = None
            # Open questions and important context accumulate across
            # checkpoints of one session instead of being replaced by the
            # latest call. Resolved questions are recorded on this session and
            # hidden from every handoff of the project when it is read, so an
            # older session is never edited (which would also make it look new).
            existing = await conn.fetchrow(
                """
                SELECT open_questions, important_context,
                       coalesce(metadata->'resolved_questions', '[]'::jsonb) AS resolved
                FROM session_summaries WHERE conversation_id = $1 FOR UPDATE
                """,
                conversation_id,
            )
            resolved_all = _merge_texts(
                existing["resolved"] if existing else [], resolved_questions
            )
            merged_questions = [
                q
                for q in _merge_texts(
                    existing["open_questions"] if existing else [], open_questions
                )
                if question_key(q) not in {question_key(r) for r in resolved_all}
            ]
            merged_context = _merge_texts(
                existing["important_context"] if existing else [], important_context
            )
            if summary and summary.strip():
                summary_id, summary_created = await self._upsert_summary(
                    conn,
                    conversation_id=conversation_id,
                    provider=provider,
                    project_id=routed_project_id,
                    title=title,
                    summary=summary.strip(),
                    open_questions=merged_questions,
                    important_context=merged_context,
                    resolved_questions=resolved_all,
                    sensitivity=sensitivity,
                    needs_review=routing.needs_review,
                    embedding=summary_embedding,
                )
                bump(created if summary_created else updated, "session_summaries")
            elif existing and (open_questions or resolved_questions or important_context):
                await conn.execute(
                    """
                    UPDATE session_summaries
                    SET open_questions = $2, important_context = $3,
                        metadata = metadata || jsonb_build_object('resolved_questions', $4::jsonb)
                    WHERE conversation_id = $1
                    """,
                    conversation_id,
                    merged_questions,
                    merged_context,
                    resolved_all,
                )
                bump(updated, "session_summaries")
            elif open_questions or resolved_questions:
                notes_out.append(
                    "Open and resolved questions are kept with the session summary; "
                    "send a summary so they are saved."
                )

            # -- decisions ------------------------------------------------
            for decision in decisions:
                project_id = (
                    await self._projects.resolve(decision.project, conn=conn) or routed_project_id
                )
                inserted = await conn.fetchval(
                    """
                    INSERT INTO decisions (
                        statement, statement_sha256, rationale, project_id,
                        source_provider, source_conversation_id, provenance
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7::sz_provenance)
                    ON CONFLICT (statement_sha256) DO NOTHING
                    RETURNING id
                    """,
                    decision.statement.strip(),
                    sha256_text(decision.statement.strip().lower()),
                    decision.rationale,
                    project_id,
                    provider,
                    conversation_id,
                    decision.provenance,
                )
                bump(created if inserted else skipped, "decisions")
                # A decision is also durable memory; storing it once in each
                # place is what makes it retrievable by both paths.
                if inserted:
                    memory_saved = await self._memories.save(
                        content=decision.statement.strip(),
                        kind="decision",
                        subject=decision.project or routing.project_slug,
                        project_id=project_id,
                        provenance=decision.provenance,
                        sensitivity=sensitivity,
                        importance=max(0.7, routing.importance),
                        source_provider=provider,
                        source_conversation_id=conversation_id,
                        conn=conn,
                        embedding=memory_vectors.get((decision.statement.strip(), sensitivity)),
                    )
                    bump(created if memory_saved.outcome == "created" else updated, "memories")

            # -- facts / durable memory -----------------------------------
            if routing.save_memory is not Verdict.NO or facts:
                for fact in facts:
                    project_id = (
                        await self._projects.resolve(fact.project, conn=conn) or routed_project_id
                    )
                    memory_saved = await self._memories.save(
                        content=fact.content,
                        kind=fact.kind,
                        subject=fact.subject,
                        project_id=project_id,
                        provenance=fact.provenance,
                        sensitivity=fact.sensitivity,
                        importance=(
                            fact.importance
                            if fact.importance is not None
                            else max(0.4, routing.importance)
                        ),
                        source_provider=provider,
                        source_conversation_id=conversation_id,
                        confidence=routing.confidence or None,
                        conn=conn,
                        embedding=memory_vectors.get((fact.content.strip(), fact.sensitivity)),
                    )
                    bump(
                        created
                        if memory_saved.outcome == "created"
                        else skipped
                        if memory_saved.outcome == "duplicate"
                        else updated,
                        "memories",
                    )
            elif facts:  # pragma: no cover - defensive
                bump(skipped, "memories", len(facts))

            # -- tasks ----------------------------------------------------
            for task in tasks:
                project_id = (
                    await self._projects.resolve(task.project, conn=conn) or routed_project_id
                )
                _, task_created = await upsert_task(
                    conn,
                    text=task.text,
                    project_id=project_id,
                    source_conversation_id=conversation_id,
                    source_provider=provider,
                    sensitivity=sensitivity,
                    priority=task.priority,
                    due_date=task.due_date,
                    home=task.home or metadata["home"],
                )
                bump(created if task_created else skipped, "tasks")

            await conn.execute(
                "UPDATE session_summaries SET metadata = metadata || $2::jsonb WHERE conversation_id = $1",
                conversation_id,
                metadata,
            )

            # -- artifact associations ------------------------------------
            for artifact_ref in artifacts:
                if artifact_ref.artifact_id is None:
                    bump(skipped, "artifacts")
                    notes_out.append(
                        f"Artifact {artifact_ref.title!r} was referenced without an id; "
                        "capture it with artifact_save_* or artifact_register_reference "
                        "first, then checkpoint."
                    )
                    continue
                await conn.execute(
                    """
                    INSERT INTO artifact_relationships (
                        artifact_id, relation, target_type, target_id, provenance
                    ) VALUES ($1,$2,'conversation',$3,'assistant_generated'::sz_provenance)
                    ON CONFLICT DO NOTHING
                    """,
                    artifact_ref.artifact_id,
                    artifact_ref.relation,
                    conversation_id,
                )
                if routed_project_id:
                    await conn.execute(
                        "UPDATE artifacts SET project_id = coalesce(project_id, $2) WHERE id = $1",
                        artifact_ref.artifact_id,
                        routed_project_id,
                    )
                bump(updated, "artifacts")

            # -- 3. reviews, reclassification --------------------------------
            # Inside the transaction on purpose. These used to be written
            # after COMMIT, each on its own pooled connection, so a crash in
            # between left a checkpoint that was saved and reported as saved
            # but never queued for review, with no row anywhere recording
            # that a review was owed. The worker only sees a job once this
            # commits, so queuing here cannot hand it work that is not there.
            review_items = 0

            if routing.needs_review:
                review_items = await self._queue_reviews(
                    routing, conn=conn, conversation_id=conversation_id, event_id=event_id
                )

            if result.degraded:
                # Classification runs before the transaction, so it had no
                # conversation id to queue against. Now there is one.
                await self._classification.queue_reclassification(
                    object_type="checkpoint",
                    object_id=conversation_id,
                    reason=result.error or "classifier_degraded",
                    conn=conn,
                )

        # The audit record stays outside the transaction, and stays
        # best-effort: a failure to log a mutation must not roll back the
        # mutation itself, which is why AuditService.record swallows its own
        # errors.

        await self._audit.record(
            tool="session_checkpoint",
            action="create",
            object_type="conversation",
            object_id=conversation_id,
            principal=principal,
            model=model,
            classification_event_id=event_id,
            resulting_version={"created": created, "updated": updated, "skipped": skipped},
            provenance="assistant_generated",
            metadata={"routing": routing.as_json()},
        )

        if result.degraded:
            notes_out.append(
                "The semantic classifier was unavailable, so routing fell back to "
                "deterministic rules. The checkpoint itself was saved in full and "
                "is queued for reclassification."
            )

        return CheckpointResult(
            conversation_id=conversation_id,
            session_summary_id=summary_id,
            created=created,
            updated=updated,
            skipped=skipped,
            classification={
                "classifier": result.classifier,
                "model": result.model,
                "degraded": result.degraded,
                **routing.as_json(),
                **metadata,
                **_triage_json(result),
            },
            review_items=review_items,
            notes=notes_out,
        )

    # ---------------------------------------------------------- internals

    async def _match_project(self, reference: str) -> inference.ProjectRow | None:
        rows = await self._db.fetch(
            "SELECT id, slug, name, status, metadata FROM projects ORDER BY last_active_at DESC"
        )
        match = inference.resolve_reference(
            reference, [inference.ProjectRow.from_record(r) for r in rows]
        )
        return match.project if match else None

    def _checkpoint_state(
        self,
        *,
        provider: str,
        title: str | None,
        summary: str | None,
        decisions: list[CheckpointDecision],
        facts: list[CheckpointFact],
        tasks: list[CheckpointTask],
        open_questions: list[str],
        important_context: list[str],
        sensitivity: str,
        stated_project: str | None,
    ) -> dict[str, Any]:
        """Compact, named-field state for the classifier.

        Named JSON fields rather than one blob: the docs are explicit that a
        structured state answers better when the context has several parts.
        """
        return {
            "title": title or "",
            "provider": provider,
            "summary": truncate_for_budget(summary or "", _CLASSIFIER_SUMMARY_TOKENS),
            "decisions": [d.statement for d in decisions][:20],
            "facts": [f.content for f in facts][:30],
            "tasks": [t.text for t in tasks][:20],
            "open_questions": open_questions[:10],
            "important_context": important_context[:10],
            "sensitivity": sensitivity,
            "stated_project": stated_project,
        }

    async def _upsert_summary(
        self,
        conn: asyncpg.Connection,
        *,
        conversation_id: UUID,
        provider: str,
        project_id: UUID | None,
        title: str | None,
        summary: str,
        open_questions: list[str],
        important_context: list[str],
        sensitivity: str,
        needs_review: bool,
        resolved_questions: list[str] | None = None,
        embedding: list[float] | None = None,
    ) -> tuple[UUID, bool]:
        row = await conn.fetchrow(
            """
            INSERT INTO session_summaries (
                conversation_id, provider, project_id, title, summary, summary_sha256,
                open_questions, important_context, sensitivity,
                classification_status, embedding, metadata
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::sz_sensitivity,
                      $10::sz_classification_status,$11::text::vector,
                      jsonb_build_object('resolved_questions', $12::jsonb))
            ON CONFLICT (conversation_id) DO UPDATE SET
                -- A later checkpoint in the same session extends the record
                -- rather than replacing it, unless the text is identical.
                summary = CASE
                    WHEN session_summaries.summary_sha256 = EXCLUDED.summary_sha256
                        THEN session_summaries.summary
                    ELSE session_summaries.summary || E'\n\n' || EXCLUDED.summary END,
                summary_sha256 = EXCLUDED.summary_sha256,
                title = coalesce(EXCLUDED.title, session_summaries.title),
                project_id = coalesce(EXCLUDED.project_id, session_summaries.project_id),
                -- Already merged with the stored lists by the caller.
                open_questions = EXCLUDED.open_questions,
                important_context = EXCLUDED.important_context,
                metadata = session_summaries.metadata || EXCLUDED.metadata,
                classification_status = EXCLUDED.classification_status,
                embedding = coalesce(EXCLUDED.embedding, session_summaries.embedding),
                version = session_summaries.version + 1,
                updated_at = now()
            RETURNING id, (xmax = 0) AS created
            """,
            conversation_id,
            provider,
            project_id,
            title,
            summary,
            sha256_text(summary),
            open_questions,
            important_context,
            sensitivity,
            "needs_review" if needs_review else "classified",
            vector_literal(embedding),
            resolved_questions or [],
        )
        await conn.execute(
            "UPDATE conversations SET summary = $2, updated_at = now() WHERE id = $1",
            conversation_id,
            truncate_for_budget(summary, 400),
        )
        return row["id"], bool(row["created"])

    async def _queue_reviews(
        self,
        routing: Any,
        *,
        conn: asyncpg.Connection,
        conversation_id: UUID,
        event_id: UUID | None,
    ) -> int:
        count = 0
        for reason in routing.review_reasons:
            question = {
                "ambiguous_project": "Which project does this session belong to?",
                "ambiguous_destination": "Where should this session's write-up be filed?",
                "sensitive_promotion": (
                    "This session looks sensitive but was marked worth promoting. "
                    "Write it to the vault?"
                ),
                "classifier_requested_review": "The classifier flagged this for review.",
                "classifier_degraded": (
                    "Classified by deterministic fallback only; confirm the routing."
                ),
                "historical_import_promotion_withheld": (
                    "Historical import: vault promotion was withheld. Promote anyway?"
                ),
            }.get(reason, "Confirm the routing for this session.")
            if await self._classification.queue_review(
                object_type="conversation",
                object_id=conversation_id,
                reason=reason,
                question=question,
                suggested=routing.as_json(),
                confidence=routing.confidence,
                classification_event_id=event_id,
                conn=conn,
            ):
                count += 1
        return count


def question_key(text: str) -> str:
    """Compare questions loosely: case, spacing and trailing punctuation ignored."""
    return " ".join(str(text).lower().split()).rstrip("?.!: ")


def _merge_texts(existing: list[Any] | None, new: list[str]) -> list[str]:
    """Union, oldest first, without near-identical repeats."""
    out: list[str] = []
    seen: set[str] = set()
    for text in [*(existing or []), *new]:
        value = str(text).strip()
        key = question_key(value)
        if value and key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _triage_json(result: Any) -> dict[str, Any]:
    """The memory-lab triage outcome for this checkpoint, when Jev answered it."""
    if not has_triage(result):
        return {}
    decision = triage(result)
    return {
        "triage": {
            "outcome": decision.outcome,
            "reason": decision.reason,
            "bucket": decision.bucket,
            "gate": decision.gate,
            "candidates": decision.candidates,
            "layers": decision.layers,
            "scores": decision.scores,
        }
    }
