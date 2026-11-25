"""Bulk import pipeline.

Order matters and is the same for every provider:

 1. preserve the original archive in restricted cold storage, hashed
 2. detect and parse
 3. insert conversations and messages (idempotent on provider ids)
 4. ingest attachments - captured only when the bytes were really in the
    archive, deduplicated by SHA-256 across the whole corpus
 5. screen sensitivity deterministically, before anything leaves the process
 6. derive a session summary and classify it with conservative historical
    thresholds (a few conversations at a time; identical inputs are answered
    from the classification cache)
 7. create memory candidates only where confidence is high
 8. associate projects, queue anything ambiguous for review
 9. emit a report

Running the same archive twice is a no-op beyond refreshed timestamps.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from subzero.artifacts.archives import ArchiveLimits
from subzero.artifacts.keys import archive_key
from subzero.artifacts.service import ArtifactService
from subzero.classifier.service import ClassificationService
from subzero.config import Settings
from subzero.db.pool import Database
from subzero.importers.adapters import IMPORTERS, detect_importer
from subzero.importers.base import (
    ConversationImporter,
    ImportReport,
    NormalizedAttachment,
    NormalizedConversation,
)
from subzero.importers.routing import ConversationRouter
from subzero.logging_setup import get_logger
from subzero.sessions.service import SessionService
from subzero.storage.base import ObjectStore
from subzero.util import sha256_bytes, truncate_for_budget

logger = get_logger(__name__)

#: A summary derived mechanically from a transcript, with no model involved.
#: It is marked `imported_metadata`, never `assistant_generated`, because no
#: assistant wrote it.
_SUMMARY_MESSAGE_BUDGET = 1200


def _hash_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def derive_summary(conversation: NormalizedConversation) -> str:
    """Mechanical summary: the opening exchange plus the closing one.

    Deliberately not model-generated. A bulk import of years of history should
    not cost thousands of LLM calls, and a truthful excerpt is more useful
    than a hallucinated précis. A model can write a better one later, on
    demand, through the normal checkpoint path.
    """
    messages = conversation.messages
    if not messages:
        return ""
    head = messages[:2]
    tail = messages[-2:] if len(messages) > 4 else []
    parts = [f"{m.role}: {truncate_for_budget(m.content, 180)}" for m in head]
    if tail:
        parts.append("…")
        parts += [f"{m.role}: {truncate_for_budget(m.content, 180)}" for m in tail]
    body = "\n".join(parts)
    return truncate_for_budget(
        f"Imported {conversation.provider} conversation with {len(messages)} messages.\n\n{body}",
        _SUMMARY_MESSAGE_BUDGET,
    )


def classification_transcript(conversation: NormalizedConversation, max_chars: int) -> str:
    """The whole conversation for Jev, as ``ROLE: text`` lines.

    Jev judges far better from the transcript than from a four-message
    excerpt. Over budget, keep the opening (where the problem is stated) and
    the end (where it is settled) and say what was cut.
    """
    lines = [
        f"{m.role.upper()}: {m.content.strip()}" for m in conversation.messages if m.content.strip()
    ]
    full = "\n\n".join(lines)
    if len(full) <= max_chars:
        return full
    head, tail = full[: int(max_chars * 0.4)], full[-int(max_chars * 0.6) :]
    omitted = len(full) - len(head) - len(tail)
    return f"{head}\n\n[... {omitted} characters from the middle omitted ...]\n\n{tail}"


class ImportRunner:
    def __init__(
        self,
        *,
        artifacts: ArtifactService,
        classification: ClassificationService,
        db: Database,
        router: ConversationRouter,
        sessions: SessionService,
        settings: Settings,
        store: ObjectStore,
    ) -> None:
        self._artifacts = artifacts
        self._classification = classification
        self._db = db
        self._router = router
        self._sessions = sessions
        self._settings = settings
        self._store = store
        self._limits = ArchiveLimits(
            max_entries=settings.max_archive_entries,
            max_total_bytes=settings.max_archive_uncompressed_bytes,
            max_entry_bytes=settings.max_artifact_file_bytes,
            max_compression_ratio=settings.max_archive_compression_ratio,
        )
        # Attachments are deduplicated by hash across the whole corpus; two
        # conversations importing the same bytes at once must not both miss
        # the duplicate check.
        self._attachment_lock = asyncio.Lock()

    async def run(
        self,
        archive: Path,
        *,
        provider: str | None = None,
        limit: int | None = None,
        store_archive: bool = True,
    ) -> ImportReport:
        # resolve() and is_file() both hit the filesystem, so they go to a
        # thread together rather than blocking the loop twice.
        archive = await asyncio.to_thread(lambda p: Path(p).expanduser().resolve(), archive)
        if not await asyncio.to_thread(archive.is_file):
            raise FileNotFoundError(f"No such export archive: {archive}")

        # A provider export can be gigabytes; hash it off the event loop.
        digest = await asyncio.to_thread(_hash_file, archive)

        importer: ConversationImporter | None
        if provider:
            factory = IMPORTERS.get(provider)
            if factory is None:
                raise ValueError(
                    f"Unknown provider {provider!r}. Choose one of: " + ", ".join(sorted(IMPORTERS))
                )
            importer = factory(self._limits)
        else:
            provider, importer = detect_importer(archive, self._limits)
            if importer is None:
                raise ValueError(
                    "Could not identify which provider produced this archive. "
                    "Pass the provider explicitly."
                )

        report = ImportReport(provider=provider, archive_sha256=digest)
        report.limitations = importer.limitations()

        existing = await self._db.fetchrow(
            "SELECT id, import_status, report FROM source_documents WHERE sha256 = $1", digest
        )
        if existing and existing["import_status"] == "imported":
            report.limitations.insert(
                0,
                "This archive was imported before. Conversations already in the "
                "library were skipped, so nothing was duplicated; any not yet "
                "imported (for example after a --limit trial) were added.",
            )

        source_id = await self._record_source(
            archive, provider=provider, digest=digest, store_archive=store_archive
        )

        # Parsing is sequential; storing and classifying run a few at a time,
        # because each conversation is dominated by a Jev round trip. At most
        # 2 x concurrency parsed conversations are held in memory.
        concurrency = max(1, self._settings.import_concurrency)
        semaphore = asyncio.Semaphore(concurrency)
        pending: set[asyncio.Task[None]] = set()

        async def import_guarded(conversation: NormalizedConversation) -> None:
            async with semaphore:
                try:
                    await self._import_one(conversation, report)
                except Exception as exc:
                    logger.exception("failed to import conversation")
                    report.errors.append(
                        f"{conversation.provider_conversation_id or '?'}: "
                        f"{type(exc).__name__}: {exc}"
                    )

        for index, conversation in enumerate(importer.parse(archive)):
            if limit is not None and index >= limit:
                break
            report.conversations_seen += 1
            if len(pending) >= concurrency * 2:
                _, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            pending.add(asyncio.create_task(import_guarded(conversation)))
        if pending:
            await asyncio.wait(pending)
        # Conversations the parser could not read are named, never dropped silently.
        report.errors.extend(getattr(importer, "problems", []))

        await self._db.execute(
            """
            UPDATE source_documents
            SET import_status = 'imported', conversation_count = $2,
                artifact_count = $3, report = $4
            WHERE id = $1
            """,
            source_id,
            report.conversations_imported,
            report.attachments_captured + report.attachments_referenced,
            report.as_dict(),
        )
        return report

    async def _record_source(
        self, archive: Path, *, provider: str, digest: str, store_archive: bool
    ) -> Any:
        object_key = None
        if store_archive and self._settings.storage_backend == "supabase":
            object_key = archive_key(provider=provider, filename=archive.name, sha256=digest)
            try:
                payload = await asyncio.to_thread(archive.read_bytes)
                await self._store.put(
                    object_key,
                    payload,
                    mime_type="application/zip",
                    bucket=self._settings.supabase_archive_bucket,
                )
            except Exception:
                logger.exception("could not archive the original export; continuing")
                object_key = None

        return await self._db.fetchval(
            """
            INSERT INTO source_documents (
                provider, original_filename, byte_size, sha256, object_key, import_status
            ) VALUES ($1,$2,$3,$4,$5,'parsed')
            ON CONFLICT (sha256) DO UPDATE
                SET import_status = 'parsed',
                    object_key = coalesce(source_documents.object_key, EXCLUDED.object_key),
                    updated_at = now()
            RETURNING id
            """,
            provider,
            archive.name,
            (await asyncio.to_thread(archive.stat)).st_size,
            digest,
            object_key,
        )

    async def _import_one(self, conversation: NormalizedConversation, report: ImportReport) -> None:
        # The conversation, its messages and how current it is land together.
        async with self._db.transaction() as conn:
            conversation_id, created = await self._sessions.ensure_conversation(
                provider=conversation.provider,
                provider_conversation_id=conversation.provider_conversation_id or None,
                title=conversation.title,
                model=conversation.model,
                imported=True,
                started_at=conversation.created_at,
                conn=conn,
            )
            inserted = await self._sessions.add_messages(
                conversation_id,
                [
                    {
                        "role": m.role,
                        "content": m.content,
                        "provenance": m.provenance,
                        "created_at": m.created_at,
                        "provider_message_id": m.provider_message_id,
                        "model": m.model,
                        "metadata": m.metadata,
                    }
                    for m in conversation.messages
                ],
                provider=conversation.provider,
                conn=conn,
            )
            # What sync-state reports to the extension, so a chat imported from
            # an export is recognised as current instead of captured again.
            if conversation.updated_at is not None:
                await conn.execute(
                    """
                    UPDATE conversations
                    SET provider_updated_at = greatest(provider_updated_at, $2::timestamptz)
                    WHERE id = $1
                    """,
                    conversation_id,
                    conversation.updated_at,
                )
        report.conversations_imported += 1 if created else 0
        report.conversations_skipped += 0 if created else 1
        report.messages_imported += inserted

        sensitivity = await self._router.screen(
            conversation_id,
            [conversation.title or "", *(m.content for m in conversation.messages)],
        )
        if sensitivity != "normal":
            report.conversations_screened_sensitive += 1

        for attachment in conversation.attachments:
            async with self._attachment_lock:
                await self._import_attachment(
                    attachment,
                    conversation=conversation,
                    conversation_id=conversation_id,
                    sensitivity=sensitivity,
                    report=report,
                )

        # Nothing further to do for a conversation we have seen before;
        # `subzero reclassify-conversations` revisits old ones.
        if not created:
            return

        await self._router.route(
            conversation_id=conversation_id,
            provider=conversation.provider,
            title=conversation.title,
            summary=derive_summary(conversation),
            message_count=len(conversation.messages),
            sensitivity=sensitivity,
            report=report,
            transcript=classification_transcript(conversation, self._router.transcript_budget()),
        )

    async def _import_attachment(
        self,
        attachment: NormalizedAttachment,
        *,
        conversation: NormalizedConversation,
        conversation_id: Any,
        sensitivity: str,
        report: ImportReport,
    ) -> None:

        if not attachment.captured:
            await self._artifacts.register_reference(
                title=attachment.filename,
                description=(
                    f"Referenced in an imported {conversation.provider} conversation. "
                    "The export did not contain this file's contents."
                ),
                status="pending_import",
                source_provider=conversation.provider,
                source_type="imported",
                source_conversation_id=conversation_id,
                expected_files=[
                    {
                        "filename": attachment.filename,
                        "mime_type": attachment.mime_type,
                        "byte_size": attachment.byte_size,
                    }
                ],
                metadata=attachment.metadata,
            )
            report.attachments_referenced += 1
            return

        assert attachment.data is not None
        digest = sha256_bytes(attachment.data)
        duplicate = await self._artifacts.find_by_hash(digest)
        if duplicate is not None:
            # Same bytes already stored, possibly from another provider's
            # export. Link it to this conversation instead of storing twice.
            await self._artifacts.relate(
                duplicate,
                relation="related_to_conversation",
                target_type="conversation",
                target_id=conversation_id,
                relationship_status="confirmed",
                provenance="imported_metadata",
            )
            return

        record = await self._artifacts.save_files(
            title=attachment.filename,
            description=f"Imported from a {conversation.provider} export.",
            files={attachment.filename: attachment.data},
            source_provider=conversation.provider,
            source_type="imported",
            source_conversation_id=conversation_id,
            original_names={attachment.filename: attachment.filename},
        )
        report.attachments_captured += 1

        # Artifacts from a bulk import are classified but never auto-promoted
        # to the vault; historical thresholds apply.
        await self._classification.classify_artifact(
            {
                "title": record.title,
                "artifact_type": record.artifact_type,
                "source_type": "imported",
                "source_provider": conversation.provider,
                "file_paths": [f.relative_path for f in record.files],
                "conversation_title": conversation.title or "",
                "sensitivity": sensitivity,
            },
            object_id=record.id,
            historical_import=True,
        )
