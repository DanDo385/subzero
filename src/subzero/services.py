"""Service container.

One object holding every service, built once at startup and reached from tool
functions through :func:`get_services`. Explicit wiring rather than a DI
framework: the graph is small enough to read in one screen, and the order of
construction is itself documentation.
"""

from __future__ import annotations

from dataclasses import dataclass

from subzero.artifacts.service import ArtifactService
from subzero.audit.service import AuditService
from subzero.auth.kv import PostgresKeyValue
from subzero.classifier.base import Classifier
from subzero.classifier.jev import build_classifier
from subzero.classifier.service import ClassificationService
from subzero.config import Settings
from subzero.db.pool import Database
from subzero.embeddings.service import EmbeddingService, build_embedding_provider
from subzero.lists.service import ListService
from subzero.memory.service import MemoryService
from subzero.notes.service import NoteService
from subzero.obsidian.paths import VaultPaths
from subzero.obsidian.queue import SyncQueue
from subzero.projects.service import ProjectService
from subzero.retrieval.service import RetrievalService
from subzero.review.service import ReviewService
from subzero.sessions.service import SessionService
from subzero.storage import build_object_store
from subzero.storage.base import ObjectStore


@dataclass
class Services:
    settings: Settings
    db: Database
    store: ObjectStore
    classifier: Classifier
    embeddings: EmbeddingService
    audit: AuditService
    projects: ProjectService
    memories: MemoryService
    lists: ListService
    notes: NoteService
    artifacts: ArtifactService
    classification: ClassificationService
    retrieval: RetrievalService
    review: ReviewService
    sessions: SessionService
    sync_queue: SyncQueue
    vault_paths: VaultPaths
    oauth_kv: PostgresKeyValue

    async def aclose(self) -> None:
        await self.embeddings.aclose()
        await self.classifier.aclose()
        await self.store.aclose()
        await self.db.close()


async def build_services(settings: Settings, db: Database) -> Services:
    store = build_object_store(settings)
    classifier = build_classifier(settings)
    embeddings = EmbeddingService(build_embedding_provider(settings), settings)

    audit = AuditService(db)
    projects = ProjectService(db)
    memories = MemoryService(db, embeddings, settings)
    notes = NoteService(db, embeddings)
    artifacts = ArtifactService(db, store, settings)
    classification = ClassificationService(db, classifier, settings)
    retrieval = RetrievalService(db, embeddings, projects, settings)
    review = ReviewService(db)
    vault_paths = VaultPaths(settings.obsidian_vault_roots)
    sync_queue = SyncQueue(db, settings.obsidian_vault_roots)
    lists = (
        ListService(db, sync_queue=sync_queue, vault_paths=vault_paths)
        if settings.obsidian_enabled
        else ListService(db)
    )

    sessions = SessionService(
        db,
        settings=settings,
        memories=memories,
        projects=projects,
        lists=lists,
        notes=notes,
        classification=classification,
        embeddings=embeddings,
        sync_queue=sync_queue,
        vault_paths=vault_paths,
        audit=audit,
    )

    return Services(
        settings=settings,
        db=db,
        store=store,
        classifier=classifier,
        embeddings=embeddings,
        audit=audit,
        projects=projects,
        memories=memories,
        lists=lists,
        notes=notes,
        artifacts=artifacts,
        classification=classification,
        retrieval=retrieval,
        review=review,
        sessions=sessions,
        sync_queue=sync_queue,
        vault_paths=vault_paths,
        oauth_kv=PostgresKeyValue(db),
    )


_services: Services | None = None


def set_services(services: Services | None) -> None:
    global _services
    _services = services


def get_services() -> Services:
    if _services is None:  # pragma: no cover - programming error
        raise RuntimeError("Services not initialised; the server lifespan did not run.")
    return _services
