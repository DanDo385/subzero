"""Human review queue tools.

Deliberately no generic delete. Dismissing a review item closes the question;
it never destroys the object the question was about.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastmcp import FastMCP
from fastmcp.server.auth import require_scopes
from pydantic import Field

from subzero.auth import scopes
from subzero.errors import NotFoundError, ValidationError
from subzero.memorylab.triage import BUCKET_NOTE_CLASS
from subzero.services import Services, get_services


async def _file_note(
    services: Services,
    item: dict[str, Any],
    *,
    destination: str | None,
    project: str | None,
) -> None:
    """Move a reviewed note out of the Inbox into its filed folder.

    Refuses a destination of ``inbox`` (that is a no-op filing) and refuses to
    move a note whose sensitivity keeps it out of the vault. Recomputes the
    vault path and queues exactly one ``write_note`` job so the file lands in
    its new folder.
    """
    if destination == "inbox":
        raise ValidationError(
            "A note cannot be filed to the Inbox; that is where it already is. "
            "Choose project, area, resource, archive or permanent."
        )
    note_class = destination if destination is not None else "project"
    if note_class not in BUCKET_NOTE_CLASS:
        raise ValidationError(
            "destination must be one of: "
            + ", ".join(BUCKET_NOTE_CLASS)
            + f". Got {destination!r}."
        )
    note_row = await services.db.fetchrow(
        "SELECT id, title, sensitivity, project_id FROM notes WHERE id = $1",
        item["object_id"],
    )
    if note_row is None:
        raise NotFoundError("The note under review no longer exists.")
    if note_row["sensitivity"] in services.settings.no_external_sensitivity:
        raise ValidationError(
            f"This note is {note_row['sensitivity']}, a sensitivity that never "
            "goes to the vault. It stays in Subzero and cannot be filed into a "
            "vault folder."
        )
    project_id = note_row["project_id"]
    if project:
        project_id = await services.projects.require(project)
    project_slug = None
    if note_class == "project" and project_id:
        project_slug = await services.db.fetchval(
            "SELECT slug FROM projects WHERE id = $1", project_id
        )
    vault_path = services.vault_paths.note(
        note_class=note_class, title=note_row["title"], project_slug=project_slug
    )
    await services.db.execute(
        "UPDATE notes SET note_class = $2, project_id = $3, vault_path = $4 WHERE id = $1",
        note_row["id"],
        note_class,
        project_id,
        vault_path,
    )
    if services.settings.obsidian_enabled:
        await services.sync_queue.enqueue(
            job_type="write_note",
            object_type="note",
            object_id=note_row["id"],
            vault_path=vault_path,
            payload={"note_id": str(note_row["id"]), "reason": "review_reclassify"},
        )


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="review_list",
        tags={"review", "read"},
        auth=require_scopes(scopes.REVIEW),
        annotations={"readOnlyHint": True},
    )
    async def review_list(
        limit: Annotated[
            int, Field(ge=1, le=100, description="Maximum review items to return.")
        ] = 25,
        object_type: Annotated[
            str | None,
            Field(description="conversation, artifact or note."),
        ] = None,
    ) -> dict:
        """Items whose routing was too uncertain to decide automatically.

        Least confident first. Use during a daily review to clear ambiguity the
        classifier correctly declined to guess at.
        """
        services = get_services()
        return {
            "counts": await services.review.counts(),
            "items": await services.review.list_open(limit=limit, object_type=object_type),
        }

    @mcp.tool(
        name="review_get",
        tags={"review", "read"},
        auth=require_scopes(scopes.REVIEW),
        annotations={"readOnlyHint": True},
    )
    async def review_get(
        review_id: Annotated[UUID, Field(description="The review item's id, from review_list.")],
    ) -> dict:
        """One review item with the classifier's full answer set and probabilities."""
        return await get_services().review.get(review_id)

    @mcp.tool(
        name="review_approve",
        tags={"review", "write"},
        auth=require_scopes(scopes.REVIEW),
    )
    async def review_approve(
        review_id: Annotated[UUID, Field(description="The review item's id, from review_list.")],
        note: Annotated[str | None, Field(description="Why, for the audit trail.")] = None,
    ) -> dict:
        """Close the item, applying nothing.

        Records that a person accepted the current routing and closes the
        question. The object itself is not moved or changed: approving an
        inbox_add note leaves it in the Inbox. Use review_reclassify to
        actually file an object somewhere else.
        """
        services = get_services()
        result = await services.review.resolve(
            review_id, status="approved", resolution={"note": note}
        )
        await services.audit.record(
            tool="review_approve",
            action="approve",
            object_type=result["object_type"],
            object_id=result["object_id"],
            approval_status="user_approved",
        )
        return result

    @mcp.tool(
        name="review_reclassify",
        tags={"review", "write"},
        auth=require_scopes(scopes.REVIEW),
    )
    async def review_reclassify(
        review_id: Annotated[UUID, Field(description="The review item's id, from review_list.")],
        project: Annotated[str | None, Field(description="Correct project slug or name.")] = None,
        destination: Annotated[
            str | None,
            Field(
                description=(
                    "Where the object belongs: project, area, resource, archive "
                    "or permanent. For a note this is applied: the note moves to "
                    "that folder. inbox is refused; the note is already there."
                )
            ),
        ] = None,
        note: Annotated[str | None, Field(description="Why the classifier was wrong.")] = None,
    ) -> dict:
        """Override the classifier's routing and file the object.

        For a note, the destination is applied: note_class, project and
        vault_path are updated and one write_note job is queued, so this is
        the way out of the Inbox. For an artifact or a conversation, only the
        project is applied. The override is recorded against the original
        classification event, which is how the classifier's accuracy gets
        measured over time.
        """
        services = get_services()
        item = await services.review.get(review_id)
        resolution = {"project": project, "destination": destination, "note": note}

        if project and item["object_type"] == "artifact":
            project_id = await services.projects.require(project)
            await services.artifacts.attach_to_project(item["object_id"], project_id)
        elif project and item["object_type"] == "conversation":
            project_id = await services.projects.require(project)
            await services.db.execute(
                "UPDATE conversations SET project_id = $2 WHERE id = $1",
                item["object_id"],
                project_id,
            )
        elif item["object_type"] == "note" and (destination or project):
            await _file_note(services, item, destination=destination, project=project)

        result = await services.review.resolve(
            review_id,
            status="reclassified",
            resolution=resolution,
            overridden=True,
            override_note=note,
        )
        await services.audit.record(
            tool="review_reclassify",
            action="update",
            object_type=result["object_type"],
            object_id=result["object_id"],
            approval_status="user_approved",
            resulting_version=resolution,
            classification_event_id=item.get("classification_event_id"),
        )
        return result

    @mcp.tool(
        name="review_dismiss",
        tags={"review", "write"},
        auth=require_scopes(scopes.REVIEW),
    )
    async def review_dismiss(
        review_id: Annotated[UUID, Field(description="The review item's id, from review_list.")],
        note: Annotated[str | None, Field(description="Why it needs no action.")] = None,
    ) -> dict:
        """Close a review item without changing anything.

        The underlying object is untouched - this only says the question has
        been considered.
        """
        services = get_services()
        return await services.review.resolve(
            review_id, status="dismissed", resolution={"note": note}
        )

    @mcp.tool(
        name="sync_status",
        tags={"review", "read"},
        auth=require_scopes(scopes.NOTES_READ),
        annotations={"readOnlyHint": True},
    )
    async def sync_status() -> dict:
        """State of the Obsidian sync queue.

        Use to answer "did that get written to my vault yet" honestly: a
        pending count above zero means the local worker has not run, or is not
        running at all.
        """
        services = get_services()
        rows = await services.db.fetch(
            """
            SELECT status, count(*) AS n, max(updated_at) AS latest
            FROM sync_jobs GROUP BY status
            """
        )
        conflicts = await services.db.fetch(
            """
            SELECT vault_path, last_error, updated_at FROM sync_jobs
            WHERE status = 'conflict' ORDER BY updated_at DESC LIMIT 10
            """
        )
        last_worker = await services.db.fetchval(
            "SELECT max(created_at) FROM sync_events WHERE worker_id IS NOT NULL"
        )
        return {
            "by_status": {r["status"]: int(r["n"]) for r in rows},
            "conflicts": [dict(c) for c in conflicts],
            "last_worker_activity": last_worker,
            "note": (
                "Writes land only when the local obsidian-sync worker polls. If "
                "last_worker_activity is stale, the worker is not running and "
                "nothing has reached the vault."
            ),
        }
