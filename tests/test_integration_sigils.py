"""Sigils typed in a save or a bootstrap query decide the home before Jev."""

from __future__ import annotations

from conftest import requires_db
from subzero.auth.principal import Principal

pytestmark = requires_db

ME = Principal(subject="tester@example.com", provider="claude")


async def _home_and_tags(services, provider_conversation_id: str) -> dict:
    return await services.db.fetchval(
        "SELECT metadata FROM conversations WHERE provider_conversation_id = $1",
        provider_conversation_id,
    )


async def test_lesson_sigil_files_the_session_under_its_home(services) -> None:
    result = await services.sessions.checkpoint(
        provider="claude",
        principal=ME,
        provider_conversation_id="cfa-1",
        title="^cfa2/fixed-income duration",
        summary="Worked through #convexity with @someone's example. Next: key rate duration.",
        open_questions=["How does convexity change with yield?"],
    )
    metadata = await _home_and_tags(services, "cfa-1")
    assert metadata["home"] == "lesson/cfa2"
    assert set(metadata["tags"]) >= {
        "lesson/cfa2",
        "concept/fixed-income",
        "concept/convexity",
        "person/someone",
    }
    assert any("Sigils read" in n for n in result.notes)


async def test_explicit_home_beats_a_sigil(services) -> None:
    await services.sessions.checkpoint(
        provider="claude",
        principal=ME,
        provider_conversation_id="zig-1",
        title="^cfa2 aside",
        summary="Mostly about comptime.",
        home="lesson/zig",
    )
    assert (await _home_and_tags(services, "zig-1"))["home"] == "lesson/zig"


async def test_unknown_project_sigil_never_creates_a_project(services) -> None:
    result = await services.sessions.checkpoint(
        provider="claude",
        principal=ME,
        provider_conversation_id="p-1",
        title="%Code/does-not-exist planning",
        summary="Sketched the idea.",
    )
    assert (
        await services.db.fetchval("SELECT count(*) FROM projects WHERE slug = 'does-not-exist'")
        == 0
    )
    assert any("No project matches" in n for n in result.notes)


async def test_known_project_sigil_routes_the_session(services) -> None:
    await services.db.execute(
        "INSERT INTO projects (slug, name) VALUES ('portfolio-site', 'Portfolio site')"
    )
    await services.sessions.checkpoint(
        provider="claude",
        principal=ME,
        provider_conversation_id="p-2",
        title="%Code/portfolio-site dark mode",
        summary="Added a toggle.",
    )
    assert (await _home_and_tags(services, "p-2"))["home"] == "projects/portfolio-site"


async def test_bootstrap_continues_a_lesson_from_its_sigil(services) -> None:
    await services.sessions.checkpoint(
        provider="chatgpt",
        principal=ME,
        provider_conversation_id="cfa-yesterday",
        title="^cfa2/fixed-income",
        summary="Covered Macaulay and modified duration. Stopped before convexity.",
        open_questions=["Why is convexity always positive for option-free bonds?"],
    )
    package = await services.retrieval.bootstrap("^cfa2 convexity")
    handoff = [i for i in package.items if i.layer == "handoff"]
    assert handoff and "Macaulay" in handoff[0].content
    assert handoff[0].open_questions
    assert any(r.name == "handoff" and r.status == "ran" for r in package.layers)
    assert any("Sigils read: ^cfa2" in n for n in package.notes)


async def test_note_write_reads_sigils(services) -> None:
    result = await services.note_writer.write(
        principal=ME,
        title="Duration cheat sheet",
        content="^cfa2/fixed-income #duration notes in my own words.",
        note_class="concept",
        queue_for_vault=False,
    )
    metadata = await services.db.fetchval(
        "SELECT metadata FROM notes WHERE id = $1", result["note_id"]
    )
    assert metadata["home"] == "lesson/cfa2"
    assert "concept/duration" in metadata["tags"]
