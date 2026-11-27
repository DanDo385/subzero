"""Provider conformance: the tool contract every app depends on.

Grok, ChatGPT, Muse, Claude and Gemini all read the same tool list, but they
differ in what they tolerate: ChatGPT truncates long descriptions, Gemini has
no Skills fallback for missing guidance, and every client reads an absent
annotation hint as the worst case (not read-only, destructive, open-world).
This test pins the contract without a database: all four MCP annotation hints
explicit on every tool, descriptions within the wire limit, unknown arguments
refused before any service runs, and compacted results JSON-clean.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from fastmcp import FastMCP
from fastmcp.server.providers.base import Provider

from subzero.mcp import (
    tools_agents,
    tools_artifacts,
    tools_cli,
    tools_context,
    tools_email,
    tools_inbox,
    tools_memory,
    tools_notes,
    tools_projects,
    tools_review,
    tools_sessions,
)
from subzero.mcp.compact import dump
from subzero.models import ContextItem, ContextPackage, Provenance_

#: ChatGPT-class clients cut tool descriptions past this; nothing may be
#: truncated on the wire, so nothing may be longer.
MAX_DESCRIPTION_CHARS = 4000

#: One probe argument no tool declares. Passed alone to every tool, it must
#: fail closed at validation: the service behind the tool must never run.
PROBE_ARG = "__conformance_probe__"


def _server() -> FastMCP:
    """Every tool module registered, services stubbed: listing and validation
    need no database, and no tool body may run in this test."""
    mcp = FastMCP(name="conformance")
    stub = MagicMock(name="services")
    tools_context.register(mcp, retrieval=stub, settings=stub)
    tools_email.register(mcp, emails=stub)
    tools_memory.register(mcp, audit=stub, memories=stub, projects=stub, sessions=stub)
    tools_inbox.register(
        mcp,
        audit=stub,
        classification=stub,
        db=stub,
        notes=stub,
        projects=stub,
        settings=stub,
        sync_queue=stub,
        vault_paths=stub,
    )
    tools_notes.register(mcp, audit=stub, notes=stub, settings=stub, sync_queue=stub)
    tools_sessions.register(mcp, audit=stub, sessions=stub)
    tools_projects.register(mcp, audit=stub, projects=stub)
    tools_artifacts.register(
        mcp,
        artifacts=stub,
        audit=stub,
        db=stub,
        projects=stub,
        settings=stub,
        store=stub,
        filing=stub,
    )
    tools_review.register(
        mcp,
        artifacts=stub,
        audit=stub,
        db=stub,
        projects=stub,
        review=stub,
        settings=stub,
        sync_queue=stub,
        vault_paths=stub,
    )
    tools_agents.register(mcp, agents=stub, audit=stub)
    tools_cli.register(mcp, audit=stub, settings=stub)
    return mcp


@pytest.fixture(scope="module")
def tools() -> list[Any]:
    """Raw tool definitions, bypassing the auth filter (no credentials here).

    Provider.list_tools is the same listing the server filters per scope, so
    what is asserted here is what every provider sees before its own grant.
    """
    import asyncio

    async def collect() -> list[Any]:
        return list(await Provider.list_tools(_server()))

    return asyncio.run(collect())


def test_every_provider_sees_the_same_tools(tools: list[Any]) -> None:
    names = sorted(t.name for t in tools)
    assert len(names) >= 51, names
    assert len(set(names)) == len(names), "duplicate tool names"
    for name in (
        "context_bootstrap",
        "context_search",
        "session_checkpoint",
        "session_open",
        "session_update",
        "session_list",
        "session_close",
        "inbox_add",
        "note_patch",
        "project_list",
        "artifact_save_text",
        "review_list",
        "dispatch",
    ):
        assert name in names, f"{name} missing from the registered surface"


def test_all_four_hints_explicit_on_every_tool(tools: list[Any]) -> None:
    """An absent hint reads as the worst case (destructive, open-world), so
    every hint must be set, not defaulted."""
    bad: list[str] = []
    for tool in tools:
        hints = tool.annotations
        for field in ("read_only_hint", "destructive_hint", "idempotent_hint", "open_world_hint"):
            if getattr(hints, field, None) is None:
                bad.append(f"{tool.name}.{field}")
    assert not bad, f"hints left unset (read as worst case): {bad}"


def test_read_only_tools_are_not_destructive(tools: list[Any]) -> None:
    bad = [t.name for t in tools if t.annotations.read_only_hint and t.annotations.destructive_hint]
    assert not bad, f"read-only tools marked destructive: {bad}"


def test_descriptions_fit_the_wire(tools: list[Any]) -> None:
    bad: list[str] = []
    for tool in tools:
        if len(tool.description or "") > MAX_DESCRIPTION_CHARS:
            bad.append(f"{tool.name} description ({len(tool.description or '')})")
        for name, spec in ((tool.parameters or {}).get("properties") or {}).items():
            if len(spec.get("description", "")) > MAX_DESCRIPTION_CHARS:
                bad.append(f"{tool.name}.{name} description")
    assert not bad, f"descriptions past the truncation limit: {bad}"


def test_unknown_arguments_are_refused_before_services_run(tools: list[Any]) -> None:
    """The probe argument is declared by no tool. Validation must refuse it
    without running the tool body (against stub services, a body that ran
    would fail downstream, not at the schema)."""
    import asyncio

    async def probe(tool: Any) -> None:
        try:
            await tool.run({PROBE_ARG: 1})
        except Exception as exc:
            assert PROBE_ARG in str(exc), f"{tool.name}: refused without naming {exc!r}"
            return
        pytest.fail(f"{tool.name} accepted an undeclared argument")

    async def run_all() -> None:
        for tool in tools:
            await probe(tool)

    asyncio.run(run_all())


def test_compact_apps_get_the_compact_budget() -> None:
    from subzero.mcp.tools_context import COMPACT_BUDGET, FULL_BUDGET, budget_for_provider

    assert budget_for_provider("chatgpt") == COMPACT_BUDGET
    assert budget_for_provider("gemini") == COMPACT_BUDGET
    for provider in ("claude", "grok", "muse", "unknown", None):
        assert budget_for_provider(provider) == FULL_BUDGET
    assert COMPACT_BUDGET < FULL_BUDGET


def test_compacted_results_are_json_clean() -> None:
    """What dump() sends must survive a JSON round trip with no nulls or
    empty containers: some hosts echo the structured payload as text."""
    item = ContextItem(
        type="memory",
        id=UUID("00000000-0000-0000-0000-000000000000"),
        content="A durable fact.",
        provenance=Provenance_(kind="user_statement"),
        layer="relevant",
    )
    package = ContextPackage(
        query="fact",
        token_budget=1200,
        estimated_tokens=10,
        items=[item],
    )
    payload = dump(package)
    text = json.dumps(payload)
    assert ": null" not in text and ": []" not in text and ": {}" not in text, text
    assert json.loads(text)["items"][0]["content"] == "A durable fact."
