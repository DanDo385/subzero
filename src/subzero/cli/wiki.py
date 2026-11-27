"""The wiki commands and vault-fill."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import click

from subzero.cli.app import _configure_cli_logging, _with_services, main
from subzero.config import get_settings
from subzero.vault_fill import fill_vault
from subzero.wiki.service import WikiService


@main.command(name="vault-fill")
def vault_fill_command() -> None:
    """Write session indexes and maps from rows already stored.

    Does not extract conversations, call a model, or ask Jev. Links a note
    that names another note's exact title, then queues a wiki render.
    """
    settings = get_settings()
    _configure_cli_logging(settings)

    async def run() -> dict[str, Any]:
        services = await _with_services(settings)
        try:
            return await fill_vault(
                db=services.db,
                notes=services.notes,
                vault_paths=services.vault_paths,
                wiki=services.wiki,
            )
        finally:
            await services.aclose()

    click.echo(json.dumps(asyncio.run(run()), indent=2, default=str))


@main.group()
def wiki() -> None:
    """The wiki graph: note links and Maps of Content (MOCs)."""


def _run_wiki(action):  # type: ignore[no-untyped-def]
    settings = get_settings()
    _configure_cli_logging(settings)

    async def run() -> None:
        services = await _with_services(settings)
        try:
            result = await action(services.wiki)
            click.echo(json.dumps(result, indent=2, default=str))
        finally:
            await services.aclose()

    asyncio.run(run())


@wiki.command(name="links")
@click.option("--limit", type=int, default=200, show_default=True, help="Notes to process.")
@click.option("--per-note", type=int, default=12, show_default=True, help="Candidates per note.")
@click.option("--redo", is_flag=True, help="Reconsider notes that already have links.")
def wiki_links(limit: int, per_note: int, redo: bool) -> None:
    """Find candidate links; link title mentions; ask Jev about the rest."""

    async def action(service: WikiService) -> dict[str, Any]:
        report = await service.build_links(limit=limit, per_note=per_note, redo=redo)
        return report.as_dict()

    _run_wiki(action)


@wiki.command(name="add-moc")
@click.argument("title")
@click.option("--description", default=None)
@click.option("--parent", default=None, help="Title or slug of the parent map.")
def wiki_add_moc(title: str, description: str | None, parent: str | None) -> None:
    """Create (or update) a Map of Content by hand."""

    async def action(service: WikiService) -> dict[str, Any]:
        moc_id = await service.add_moc(title, description=description, parent=parent)
        return {"moc_id": str(moc_id), "title": title}

    _run_wiki(action)


@wiki.command(name="seed-mocs")
@click.option("--min-mentions", type=int, default=3, show_default=True)
def wiki_seed_mocs(min_mentions: int) -> None:
    """Propose maps from topics that deep extraction found repeatedly."""

    async def action(service: WikiService) -> dict[str, Any]:
        return {"created": await service.seed_mocs(min_mentions=min_mentions)}

    _run_wiki(action)


@wiki.command(name="mocs")
@click.option("--limit", type=int, default=200, show_default=True)
@click.option("--redo", is_flag=True, help="Reassign notes that already have a map.")
def wiki_mocs(limit: int, redo: bool) -> None:
    """Ask Jev which map, and which section of it, each note belongs in."""

    async def action(service: WikiService) -> dict[str, Any]:
        return (await service.assign_mocs(limit=limit, redo=redo)).as_dict()

    _run_wiki(action)


@wiki.command(name="render")
@click.option("--all", "everything", is_flag=True, help="Re-render every note and map.")
def wiki_render(everything: bool) -> None:
    """Queue vault writes for notes and maps whose graph changed."""

    async def action(service: WikiService) -> dict[str, Any]:
        return (await service.render(everything=everything)).as_dict()

    _run_wiki(action)


@wiki.command(name="doctor")
def wiki_doctor() -> None:
    """Orphans, notes without a map, empty maps and pending reviews."""

    async def action(service: WikiService) -> dict[str, Any]:
        return await service.doctor()

    _run_wiki(action)
