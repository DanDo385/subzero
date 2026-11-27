"""The `subzero` command group and the helpers every command shares."""

from __future__ import annotations

import click

from subzero.config import Settings
from subzero.db.pool import Database
from subzero.logging_setup import configure_logging
from subzero.services import build_services


async def _with_services(settings: Settings):
    db = await Database.connect(settings)
    services = build_services(settings, db)
    return services


def _configure_cli_logging(settings: Settings) -> None:
    """One-off commands print their own readable output, so their log lines
    are limited to warnings unless SUBZERO_LOG_LEVEL was set explicitly. The
    server (``serve``) keeps its configured level."""
    explicit = "log_level" in settings.model_fields_set
    configure_logging(settings.log_level if explicit else "WARNING")


@click.group()
@click.version_option(package_name="subzero")
def main() -> None:
    """Shared AI context: memory, projects, tasks, notes and artifacts."""
