"""Human review queue tools.

Deliberately no generic delete. Dismissing a review item closes the question;
it never destroys the object the question was about.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastmcp import FastMCP
from fastmcp.server.auth import require_scopes
from pydantic import Field

from subzero.auth import scopes
from subzero.review.filing import apply_routing_override, file_note_after_review
from subzero.services import get_services


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
        filed_note: str | None = None

        await apply_routing_override(services, item, project=project)
        if item["object_type"] == "note" and (destination or project):
            filed_note = await file_note_after_review(
                services, item, destination=destination, project=project
            )

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
        if filed_note:
            result["tell_the_user"] = filed_note
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
        return await get_services().sync_queue.status_report()
