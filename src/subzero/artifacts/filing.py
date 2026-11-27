"""Classify a stored artifact and file it where the policy allows.

Shared by the artifact tools and by the reclassification queue, so an
artifact retried after a classifier outage is filed exactly as a fresh one
would have been.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from subzero.artifacts.service import ArtifactService
from subzero.classifier.policy import Verdict
from subzero.classifier.service import ClassificationService
from subzero.config import Settings
from subzero.logging_setup import get_logger
from subzero.models import ArtifactRecord
from subzero.obsidian.paths import VaultPaths
from subzero.obsidian.queue import SyncQueue
from subzero.projects.service import ProjectService

logger = get_logger(__name__)


@dataclass(frozen=True)
class ArtifactFiling:
    """What it takes to file an artifact, passed in once instead of looked up."""

    artifacts: ArtifactService
    classification: ClassificationService
    projects: ProjectService
    settings: Settings
    sync_queue: SyncQueue
    vault_paths: VaultPaths

    async def classify_and_file(
        self, record: ArtifactRecord, *, stated_project: str | None
    ) -> dict:
        """Run artifact classification and apply whatever the policy permits."""
        state = {
            "title": record.title,
            "description": record.description or "",
            "artifact_type": record.artifact_type,
            "source_type": record.source_type,
            "source_provider": record.source_provider or "",
            "file_paths": [f.relative_path for f in record.files],
            "file_types": sorted({f.mime_type for f in record.files}),
            "total_bytes": record.total_bytes,
            "stated_project": stated_project,
            # The stored level, never a default: the classifier's privacy check
            # runs before any external call and can only act on what it is told.
            "sensitivity": record.sensitivity,
        }
        # Small text files carry their own best description; include a little.
        text_preview = [
            f.relative_path
            for f in record.files
            if f.mime_type.startswith("text/") and f.byte_size < 60_000
        ]
        if text_preview:
            state["textual_files"] = text_preview[:20]

        result, routing, event_id = await self.classification.classify_artifact(
            state, object_id=record.id
        )
        policy = self.classification.policy()
        project_slug, _ = policy.artifact_project(result)
        semantic_category, _ = result.choice("semantic_category")

        project_id = await self.projects.resolve(stated_project or project_slug)
        if project_id:
            await self.artifacts.attach_to_project(record.id, project_id)

        await self.artifacts.record_classification(
            record.id,
            semantic_category=semantic_category,
            project_id=project_id,
            promote_to_vault=routing.promote_to_vault is Verdict.YES,
            needs_human_review=routing.needs_review,
            long_term_value=routing.importance,
            confidence=routing.confidence,
            classifier=f"{result.classifier}:{result.model}" if result.model else result.classifier,
            answers=result.answers_json(),
        )

        queued = 0
        if (
            routing.promote_to_vault is Verdict.YES
            and self.settings.obsidian_enabled
            and record.storage_status == "captured"
        ):
            queued = await self.queue_vault_mirror(
                record,
                project_slug=stated_project or project_slug,
                classification_event_id=event_id,
            )

        if routing.needs_review:
            await self.classification.queue_review(
                object_type="artifact",
                object_id=record.id,
                reason=routing.review_reasons[0] if routing.review_reasons else "low_confidence",
                question=f"Where should the artifact {record.title!r} be filed?",
                suggested=routing.as_json(),
                confidence=routing.confidence,
                classification_event_id=event_id,
            )

        return {
            "classifier": result.classifier,
            "model": result.model,
            "degraded": result.degraded,
            "semantic_category": semantic_category,
            "project": stated_project or project_slug,
            "queued_vault_jobs": queued,
            **routing.as_json(),
        }

    async def queue_vault_mirror(
        self,
        record: ArtifactRecord,
        *,
        project_slug: str | None,
        classification_event_id: UUID | None,
    ) -> int:
        """Queue an artifact note, plus file mirrors for anything small enough."""
        note_path = self.vault_paths.artifact_note(record.slug, project_slug=project_slug)
        queued = 0
        if await self.sync_queue.enqueue(
            job_type="write_artifact_note",
            object_type="artifact",
            object_id=record.id,
            vault_path=note_path,
            payload={"artifact_id": str(record.id), "project_slug": project_slug},
            classification_event_id=classification_event_id,
        ):
            queued += 1

        max_bytes = int(self.settings.obsidian_mirror_max_mb * 1024 * 1024)
        for file in await self.artifacts.files_for_mirror(record.id, max_bytes=max_bytes):
            try:
                path = self.vault_paths.artifact_file(
                    record.slug, file["relative_path"], project_slug=project_slug
                )
            except Exception as exc:
                # A file whose name cannot be represented safely in the vault is
                # skipped; it is still available from object storage.
                logger.info(
                    "artifact file not mirrored to the vault",
                    extra={"relative_path": file["relative_path"], "why": str(exc)},
                )
                continue
            if await self.sync_queue.enqueue(
                job_type="mirror_artifact_files",
                object_type="artifact",
                object_id=record.id,
                vault_path=path,
                payload={
                    "artifact_id": str(record.id),
                    "object_key": file["object_key"],
                    "relative_path": file["relative_path"],
                },
                content_sha256=file["sha256"],
                classification_event_id=classification_event_id,
            ):
                queued += 1
        return queued
