"""Park uncertain notes in the Inbox without classification."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from subzero.auth.principal import Principal
from subzero.memorylab.triage import BUCKET_NOTE_CLASS
from subzero.util import slugify

if TYPE_CHECKING:
    from subzero.audit.service import AuditService
    from subzero.classifier.service import ClassificationService
    from subzero.config import Settings
    from subzero.db.pool import Database
    from subzero.notes.service import NoteService
    from subzero.obsidian.paths import VaultPaths
    from subzero.obsidian.queue import SyncQueue
    from subzero.projects.service import ProjectService

SENSITIVITIES = ("normal", "private", "highly_sensitive")


async def park_uncertain(
    *,
    audit: AuditService,
    classification: ClassificationService,
    db: Database,
    notes: NoteService,
    projects: ProjectService,
    settings: Settings,
    sync_queue: SyncQueue,
    vault_paths: VaultPaths,
    title: str,
    content: str,
    principal: Principal,
    why_uncertain: str | None = None,
    project: str | None = None,
    sensitivity: str = "normal",
    provenance: str = "user_statement",
) -> dict[str, Any]:
    project_id = await projects.require(project) if project else None
    blocked = sensitivity in settings.no_external_sensitivity
    vault_path = None if blocked else vault_paths.note(note_class="inbox", title=title)

    slug = slugify(title, fallback="note")
    prior_version = await db.fetchval("SELECT version FROM notes WHERE slug = $1", slug)
    note_id, created, version = await notes.upsert(
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
    await notes.merge_metadata(note_id, extra)

    queued = False
    if settings.obsidian_enabled and vault_path is not None and not unchanged:
        job_id = await sync_queue.enqueue(
            job_type="write_note",
            object_type="note",
            object_id=note_id,
            vault_path=vault_path,
            payload={"note_id": str(note_id), "reason": "inbox_add"},
        )
        queued = job_id is not None

    review_id = await classification.queue_review(
        object_type="note",
        object_id=note_id,
        reason="unplaceable",
        question="Where does this belong?",
        options=[{"destination": bucket} for bucket in BUCKET_NOTE_CLASS],
        suggested={"destination": "project"} if project_id else None,
        confidence=None,
    )
    await audit.record(
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

    detail = _detail(
        obsidian_enabled=settings.obsidian_enabled,
        blocked=blocked,
        vault_path=vault_path,
        unchanged=unchanged,
        queued=queued,
        sensitivity=sensitivity,
    )
    return {
        "note_id": str(note_id),
        "title": title,
        "vault_path": vault_path,
        "queued": queued,
        "review_id": str(review_id) if review_id else None,
        "detail": detail,
    }


def _detail(
    *,
    obsidian_enabled: bool,
    blocked: bool,
    vault_path: str | None,
    unchanged: bool,
    queued: bool,
    sensitivity: str,
) -> str:
    if blocked:
        return (
            "Stored in Subzero only, and not sent to the vault: sensitivity "
            f"'{sensitivity}' is listed in SUBZERO_NO_EXTERNAL_SENSITIVITY, so "
            "the note has no vault path and nothing was queued."
        )
    if not obsidian_enabled:
        return (
            "Stored in Subzero. Obsidian sync is disabled on this server, so "
            "nothing was queued for the vault."
        )
    if unchanged:
        return (
            "This exact note was already parked; nothing new was written or "
            "queued, and the open review question was refreshed."
        )
    if queued:
        return (
            f"Queued for the vault at {vault_path}. It lands when the local "
            "obsidian-sync worker next polls; see sync_status."
        )
    return "Stored in Subzero; the vault write was not queued."
