"""The steps of a session checkpoint.

`SessionService.checkpoint` runs these in order, the database ones inside a
single transaction. Each step is a plain function over what it needs, so the
checkpoint reads as a list of steps and every step can be read on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import asyncpg

from subzero.classifier.base import ClassificationResult
from subzero.classifier.policy import Routing, Verdict
from subzero.homes import home_metadata
from subzero.memory.service import MemoryService
from subzero.models import (
    CheckpointArtifactRef,
    CheckpointDecision,
    CheckpointFact,
    CheckpointProjectUpdate,
    CheckpointTask,
)
from subzero.projects.service import ProjectService
from subzero.retrieval.inference import ProjectRow
from subzero.tasks import upsert_task
from subzero.util import sha256_text


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


@dataclass(frozen=True)
class Route:
    """Everything decided before the checkpoint's transaction opens."""

    explicit: ProjectRow | None
    result: ClassificationResult
    routing: Routing
    event_id: UUID | None
    summary_embedding: list[float] | None
    memory_vectors: dict[tuple[str, str], list[float] | None]


@dataclass
class Tally:
    """What one checkpoint created, updated and skipped, plus notes for the caller."""

    created: dict[str, int] = field(default_factory=dict)
    updated: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def bump(self, bucket: dict[str, int], key: str, n: int = 1) -> None:
        if n:
            bucket[key] = bucket.get(key, 0) + n


async def write_project_updates(
    conn: asyncpg.Connection,
    projects: ProjectService,
    updates: list[CheckpointProjectUpdate],
    *,
    provider: str,
    conversation_id: UUID,
    routed_project_id: UUID | None,
    tally: Tally,
) -> UUID | None:
    """Apply explicit project updates; returns the project the session routes to."""
    for update in updates:
        project_id = await projects.resolve(update.project, conn=conn)
        if project_id is None:
            project_id, _ = await projects.create(
                name=update.project,
                goals=update.goals,
                source_provider=provider,
                conn=conn,
            )
            tally.bump(tally.created, "projects")
        else:
            await projects.update(
                update.project, status=update.status, goals=update.goals, conn=conn
            )
            tally.bump(tally.updated, "projects")
        if update.event:
            if await projects.add_event(
                project_id,
                summary=update.event,
                source_provider=provider,
                source_conversation_id=conversation_id,
                conn=conn,
            ):
                tally.bump(tally.created, "project_events")
            else:
                tally.bump(tally.skipped, "project_events")
        routed_project_id = routed_project_id or project_id
    return routed_project_id


async def write_home_metadata(
    conn: asyncpg.Connection,
    *,
    conversation_id: UUID,
    explicit_project: bool,
    home: str | None,
    concepts: list[str] | None,
) -> dict[str, Any]:
    """Decide the session's home and tags and store them on the conversation."""
    saved = await conn.fetchrow(
        "SELECT c.metadata, p.slug FROM conversations c LEFT JOIN projects p ON p.id = c.project_id WHERE c.id = $1 FOR UPDATE OF c",
        conversation_id,
    )
    assert saved is not None
    previous = saved["metadata"] or {}
    # An explicit project can replace the initial unassigned home.
    default_home = None if (explicit_project and not home) else previous.get("home")
    metadata = home_metadata(
        home or default_home,
        saved["slug"],
        concepts
        if concepts is not None
        else [
            t.removeprefix("concept/") for t in previous.get("tags", []) if t.startswith("concept/")
        ],
    )
    await conn.execute(
        "UPDATE conversations SET metadata = metadata || $2::jsonb WHERE id = $1",
        conversation_id,
        metadata,
    )
    return metadata


@dataclass
class MergedSummaryLists:
    questions: list[str]
    context: list[str]
    resolved: list[str]
    exists: bool


async def merge_summary_lists(
    conn: asyncpg.Connection,
    *,
    conversation_id: UUID,
    open_questions: list[str],
    resolved_questions: list[str],
    important_context: list[str],
) -> MergedSummaryLists:
    """Open questions and important context accumulate across checkpoints.

    Resolved questions are recorded on this session and hidden from every
    handoff of the project when it is read, so an older session is never
    edited (which would also make it look new).
    """
    existing = await conn.fetchrow(
        """
        SELECT open_questions, important_context,
               coalesce(metadata->'resolved_questions', '[]'::jsonb) AS resolved
        FROM session_summaries WHERE conversation_id = $1 FOR UPDATE
        """,
        conversation_id,
    )
    resolved_all = _merge_texts(existing["resolved"] if existing else [], resolved_questions)
    resolved_keys = {question_key(r) for r in resolved_all}
    merged_questions = [
        q
        for q in _merge_texts(existing["open_questions"] if existing else [], open_questions)
        if question_key(q) not in resolved_keys
    ]
    merged_context = _merge_texts(
        existing["important_context"] if existing else [], important_context
    )
    return MergedSummaryLists(
        questions=merged_questions,
        context=merged_context,
        resolved=resolved_all,
        exists=existing is not None,
    )


async def write_decisions(
    conn: asyncpg.Connection,
    projects: ProjectService,
    memories: MemoryService,
    decisions: list[CheckpointDecision],
    *,
    provider: str,
    conversation_id: UUID,
    routed_project_id: UUID | None,
    routing: Routing,
    sensitivity: str,
    memory_vectors: dict[tuple[str, str], list[float] | None],
    tally: Tally,
) -> None:
    for decision in decisions:
        project_id = await projects.resolve(decision.project, conn=conn) or routed_project_id
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
        tally.bump(tally.created if inserted else tally.skipped, "decisions")
        # A decision is also durable memory; storing it once in each place is
        # what makes it retrievable by both paths.
        if inserted:
            memory_saved = await memories.save(
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
            tally.bump(
                tally.created if memory_saved.outcome == "created" else tally.updated, "memories"
            )


async def write_facts(
    conn: asyncpg.Connection,
    projects: ProjectService,
    memories: MemoryService,
    facts: list[CheckpointFact],
    *,
    provider: str,
    conversation_id: UUID,
    routed_project_id: UUID | None,
    routing: Routing,
    memory_vectors: dict[tuple[str, str], list[float] | None],
    tally: Tally,
) -> None:
    if routing.save_memory is Verdict.NO and not facts:
        return
    for fact in facts:
        project_id = await projects.resolve(fact.project, conn=conn) or routed_project_id
        memory_saved = await memories.save(
            content=fact.content,
            kind=fact.kind,
            subject=fact.subject,
            project_id=project_id,
            provenance=fact.provenance,
            sensitivity=fact.sensitivity,
            importance=(
                fact.importance if fact.importance is not None else max(0.4, routing.importance)
            ),
            source_provider=provider,
            source_conversation_id=conversation_id,
            confidence=routing.confidence or None,
            conn=conn,
            embedding=memory_vectors.get((fact.content.strip(), fact.sensitivity)),
        )
        tally.bump(
            tally.created
            if memory_saved.outcome == "created"
            else tally.skipped
            if memory_saved.outcome == "duplicate"
            else tally.updated,
            "memories",
        )


async def write_tasks(
    conn: asyncpg.Connection,
    projects: ProjectService,
    tasks: list[CheckpointTask],
    *,
    provider: str,
    conversation_id: UUID,
    routed_project_id: UUID | None,
    sensitivity: str,
    home: str,
    tally: Tally,
) -> None:
    for task in tasks:
        project_id = await projects.resolve(task.project, conn=conn) or routed_project_id
        _, task_created = await upsert_task(
            conn,
            text=task.text,
            project_id=project_id,
            source_conversation_id=conversation_id,
            source_provider=provider,
            sensitivity=sensitivity,
            priority=task.priority,
            due_date=task.due_date,
            home=task.home or home,
        )
        tally.bump(tally.created if task_created else tally.skipped, "tasks")


async def link_artifacts(
    conn: asyncpg.Connection,
    artifacts: list[CheckpointArtifactRef],
    *,
    conversation_id: UUID,
    routed_project_id: UUID | None,
    tally: Tally,
) -> None:
    for artifact_ref in artifacts:
        if artifact_ref.artifact_id is None:
            tally.bump(tally.skipped, "artifacts")
            tally.notes.append(
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
        tally.bump(tally.updated, "artifacts")
