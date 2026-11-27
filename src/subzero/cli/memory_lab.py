"""The memory-lab commands."""

from __future__ import annotations

import asyncio
import json

import click

from subzero.cli.app import _configure_cli_logging, _with_services, main
from subzero.config import get_settings
from subzero.extraction.deep import estimate_cost

# ----------------------------------------------------------------- memory lab


@main.group(name="memory-lab")
def memory_lab() -> None:
    """Jev triage of sessions into PARA notes, Inbox drafts and logs (guide 16)."""


@memory_lab.command(name="recover")
@click.option(
    "--apply", "apply_", is_flag=True, help="Write notes and decisions (default: dry run)."
)
@click.option("--limit", type=int, default=None, help="Stop after this many sessions.")
@click.option("--redo", is_flag=True, help="Decide again even when nothing changed.")
def memory_lab_recover(apply_: bool, limit: int | None, redo: bool) -> None:
    """Apply the triage policy to classified sessions and print the recovery report.

    Run `subzero reclassify-conversations` first so sessions have answers under
    the current question set. Without --apply nothing is written.
    """
    from subzero.memorylab.recover import recover

    settings = get_settings()
    _configure_cli_logging(settings)

    async def run() -> dict:
        services = await _with_services(settings)
        try:
            report = await recover(
                dry_run=not apply_,
                limit=limit,
                redo=redo,
                settings=services.settings,
                db=services.db,
                notes=services.notes,
                sync_queue=services.sync_queue,
                vault_paths=services.vault_paths,
            )
            return report.as_dict()
        finally:
            await services.aclose()

    click.echo(json.dumps(asyncio.run(run()), indent=2))


@memory_lab.command(name="structured")
def memory_lab_structured() -> None:
    """Regenerate the structured-memories note (facts, instructions, events, tasks)."""
    from subzero.memorylab.recover import write_structured

    settings = get_settings()
    _configure_cli_logging(settings)

    async def run() -> int:
        services = await _with_services(settings)
        try:
            return await write_structured(
                settings=services.settings,
                db=services.db,
                notes=services.notes,
                sync_queue=services.sync_queue,
                vault_paths=services.vault_paths,
            )
        finally:
            await services.aclose()

    click.echo(f"Structured memories: {asyncio.run(run())} items queued for the vault.")


@memory_lab.command(name="report")
def memory_lab_report() -> None:
    """Counts of stored triage decisions, by outcome and reason."""
    settings = get_settings()
    _configure_cli_logging(settings)

    async def run() -> list:
        services = await _with_services(settings)
        try:
            return await services.db.fetch(
                "SELECT outcome, reason, count(*) AS n FROM memory_triage "
                "GROUP BY 1, 2 ORDER BY 1, 3 DESC"
            )
        finally:
            await services.aclose()

    rows = asyncio.run(run())
    if not rows:
        click.echo("No triage decisions yet. Run `subzero memory-lab recover --apply`.")
    for row in rows:
        click.echo(f"{row['outcome']:8} {row['reason']:24} {row['n']}")


@memory_lab.command(name="extract")
@click.option("--limit", type=int, default=25, show_default=True, help="Sessions per run.")
@click.option("--apply", "apply_", is_flag=True, help="Call Claude (default: dry run with cost).")
@click.option("--redo", is_flag=True, help="Also redo sessions already extracted.")
@click.option(
    "--concurrency", type=int, default=None, help="Default: SUBZERO_EXTRACTION_CONCURRENCY."
)
def memory_lab_extract(limit: int, apply_: bool, redo: bool, concurrency: int | None) -> None:
    """Claude reads the sessions triage routed to the vault or structured layer.

    Vault sessions get their note written in the PARA bucket Jev chose;
    structured-only sessions get decisions, facts and tasks with no note. Costs
    money: run without --apply first to see the estimate.
    """
    from subzero.memorylab.layers import extraction_targets, mark_extracted

    settings = get_settings()
    _configure_cli_logging(settings)

    async def run() -> None:
        services = await _with_services(settings)
        try:
            targets = await extraction_targets(
                limit=limit, redo=redo, settings=services.settings, db=services.db
            )
            click.echo(json.dumps(estimate_cost(targets, settings), indent=2))
            if not apply_ or not targets:
                for c in targets[:20]:
                    click.echo(
                        f"  {c.importance or 0:.2f}  {c.destination:<9} {c.chars:>8} chars  {c.title}"
                    )
                return
            if settings.extraction_provider != "anthropic":
                raise click.ClickException(
                    "Set SUBZERO_EXTRACTION_PROVIDER=anthropic and SUBZERO_ANTHROPIC_API_KEY to run."
                )
            extractor = services.deep_extractor()
            try:
                report = await extractor.run(
                    [c.conversation_id for c in targets],
                    concurrency=concurrency,
                    destinations={c.conversation_id: c.destination for c in targets},
                )
            finally:
                await extractor.aclose()
            marked = await mark_extracted([c.conversation_id for c in targets], db=services.db)
            click.echo(json.dumps({**report.as_dict(), "marked_extracted": marked}, indent=2))
        finally:
            await services.aclose()

    asyncio.run(run())


@memory_lab.command(name="honcho-sync")
@click.option("--limit", type=int, default=100, show_default=True, help="Sessions per run.")
@click.option("--apply", "apply_", is_flag=True, help="Send to Honcho (default: dry run).")
def memory_lab_honcho_sync(limit: int, apply_: bool) -> None:
    """Send your own messages from preference-bearing sessions to Honcho, oldest first."""
    from subzero.memorylab.layers import honcho_sync

    settings = get_settings()
    _configure_cli_logging(settings)

    async def run() -> dict:
        services = await _with_services(settings)
        try:
            return (
                await honcho_sync(
                    dry_run=not apply_, limit=limit, settings=services.settings, db=services.db
                )
            ).as_dict()
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc
        finally:
            await services.aclose()

    click.echo(json.dumps(asyncio.run(run()), indent=2))


@memory_lab.command(name="honcho-snapshot")
def memory_lab_honcho_snapshot() -> None:
    """Copy Honcho's card and newest conclusions into Experiments/Analysis."""
    from subzero.memorylab.layers import honcho_snapshot

    settings = get_settings()
    _configure_cli_logging(settings)

    async def run() -> str:
        services = await _with_services(settings)
        try:
            return await honcho_snapshot(
                settings=services.settings,
                db=services.db,
                notes=services.notes,
                sync_queue=services.sync_queue,
                vault_paths=services.vault_paths,
            )
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc
        finally:
            await services.aclose()

    click.echo(f"Queued {asyncio.run(run())} for the vault.")
