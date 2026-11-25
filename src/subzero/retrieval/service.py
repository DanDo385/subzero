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

import math
from dataclasses import dataclass, replace
from typing import Literal
from uuid import UUID

import asyncpg

from subzero.config import Settings
from subzero.db.pool import Database
from subzero.embeddings.service import EmbeddingService
from subzero.homes import validate_home
from subzero.models import (
    ConflictReport,
    ContextItem,
    ContextPackage,
    LayerReport,
    ProjectCandidate,
    ProjectChoice,
)
from subzero.projects.service import ProjectService
from subzero.retrieval import inference, semantic
from subzero.retrieval.items import (
    COMPACT_BELOW as _COMPACT_BELOW,
)
from subzero.retrieval.items import (
    KIND_WEIGHT,
    PROVENANCE_WEIGHT,
)
from subzero.retrieval.items import (
    MAX_CONFLICT_REPORTS as _MAX_CONFLICT_REPORTS,
)
from subzero.retrieval.items import (
    MAX_ITEM_TOKENS as _MAX_ITEM_TOKENS,
)
from subzero.retrieval.items import (
    NOTHING_MATCHED as _NOTHING_MATCHED,
)
from subzero.retrieval.items import (
    PACKAGE_OVERHEAD as _PACKAGE_OVERHEAD,
)
from subzero.retrieval.items import (
    cost as _cost,
)
from subzero.retrieval.items import (
    item as _item,
)
from subzero.retrieval.items import (
    now as _now,
)
from subzero.retrieval.items import (
    recency as _recency,
)
from subzero.retrieval.items import (
    wire_tokens as _wire_tokens,
)
from subzero.retrieval.query import ParsedQuery, parse
from subzero.retrieval.sql import (
    CONFLICT_SQL as _CONFLICT_SQL,
)
from subzero.retrieval.sql import (
    HANDOFF_SQL as _HANDOFF_SQL,
)
from subzero.retrieval.sql import (
    HOME_HANDOFF_SQL as _HOME_HANDOFF_SQL,
)
from subzero.retrieval.sql import (
    SEARCH_SQL as _SEARCH_SQL,
)
from subzero.sessions.service import question_key
from subzero.util import truncate_for_budget


def _name_query(project: inference.ProjectRow | None) -> str:
    """tsquery text matching any way of writing the project's name."""
    if project is None:
        return ""
    phrases = []
    for spelling in project.spellings():
        if len(spelling) == 1 and (len(spelling[0]) < 4 or spelling[0] in inference._GENERIC):
            continue
        phrases.append("(" + " <-> ".join(f"'{w}'" for w in spelling) + ")")
    return " | ".join(phrases)


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
        rows = await self._db.fetch(
            "SELECT id, slug, name, status, metadata FROM projects ORDER BY last_active_at DESC"
        )
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
            _name_query(project),
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
        decision_ids = [i.id for i in items if i.type == "decision" and i.status != "superseded"]
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
        return await self._build_package(
            query=text,
            token_budget=token_budget or self._settings.default_token_budget,
            project=project,
            include_handoff=True,
            notes=notes,
            home=home,
        )

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
        """Gather candidates from every layer, then fill the budget by priority.

        Priority: the newest session, the two best matches for the query (each
        with the newer item that may overrule it), two open tasks, two
        decisions, older sessions, then everything else. Items are then shown
        in reading order: handoff, relevant, raw.
        """
        notes = list(notes or [])
        parsed = parse(query)
        compact = token_budget < _COMPACT_BELOW
        item_cap = max(60, min(_MAX_ITEM_TOKENS, token_budget // 6))
        layers: list[LayerReport] = []

        # -- which project ------------------------------------------------
        rows = await self._project_rows()
        project_query = "" if home and not project else parsed.text
        choice, focus, candidates = self._choose_project(project, project_query, rows)

        # -- 1. handoff candidates ---------------------------------------
        handoffs: list[list[ContextItem]] = []
        home_handoff = include_handoff and home is not None and focus is None
        if not include_handoff:
            layers.append(LayerReport(name="handoff", status="skipped", reason="topic package"))
        elif home_handoff:
            assert home is not None
            handoffs.append(await self._home_handoff(home, item_cap=item_cap))
        elif focus is None and not candidates:
            layers.append(LayerReport(name="handoff", status="skipped", reason="no project"))
        else:
            for target in [focus] if focus else candidates[:2]:
                handoffs.append(
                    await self._handoff(
                        target, short=focus is None, item_cap=item_cap, parsed=parsed
                    )
                )
        handoff_ids = {i.id for h in handoffs for i in h}

        # -- 2. relevant candidates --------------------------------------
        # Once the project is known, its own name is not a search term: it
        # matches nearly everything the project holds.
        if focus is not None:
            name_words = {w for spelling in focus.spellings() for w in spelling}
            searched = replace(
                parsed,
                terms=tuple(t for t in parsed.terms if t not in name_words),
                compounds=tuple(c for c in parsed.compounds if not set(c) <= name_words),
            )
        else:
            searched = parsed
        relevant: list[ContextItem] = []
        if not searched.is_empty:
            relevant = await self._search(
                searched,
                limit=12,
                project=focus,
                include_unscoped=focus is not None,
                exclude=handoff_ids,
                max_item_tokens=item_cap,
                flag_conflicts=False,
            )

        # -- 3. raw candidates, only when nothing else matched -----------
        raw: list[ContextItem] = []
        if not handoff_ids and not relevant and not searched.is_empty:
            found = await self._search(
                searched,
                limit=4,
                project=focus,
                include_messages=True,
                include_unscoped=focus is not None,
                max_item_tokens=item_cap,
                flag_conflicts=False,
            )
            raw = [i for i in found if i.type == "message"]

        # -- 4. semantic candidates: past messages nearest in meaning --------
        semantic, semantic_reason = await self._semantic_layer(
            query,
            enabled=include_handoff and not searched.is_empty,
            exclude=handoff_ids | {i.id for i in relevant + raw},
            item_cap=item_cap,
        )

        for layer, group in (
            ("handoff", [i for h in handoffs for i in h]),
            ("relevant", relevant),
            ("semantic", semantic),
            ("raw", raw),
        ):
            for item in group:
                item.layer = layer
                if focus is not None and item.project == focus.slug:
                    item.project = None  # the package names it once
                if compact or item.type != "summary":
                    item.provenance.conversation_id = None
                item.estimated_tokens = _cost(item)

        everything = {i.id: i for h in handoffs for i in h} | {
            i.id: i for i in relevant + semantic + raw
        }
        # Each checked decision costs a lookup, so check the ones that matter:
        # those matching the query first, then the handoff's newest.
        likely = [i for i in relevant if i.type == "decision"][:5]
        for h in handoffs:
            likely += [i for i in h if i.type == "decision"][:5]
        conflicts = await self._find_conflicts(likely)
        partners: dict[UUID, list[UUID]] = {}
        for c in conflicts:
            partners.setdefault(c.older, []).append(c.newer)
            partners.setdefault(c.newer, []).append(c.older)

        # -- fill by priority ----------------------------------------------
        order: list[ContextItem] = []
        for h in handoffs:
            order += h[:1]  # newest session of each project
        order += relevant[:2]
        for h in handoffs:
            order += h[1:5]  # two open tasks, two decisions
        for h in handoffs:
            order += h[5:]
        order += relevant[2:] + semantic + raw

        budget = _Budget(token_budget, spent=_PACKAGE_OVERHEAD)
        chosen: dict[UUID, ContextItem] = {}
        left_out = 0

        def add(item: ContextItem) -> bool:
            if item.id in chosen:
                return True
            if not budget.fits(item.estimated_tokens):
                return False
            chosen[item.id] = item
            budget.take(item.estimated_tokens)
            return True

        priority: dict[UUID, int] = {}
        for item in order:
            if not add(item):
                left_out += 1
                continue
            priority.setdefault(item.id, len(priority))
            # The other side of a conflict travels with the item that brought it,
            # at the same priority, so the pair is kept or trimmed together.
            for other in partners.get(item.id, []):
                if other in everything and add(everything[other]):
                    priority.setdefault(other, priority[item.id])

        # -- conflict reports for what was chosen ----------------------------
        reports: list[ConflictReport] = []
        reported_older: set[UUID] = set()
        for c in sorted(
            conflicts,
            key=lambda c: (
                c.kind != "superseded",
                not (c.older in chosen and c.newer in chosen),
                c.newer_provenance != "user_statement",
                -c.shared,
            ),
        ):
            if c.older not in chosen and c.newer not in chosen:
                continue
            if c.older in reported_older:
                continue  # one report per decision: its strongest challenger
            if len(reports) == _MAX_CONFLICT_REPORTS:
                break
            reported_older.add(c.older)
            older_text = None
            if c.older not in chosen and c.older in everything:
                older_text = truncate_for_budget(everything[c.older].content, 40)
            newer_text = None
            if c.newer not in chosen:
                body = everything[c.newer].content if c.newer in everything else c.newer_body
                newer_text = truncate_for_budget(body, 40) if body else "see superseded_by"
            reports.append(
                ConflictReport(
                    kind=c.kind,
                    ids=[c.older, c.newer],
                    older=older_text,
                    newer=newer_text,
                    newer_provenance=c.newer_provenance,
                    advice=(
                        "Replaced; use the newer decision."
                        if c.kind == "superseded"
                        else f"Newer {c.newer_kind} on the same subject; ask the user which stands."
                    ),
                )
            )
        shown = [i for i in order if i.id in chosen]
        self._mark_conflicts(
            shown,
            [c for c in conflicts if any(r.ids == [c.older, c.newer] for r in reports)],
        )

        # -- reading order and layer report ----------------------------------
        rank = {"handoff": 0, "relevant": 1, "semantic": 2, "raw": 3}
        position = {
            i.id: n
            for n, i in enumerate([*(i for h in handoffs for i in h), *relevant, *semantic, *raw])
        }
        items = sorted(shown, key=lambda i: (rank[i.layer or "relevant"], position[i.id]))
        counts = {name: sum(1 for i in items if i.layer == name) for name in rank}

        if handoffs:
            layers.append(
                LayerReport(
                    name="handoff",
                    status="ran",
                    reason=None
                    if focus
                    else f"sessions filed under {home}"
                    if home_handoff
                    else "short handoff per candidate project",
                    items=counts["handoff"] or None,
                )
            )
        if searched.is_empty:
            layers.append(
                LayerReport(
                    name="relevant",
                    status="skipped",
                    reason="no query terms" if parsed.is_empty else "query only names the project",
                )
            )
        else:
            layers.append(
                LayerReport(name="relevant", status="ran", items=counts["relevant"] or None)
            )
        # Reported only when it ran or failed: a routine skip (embeddings not
        # configured, no query) would spend small budgets on noise.
        if semantic_reason is None:
            layers.append(
                LayerReport(name="semantic", status="ran", items=counts["semantic"] or None)
            )
        elif semantic_reason.startswith("query could not"):
            layers.append(LayerReport(name="semantic", status="skipped", reason=semantic_reason))
        if raw or (not handoff_ids and not relevant and not searched.is_empty):
            layers.append(
                LayerReport(name="raw_messages", status="ran", items=counts["raw"] or None)
            )
            if counts["raw"]:
                notes.append(
                    "Included raw message excerpts because nothing structured matched. "
                    "These are verbatim transcript fragments."
                )
        else:
            layers.append(
                LayerReport(
                    name="raw_messages",
                    status="skipped",
                    reason="not needed" if (handoff_ids or relevant) else "no query terms",
                )
            )
        if compact:
            # layers_used already names the layers that returned items.
            layers = [r for r in layers if r.status == "skipped" or not r.items]

        if left_out:
            notes.append(f"{left_out} more items did not fit token_budget.")
        if not items:
            notes.append(_NOTHING_MATCHED)

        package = ContextPackage(
            query=truncate_for_budget(query, 60),
            items=items,
            token_budget=token_budget,
            estimated_tokens=0,
            layers_used=[{"raw": "raw_messages"}.get(name, name) for name in rank if counts[name]],
            truncated=left_out > 0,
            notes=notes,
            keywords=parsed.keywords,
            project=choice,
            layers=layers,
            conflicts=reports,
        )
        # The budget covers the whole result as sent. If the estimate above was
        # short, drop the lowest-priority items until it fits.
        size = _wire_tokens(package)
        while size > token_budget and package.items:
            worst = max(package.items, key=lambda i: (priority.get(i.id, len(order)), i.layer))
            package.items.remove(worst)
            for report in package.conflicts:
                if report.ids[0] == worst.id and report.older is None:
                    report.older = truncate_for_budget(worst.content, 40)
                if report.ids[-1] == worst.id and report.newer is None:
                    report.newer = truncate_for_budget(worst.content, 40)
            for kept_item in package.items:
                if worst.id in kept_item.conflicts_with:
                    kept_item.conflicts_with.remove(worst.id)
            package.truncated = True
            size = _wire_tokens(package)
        package.estimated_tokens = size
        return package

    async def _semantic_layer(
        self, query: str, *, enabled: bool, exclude: set[UUID], item_cap: int
    ) -> tuple[list[ContextItem], str | None]:
        """Up to three past messages nearest the query in meaning.

        Returns (items, None) when the layer ran, or ([], reason) when skipped.
        """
        if not enabled:
            return [], "no query terms"
        if not self._settings.semantic_bootstrap:
            return [], "turned off (SUBZERO_SEMANTIC_BOOTSTRAP)"
        if not self._embeddings.enabled:
            return [], "embeddings not configured"
        vector = await semantic.embed_query(self._embeddings, query)
        if vector is None:
            return [], "query could not be embedded in time"
        hits = await semantic.semantic_messages(self._db, vector, limit=8)
        items = []
        for hit in hits:
            if hit.id in exclude:
                continue
            row = {
                "id": hit.id,
                "project_slug": hit.project,
                "provenance": hit.provenance,
                "provider": hit.provider,
                "conversation_id": hit.conversation_id,
                "created_at": hit.created_at,
            }
            items.append(
                _item(
                    kind="message",
                    row=row,
                    content=hit.content,
                    title=hit.title,
                    score=hit.similarity,
                    max_tokens=item_cap,
                )
            )
            if len(items) == 3:
                break
        return items, None

    def _choose_project(
        self, project: str | None, text: str, rows: list[inference.ProjectRow]
    ) -> tuple[ProjectChoice, inference.ProjectRow | None, list[inference.ProjectRow]]:
        if project and project.strip():
            match = inference.resolve_reference(project, rows)
            if match:
                return (
                    ProjectChoice(
                        slug=match.project.slug,
                        name=match.project.name,
                        source="given",
                        reason=match.reason,
                    ),
                    match.project,
                    [],
                )
            guess = inference.infer(project, rows)
            return (
                ProjectChoice(
                    source="none",
                    reason=f"no project matches {project!r}",
                    candidates=[
                        ProjectCandidate(slug=m.project.slug, name=m.project.name, reason=m.reason)
                        for m in guess.candidates[:2]
                    ],
                ),
                None,
                [],
            )
        found = inference.infer(text, rows)
        if found.project:
            return (
                ProjectChoice(
                    slug=found.project.slug,
                    name=found.project.name,
                    source="inferred",
                    reason=found.reason,
                ),
                found.project,
                [],
            )
        return (
            ProjectChoice(
                source="none",
                reason=found.reason,
                candidates=[
                    ProjectCandidate(slug=m.project.slug, name=m.project.name, reason=m.reason)
                    for m in found.candidates[:2]
                ],
            ),
            None,
            # Short handoffs only when the query really could mean either
            # project; a single weak hint is reported, not acted on.
            [m.project for m in found.candidates[:2]] if found.tied else [],
        )

    async def _home_handoff(self, home: str, *, item_cap: int) -> list[ContextItem]:
        """Newest sessions filed under a lesson or setup home, newest first.

        Lessons and setups are homes, not projects, so they have no tasks or
        decisions of their own; their sessions and open questions are the
        handoff that lets any app continue yesterday's lesson.
        """
        rows = await self._db.fetch(_HOME_HANDOFF_SQL, home, 3)
        items: list[ContextItem] = []
        for row in rows:
            items.append(
                _item(
                    kind="summary",
                    row=dict(row),
                    content=row["body"],
                    title=row["title"],
                    max_tokens=item_cap,
                )
            )
        return items

    async def _handoff(
        self,
        project: inference.ProjectRow,
        *,
        short: bool,
        item_cap: int,
        parsed: ParsedQuery | None = None,
    ) -> list[ContextItem]:
        """Newest summaries, open tasks and active decisions, in reading order."""
        rows = await self._db.fetch(
            _HANDOFF_SQL,
            project.id,
            _name_query(project),
            1 if short else 3,
            2 if short else 8,
            2 if short else 8,
        )
        summaries: list[ContextItem] = []
        tasks: list[ContextItem] = []
        decisions: list[ContextItem] = []
        resolved = {question_key(r["body"]) for r in rows if r["kind"] == "resolved"}
        for row in rows:
            if row["kind"] == "resolved":
                continue
            data = dict(row)
            if row["kind"] == "summary" and resolved:
                data["open_questions"] = [
                    q for q in row["open_questions"] or [] if question_key(q) not in resolved
                ]
            data["project_slug"] = project.slug
            if row["kind"] == "summary" and row["untagged"]:
                data["reason"] = f"untagged; mentions {project.name}"
            item = _item(
                kind=row["kind"],
                row=data,
                content=row["body"],
                title=row["title"],
                max_tokens=item_cap,
            )
            {"summary": summaries, "task": tasks, "decision": decisions}[row["kind"]].append(item)
        summaries.sort(key=lambda i: i.effective_at or _now(), reverse=True)
        if parsed is not None and parsed.terms:
            # Within tasks and decisions, the ones that mention the query lead.
            stems = [t[:5] for t in parsed.terms]

            def mentions(item: ContextItem) -> bool:
                text = item.content.lower()
                return any(stem in text for stem in stems)

            tasks.sort(key=lambda i: not mentions(i))
            decisions.sort(key=lambda i: not mentions(i))
        # Newest session first; then what is open; then what was decided. Older
        # sessions come after, so a tight budget still gets the latest of each.
        ordered = summaries[:1] + tasks[:2] + decisions[:2] + summaries[1:] + tasks[2:]
        return ordered + decisions[2:]
