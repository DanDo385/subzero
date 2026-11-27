"""Note patching: one section or metadata key, guarded by version."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.server.auth import require_scopes
from pydantic import Field

from subzero.audit.service import AuditService
from subzero.auth import scopes
from subzero.auth.principal import current_principal
from subzero.config import Settings
from subzero.mcp.compact import dump
from subzero.mcp.result import ToolResult, object_output_schema, text_and_structured
from subzero.notes.service import NoteService
from subzero.obsidian.queue import SyncQueue


def register(
    mcp: FastMCP,
    *,
    audit: AuditService,
    notes: NoteService,
    settings: Settings,
    sync_queue: SyncQueue,
) -> None:
    @mcp.tool(
        name="note_patch",
        tags={"notes", "write"},
        auth=require_scopes(scopes.NOTES_WRITE),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        output_schema=object_output_schema(),
    )
    async def note_patch(
        note: Annotated[str, Field(description="Note id, slug, or title.")],
        operation: Annotated[
            Literal["replace", "prepend", "append", "delete"],
            Field(description="What to do with the addressed span. Delete ignores text."),
        ],
        section: Annotated[
            str | None,
            Field(
                description=(
                    "Heading path of the section to edit, e.g. 'Plan > Risks'. "
                    "The same paths the search index chunks on; a miss lists them."
                )
            ),
        ] = None,
        key: Annotated[
            str | None,
            Field(description="Metadata key to set or delete. Exactly one of section or key."),
        ] = None,
        text: Annotated[
            str, Field(description="Replacement or inserted Markdown (JSON for a key).")
        ] = "",
        expected_version: Annotated[
            int | None,
            Field(
                description=(
                    "The note version you read. The write is refused when the note "
                    "has moved on, so a human edit in between is never overwritten."
                )
            ),
        ] = None,
        dry_run: Annotated[
            bool,
            Field(description="Validate and show the diff without writing anything."),
        ] = False,
    ) -> ToolResult:
        """Fix one section of a note instead of rewriting the whole note.

        Use when a small part of a note is wrong and the rest must not move:
        a stale paragraph, a missing step, a renamed heading body. Prefer
        this over rewriting the full content, which risks clobbering human
        prose around the edit. Pass the version you read; a refusal names
        both versions, so re-read and retry. dry_run previews the diff.
        """
        result = await notes.patch_note(
            note,
            section=section,
            key=key,
            operation=operation,
            text=text,
            expected_version=expected_version,
            dry_run=dry_run,
        )
        queued = False
        if result["applied"] and settings.obsidian_enabled and result["vault_path"]:
            job_id = await sync_queue.enqueue(
                job_type="write_note",
                object_type="note",
                object_id=result["note_id"],
                vault_path=result["vault_path"],
                payload={"note_id": str(result["note_id"]), "reason": "note_patch"},
            )
            queued = job_id is not None
        await audit.record(
            tool="note_patch",
            action="patch",
            object_type="note",
            object_id=result["note_id"],
            principal=current_principal(),
            resulting_version={"version": result["version"], "dry_run": dry_run},
        )
        payload: dict[str, Any] = {
            "note_id": str(result["note_id"]),
            "title": result["title"],
            "version": result["version"],
            "applied": result["applied"],
            "vault_queued": queued,
            "diff": result["diff"],
        }
        if result["applied"]:
            line = f"patched {result['title']} to version {result['version']}"
        elif dry_run:
            line = f"dry run against version {result['version']}: no write"
        else:
            line = f"no change at version {result['version']}"
        return text_and_structured(line, dump(payload))
