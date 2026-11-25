"""Composition root.

Builds every service once, in dependency order, and returns them in one
:class:`Services` object. Only the server and the CLI read that object, and
they hand each tool module, route and command just the pieces it uses. Nothing
else imports this module, so no code can reach for "everything". Explicit
wiring rather than a DI framework: the graph is small enough to read in one
screen, and the order of construction is itself documentation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from subzero.agents.service import AgentService
from subzero.artifacts.filing import ArtifactFiling
from subzero.artifacts.service import ArtifactService
from subzero.audit.service import AuditService
from subzero.auth.kv import PostgresKeyValue
from subzero.bridge.service import BridgeService
from subzero.classifier.base import Classifier
from subzero.classifier.jev import build_classifier
from subzero.classifier.service import ClassificationService
from subzero.config import Settings
from subzero.db.pool import Database
from subzero.email import EmailService
from subzero.embeddings.service import EmbeddingService, build_embedding_provider
from subzero.extraction.deep import DeepExtractor
from subzero.importers.routing import ConversationRouter
from subzero.importers.runner import ImportRunner
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
from subzero.wiki.service import WikiService

if TYPE_CHECKING:
    import anthropic


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
    notes: NoteService
    artifacts: ArtifactService
    filing: ArtifactFiling
    classification: ClassificationService
    retrieval: RetrievalService
    review: ReviewService
    sessions: SessionService
    sync_queue: SyncQueue
    vault_paths: VaultPaths
    oauth_kv: PostgresKeyValue
    email: EmailService
    agents: AgentService
    bridge: BridgeService
    router: ConversationRouter
    importer: ImportRunner
    wiki: WikiService

    def deep_extractor(self, client: anthropic.AsyncAnthropic | None = None) -> DeepExtractor:
        """A fresh extractor; tests pass a stand-in client."""
        return DeepExtractor(
            db=self.db,
            notes=self.notes,
            projects=self.projects,
            sessions=self.sessions,
            settings=self.settings,
            sync_queue=self.sync_queue,
            vault_paths=self.vault_paths,
            client=client,
        )

    async def aclose(self) -> None:
        await self.embeddings.aclose()
        await self.classifier.aclose()
        await self.store.aclose()
        await self.db.close()


def build_services(
    settings: Settings, db: Database, *, classifier: Classifier | None = None
) -> Services:
    """Build every service. ``classifier`` replaces the configured one (tests)."""
    store = build_object_store(settings)
    classifier = classifier or build_classifier(settings)
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
    sessions = SessionService(
        db,
        memories=memories,
        projects=projects,
        classification=classification,
        embeddings=embeddings,
        audit=audit,
    )
    bridge = BridgeService(db)
    filing = ArtifactFiling(
        artifacts=artifacts,
        classification=classification,
        projects=projects,
        settings=settings,
        sync_queue=sync_queue,
        vault_paths=vault_paths,
    )

    router = ConversationRouter(
        classification=classification,
        db=db,
        memories=memories,
        notes=notes,
        projects=projects,
        sessions=sessions,
        settings=settings,
    )
    importer = ImportRunner(
        artifacts=artifacts,
        classification=classification,
        db=db,
        router=router,
        sessions=sessions,
        settings=settings,
        store=store,
    )
    wiki = WikiService(
        classification=classification,
        db=db,
        settings=settings,
        sync_queue=sync_queue,
        vault_paths=vault_paths,
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
        notes=notes,
        artifacts=artifacts,
        filing=filing,
        classification=classification,
        retrieval=retrieval,
        review=review,
        sessions=sessions,
        sync_queue=sync_queue,
        vault_paths=vault_paths,
        oauth_kv=PostgresKeyValue(db),
        email=EmailService(db, settings),
        agents=AgentService(db, classifier, settings),
        bridge=bridge,
        router=router,
        importer=importer,
        wiki=wiki,
    )
