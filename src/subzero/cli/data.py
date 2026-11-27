"""Import, export, embedding backfill and the digest."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import asyncpg
import click

from subzero.cli.app import _configure_cli_logging, _with_services, main
from subzero.config import get_settings
from subzero.db.pool import Database
from subzero.digest import build_digest
from subzero.importers.adapters import IMPORTERS


@main.command(name="embed-backfill")
@click.option("--batch-size", default=64, show_default=True, help="Rows per embedding call.")
@click.option(
    "--max-batches", default=0, help="Stop after this many batches (0: until nothing is pending)."
)
def embed_backfill(batch_size: int, max_batches: int) -> None:
    """Embed stored messages, notes, memories and summaries with no vector yet.

    The server does this by itself every few minutes; run this to catch up a
    large backlog at once. Needs SUBZERO_EMBEDDING_PROVIDER and its API key.
    """
    from subzero.embeddings.refresh import refresh_backlog

    settings = get_settings()
    _configure_cli_logging(settings)

    async def run() -> None:
        services = await _with_services(settings)
        try:
            if not services.embeddings.enabled:
                raise click.ClickException(
                    "Embeddings are off. Set SUBZERO_EMBEDDING_PROVIDER (openai or gemini) "
                    "and its API key."
                )
            total = {"embedded": 0, "skipped": 0, "failed": 0}
            batches = 0
            while not max_batches or batches < max_batches:
                step = await refresh_backlog(
                    services.db, services.embeddings, batch_size=batch_size, max_batches=1
                )
                batches += 1
                total["embedded"] += step.embedded
                total["skipped"] += step.skipped
                total["failed"] += step.failed
                click.echo(
                    f"  batch {batches}: {step.embedded} embedded, {step.skipped} skipped"
                    + (f", {step.failed} failed" if step.failed else "")
                )
                if step.touched == 0 or step.failed:
                    break
            pending = await services.db.fetchval(
                """
                SELECT (SELECT count(*) FROM messages
                        WHERE embedding IS NULL AND NOT embedding_skipped)
                     + (SELECT count(*) FROM notes
                        WHERE embedding IS NULL AND NOT embedding_skipped)
                     + (SELECT count(*) FROM memories
                        WHERE embedding IS NULL AND NOT embedding_skipped
                          AND status = 'active')
                     + (SELECT count(*) FROM session_summaries
                        WHERE embedding IS NULL AND NOT embedding_skipped)
                """
            )
            click.secho(
                f"Done: {total['embedded']} embedded, {total['skipped']} skipped, "
                f"{total['failed']} failed; {pending} still pending.",
                fg="green" if not total["failed"] else "yellow",
            )
        finally:
            await services.aclose()

    asyncio.run(run())


@main.command(name="import")
@click.argument("provider", type=click.Choice([*sorted(IMPORTERS), "auto"]))
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--limit", type=int, default=None, help="Stop after N conversations (for a trial run)."
)
@click.option("--no-store-archive", is_flag=True, help="Do not copy the export to cold storage.")
@click.option(
    "--concurrency",
    type=int,
    default=None,
    help="Conversations at once (SUBZERO_IMPORT_CONCURRENCY).",
)
def import_export(
    provider: str,
    archive: Path,
    limit: int | None,
    no_store_archive: bool,
    concurrency: int | None,
) -> None:
    """Import a provider export archive.

    Safe to re-run: conversations are keyed on their provider ids and
    attachments on their SHA-256, so nothing is duplicated.
    """
    settings = get_settings()
    if concurrency is not None:
        settings.import_concurrency = concurrency
    _configure_cli_logging(settings)

    async def run() -> None:
        services = await _with_services(settings)
        try:
            runner = services.importer
            report = await runner.run(
                archive,
                provider=None if provider == "auto" else provider,
                limit=limit,
                store_archive=not no_store_archive,
            )
            click.echo(json.dumps(report.as_dict(), indent=2))
            if report.limitations:
                click.secho("\nKnown limitations of this export format:", fg="yellow", bold=True)
                for note in report.limitations:
                    click.secho(f"  - {note}", fg="yellow")
            if report.attachments_referenced:
                click.secho(
                    f"\n{report.attachments_referenced} attachment(s) were named in the "
                    "export but their bytes were not present. They are recorded as "
                    "pending_import, NOT as archived files.",
                    fg="yellow",
                )
        finally:
            await services.aclose()

    asyncio.run(run())


@main.command(name="export-lists")
@click.option(
    "--out",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to write the export into.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["md", "json", "both"]),
    default="both",
    show_default=True,
    help="What to write per list.",
)
def export_lists(out: Path, fmt: str) -> None:
    """Export every list and item before migration 0020 drops the tables.

    Task items are copied into the new tasks table by the migration; every
    other list (shopping, reading, ideas) exists only in this export
    afterwards, so run this first. Writes one file per list plus an index,
    including item status, priority, due date and which project a task list
    belonged to.
    """
    settings = get_settings()
    _configure_cli_logging(settings)

    async def run() -> list[dict[str, Any]]:
        db = await Database.connect(settings)
        try:
            return await _fetch_lists(db)
        finally:
            await db.close()

    records = asyncio.run(run())
    written = _write_export(records, out, fmt)
    click.echo(f"Exported {written['lists']} lists ({written['items']} items) to {out}.")


async def _fetch_lists(db: Database) -> list[dict[str, Any]]:
    """Every list with its items, ordered for the export."""
    try:
        lists = await db.fetch(
            """
            SELECT l.*, p.slug AS project_slug, p.name AS project_name
            FROM lists l LEFT JOIN projects p ON p.id = l.project_id
            ORDER BY l.list_type, lower(l.name)
            """
        )
    except asyncpg.UndefinedTableError as exc:
        raise click.ClickException(
            "The lists tables are gone; migration 0020 already dropped them. "
            "export-lists must run before `subzero migrate` applies it."
        ) from exc

    project_slugs = {row["slug"] for row in await db.fetch("SELECT slug FROM projects")}
    records: list[dict[str, Any]] = []
    for row in lists:
        items = [
            dict(i)
            for i in await db.fetch(
                """
                SELECT id, text, status, priority, position, due_date, notes,
                       created_at, completed_at, metadata
                FROM list_items WHERE list_id = $1
                ORDER BY status = 'done', priority DESC, position
                """,
                row["id"],
            )
        ]
        record = dict(row)
        record["items"] = items
        # A checkpoint task list is named {slug}-tasks; that is how it says
        # which project it belonged to when lists.project_id was never set.
        if not record["project_slug"] and record["slug"].endswith("-tasks"):
            stem = record["slug"][: -len("-tasks")]
            if stem in project_slugs:
                record["project_slug"] = stem
        records.append(record)
    return records


def _write_export(records: list[dict[str, Any]], out: Path, fmt: str) -> dict[str, int]:
    """Write one file per list plus an index; return what was written."""
    out.mkdir(parents=True, exist_ok=True)
    index: list[dict[str, Any]] = []
    item_count = 0
    for record in records:
        items = record["items"]
        item_count += len(items)
        if fmt in ("md", "both"):
            (out / f"{record['slug']}.md").write_text(_list_markdown(record), encoding="utf-8")
        if fmt in ("json", "both"):
            (out / f"{record['slug']}.json").write_text(
                json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8"
            )
        index.append(
            {
                "slug": record["slug"],
                "name": record["name"],
                "list_type": record["list_type"],
                "status": record["status"],
                "project": record["project_slug"],
                "items": len(items),
                "open_items": sum(1 for i in items if i["status"] == "open"),
            }
        )
    if fmt in ("md", "both"):
        (out / "index.md").write_text(_index_markdown(index, fmt), encoding="utf-8")
    if fmt in ("json", "both"):
        (out / "index.json").write_text(
            json.dumps(index, indent=2, default=str) + "\n", encoding="utf-8"
        )
    return {"lists": len(index), "items": item_count}


def _list_markdown(record: dict[str, Any]) -> str:
    lines = [f"# {record['name']}", ""]
    facts = [f"Type: {record['list_type']}", f"Status: {record['status']}"]
    if record.get("project_slug"):
        facts.append(f"Project: {record['project_slug']}")
    lines += [" | ".join(facts), ""]
    if record.get("description"):
        lines += [record["description"], ""]
    lines += [f"## Items ({len(record['items'])})", ""]
    for item in record["items"]:
        box = "x" if item["status"] == "done" else " "
        extras = [item["status"], f"priority {item['priority']}"]
        if item["due_date"]:
            extras.append(f"due {item['due_date']}")
        lines.append(f"- [{box}] {item['text']} ({', '.join(extras)})")
        if item.get("notes"):
            lines.append(f"      {item['notes']}")
    if not record["items"]:
        lines.append("(no items)")
    lines.append("")
    return "\n".join(lines)


def _index_markdown(index: list[dict[str, Any]], fmt: str) -> str:
    lines = [
        "# Lists export",
        "",
        "Written by `subzero export-lists` before migration 0020 dropped the",
        "lists tables. Task items were copied into the tasks table; everything",
        "else lives only in these files.",
        "",
    ]
    for entry in index:
        project = f", project {entry['project']}" if entry["project"] else ""
        name = f"[{entry['name']}]({entry['slug']}.md)" if fmt != "json" else entry["name"]
        lines.append(
            f"- {name} ({entry['list_type']}, {entry['open_items']}/{entry['items']} open{project})"
        )
    lines.append("")
    return "\n".join(lines)


@main.command()
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.option("--days", type=int, default=None, help="Recent sessions window (default 14).")
def digest(output: Path | None, days: int | None) -> None:
    """Write a readable digest of Subzero (for assistants that can't connect)."""
    settings = get_settings()
    _configure_cli_logging(settings)

    async def run() -> str:
        services = await _with_services(settings)
        try:
            return await build_digest(
                services.db, settings, recent_days=days or settings.gemini_digest_days
            )
        finally:
            await services.aclose()

    text = asyncio.run(run())
    if output:
        output.write_text(text, encoding="utf-8")
        click.echo(f"Wrote {output} ({len(text)} characters).")
    else:
        click.echo(text)
