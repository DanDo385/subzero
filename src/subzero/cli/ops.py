"""Setup and operations: init, serve, migrate, check, probe, pair-device."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click

from subzero.cli.app import _configure_cli_logging, main
from subzero.config import get_settings, legacy_env_names
from subzero.db.migrate import apply_migrations, migration_status
from subzero.db.pool import Database
from subzero.probe import DEFAULT_PROTOCOL_VERSION, run_probe
from subzero.services import build_services
from subzero.setup_wizard import Answers, build_env, next_steps


@main.command()
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path(".env.local"),
    show_default=True,
    help="Where to write the settings file.",
)
@click.option("--force", is_flag=True, help="Overwrite the file if it already exists.")
def init(output: Path, force: bool) -> None:
    """Answer a few questions and get a working settings file.

    Generates every secret for you. Safe to run again: it will not overwrite
    an existing file unless you pass --force.
    """
    if output.exists() and not force:
        raise click.ClickException(
            f"{output} already exists. Run again with --force to replace it "
            "(the old secrets will stop working)."
        )

    click.secho("\nLet's set up subzero.\n", bold=True)
    click.echo("Press Enter to accept the suggestion shown in [brackets].\n")

    click.echo(
        "1. Where will it run?\n"
        "   local  - only on this computer, to try it out. The AI apps on your phone\n"
        "            and on the web cannot reach it.\n"
        "   online - on the internet (for example Vercel), so ChatGPT, Claude, Gemini\n"
        "            and Grok can all use it. Needs a free Google sign-in app and a\n"
        "            free Supabase database first (the guides walk you through both)."
    )
    mode = click.prompt("   Choose", type=click.Choice(["local", "online"]), default="local")

    click.echo(
        "\n2. Which email do you sign in to your AI apps with? Only this account will\n"
        "   be allowed in. Separate several with commas."
    )
    emails = [e.strip() for e in click.prompt("   Email").split(",") if e.strip() and "@" in e]
    if not emails:
        raise click.ClickException("At least one email address is needed.")

    answers = Answers(mode=mode, emails=emails)  # type: ignore[arg-type]

    if mode == "online":
        click.echo(
            "\n3. What web address will it live at? It must start with https://\n"
            "   (for example https://context.example.com or https://my-memory.vercel.app)."
        )
        answers.base_url = click.prompt("   Address").rstrip("/")
        if not answers.base_url.startswith("https://"):
            raise click.ClickException("The address must start with https://")
        answers.auth = "google"
        click.echo(
            "\n4. Google sign-in app (docs/guides/03-google-sign-in.md shows how to make\n"
            "   one in about 10 minutes). Leave blank to fill in later."
        )
        answers.oauth_client_id = click.prompt("   Client ID", default="", show_default=False)
        answers.oauth_client_secret = click.prompt(
            "   Client secret", default="", show_default=False, hide_input=True
        )
        click.echo(
            "\n5. Supabase database (docs/guides/04-database.md). Paste the connection\n"
            "   string that starts with postgresql:// and the project URL and service key."
        )
        answers.database_url = click.prompt("   Connection string")
        answers.supabase_url = click.prompt(
            "   Project URL (https://....supabase.co)", default="", show_default=False
        )
        if answers.supabase_url:
            answers.supabase_service_key = click.prompt("   Service role key", hide_input=True)
    else:
        click.echo("\n3. Database. The suggestion matches `docker compose up -d db` in this repo.")
        answers.database_url = click.prompt("   Connection string", default=answers.database_url)

    click.echo(
        "\nOptional extras - press Enter to skip; you can add them to the file later.\n"
        "   TypeSafe (Jev) key: sorts years of old chats cheaply. https://console.typesafe.ai/keys\n"
        "   Anthropic key: reads your most important old chats closely and writes proper notes."
    )
    answers.typesafe_api_key = click.prompt(
        "   TypeSafe key", default="", show_default=False, hide_input=True
    )
    answers.anthropic_api_key = click.prompt(
        "   Anthropic key", default="", show_default=False, hide_input=True
    )
    answers.vault_path = click.prompt(
        "\nWhere is (or should be) your Obsidian vault?", default=answers.vault_path
    )

    output.write_text(build_env(answers), encoding="utf-8")
    output.chmod(0o600)
    click.secho(f"\nWrote {output} (readable only by you).", fg="green", bold=True)
    if "static_token" in answers.generated:
        click.echo(
            "\nYour test access token (also saved in the file):\n"
            f"   {answers.generated['static_token']}"
        )
    click.secho("\nNext steps:", bold=True)
    for number, step in enumerate(next_steps(answers), start=1):
        click.echo(f"  {number}. {step}")


@main.command()
def serve() -> None:
    """Run the MCP server."""
    from subzero.server import main as run_server

    run_server()


@main.command()
@click.option("--status", is_flag=True, help="Show pending migrations without applying.")
def migrate(status: bool) -> None:
    """Apply database migrations."""
    settings = get_settings()
    _configure_cli_logging(settings)

    async def run() -> None:
        db = await Database.connect(settings)
        try:
            async with db.acquire() as conn:
                if status:
                    click.echo(json.dumps(await migration_status(conn), indent=2))
                    return
                applied = await apply_migrations(conn)
                if applied:
                    click.echo(f"Applied: {', '.join(applied)}")
                else:
                    click.echo("Already up to date.")
        finally:
            await db.close()

    asyncio.run(run())


@main.command(name="pair-device")
@click.option("--device-name", default="", help="Label for the browser being paired.")
@click.option(
    "--agents",
    is_flag=True,
    help="For a machine running coding agents (`subzero connect`): may also register "
    "sessions, send messages, dispatch jobs and read context. Browsers do not need it.",
)
def pair_device(device_name: str, agents: bool) -> None:
    """Generate a one-time pairing code for a browser extension.

    Run this, then enter the code in the extension popup within 5 minutes.
    """
    settings = get_settings()
    _configure_cli_logging(settings)

    import secrets as _secrets

    code = "".join(_secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))

    async def run() -> None:
        db = await Database.connect(settings)
        try:
            await db.execute(
                """
                INSERT INTO pairing_codes (code, device_name, scopes)
                VALUES ($1, $2, $3)
                """,
                code,
                device_name or None,
                ["ingest", "agents"] if agents else ["ingest"],
            )
        finally:
            await db.close()

    import asyncio as _asyncio

    _asyncio.run(run())

    click.secho(f"\n  Pairing code: {click.style(code, bold=True, fg='green')}", fg="white")
    click.secho(f"  Server:       {str(settings.base_url).rstrip('/')}", fg="white")
    click.secho(f"  Database:     {settings.database_target()}", fg="white")
    click.secho(
        "  Enter the server address and this code in the extension popup within 5 minutes.\n"
        "  The code only works on a server that uses the database above.\n",
        fg="white",
    )


@main.command()
def check() -> None:
    """Validate configuration and connectivity without starting the server."""
    settings = get_settings()
    _configure_cli_logging(settings)

    problems = settings.validate_startup()
    for problem in problems:
        click.secho(f"  config: {problem}", fg="red")
    if legacy := legacy_env_names():
        click.secho(
            f"  config: {len(legacy)} setting{'s' if len(legacy) != 1 else ''} still use the old "
            f"PC_ prefix ({', '.join(legacy[:3])}{', ...' if len(legacy) > 3 else ''}). They are "
            "no longer read, and the server refuses to start while one is set: rename each to "
            "SUBZERO_.",
            fg="red",
        )

    # Not a failure: email is optional. But a deployment that believes it can
    # send and cannot is worth naming next to the things that are broken.
    if not settings.agentmail_api_key.get_secret_value() or not settings.agentmail_inbox_id:
        click.secho(
            "  email: AgentMail is not configured, so the email tool can only dry-run "
            "(set SUBZERO_AGENTMAIL_API_KEY and SUBZERO_AGENTMAIL_INBOX_ID).",
            fg="yellow",
        )

    async def run() -> int:
        failures = len(problems)
        try:
            db = await Database.connect(settings)
        except Exception as exc:
            click.secho(f"  database: cannot connect ({type(exc).__name__})", fg="red")
            return failures + 1

        async with db.acquire() as conn:
            state = await migration_status(conn)
        if state["pending"]:
            click.secho(
                f"  database: {len(state['pending'])} pending migrations "
                f"({', '.join(state['pending'])}) - run `subzero migrate`",
                fg="yellow",
            )
        else:
            click.secho("  database: connected, schema up to date", fg="green")

        services = build_services(settings, db)

        if settings.storage_backend == "supabase":
            try:
                await services.store.ensure_buckets()  # type: ignore[attr-defined]
                click.secho("  storage: buckets present and private", fg="green")
            except Exception as exc:
                click.secho(f"  storage: {exc}", fg="red")
                failures += 1
        else:
            click.secho(
                f"  storage: local filesystem at {settings.local_storage_root}",
                fg="yellow",
            )

        if settings.classifier == "jev":
            try:
                from typesafe_sdk import Noul

                result = await services.classifier.classify_list_candidate(  # type: ignore[union-attr]
                    {"candidate_text": "buy milk", "sensitivity": "normal"},
                    {"shopping": "Things to buy"},
                )
                _ = Noul
                if result.degraded:
                    click.secho(
                        f"  classifier: Jev unreachable ({result.error}); "
                        "the server will fall back to deterministic rules",
                        fg="yellow",
                    )
                else:
                    click.secho(f"  classifier: Jev reachable ({result.model})", fg="green")
            except Exception as exc:
                click.secho(f"  classifier: {type(exc).__name__}: {exc}", fg="yellow")
        else:
            click.secho("  classifier: deterministic rules only", fg="yellow")

        click.secho(
            f"  embeddings: {settings.embedding_provider}"
            + ("" if services.embeddings.enabled else " (text search only)"),
            fg="green" if services.embeddings.enabled else "yellow",
        )
        click.echo(f"\n  MCP endpoint: {settings.resource_url}")
        click.echo(f"  Auth mode:    {settings.auth_mode}")
        await services.aclose()
        return failures

    failures = asyncio.run(run())
    if failures:
        raise SystemExit(1)
    click.secho("\nAll checks passed.", fg="green", bold=True)


@main.command(name="notes-freshness")
@click.option("--days", default=7, show_default=True, help="A marker older than this is stale.")
@click.option("--limit", default=50, show_default=True, help="Most stale notes to report.")
@click.option(
    "--queue/--no-queue",
    default=True,
    show_default=True,
    help="File a review item per stale note (idempotent: one open item per note).",
)
def notes_freshness(days: int, limit: int, queue: bool) -> None:
    """Report notes with stale (as of ...) markers; optionally file reviews.

    Weekly hygiene, run by hand or on a schedule: a dated claim that has
    outlived its freshness window is flagged for a human (or a model with
    the note open) to refresh or convert to a pointer. Never edits.
    """
    settings = get_settings()
    _configure_cli_logging(settings)

    async def run() -> None:
        db = await Database.connect(settings)
        try:
            services = build_services(settings, db)
            stale = await services.notes.stale_markers(days=days, limit=limit)
            if not stale:
                click.secho(f"No markers older than {days} days.", fg="green")
                return
            for row in stale:
                line = f"{row['title']}: oldest marker {row['oldest']}"
                if queue:
                    await services.classification.queue_review(
                        object_type="note",
                        object_id=row["id"],
                        reason="stale_marker",
                        question=(
                            f"“{row['title']}” has a dated claim from {row['oldest']} "
                            f"(older than {days} days). Refresh the observation, convert "
                            "it to a pointer at where truth lives, or confirm it is timeless."
                        ),
                    )
                    line += " (review filed)"
                click.echo(f"  {line}")
        finally:
            await db.close()

    asyncio.run(run())


@main.command()
@click.argument("url")
@click.option(
    "--token",
    envvar="SUBZERO_PROBE_TOKEN",
    default=None,
    help="Bearer token for the full handshake. Without one, only the sign-in "
    "discovery is checked. Also read from SUBZERO_PROBE_TOKEN.",
)
@click.option(
    "--protocol-version",
    default=DEFAULT_PROTOCOL_VERSION,
    show_default=True,
    help="MCP protocol revision to ask for in initialize.",
)
@click.option(
    "--no-oauth",
    is_flag=True,
    help="The server uses SUBZERO_AUTH_MODE=static or jwt: skip the sign-in discovery checks.",
)
def probe(url: str, token: str | None, protocol_version: str, no_oauth: bool) -> None:
    """Connect to a running server the way an AI app does, and report problems.

    URL is the MCP endpoint, for example https://<your-host>/mcp. Prints every
    response as received; read-only tool calls only.
    """
    report = run_probe(
        url,
        token=token,
        protocol_version=protocol_version,
        oauth=not no_oauth,
        echo=click.echo,
    )
    click.echo()
    for failure in report.failures:
        click.secho(f"  FAIL {failure}", fg="red")
    if report.failures:
        click.secho(f"{len(report.failures)} of {report.checks} checks failed.", fg="red")
        raise SystemExit(1)
    click.secho(f"All {report.checks} checks passed.", fg="green", bold=True)
