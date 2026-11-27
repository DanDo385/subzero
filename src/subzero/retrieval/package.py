"""Assemble context packages for bootstrap and topic retrieval."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING
from uuid import UUID

from subzero.models import (
    AgentContext,
    ConflictReport,
    ContextItem,
    ContextPackage,
    LayerReport,
    ProjectCandidate,
    ProjectChoice,
)
from subzero.retrieval import inference, semantic
from subzero.retrieval.inference import name_tsquery
from subzero.retrieval.items import (
    COMPACT_BELOW as _COMPACT_BELOW,
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
    wire_tokens as _wire_tokens,
)
from subzero.retrieval.query import ParsedQuery, parse
from subzero.retrieval.sql import (
    HANDOFF_SQL as _HANDOFF_SQL,
)
from subzero.retrieval.sql import (
    HOME_HANDOFF_SQL as _HOME_HANDOFF_SQL,
)
from subzero.sessions.checkpoint import question_key
from subzero.util import estimate_tokens, truncate_for_budget

if TYPE_CHECKING:
    from subzero.retrieval.service import RetrievalService


@dataclass
class _Budget:
    total: int
    spent: int = 0

    def fits(self, cost: int, ceiling: int | None = None) -> bool:
        limit = self.total if ceiling is None else min(self.total, ceiling)
        return self.spent + cost <= limit

    def take(self, cost: int) -> None:
        self.spent += cost


class PackageBuilder:
    """Bootstrap and topic packages; RetrievalService delegates here."""

    def __init__(self, svc: RetrievalService) -> None:
        self._svc = svc

    async def build(
        self,
        *,
        query: str,
        token_budget: int,
        project: str | None,
        include_handoff: bool,
        notes: list[str] | None = None,
        home: str | None = None,
        guidance: AgentContext | None = None,
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
        rows = await self._svc._project_rows()
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
            relevant = await self._svc._search(
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
            found = await self._svc._search(
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
        conflicts = await self._svc._find_conflicts(likely)
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

        guidance_cost = estimate_tokens(guidance.model_dump_json()) if guidance else 0
        budget = _Budget(token_budget, spent=_PACKAGE_OVERHEAD + guidance_cost)
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
        self._svc._mark_conflicts(
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
            agent_context=guidance,
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
        if not self._svc._settings.semantic_bootstrap:
            return [], "turned off (SUBZERO_SEMANTIC_BOOTSTRAP)"
        if not self._svc._embeddings.enabled:
            return [], "embeddings not configured"
        vector = await semantic.embed_query(self._svc._embeddings, query)
        if vector is None:
            return [], "query could not be embedded in time"
        hits = await semantic.semantic_messages(self._svc._db, vector, limit=8)
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
        rows = await self._svc._db.fetch(_HOME_HANDOFF_SQL, home, 3)
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
        rows = await self._svc._db.fetch(
            _HANDOFF_SQL,
            project.id,
            name_tsquery(project),
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
