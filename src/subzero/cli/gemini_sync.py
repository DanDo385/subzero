"""Push the digest to Gemini."""

from __future__ import annotations

import asyncio
from pathlib import Path

import click

from subzero.cli.app import _configure_cli_logging, _with_services, main
from subzero.config import get_settings
from subzero.digest import build_digest


@main.group(name="gemini-sync")
def gemini_sync_group() -> None:
    """Keep a Google Doc of your Subzero up to date for a Gemini Gem."""


@gemini_sync_group.command(name="authorize")
@click.option("--op-item", default=None, help="1Password item id to store the token in.")
@click.option("--op-vault", default="Private", show_default=True)
@click.option(
    "--env-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path(".env.local"),
    show_default=True,
    help="Where to store the token when not using 1Password.",
)
def gemini_sync_authorize(op_item: str | None, op_vault: str, env_file: Path) -> None:
    """Sign in to Google once so the digest can be written to your Drive."""
    import shutil
    import subprocess

    from subzero.gemini_sync import DriveSyncError, authorize

    settings = get_settings()
    try:
        token = authorize(settings)
    except DriveSyncError as exc:
        raise click.ClickException(str(exc)) from exc

    if op_item:
        op = shutil.which("op")
        if op is None:
            raise click.ClickException("The 1Password CLI (op) is not installed.")
        subprocess.run(  # noqa: S603 - argument list, no shell
            [
                op,
                "item",
                "edit",
                op_item,
                "--vault",
                op_vault,
                f"Google Drive.refresh_token[concealed]={token}",
                "--format",
                "json",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        click.secho(
            f"Saved to 1Password: op://{op_vault}/{op_item}/Google Drive/refresh_token", fg="green"
        )
        return
    lines = env_file.read_text().splitlines() if env_file.exists() else []
    lines = [line for line in lines if not line.startswith("SUBZERO_GOOGLE_DRIVE_REFRESH_TOKEN=")]
    lines.append(f"SUBZERO_GOOGLE_DRIVE_REFRESH_TOKEN={token}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env_file.chmod(0o600)
    click.secho(f"Saved to {env_file}.", fg="green")


@gemini_sync_group.command(name="push")
@click.option("--dry-run", is_flag=True, help="Build the digest and show its size; don't upload.")
def gemini_sync_push(dry_run: bool) -> None:
    """Build the digest and write it to the Google Doc (created the first time)."""
    from subzero.gemini_sync import DriveSyncError, doc_url, push

    settings = get_settings()
    _configure_cli_logging(settings)

    async def run() -> None:
        services = await _with_services(settings)
        try:
            text = await build_digest(
                services.db, settings, recent_days=settings.gemini_digest_days
            )
        finally:
            await services.aclose()
        if dry_run:
            click.echo(
                f"Digest ready: {len(text)} characters, {text.count(chr(10) + '## ')} sections."
            )
            return
        try:
            doc_id, created = await push(settings, text)
        except DriveSyncError as exc:
            raise click.ClickException(str(exc)) from exc
        click.secho(
            f"{'Created' if created else 'Updated'} {settings.gemini_digest_doc_name}: {doc_url(doc_id)}",
            fg="green",
        )

    asyncio.run(run())
