"""Turning gathered candidates into a context package that fits its budget.

Pure functions over items already fetched: no database, no network. The
retrieval service gathers candidates from each layer and hands them here to be
ranked, paired with their conflicts, cut to the token budget and put in
reading order.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from subzero.models import ConflictReport, ContextItem, ContextPackage, LayerReport
from subzero.util import estimate_tokens, truncate_for_budget

#: Starting estimate for the package's own fields (project, layers, notes).
#: The finished package is measured and trimmed to the budget anyway.
PACKAGE_OVERHEAD = 110
#: Conflict reports per package; the rest are dropped, newest-statement first kept.
MAX_CONFLICT_REPORTS = 3

NOTHING_MATCHED = (
    "Nothing in Subzero matched. Answer from the current conversation; "
    "do not assume prior context exists."
)

#: Reading order of the layers, which is also the order of `layers_used`.
LAYER_RANK = {"handoff": 0, "relevant": 1, "semantic": 2, "raw": 3}


@dataclass
class Conflict:
    older: UUID
    newer: UUID
    kind: Literal["superseded", "possible_conflict"]
    newer_kind: str
    newer_provenance: str
    newer_body: str | None
    #: Topic words the two share; more means more likely the same subject.
    shared: int = 0


@dataclass
class Budget:
    total: int
    spent: int = 0

    def fits(self, cost: int, ceiling: int | None = None) -> bool:
        limit = self.total if ceiling is None else min(self.total, ceiling)
        return self.spent + cost <= limit

    def take(self, cost: int) -> None:
        self.spent += cost


def wire_tokens(model: ContextItem | ContextPackage) -> int:
    """Tokens the object costs as sent: the tool's JSON at 3.6 characters a token."""
    # Imported here: the mcp package imports the tools, which import services.
    from subzero.mcp.compact import dump

    return estimate_tokens(json.dumps(dump(model)))


def cost(item: ContextItem) -> int:
    return wire_tokens(item)


@dataclass
class Candidates:
    """Every layer's candidates, before anything is cut."""

    handoffs: list[list[ContextItem]]
    relevant: list[ContextItem]
    semantic: list[ContextItem]
    raw: list[ContextItem]

    def in_layer_order(self) -> list[ContextItem]:
        return [*(i for h in self.handoffs for i in h), *self.relevant, *self.semantic, *self.raw]

    def by_id(self) -> dict[UUID, ContextItem]:
        return {i.id: i for i in self.in_layer_order()}


def stamp_layers(c: Candidates, *, focus_slug: str | None, compact: bool) -> None:
    """Name each item's layer, drop what the package already says, and price it."""
    for layer, group in (
        ("handoff", [i for h in c.handoffs for i in h]),
        ("relevant", c.relevant),
        ("semantic", c.semantic),
        ("raw", c.raw),
    ):
        for item in group:
            item.layer = layer
            if focus_slug is not None and item.project == focus_slug:
                item.project = None  # the package names it once
            if compact or item.type != "summary":
                item.provenance.conversation_id = None
            item.estimated_tokens = cost(item)


def likely_conflicted(c: Candidates) -> list[ContextItem]:
    """Each checked decision costs a lookup, so check the ones that matter:
    those matching the query first, then the handoff's newest."""
    likely = [i for i in c.relevant if i.type == "decision"][:5]
    for h in c.handoffs:
        likely += [i for i in h if i.type == "decision"][:5]
    return likely


def priority_order(c: Candidates) -> list[ContextItem]:
    """The newest session, the two best matches for the query, two open tasks,
    two decisions, older sessions, then everything else."""
    order: list[ContextItem] = []
    for h in c.handoffs:
        order += h[:1]  # newest session of each project
    order += c.relevant[:2]
    for h in c.handoffs:
        order += h[1:5]  # two open tasks, two decisions
    for h in c.handoffs:
        order += h[5:]
    order += c.relevant[2:] + c.semantic + c.raw
    return order


@dataclass
class Selection:
    chosen: dict[UUID, ContextItem]
    #: Lower is kept longer when the package has to be trimmed.
    priority: dict[UUID, int]
    left_out: int


def fill_by_priority(
    order: list[ContextItem],
    *,
    conflicts: list[Conflict],
    everything: dict[UUID, ContextItem],
    token_budget: int,
) -> Selection:
    """Take items in priority order while they fit; a conflict's other side comes along."""
    partners: dict[UUID, list[UUID]] = {}
    for c in conflicts:
        partners.setdefault(c.older, []).append(c.newer)
        partners.setdefault(c.newer, []).append(c.older)

    budget = Budget(token_budget, spent=PACKAGE_OVERHEAD)
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
    return Selection(chosen=chosen, priority=priority, left_out=left_out)


def conflict_reports(
    conflicts: list[Conflict],
    *,
    chosen: dict[UUID, ContextItem],
    everything: dict[UUID, ContextItem],
) -> list[ConflictReport]:
    """One report per decision that made it in: its strongest challenger."""
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
        if len(reports) == MAX_CONFLICT_REPORTS:
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
    return reports


def reading_order(
    shown: list[ContextItem], c: Candidates
) -> tuple[list[ContextItem], dict[str, int]]:
    """Handoff, relevant, semantic, raw; within a layer, the order it was found in."""
    position = {i.id: n for n, i in enumerate(c.in_layer_order())}
    items = sorted(shown, key=lambda i: (LAYER_RANK[i.layer or "relevant"], position[i.id]))
    counts = {name: sum(1 for i in items if i.layer == name) for name in LAYER_RANK}
    return items, counts


def trim_to_budget(
    package: ContextPackage, *, priority: dict[UUID, int], fallback: int, token_budget: int
) -> None:
    """The budget covers the whole result as sent. If the estimate was short,
    drop the lowest-priority items until it fits."""
    size = wire_tokens(package)
    while size > token_budget and package.items:
        worst = max(package.items, key=lambda i: (priority.get(i.id, fallback), i.layer))
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
        size = wire_tokens(package)
    package.estimated_tokens = size


def report_layers(
    layers: list[LayerReport],
    *,
    ran_handoff: bool,
    focused: bool,
    home_handoff: bool,
    home: str | None,
    searched_empty: bool,
    parsed_empty: bool,
    semantic_reason: str | None,
    had_raw: bool,
    structured: bool,
    counts: dict[str, int],
    compact: bool,
    notes: list[str],
) -> list[LayerReport]:
    """Say which layers ran, which were skipped and why; adds a note for raw excerpts."""
    if ran_handoff:
        layers.append(
            LayerReport(
                name="handoff",
                status="ran",
                reason=None
                if focused
                else f"sessions filed under {home}"
                if home_handoff
                else "short handoff per candidate project",
                items=counts["handoff"] or None,
            )
        )
    if searched_empty:
        layers.append(
            LayerReport(
                name="relevant",
                status="skipped",
                reason="no query terms" if parsed_empty else "query only names the project",
            )
        )
    else:
        layers.append(LayerReport(name="relevant", status="ran", items=counts["relevant"] or None))
    # Reported only when it ran or failed: a routine skip (embeddings not
    # configured, no query) would spend small budgets on noise.
    if semantic_reason is None:
        layers.append(LayerReport(name="semantic", status="ran", items=counts["semantic"] or None))
    elif semantic_reason.startswith("query could not"):
        layers.append(LayerReport(name="semantic", status="skipped", reason=semantic_reason))
    if had_raw or (not structured and not searched_empty):
        layers.append(LayerReport(name="raw_messages", status="ran", items=counts["raw"] or None))
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
                reason="not needed" if (structured) else "no query terms",
            )
        )
    if compact:
        # layers_used already names the layers that returned items.
        layers = [r for r in layers if r.status == "skipped" or not r.items]
    return layers
