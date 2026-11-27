"""Context retrieval: ranked search, and the package that starts a chat.

Search (``context_search``) runs one SQL statement over every structured
layer. The query is parsed in Python (``retrieval/query.py``): content words
are ORed, rows that match more of them score higher, and quoted phrases,
``-negation``, exact ids and the project filter keep their meaning. Ranking is
text match times who said it (the user's own words above an assistant's
summary, imported text lowest) times recency.

Bootstrap (``context_bootstrap``) fills a token budget in this order:

    1 handoff    the project's newest session summaries with their open
                 questions, open tasks, and active decisions
    2 relevant   items matching the query, ranked as above
    3 raw        transcript excerpts, only when 1 and 2 found nothing

The project is the one passed, or one read from the query with the reason
said out loud; when the query could mean two projects, both get a short
handoff. Decisions that a newer item may overrule are flagged, never silently
dropped or silently preferred. Every layer reports whether it ran and why.

Nothing on these paths calls a model, the classifier or the vault: a few SQL
statements and some arithmetic.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import asyncpg

from subzero import agent_context
from subzero.agent_context import read_standing
from subzero.config import Settings
from subzero.db.pool import Database
from subzero.embeddings.service import EmbeddingService
from subzero.homes import validate_home
from subzero.models import (
    ContextItem,
    ContextPackage,
)
from subzero.projects.service import ProjectService
from subzero.retrieval import inference, semantic
from subzero.retrieval.inference import name_tsquery
from subzero.retrieval.items import (
    KIND_WEIGHT,
    PROVENANCE_WEIGHT,
)
from subzero.retrieval.items import (
    MAX_ITEM_TOKENS as _MAX_ITEM_TOKENS,
)
from subzero.retrieval.items import (
    item as _item,
)
from subzero.retrieval.items import (
    recency as _recency,
)
from subzero.retrieval.package import PackageBuilder
from subzero.retrieval.query import ParsedQuery, parse
from subzero.retrieval.semantic import DeepSearchResult
from subzero.retrieval.sql import (
    CONFLICT_SQL as _CONFLICT_SQL,
)
from subzero.retrieval.sql import (
    SEARCH_SQL as _SEARCH_SQL,
)


@dataclass
class _Conflict:
    older: UUID
    newer: UUID
    kind: Literal["superseded", "possible_conflict"]
    newer_kind: str
    newer_provenance: str
    newer_body: str | None
    #: Topic words the two share; more means more likely the same subject.
    shared: int = 0


@dataclass
class _Budget:
    total: int
    spent: int = 0

    def fits(self, cost: int, ceiling: int | None = None) -> bool:
        limit = self.total if ceiling is None else min(self.total, ceiling)
        return self.spent + cost <= limit

    def take(self, cost: int) -> None:
        self.spent += cost


class RetrievalService:
    def __init__(
        self,
        db: Database,
        embeddings: EmbeddingService,
        projects: ProjectService,
        settings: Settings,
    ) -> None:
        self._db = db
        # Search makes no embedding call: no model on the read path. The one
        # exception is bootstrap's optional semantic layer (one call, short
        # timeout, skipped when embeddings are off): see _semantic_layer.
        self._embeddings = embeddings
        self._projects = projects
        self._settings = settings

    # ------------------------------------------------------------ projects

    async def _project_rows(self) -> list[inference.ProjectRow]:
        # Cached in ProjectService (30 s TTL, invalidated on create/update):
        # inference scans the whole table on every bootstrap and search.
        rows = await self._projects.list_rows()
        return [inference.ProjectRow.from_record(r) for r in rows]

    # ----------------------------------------------------------- searching

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        project: str | None = None,
        provider: str | None = None,
        include_messages: bool = False,
    ) -> list[ContextItem]:
        """Ranked search across every structured layer in one statement."""
        rows = await self._project_rows() if project else []
        match = inference.resolve_reference(project, rows) if project else None
        if project and match is None:
            return []
        return await self._search(
            parse(query),
            limit=limit,
            project=match.project if match else None,
            provider=provider,
            include_messages=include_messages,
        )

    async def _search(
        self,
        parsed: ParsedQuery,
        *,
        limit: int,
        project: inference.ProjectRow | None,
        provider: str | None = None,
        include_messages: bool = False,
        include_unscoped: bool = False,
        exclude: set[UUID] | None = None,
        max_item_tokens: int = _MAX_ITEM_TOKENS,
        flag_conflicts: bool = True,
    ) -> list[ContextItem]:
        if parsed.is_empty:
            return []
        coverage_queries = parsed.coverage_queries()
        rows = await self._db.fetch(
            _SEARCH_SQL,
            parsed.match_query(),
            parsed.rank_query(),
            coverage_queries,
            parsed.phrase_query(),
            list(parsed.ids),
            project.id if project else None,
            provider,
            max(limit * 2, 10),
            include_messages,
            include_unscoped,
            name_tsquery(project),
        )

        exclude = exclude or set()
        wanted = max(1, len(coverage_queries))
        # With three or more terms, a row must match a fair share of them: one
        # common word ("notes") is not enough to call a row relevant.
        needed = min(3, math.ceil(wanted / 3)) if wanted >= 3 else 1
        scored: list[tuple[float, asyncpg.Record]] = []
        for row in rows:
            if row["id"] in exclude:
                continue
            if row["exact"]:
                text = 100.0
            elif row["coverage"] < needed and not row["phrase_hit"]:
                continue
            else:
                coverage = min(1.0, row["coverage"] / wanted)
                text = (
                    0.15
                    + 0.85 * coverage
                    + 0.1 * min(1.0, row["rank"] * 3)
                    + (0.3 if row["phrase_hit"] else 0.0)
                )
            # Who said it and what kind of item break ties between similar
            # matches; they do not let a weak match outrank a strong one.
            score = (
                text
                * (0.6 + 0.4 * PROVENANCE_WEIGHT.get(row["provenance"], 0.7))
                * (0.7 + 0.3 * KIND_WEIGHT.get(row["kind"], 0.8))
                * _recency(row["effective_at"])
            )
            if row["status"] == "superseded":
                score *= 0.3
            scored.append((score, row))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        items: list[ContextItem] = []
        seen_text: set[str] = set()
        for score, row in scored:
            normalized = " ".join(row["body"].lower().split())
            if normalized in seen_text:
                continue
            seen_text.add(normalized)
            items.append(
                _item(
                    kind=row["kind"],
                    row=row,
                    content=row["body"],
                    title=row["title"],
                    score=score,
                    max_tokens=max_item_tokens,
                )
            )
            if len(items) >= limit:
                break
        if flag_conflicts:
            self._mark_conflicts(items, await self._find_conflicts(items))
        return items

    # ------------------------------------------------------------ conflicts

    async def _find_conflicts(self, items: list[ContextItem]) -> list[_Conflict]:
        """Pairs where a newer item may overrule a returned decision. One query.

        An explicit supersede link is certain. Shared topic words are not, so
        those are possible conflicts for the user to settle; the older decision
        is never silently preferred or silently dropped.
        """
        found: list[_Conflict] = [
            _Conflict(
                older=i.id,
                newer=i.superseded_by,
                kind="superseded",
                newer_kind="decision",
                newer_provenance="user_statement",
                newer_body=None,
            )
            for i in items
            if i.status == "superseded" and i.superseded_by
        ]
        # Bounded: the conflict query samples up to 300 rows per decision, so
        # checking the whole result list would make a broad search pay for
        # its tail. The package path already passes at most ten.
        decision_ids = [i.id for i in items if i.type == "decision" and i.status != "superseded"][
            :10
        ]
        if not decision_ids:
            return found
        for row in await self._db.fetch(_CONFLICT_SQL, decision_ids):
            older_lex = set(row["older_lex"] or [])
            shared = older_lex & set(row["lex"] or [])
            if not older_lex or not (
                len(shared) >= 3 or (len(shared) >= 2 and len(shared) / len(older_lex) >= 0.5)
            ):
                continue
            found.append(
                _Conflict(
                    older=row["older_id"],
                    newer=row["id"],
                    kind="possible_conflict",
                    newer_kind=row["kind"],
                    newer_provenance=row["provenance"],
                    newer_body=row["body"],
                    shared=len(shared),
                )
            )
        return found

    @staticmethod
    def _mark_conflicts(items: list[ContextItem], conflicts: list[_Conflict]) -> None:
        """Point each item at the other side of its conflicts, when both are present."""
        by_id = {i.id: i for i in items}
        for conflict in conflicts:
            if conflict.kind == "superseded":
                continue  # carried by status and superseded_by
            older, newer = by_id.get(conflict.older), by_id.get(conflict.newer)
            if older is not None and conflict.newer not in older.conflicts_with:
                older.conflicts_with.append(conflict.newer)
            if older is not None and newer is not None and older.id not in newer.conflicts_with:
                newer.conflicts_with.append(older.id)

    # ------------------------------------------------------------- recent

    async def recent(
        self, *, limit: int = 10, provider: str | None = None, project: str | None = None
    ) -> list[ContextItem]:
        project_id = await self._projects.resolve(project) if project else None
        rows = await self._db.fetch(
            """
            SELECT s.id, s.title, s.summary, s.provider AS source_provider,
                   s.conversation_id, s.updated_at, p.slug AS project_slug
            FROM session_summaries s LEFT JOIN projects p ON p.id = s.project_id
            WHERE ($1::text IS NULL OR s.provider = $1)
              AND ($2::uuid IS NULL OR s.project_id = $2)
            ORDER BY s.updated_at DESC LIMIT $3
            """,
            provider,
            project_id,
            limit,
        )
        return [_item(kind="summary", row=r, content=r["summary"], title=r["title"]) for r in rows]

    async def deep_search(
        self,
        query: str,
        *,
        limit: int = 8,
        provider: str | None = None,
    ) -> DeepSearchResult:
        """Hybrid message search for memory_deep_search (one embedding call when enabled)."""
        return await semantic.deep_search(
            self._db,
            self._embeddings,
            query,
            limit=limit,
            provider=provider,
        )

    # ---------------------------------------------------------- packages

    async def for_topic(
        self, topic: str, *, token_budget: int | None = None, project: str | None = None
    ) -> ContextPackage:
        return await self._build_package(
            query=topic,
            token_budget=token_budget or self._settings.default_token_budget,
            project=project,
            include_handoff=False,
        )

    async def bootstrap(
        self,
        query: str | None = None,
        *,
        user_message: str | None = None,
        token_budget: int | None = None,
        project: str | None = None,
        home: str | None = None,
    ) -> ContextPackage:
        """The package that starts a chat: handoff, then relevant items, then raw.

        ``query`` is a few key terms chosen by the calling model. A whole
        ``user_message`` (the older calling convention) still works: it is
        reduced to its key terms here rather than searched as one block.
        """
        text = query if query and query.strip() else (user_message or "")
        notes: list[str] = []
        if user_message and not (query and query.strip()):
            notes.append(
                "user_message is deprecated; pass query (key terms). Reduced to key terms."
            )
        if home is not None:
            validate_home(home)
            if home.startswith("projects/"):
                project = project or home.removeprefix("projects/")
                home = None
        # The standing context (dotfiles header, SOUL, chat AGENTS) is read
        # while the package is built, and sent on top of token_budget: it is
        # the chat's instructions, not a retrieval result competing for room.
        standing, package = await asyncio.gather(
            read_standing(self._settings),
            self._build_package(
                query=text,
                token_budget=token_budget or self._settings.default_token_budget,
                project=project,
                include_handoff=True,
                notes=notes,
                home=home,
            ),
        )
        package.standing = standing
        if not agent_context.configured(self._settings):
            package.notes.append("No dotfiles source is configured on this server.")
        elif not standing:
            package.notes.append("Dotfiles source configured, but no standing document was read.")
        return package

    async def _build_package(
        self,
        *,
        query: str,
        token_budget: int,
        project: str | None,
        include_handoff: bool,
        notes: list[str] | None = None,
        home: str | None = None,
    ) -> ContextPackage:
        return await PackageBuilder(self).build(
            query=query,
            token_budget=token_budget,
            project=project,
            include_handoff=include_handoff,
            notes=notes,
            home=home,
        )
