"""Text-only and structured MCP clients receive the same bootstrap package.

Claude and Gemini show the model only the text block, so context_bootstrap's
text must carry the standing context and every item, not a count line.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from fastmcp import FastMCP
from fastmcp.server.mixins.mcp_operations import _normalize_call_tool_result
from mcp.types import TextContent

from subzero.mcp import tools_context
from subzero.mcp.result import text_and_structured
from subzero.models import (
    AgentContext,
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


def _wire(result: Any, *, one_line: bool = True) -> tuple[str, dict[str, Any]]:
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
    if one_line:
        assert "\n" not in text
    return text, structured


def _standing() -> list[AgentContext]:
    return [
        AgentContext(
            document=doc,
            path=path,
            source="local",
            sha256=f"{n}" * 64,
            content_id=f"{n}" * 12,
            content=body,
        )
        for n, (doc, path, body) in enumerate(
            [
                ("context", "subzero/CONTEXT.md", "# Context header\n\nFILE-MAP"),
                ("soul", "subzero/SOUL.md", "# SOUL\n\nLead with the conclusion."),
                ("agents", "subzero/AGENTS.md", "# Subzero chat instructions\n\nCHAT-RULE"),
            ],
            start=1,
        )
    ]


async def test_context_bootstrap_text_carries_standing_context_and_items(mcp: FastMCP) -> None:
    decision = "Supabase is canonical structured state"
    package = _package(truncated=False, tokens=40, contents=[decision])
    package.standing = _standing()
    mcp._holder["services"] = _Services(retrieval=_Retrieval(package))  # type: ignore[attr-defined]
    tool = await _tool(mcp, "context_bootstrap")
    assert tool.output_schema == {"type": "object", "additionalProperties": True}

    text, structured = _wire(await tool.run({"query": "architecture"}), one_line=False)

    # Standing context first, in order, with the ids a chat can quote back.
    assert text.index("FILE-MAP") < text.index("Lead with the conclusion") < text.index("CHAT-RULE")
    assert text.index("CHAT-RULE") < text.index(decision)
    assert "subzero/SOUL.md (id 222222222222, local)" in text
    # Every item is readable, with its provenance; the object is unchanged.
    assert "user_statement" in text
    assert "1 item, 40 of 1200 tokens" in text
    assert structured["items"][0]["content"] == decision
    assert [d["document"] for d in structured["standing"]] == ["context", "soul", "agents"]
    assert structured["estimated_tokens"] == 40
    assert structured["truncated"] is False


async def test_context_bootstrap_text_without_dotfiles_says_so(mcp: FastMCP) -> None:
    package = _package(truncated=True, tokens=900, contents=["one body", "another body"])
    package.notes = ["No dotfiles source is configured on this server."]
    mcp._holder["services"] = _Services(retrieval=_Retrieval(package))  # type: ignore[attr-defined]
    tool = await _tool(mcp, "context_bootstrap")

    text, structured = _wire(await tool.run({"query": "catch me up"}), one_line=False)

    assert "Standing context" not in text
    assert "one body" in text and "another body" in text
    assert "truncated (raise token_budget for more)" in text
    assert "Note: No dotfiles source is configured on this server." in text
    assert structured["truncated"] is True
    assert len(structured["items"]) == 2


def test_text_and_structured_collapses_whitespace() -> None:
    result = text_and_structured("Groceries:\n1 of 5 open", {"shown": 1})
    text, structured = _wire(result)
    assert text == "Groceries: 1 of 5 open"
    assert structured == {"shown": 1}
