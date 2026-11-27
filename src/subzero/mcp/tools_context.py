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

from subzero.agent_context import Document, read_agent_context
from subzero.auth import scopes
from subzero.config import Settings
from subzero.homes import Home
from subzero.mcp.compact import dump
from subzero.mcp.render import bootstrap_text
from subzero.mcp.result import ToolResult, object_output_schema
from subzero.retrieval.service import RetrievalService


def register(mcp: FastMCP, *, retrieval: RetrievalService, settings: Settings) -> None:
    @mcp.tool(
        name="context_bootstrap",
        tags={"context", "read"},
        auth=require_scopes(scopes.CONTEXT_READ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
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
        """Call once at the start of every chat: the owner's standing context plus memory.

        The text begins with the owner's standing context from dotfiles (a
        context header, SOUL for voice, AGENTS for what to do); follow it for
        the whole chat. Pass query terms and the project only when the request
        depends on past work (a project, earlier decisions, "remember", "last
        time"); leave them empty for a standalone question.

        Returns the project's handoff first (latest session summaries with
        their open questions, open tasks, active decisions), then items
        matching query, then raw transcript excerpts only if nothing else
        matched. Says which project it used and why, which layers ran, and
        flags decisions a newer item may overrule. Raise token_budget only if
        truncated; token_budget covers the memory items, and the standing
        context (about 1k tokens) comes on top. The same package is in the
        text and in the structured result.
        """
        package = await retrieval.bootstrap(
            query, user_message=user_message, token_budget=token_budget, project=project, home=home
        )
        payload = dump(package)
        return ToolResult(content=bootstrap_text(payload), structured_content=payload)

    @mcp.tool(
        name="context_read_guidance",
        tags={"context", "read"},
        auth=require_scopes(scopes.CONTEXT_READ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def context_read_guidance(
        document: Annotated[
            Document,
            Field(
                description=(
                    "machines: the owner's machines, names and roles. memory: where facts "
                    "about the owner live. agents, soul, context: the full standing "
                    "documents bootstrap already sends (read them again only if cut)."
                )
            ),
        ] = "machines",
    ) -> dict[str, Any]:
        """Open one of the owner's dotfiles context documents, read-only.

        Use when the task needs it: machines before any work on a machine,
        memory to find where facts live. Returns path, source, sha256, a short
        content_id and the content. To change a document, give the owner the
        edit for their dotfiles repository; this tool cannot write.
        """
        guidance = await read_agent_context(settings, document)
        return dump(guidance) if guidance else {"status": "unavailable", "document": document}

    @mcp.tool(
        name="context_search",
        tags={"context", "read"},
        auth=require_scopes(scopes.CONTEXT_READ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
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
    ) -> list[dict[str, Any]]:
        """Ranked search over decisions, memories, summaries, notes, tasks and artifacts.

        Use when bootstrap did not cover what you need. Results match any of the
        terms, rank higher the more they match, and favour the user's own words
        over an assistant's summary and recent items over old ones. An empty
        result means nothing matched: retry once with fewer or different
        terms (or memory_deep_search for meaning rather than words), then
        say so rather than guess. Every item carries retrieval_confidence:
        "low" means nothing matched every term, so try different terms.
        """
        items = await retrieval.search(
            query,
            limit=limit,
            project=project,
            provider=provider,
            include_messages=include_messages,
        )
        return [dump(item) for item in items]

    @mcp.tool(
        name="context_recent",
        tags={"context", "read"},
        auth=require_scopes(scopes.CONTEXT_READ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
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
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
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
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
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
        every request. "arms" says which half ran (keyword always, semantic
        only when the query could be embedded); "confidence" is "low" when
        the semantic arm found nothing, so try different terms.
        """
        result = await retrieval.deep_search(query, limit=limit, provider=provider)
        payload: dict[str, Any] = {
            "query": result.query,
            "semantic": result.semantic,
            "arms": result.arms,
            "confidence": result.confidence,
            "conversations": result.conversations,
        }
        if result.note:
            payload["note"] = result.note
        return payload
