"""context_bootstrap sends one line of text and keeps the object structured."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import pytest
from fastmcp import FastMCP
from fastmcp.server.mixins.mcp_operations import _normalize_call_tool_result
from mcp.types import TextContent

from subzero.mcp import tools_context
from subzero.mcp.result import text_and_structured
from subzero.models import (
    ContextItem,
    ContextPackage,
    Provenance_,
)

NOTE_ID = UUID("00000000-0000-4000-8000-000000000012")


class _Retrieval:
    def __init__(self, package: ContextPackage) -> None:
        self.package = package

    async def bootstrap(
        self,
        _query: str | None = None,
        *,
        user_message: str | None = None,
        token_budget: int = 1200,
        project: str | None = None,
    ) -> ContextPackage:
        return self.package


class _Services:
    def __init__(self, retrieval: _Retrieval | None = None) -> None:
        self.retrieval = retrieval


def _package(*, truncated: bool, tokens: int, contents: list[str]) -> ContextPackage:
    return ContextPackage(
        query="what did I decide about the secret architecture",
        items=[
            ContextItem(
                type="decision",
                id=NOTE_ID,
                content=content,
                provenance=Provenance_(kind="user_statement"),
                estimated_tokens=tokens // len(contents),
            )
            for content in contents
        ],
        token_budget=1200,
        estimated_tokens=tokens,
        layers_used=["memory"],
        truncated=truncated,
    )


@pytest.fixture
def mcp(monkeypatch: pytest.MonkeyPatch) -> FastMCP:
    holder: dict[str, Any] = {}
    monkeypatch.setattr(tools_context, "get_services", lambda: holder["services"])
    server = FastMCP("token-cut-test")
    tools_context.register(server)
    server._holder = holder  # type: ignore[attr-defined]
    return server


async def _tool(mcp: FastMCP, name: str) -> Any:
    """The registered function, skipping the auth check that hides it from get_tool."""
    tool = await mcp.local_provider._get_tool(name)
    assert tool is not None
    return tool


def _wire(result: Any) -> tuple[str, dict[str, Any]]:
    raw = result.to_mcp_result()
    assert isinstance(raw, tuple)
    content, structured = raw
    assert len(content) == 1
    assert isinstance(content[0], TextContent)
    normalized = _normalize_call_tool_result(raw)
    assert len(normalized.content) == 1
    assert isinstance(normalized.content[0], TextContent)
    assert normalized.content[0].text == content[0].text
    assert normalized.structured_content == structured
    text = content[0].text
    assert "\n" not in text
    assert text != json.dumps(structured)
    assert json.dumps(structured) not in text
    return text, structured


async def test_context_bootstrap_text_omits_the_package(mcp: FastMCP) -> None:
    secret = "Supabase is canonical structured state"
    mcp._holder["services"] = _Services(  # type: ignore[attr-defined]
        retrieval=_Retrieval(_package(truncated=False, tokens=40, contents=[secret]))
    )
    tool = await _tool(mcp, "context_bootstrap")
    assert tool.output_schema == {"type": "object", "additionalProperties": True}

    text, structured = _wire(
        await tool.run({"user_message": "what did I decide about the secret architecture"})
    )

    assert text == "1 item, 40 of 1200 tokens"
    assert secret not in text
    assert "secret architecture" not in text
    assert structured["items"][0]["content"] == secret
    assert structured["estimated_tokens"] == 40
    assert structured["token_budget"] == 1200
    assert structured["truncated"] is False


async def test_context_bootstrap_line_marks_truncation(mcp: FastMCP) -> None:
    mcp._holder["services"] = _Services(  # type: ignore[attr-defined]
        retrieval=_Retrieval(
            _package(truncated=True, tokens=900, contents=["one body", "another body"])
        )
    )
    tool = await _tool(mcp, "context_bootstrap")

    text, structured = _wire(await tool.run({"user_message": "catch me up"}))

    assert text == "2 items, 900 of 1200 tokens, truncated"
    assert "one body" not in text
    assert structured["truncated"] is True
    assert len(structured["items"]) == 2


def test_text_and_structured_collapses_whitespace() -> None:
    result = text_and_structured("Groceries:\n1 of 5 open", {"shown": 1})
    text, structured = _wire(result)
    assert text == "Groceries: 1 of 5 open"
    assert structured == {"shown": 1}
