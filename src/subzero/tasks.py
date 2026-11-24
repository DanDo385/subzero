"""Writes to the tasks table.

A task is a concrete next step for a project or an area: context about what is
unfinished there. Shopping lists, errands and personal todos are not tasks;
they live in the user's own apps through those apps' connectors.

Idempotency matches what list items did before the cutover: the same
normalized text is one task per project, or one per source conversation for a
task with no project. Re-adding existing text merges notes, priority and due
date into the stored row instead of creating a second one, and never reopens a
finished task.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

import asyncpg

from subzero.db.pool import Database
from subzero.errors import ValidationError
from subzero.util import normalized_sha256

_COLUMNS = """
    project_id, text, content_sha256, notes, priority, due_date, home,
    source_provider, source_conversation_id, provenance, sensitivity
"""

_VALUES = "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::sz_provenance,$11::sz_sensitivity)"

#: On a repeat, enrich the stored row the way list items used to be merged.
_MERGE = """
    SET notes = coalesce(EXCLUDED.notes, tasks.notes),
        priority = greatest(tasks.priority, EXCLUDED.priority),
        due_date = coalesce(EXCLUDED.due_date, tasks.due_date),
        updated_at = now()
"""

_INSERT_FOR_PROJECT = f"""
    INSERT INTO tasks ({_COLUMNS})
    {_VALUES}
    ON CONFLICT (project_id, content_sha256) WHERE project_id IS NOT NULL
    DO UPDATE {_MERGE}
    RETURNING id, (xmax = 0) AS created
"""

_INSERT_FOR_CONVERSATION = f"""
    INSERT INTO tasks ({_COLUMNS})
    {_VALUES}
    ON CONFLICT (source_conversation_id, content_sha256)
        WHERE project_id IS NULL AND source_conversation_id IS NOT NULL
    DO UPDATE {_MERGE}
    RETURNING id, (xmax = 0) AS created
"""

_INSERT_PLAIN = f"""
    INSERT INTO tasks ({_COLUMNS})
    {_VALUES}
    RETURNING id, (xmax = 0) AS created
"""


async def upsert_task(
    executor: asyncpg.Connection | Database,
    *,
    text: str,
    project_id: UUID | None = None,
    source_conversation_id: UUID | None = None,
    source_provider: str | None = None,
    provenance: str = "user_statement",
    sensitivity: str = "normal",
    notes: str | None = None,
    priority: int = 0,
    due_date: date | None = None,
    home: str | None = None,
) -> tuple[UUID, bool]:
    """Store one task. Returns (id, created); an existing twin is not duplicated.

    Pass the connection of the surrounding transaction so the task is part of
    the same all-or-nothing write as everything around it.
    """
    text = text.strip()
    if not text:
        raise ValidationError("Task text is empty.")
    if project_id is not None:
        sql = _INSERT_FOR_PROJECT
    elif source_conversation_id is not None:
        sql = _INSERT_FOR_CONVERSATION
    else:
        sql = _INSERT_PLAIN
    row = await executor.fetchrow(
        sql,
        project_id,
        text,
        normalized_sha256(text),
        notes,
        priority,
        due_date,
        home,
        source_provider,
        source_conversation_id,
        provenance,
        sensitivity,
    )
    if row is None:  # pragma: no cover - the upsert always returns a row
        raise RuntimeError("task upsert returned no row")
    return row["id"], bool(row["created"])
