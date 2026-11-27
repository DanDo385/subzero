"""Reclassification, project proposals and the review queue."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click

from subzero.cli.app import _configure_cli_logging, _with_services, main
from subzero.config import get_settings
from subzero.db.pool import Database
from subzero.importers.adapters import IMPORTERS
from subzero.importers.reclassify import (
    reclassify_conversations,
    stale_conversations,
    summarise,
)


@main.command()
@click.option("--limit", type=int, default=50, help="Maximum objects to reclassify.")
def reclassify(limit: int) -> None:
    """Retry classification for objects a classifier outage left pending."""
    settings = get_settings()
    _configure_cli_logging(settings)

    from subzero.importers.reclassify import drain_reclassification_queue

    async def run() -> None:
        services = await _with_services(settings)
        try:
            done, total = await drain_reclassification_queue(
                limit=limit,
                settings=services.settings,
                db=services.db,
                artifacts=services.artifacts,
                filing=services.filing,
                router=services.router,
            )
            if total == 0:
                click.echo("Nothing queued for reclassification.")
                return
            click.echo(f"Reclassified {done} of {total} queued objects.")
        finally:
            await services.aclose()

    asyncio.run(run())


@main.command(name="propose-session-projects")
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    required=True,
    help="CSV file to write the proposals to.",
)
@click.option("--limit", type=int, default=None, help="Only the newest N untagged sessions.")
def propose_session_projects_cmd(out_path: Path, limit: int | None) -> None:
    """Propose a project for each session saved without one. Dry run: writes nothing.

    Reads in a read-only transaction and writes a CSV with the proposed project,
    a score and the reason for each session, for a person to review.
    """
    from subzero.sessions.backfill import untagged_sessions, write_csv

    settings = get_settings()
    _configure_cli_logging(settings)

    async def run() -> None:
        db = await Database.connect(settings)
        try:
            proposals = await untagged_sessions(db, limit=limit)
        finally:
            await db.close()
        click.echo(json.dumps(write_csv(proposals, out_path), indent=2))

    asyncio.run(run())


@main.command(name="reclassify-conversations")
@click.option("--limit", type=int, default=200, show_default=True)
@click.option("--provider", type=click.Choice(sorted(IMPORTERS)), default=None)
@click.option(
    "--all",
    "include_current",
    is_flag=True,
    help="Also redo conversations already classified under the current question set.",
)
@click.option("--no-cache", is_flag=True, help="Ask Jev again even for identical input.")
@click.option("--concurrency", type=int, default=None, help="Default: SUBZERO_IMPORT_CONCURRENCY.")
@click.option("--dry-run", is_flag=True, help="Only count what would be reclassified.")
def reclassify_conversations_cmd(
    limit: int,
    provider: str | None,
    include_current: bool,
    no_cache: bool,
    concurrency: int | None,
    dry_run: bool,
) -> None:
    """Re-run pass-1 classification over imported conversations.

    By default only conversations with no usable result under the current
    question set: never classified, classified by an older QUESTIONS_VERSION,
    or classified while Jev was unavailable. Works from stored messages; the
    export archive is not needed again.
    """
    settings = get_settings()
    _configure_cli_logging(settings)

    async def run() -> None:
        services = await _with_services(settings)
        try:
            ids = await stale_conversations(
                limit=limit, provider=provider, include_current=include_current, db=services.db
            )
            click.echo(f"{len(ids)} conversation(s) to reclassify.")
            if dry_run or not ids:
                return
            report = await reclassify_conversations(
                ids,
                concurrency=concurrency,
                use_cache=not no_cache,
                settings=services.settings,
                db=services.db,
                router=services.router,
            )
            click.echo(json.dumps(summarise(report), indent=2))
        finally:
            await services.aclose()

    asyncio.run(run())


@main.command()
@click.option("--limit", type=int, default=25)
def review(limit: int) -> None:
    """Show the open human-review queue."""
    settings = get_settings()
    _configure_cli_logging(settings)

    async def run() -> None:
        services = await _with_services(settings)
        try:
            items = await services.review.list_open(limit=limit)
            if not items:
                click.secho("Review queue is empty.", fg="green")
                return
            for item in items:
                confidence = item["confidence"]
                click.echo(
                    f"{item['id']}  {item['object_type']:12} {item['reason']:28} "
                    f"conf={confidence:.2f}"
                    if confidence is not None
                    else f"{item['id']}  {item['object_type']:12} {item['reason']:28} conf=n/a"
                )
                click.echo(f"    {item['question']}")
        finally:
            await services.aclose()

    asyncio.run(run())
