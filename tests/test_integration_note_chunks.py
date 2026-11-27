"""Note chunks follow the body and are searchable by meaning.

Rebuild is delete + insert inside the writing transaction (upsert and vault
edits), so chunks can never describe a previous version. Vector search over
chunks uses hand-placed vectors: no embedding provider is needed to prove
the SQL orders by distance and the mapping names the section.
"""

from __future__ import annotations

from conftest import requires_db

pytestmark = requires_db

BODY = """\
# Plan

Risks are listed here, at length, with mitigation notes for each one we
could think of during the review session and after it ended too, plus
the follow-up actions and owners assigned to every single mitigation.

## Risks

The first risk is delay, the second risk is cost, and both need owners
assigned before the next milestone review happens in due course, with
weekly check-ins until each risk is retired or accepted by the team.

## Budget

Numbers live here: the budget table, the forecast, and the variance
explanations attached to every single line item we approved, including
the contingency reserve and the rules for spending it down over time.
"""


async def test_upsert_builds_chunks_at_headings(services) -> None:
    note_id, created, _ = await services.notes.upsert(title="Chunked", content=BODY)
    assert created
    rows = await services.db.fetch(
        "SELECT heading_path, chunk_index FROM note_chunks WHERE note_id = $1 ORDER BY chunk_index",
        note_id,
    )
    assert [r["heading_path"] for r in rows] == ["Plan", "Plan > Risks", "Plan > Budget"]
    assert [r["chunk_index"] for r in rows] == [0, 1, 2]
    assert await services.db.fetchval(
        "SELECT count(*) FROM note_chunks WHERE note_id = $1 AND embedding IS NULL", note_id
    ) == len(rows)


async def test_rewrite_replaces_chunks_not_appends(services) -> None:
    note_id, _, _ = await services.notes.upsert(title="Chunked", content=BODY)
    await services.notes.upsert(title="Chunked", content="# Solo\n\n" + "word " * 120 + "\n")
    rows = await services.db.fetch(
        "SELECT heading_path FROM note_chunks WHERE note_id = $1", note_id
    )
    assert [r["heading_path"] for r in rows] == ["Solo"]


async def test_chunk_search_orders_by_distance(services) -> None:
    from subzero.db.pool import vector_literal
    from subzero.retrieval import semantic

    note_id, _, _ = await services.notes.upsert(title="Chunked", content=BODY)
    rows = await services.db.fetch(
        "SELECT id, heading_path FROM note_chunks WHERE note_id = $1 ORDER BY chunk_index",
        note_id,
    )
    assert len(rows) == 3
    dim = 1536
    risks = [0.0] * dim
    risks[0] = 1.0
    plan_vec = [0.0] * dim
    plan_vec[0], plan_vec[1] = 0.6, 0.8
    budget_vec = [0.0] * dim
    budget_vec[0], budget_vec[1] = 0.4, 0.9165
    vecs = {"Plan > Risks": risks, "Plan": plan_vec, "Plan > Budget": budget_vec}
    for row in rows:
        await services.db.execute(
            "UPDATE note_chunks SET embedding = $2::text::vector WHERE id = $1",
            row["id"],
            vector_literal(vecs[row["heading_path"]]),
        )
    hits = await semantic.chunk_hits(services.db, risks, limit=8)
    assert [h["heading_path"] for h in hits] == ["Plan > Risks", "Plan", "Plan > Budget"]
    assert hits[0]["note_id"] == note_id
    assert hits[0]["title"] == "Chunked"
