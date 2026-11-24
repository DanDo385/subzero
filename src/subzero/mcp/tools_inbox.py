"""The one way into the Inbox.

The Inbox is the single holding area for anything that still needs a human or
a higher-confidence classification. This tool is deliberately dumb: no Jev
call, no embedding, no classifier round trip. The model calling it has
already decided it cannot place the item, so the only honest routing is
``00-Inbox/`` plus one open review question. The way out is a person, through
``review_reclassify`` or the weekly analysis.
"""

from __future__ import annotations

from typing import Annotated

from fastmcp import FastMCP
from fastmcp.server.auth import require_scopes
from pydantic import Field

from subzero.auth import scopes
from subzero.auth.principal import current_principal
from subzero.errors import ValidationError
from subzero.memorylab.triage import BUCKET_NOTE_CLASS
from subzero.services import get_services
from subzero.util import slugify

#: The sensitivity levels the notes table accepts (sz_sensitivity).
SENSITIVITIES = ("normal", "private", "highly_sensitive")


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="inbox_add",
        tags={"inbox", "write"},
        auth=require_scopes(scopes.NOTES_WRITE),
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    async def inbox_add(
        title: Annotated[
            str,
            Field(min_length=1, max_length=200, description="Short name for the item."),
        ],
        content: Annotated[
            str,
            Field(
                min_length=1,
                description=(
                    "The item itself, written so it makes sense months from now "
                    "without this conversation for context."
                ),
            ),
        ],
        why_uncertain: Annotated[
            str | None,
            Field(
                description=(
                    "One line for the person triaging: why you could not place this confidently."
                )
            ),
        ] = None,
        project: Annotated[
            str | None,
            Field(description="Project slug or name, when the item is probably about one."),
        ] = None,
        sensitivity: Annotated[
            str,
            Field(
                description=(
                    "normal, private or highly_sensitive. A level listed in "
                    "SUBZERO_NO_EXTERNAL_SENSITIVITY is stored in Subzero only "
                    "and never written to the vault."
                )
            ),
        ] = "normal",
        provenance: Annotated[
            str,
            Field(
                description=(
                    "user_statement if the content is the user's own words; "
                    "assistant_generated if you summarised or reworded it."
                )
            ),
        ] = "user_statement",
    ) -> dict:
        """Park something you cannot place confidently, instead of guessing.

        Use this when an item has no clear project or folder, or when you are
        not sure your classification is right: guessing a home is worse than
        parking it. The note lands in 00-Inbox/ and opens one review question;
        it stays in the Inbox until a person files it with review_reclassify.
        Calling twice with the same title and content does not create a second
        item. Nothing is classified or embedded here, by design: you already
        know you cannot place it.
        """
        if sensitivity not in SENSITIVITIES:
            raise ValidationError("sensitivity must be normal, private or highly_sensitive.")
        if provenance not in ("user_statement", "assistant_generated"):
            raise ValidationError("provenance must be user_statement or assistant_generated.")
        services = get_services()
        principal = current_principal()
        project_id = await services.projects.require(project) if project else None

        blocked = sensitivity in services.settings.no_external_sensitivity
        # A blocked note gets no vault home at all: nothing may ever place it
        # in the vault, and a stored path would invite exactly that.
        vault_path = None if blocked else services.vault_paths.note(note_class="inbox", title=title)

        slug = slugify(title, fallback="note")
        prior_version = await services.db.fetchval(
            "SELECT version FROM notes WHERE slug = $1", slug
        )
        note_id, created, version = await services.notes.upsert(
            title=title,
            content=content,
            note_class="inbox",
            project_id=project_id,
            vault_path=vault_path,
            sensitivity=sensitivity,
            provenance=provenance,
            source_provider=principal.provider if principal.provider != "unknown" else None,
        )
        unchanged = not created and prior_version is not None and version == prior_version
        extra: dict[str, object] = {"parked_by": principal.provider}
        if why_uncertain is not None:
            extra["why_uncertain"] = why_uncertain
        await services.db.execute(
            "UPDATE notes SET metadata = metadata || $2::jsonb WHERE id = $1",
            note_id,
            extra,  # the pool's jsonb codec encodes dicts; never json.dumps here
        )

        queued = False
        if services.settings.obsidian_enabled and vault_path is not None and not unchanged:
            job_id = await services.sync_queue.enqueue(
                job_type="write_note",
                object_type="note",
                object_id=note_id,
                vault_path=vault_path,
                payload={"note_id": str(note_id), "reason": "inbox_add"},
            )
            queued = job_id is not None

        review_id = await services.classification.queue_review(
            object_type="note",
            object_id=note_id,
            reason="unplaceable",
            question="Where does this belong?",
            options=[{"destination": bucket} for bucket in BUCKET_NOTE_CLASS],
            suggested={"destination": "project"} if project_id else None,
            confidence=None,
        )
        await services.audit.record(
            tool="inbox_add",
            action="create" if created else "update",
            object_type="note",
            object_id=note_id,
            principal=principal,
            provenance=provenance,
            resulting_version={
                "note_class": "inbox",
                "vault_path": vault_path,
                "queued": queued,
                "review_id": str(review_id) if review_id else None,
            },
        )

        if blocked:
            detail = (
                "Stored in Subzero only, and not sent to the vault: sensitivity "
                f"'{sensitivity}' is listed in SUBZERO_NO_EXTERNAL_SENSITIVITY, so "
                "the note has no vault path and nothing was queued."
            )
        elif not services.settings.obsidian_enabled:
            detail = (
                "Stored in Subzero. Obsidian sync is disabled on this server, so "
                "nothing was queued for the vault."
            )
        elif unchanged:
            detail = (
                "This exact note was already parked; nothing new was written or "
                "queued, and the open review question was refreshed."
            )
        elif queued:
            detail = (
                f"Queued for the vault at {vault_path}. It lands when the local "
                "obsidian-sync worker next polls; see sync_status."
            )
        else:
            detail = "Stored in Subzero; the vault write was not queued."
        return {
            "note_id": str(note_id),
            "title": title,
            "vault_path": vault_path,
            "queued": queued,
            "review_id": str(review_id) if review_id else None,
            "detail": detail,
        }
