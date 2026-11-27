"""Context retrieval tools.

Tool descriptions are the main place a model learns *when* to call something,
so each one says what it is for and, just as importantly, when not to bother.
Results are compacted: nulls and empty containers are stripped before they
cross the MCP boundary.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.server.auth import require_scopes
from pydantic import Field

from subzero.auth import scopes
from subzero.homes import Home
from subzero.mcp.compact import dump
from subzero.mcp.result import ToolResult, object_output_schema, text_and_structured
from subzero.retrieval.service import RetrievalService


def register(mcp: FastMCP, *, retrieval: RetrievalService) -> None:
    @mcp.tool(
        name="context_bootstrap",
        tags={"context", "read"},
        auth=require_scopes(scopes.CONTEXT_READ),
        annotations={"readOnlyHint": True, "openWorldHint": False},
        output_schema=object_output_schema(),
    )
    async def context_bootstrap(
        query: Annotated[
            str | None,
            Field(
                description=(
                    "A few key terms you choose, like your own memory search: names, "
                    "projects, topics (e.g. 'harbor notes deploy'). Not the user's message. "
                    "Leave empty to get only the project's handoff."
                )
            ),
        ] = None,
        project: Annotated[
            str | None,
            Field(description="Project slug or name, when you know it. Inferred otherwise."),
        ] = None,
        home: Annotated[
            Home | None,
            Field(
                description="Explicit home for a handoff, for example lesson/cfa2 or setup/neovim."
            ),
        ] = None,
        token_budget: Annotated[
            int, Field(ge=200, le=4000, description="Approximate token ceiling for the result.")
        ] = 1200,
        user_message: Annotated[
            str | None,
            Field(
                description=(
                    "Deprecated: use query. If sent, only its key terms are searched, "
                    "never the whole text."
                )
            ),
        ] = None,
    ) -> ToolResult:
        """Start a chat that continues earlier work: one call, one package.

        WHEN TO USE: The request depends on past work (a project, earlier decisions, tasks, "remember", "last time", a continuing task).
        WHEN NOT TO USE: Trivial questions or anything self-contained that doesn't need memory.
        EXAMPLE: User says "continue the harbor notes deploy" -> call with query="harbor notes deploy".

        Returns the project's handoff first, then matching items. The text line shows used/budget tokens.
        Raise token_budget only if truncated. Skip for self-contained questions.
        """
        package = await retrieval.bootstrap(
            query, user_message=user_message, token_budget=token_budget, project=project, home=home
        )
        payload = dump(package)
        items = payload.get("items") or []
        count = len(items)
        used = payload.get("estimated_tokens", 0)
        budget = payload.get("token_budget", token_budget)
        noun = "item" if count == 1 else "items"
        line = f"{count} {noun}, {used}/{budget} tokens"
        if payload.get("truncated"):
            line = f"{line}, truncated"
        # Ensure stable header for caching
        payload["header"] = {"used": used, "budget": budget, "truncated": payload.get("truncated", False)}
        if not items:
            payload["empty_message"] = "0 results"
        payload["help"] = ["Raise token_budget if truncated.", "Use context_search for more targeted results."]
        return text_and_structured(line, payload)

    @mcp.tool(
        name="context_search",
        tags={"context", "read"},
        auth=require_scopes(scopes.CONTEXT_READ),
        annotations={"readOnlyHint": True, "openWorldHint": False},
    )
    async def context_search(
        query: Annotated[
            str,
            Field(
                description=(
                    "Key terms you choose, not the user's message: names, topics, ids. "
                    'Use "quoted phrases" for exact wording and -word to exclude.'
                )
            ),
        ],
        limit: Annotated[int, Field(ge=1, le=20, description="Maximum results to return.")] = 5,
        project: Annotated[str | None, Field(description="Project slug or name.")] = None,
        provider: Annotated[
            str | None,
            Field(description="chatgpt, claude, gemini or grok."),
        ] = None,
        include_messages: Annotated[
            bool,
            Field(description="Verbatim excerpts. Leave false; summaries are smaller."),
        ] = False,
        response_format: Annotated[
            str,
            Field(description="concise (default, 3-4 key fields) or detailed (full item)."),
        ] = "concise",
    ) -> dict[str, Any]:
        """Ranked search over decisions, memories, summaries, notes, tasks and artifacts.

        Use when bootstrap did not cover what you need. Results match any of the
        terms, rank higher the more they match, and favour the user's own words
        over an assistant's summary and recent items over old ones. An empty
        result means nothing matched: say so rather than guess.
        """
        items = await retrieval.search(
            query,
            limit=limit,
            project=project,
            provider=provider,
            include_messages=include_messages,
        )
        dumped = [dump(item) for item in items]
        total = len(dumped)
        if response_format == "concise":
            # Keep 3-4 key fields per item
            concise_items = []
            for it in dumped:
                concise_items.append({
                    k: it[k] for k in ("id", "type", "title", "content", "score") if k in it
                })
            dumped = concise_items
        empty = total == 0
        result = {
            "total": total,
            "items": dumped,
            "truncated": total >= limit,
            "help": ["Use memory_deep_search if nothing matched and you need semantic search."] if empty else [],
        }
        if empty:
            proj = f" in %{project}" if project else ""
            result["empty_message"] = f"0 results for {query!r}{proj}"
        return result

    @mcp.tool(
        name="context_recent",
        tags={"context", "read"},
        auth=require_scopes(scopes.CONTEXT_READ),
        annotations={"readOnlyHint": True, "openWorldHint": False},
    )
    async def context_recent(
        limit: Annotated[int, Field(ge=1, le=20, description="Maximum results to return.")] = 5,
        provider: Annotated[str | None, Field(description="Filter by provider.")] = None,
        project: Annotated[str | None, Field(description="Filter by project.")] = None,
    ) -> list[dict[str, Any]]:
        """Newest session summaries. Use for catch-up, not for a specific topic."""
        items = await retrieval.recent(limit=limit, provider=provider, project=project)
        return [dump(item) for item in items]

    @mcp.tool(
        name="context_for_topic",
        tags={"context", "read"},
        auth=require_scopes(scopes.CONTEXT_READ),
        annotations={"readOnlyHint": True, "openWorldHint": False},
    )
    async def context_for_topic(
        topic: Annotated[str, Field(description="The topic to assemble context about.")],
        token_budget: Annotated[
            int,
            Field(
                ge=200,
                le=4000,
                description="Approximate size of the answer in tokens. Raise it for broad topics.",
            ),
        ] = 1200,
        project: Annotated[str | None, Field(description="Project slug or name.")] = None,
    ) -> dict[str, Any]:
        """Budgeted package about one topic. Prefer this over include_messages."""
        package = await retrieval.for_topic(topic, token_budget=token_budget, project=project)
        return dump(package)

    @mcp.tool(
        name="memory_deep_search",
        tags={"context", "read"},
        auth=require_scopes(scopes.CONTEXT_READ),
        annotations={"readOnlyHint": True, "openWorldHint": False},
    )
    async def memory_deep_search(
        query: Annotated[
            str,
            Field(
                description=(
                    "What to find, in plain words: a description of the idea works "
                    "(e.g. 'how we decided to price the API'), exact wording is not needed."
                )
            ),
        ],
        limit: Annotated[
            int, Field(ge=1, le=15, description="Maximum conversations to return.")
        ] = 6,
        provider: Annotated[
            str | None,
            Field(description="Only this source: chatgpt, claude, gemini, grok or muse."),
        ] = None,
    ) -> dict[str, Any]:
        """Search every past conversation by meaning, then by words.

        Use when bootstrap and context_search found nothing, or the user asks
        about something discussed in an old chat whose wording you do not
        know ("that idea about...", "when did I talk about..."). Returns the
        best-matching conversations, each with a few verbatim excerpts and its
        conversation_id. Costs more than context_search; do not call it for
        every request. If "semantic" is false, only exact words were matched.
        """
        result = await retrieval.deep_search(query, limit=limit, provider=provider)
        payload: dict[str, Any] = {
            "query": result.query,
            "semantic": result.semantic,
            "conversations": result.conversations,
        }
        if result.note:
            payload["note"] = result.note
        return payload
