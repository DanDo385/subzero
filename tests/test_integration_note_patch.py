"""Section patch: one address, one operation, guarded by version."""

from __future__ import annotations

import pytest

from conftest import requires_db
from subzero.errors import ConflictError

pytestmark = requires_db

BODY = """\
# Plan

The old plan text, long enough to survive chunk grouping on its own merits
and then some padding to be safe across the threshold boundary line here,
with extra sentences to push well past the minimum section length always.

## Risks

The old risks text, also long enough to stand alone as its own chunk with
room to spare for the grouping rule that joins short sections together,
plus another sentence so the section is safely above the limit for sure.
"""


async def _note(services):
    note_id, _, version = await services.notes.upsert(title="Patch me", content=BODY)
    return note_id, version


async def test_replace_section_body(services) -> None:
    _, version = await _note(services)
    out = await services.notes.patch_note(
        "patch-me",
        section="Plan > Risks",
        operation="replace",
        text="New risks.",
        expected_version=version,
    )
    assert out["applied"] and out["version"] == version + 1
    row = await services.db.fetchrow("SELECT content, version FROM notes WHERE slug = 'patch-me'")
    assert "New risks." in row["content"] and "old risks" not in row["content"].lower()
    assert "The old plan text" in row["content"]  # untouched section kept
    assert "## Risks" in row["content"]  # heading line kept
    assert out["diff"]


async def test_stale_version_is_refused(services) -> None:
    _, version = await _note(services)
    with pytest.raises(ConflictError, match=str(version + 1)):
        await services.notes.patch_note(
            "patch-me",
            section="Plan",
            operation="append",
            text="More.",
            expected_version=version + 1,
        )
    row = await services.db.fetchrow("SELECT version FROM notes WHERE slug = 'patch-me'")
    assert row["version"] == version  # nothing was written


async def test_dry_run_validates_without_writing(services) -> None:
    _, version = await _note(services)
    out = await services.notes.patch_note(
        "patch-me",
        section="Plan",
        operation="append",
        text="More plan.",
        expected_version=version,
        dry_run=True,
    )
    assert not out["applied"] and out["diff"] and out["version"] == version
    row = await services.db.fetchrow("SELECT version FROM notes WHERE slug = 'patch-me'")
    assert row["version"] == version


async def test_unknown_section_lists_sections(services) -> None:
    await _note(services)
    with pytest.raises(Exception, match="Plan"):
        await services.notes.patch_note(
            "patch-me",
            section="Nope",
            operation="replace",
            text="x",
        )


async def test_key_replace_and_delete(services) -> None:
    await services.notes.upsert(title="Keyed", content="Body text here.")
    out = await services.notes.patch_note(
        "keyed",
        key="reviewed",
        operation="replace",
        text='"yes"',
    )
    assert out["applied"]
    row = await services.db.fetchrow("SELECT metadata FROM notes WHERE slug = 'keyed'")
    assert row["metadata"]["reviewed"] == "yes"
    out = await services.notes.patch_note("keyed", key="reviewed", operation="delete")
    assert out["applied"]
    row = await services.db.fetchrow("SELECT metadata FROM notes WHERE slug = 'keyed'")
    assert "reviewed" not in row["metadata"]


async def test_chunks_follow_a_patch(services) -> None:
    note_id, _ = await _note(services)
    before = await services.db.fetchval(
        "SELECT count(*) FROM note_chunks WHERE note_id = $1", note_id
    )
    assert before == 2
    await services.notes.patch_note(
        "patch-me",
        section="Plan > Risks",
        operation="delete",
    )
    rows = await services.db.fetch(
        "SELECT heading_path FROM note_chunks WHERE note_id = $1 ORDER BY chunk_index", note_id
    )
    assert [r["heading_path"] for r in rows] == ["Plan"]
