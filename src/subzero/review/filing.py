"""Apply human review decisions to notes and related objects."""

from __future__ import annotations

from typing import Any

from subzero.errors import NotFoundError, ValidationError
from subzero.memorylab.triage import BUCKET_NOTE_CLASS
from subzero.services import Services


async def file_note_after_review(
    services: Services,
    item: dict[str, Any],
    *,
    destination: str | None,
    project: str | None,
) -> str | None:
    """Move a reviewed note out of the Inbox into its filed folder."""
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
        "SELECT id, title, sensitivity, project_id, vault_path FROM notes WHERE id = $1",
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
    old_path = note_row["vault_path"]
    if services.settings.obsidian_enabled:
        await services.sync_queue.enqueue(
            job_type="write_note",
            object_type="note",
            object_id=note_row["id"],
            vault_path=vault_path,
            payload={"note_id": str(note_row["id"]), "reason": "review_reclassify"},
        )
    if not old_path or old_path == vault_path:
        return None
    already_written = await services.db.fetchval(
        "SELECT count(*) FROM sync_jobs WHERE vault_path = $1 AND status = 'done'",
        old_path,
    )
    if not already_written:
        return None
    return (
        f"The note is filed under {vault_path}. Its earlier copy at {old_path} "
        "already reached your vault, and nothing here deletes vault files: move "
        "or remove that one in Obsidian yourself."
    )


async def apply_routing_override(
    services: Services,
    item: dict[str, Any],
    *,
    project: str | None,
) -> None:
    """Attach a project override from a review to artifacts or conversations."""
    if not project:
        return
    project_id = await services.projects.require(project)
    if item["object_type"] == "artifact":
        await services.artifacts.attach_to_project(item["object_id"], project_id)
    elif item["object_type"] == "conversation":
        await services.db.execute(
            "UPDATE conversations SET project_id = $2 WHERE id = $1",
            item["object_id"],
            project_id,
        )
