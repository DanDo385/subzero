"""The one way into the Inbox.

The Inbox is the single holding area for anything that still needs a human or
a higher-confidence classification. This tool is deliberately dumb: no Jev
call, no embedding, no classifier round trip. The model calling it has
already decided it cannot place the item, so the only honest routing is
``00-Inbox/`` plus one open review question. The way out is a person, through
``review_reclassify`` or the weekly analysis.
"""

from __future__ import annotations

from typing import Annotated

from fastmcp import FastMCP
from fastmcp.server.auth import require_scopes
from pydantic import Field

from subzero.audit.service import AuditService
from subzero.auth import scopes
from subzero.auth.principal import current_principal
from subzero.classifier.service import ClassificationService
from subzero.config import Settings
from subzero.db.pool import Database
from subzero.errors import ValidationError
from subzero.notes.inbox import SENSITIVITIES, park_uncertain
from subzero.notes.service import NoteService
from subzero.obsidian.paths import VaultPaths
from subzero.obsidian.queue import SyncQueue
from subzero.projects.service import ProjectService


def register(
    mcp: FastMCP,
    *,
    audit: AuditService,
    classification: ClassificationService,
    db: Database,
    notes: NoteService,
    projects: ProjectService,
    settings: Settings,
    sync_queue: SyncQueue,
    vault_paths: VaultPaths,
) -> None:
    @mcp.tool(
        name="inbox_add",
        tags={"inbox", "write"},
        auth=require_scopes(scopes.NOTES_WRITE),
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    async def inbox_add(
        title: Annotated[
            str,
            Field(min_length=1, max_length=200, description="Short name for the item."),
        ],
        content: Annotated[
            str,
            Field(
                min_length=1,
                description=(
                    "The item itself, written so it makes sense months from now "
                    "without this conversation for context."
                ),
            ),
        ],
        why_uncertain: Annotated[
            str | None,
            Field(
                description=(
                    "One line for the person triaging: why you could not place this confidently."
                )
            ),
        ] = None,
        project: Annotated[
            str | None,
            Field(description="Project slug or name, when the item is probably about one."),
        ] = None,
        sensitivity: Annotated[
            str,
            Field(
                description=(
                    "normal, private or highly_sensitive. A level listed in "
                    "SUBZERO_NO_EXTERNAL_SENSITIVITY is stored in Subzero only "
                    "and never written to the vault."
                )
            ),
        ] = "normal",
        provenance: Annotated[
            str,
            Field(
                description=(
                    "user_statement if the content is the user's own words; "
                    "assistant_generated if you summarised or reworded it."
                )
            ),
        ] = "user_statement",
    ) -> dict:
        """Park something you cannot place confidently, instead of guessing.

        Use this when an item has no clear project or folder, or when you are
        not sure your classification is right: guessing a home is worse than
        parking it. The note lands in 00-Inbox/ and opens one review question;
        it stays in the Inbox until a person files it with review_reclassify.
        Calling twice with the same title and content does not create a second
        item. Nothing is classified or embedded here, by design: you already
        know you cannot place it.
        """
        if sensitivity not in SENSITIVITIES:
            raise ValidationError("sensitivity must be normal, private or highly_sensitive.")
        if provenance not in ("user_statement", "assistant_generated"):
            raise ValidationError("provenance must be user_statement or assistant_generated.")
        return await park_uncertain(
            audit=audit,
            classification=classification,
            db=db,
            notes=notes,
            projects=projects,
            settings=settings,
            sync_queue=sync_queue,
            vault_paths=vault_paths,
            title=title,
            content=content,
            principal=current_principal(),
            why_uncertain=why_uncertain,
            project=project,
            sensitivity=sensitivity,
            provenance=provenance,
        )
