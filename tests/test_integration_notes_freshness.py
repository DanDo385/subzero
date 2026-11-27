"""Stale (as of ...) markers surface for review instead of rotting silently."""

from __future__ import annotations

from conftest import requires_db

pytestmark = requires_db


async def test_markers_newer_than_the_window_are_not_stale(services) -> None:
    await services.notes.upsert(
        title="Fresh",
        content="The pipeline has 3 deals (as of 2099-01-01, crm.example.com).",
    )
    assert await services.notes.stale_markers(days=7) == []


async def test_oldest_marker_per_note_is_reported(services) -> None:
    await services.notes.upsert(
        title="Stale",
        content=(
            "The pipeline has 3 deals (as of 2020-01-01, crm.example.com).\n\n"
            "Still 3 deals (as of 2021-06-01, crm.example.com)."
        ),
    )
    await services.notes.upsert(
        title="Timeless",
        content="Deals live in the CRM. Invoices are issued monthly.",
    )
    stale = await services.notes.stale_markers(days=7)
    assert [(r["title"], r["oldest"]) for r in stale] == [("Stale", "2020-01-01")]


async def test_filing_a_review_is_idempotent(services) -> None:
    note_id, _, _ = await services.notes.upsert(
        title="Stale",
        content="The pipeline has 3 deals (as of 2020-01-01, crm.example.com).",
    )
    for _ in range(2):
        await services.classification.queue_review(
            object_type="note",
            object_id=note_id,
            reason="stale_marker",
            question="Refresh the observation.",
        )
    rows = await services.db.fetch(
        "SELECT status FROM review_queue WHERE object_type = 'note' AND reason = 'stale_marker'"
    )
    assert [r["status"] for r in rows] == ["open"]
