"""Explicit homes for saved sessions and handoffs."""

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


async def test_lesson_home_and_concept_fields_are_saved(services) -> None:
    await services.sessions.checkpoint(
        provider="claude",
        principal=ME,
        provider_conversation_id="cfa-1",
        title="Fixed income duration",
        summary="Worked through convexity. Next: key rate duration.",
        home="lesson/cfa2",
        concepts=["fixed income", "convexity"],
        open_questions=["How does convexity change with yield?"],
    )
    metadata = await _home_and_tags(services, "cfa-1")
    assert metadata["home"] == "lesson/cfa2"
    assert set(metadata["tags"]) >= {
        "lesson/cfa2",
        "concept/fixed-income",
        "concept/convexity",
    }


async def test_symbols_in_session_text_do_not_set_home_or_tags(services) -> None:
    await services.sessions.checkpoint(
        provider="claude",
        principal=ME,
        provider_conversation_id="zig-1",
        title="^cfa2 &tailscale %ghost",
        summary="Mostly about #comptime with @someone.",
    )
    assert await _home_and_tags(services, "zig-1") == {"home": "other", "tags": ["other"]}


async def test_unknown_project_never_creates_a_project(services) -> None:
    result = await services.sessions.checkpoint(
        provider="claude",
        principal=ME,
        provider_conversation_id="p-1",
        title="Planning",
        project="does-not-exist",
        summary="Sketched the idea.",
    )
    assert (
        await services.db.fetchval("SELECT count(*) FROM projects WHERE slug = 'does-not-exist'")
        == 0
    )
    assert any("No project matches" in n for n in result.notes)


async def test_known_project_home_routes_the_session(services) -> None:
    await services.db.execute(
        "INSERT INTO projects (slug, name) VALUES ('portfolio-site', 'Portfolio site')"
    )
    await services.sessions.checkpoint(
        provider="claude",
        principal=ME,
        provider_conversation_id="p-2",
        title="Dark mode",
        home="projects/portfolio-site",
        summary="Added a toggle.",
    )
    assert (await _home_and_tags(services, "p-2"))["home"] == "projects/portfolio-site"


async def test_bootstrap_continues_a_lesson_from_its_home(services) -> None:
    await services.sessions.checkpoint(
        provider="chatgpt",
        principal=ME,
        provider_conversation_id="cfa-yesterday",
        title="Fixed income",
        home="lesson/cfa2",
        summary="Covered Macaulay and modified duration. Stopped before convexity.",
        open_questions=["Why is convexity always positive for option-free bonds?"],
    )
    package = await services.retrieval.bootstrap("convexity", home="lesson/cfa2")
    handoff = [i for i in package.items if i.layer == "handoff"]
    assert handoff and "Macaulay" in handoff[0].content
    assert handoff[0].open_questions
    assert any(r.name == "handoff" and r.status == "ran" for r in package.layers)

    unscoped = await services.retrieval.bootstrap("^cfa2 convexity")
    assert not any(item.layer == "handoff" for item in unscoped.items)
