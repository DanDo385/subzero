"""subzero command line.

subzero serve
subzero migrate [--status]
subzero check
subzero probe <url> [--token TOKEN]
subzero import <provider|auto> <archive.zip>
subzero export-lists --out DIR [--format md|json|both]
subzero reclassify [--limit N]
subzero review
subzero vault-fill
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import asyncpg
import click
import httpx

from subzero.cli_agents import register as register_agent_commands
from subzero.config import Settings, get_settings, legacy_env_names
from subzero.db.migrate import apply_migrations, migration_status
from subzero.db.pool import Database
from subzero.digest import build_digest
from subzero.extraction.deep import DeepExtractor, estimate_cost
from subzero.importers.adapters import IMPORTERS
from subzero.importers.reclassify import (
    reclassify_conversations,
    stale_conversations,
    summarise,
)
from subzero.importers.runner import ImportRunner
from subzero.logging_setup import configure_logging
from subzero.probe import DEFAULT_PROTOCOL_VERSION, run_probe
from subzero.services import build_services, set_services
from subzero.setup_wizard import Answers, build_env, next_steps
from subzero.vault_fill import fill_vault
from subzero.wiki.service import WikiService


async def _with_services(settings: Settings):
    db = await Database.connect(settings)
    services = await build_services(settings, db)
    set_services(services)
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


def _bridge_request(
    settings: Settings, method: str, path: str, body: dict[str, Any]
) -> dict[str, Any]:
    token = settings.harness_api_key.get_secret_value()
    if not token:
        raise click.ClickException("SUBZERO_HARNESS_API_KEY is required for bridge commands.")
    url = f"{str(settings.base_url).rstrip('/')}{path}"
    try:
        response = httpx.request(
            method,
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=body,
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise click.ClickException(f"Bridge request failed: {exc}") from exc
    if not response.is_success:
        raise click.ClickException(f"Bridge request failed with status {response.status_code}.")
    try:
        payload = response.json()
    except ValueError as exc:
        raise click.ClickException("Bridge returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise click.ClickException("Bridge returned an invalid response.")
    return payload


@main.command(name="bridge-session")
@click.option("--provider", default="firstmate", show_default=True)
@click.option("--title", default=None)
@click.option("--model", default=None)
def bridge_session(provider: str, title: str | None, model: str | None) -> None:
    """Create a CLI harness session for the browser bridge."""
    settings = get_settings()
    _configure_cli_logging(settings)
    payload = _bridge_request(
        settings,
        "POST",
        "/api/v1/sessions",
        {"provider": provider, "title": title, "model": model},
    )
    click.echo(f"Session ID: {payload['session_id']}")
    click.echo(f"Bridge link: {payload['link']}")


@main.command(name="bridge-message")
@click.argument("session_id")
@click.argument("text")
@click.option(
    "--role",
    type=click.Choice(["user", "assistant", "system", "tool"]),
    default="assistant",
)
@click.option("--model", default=None)
def bridge_message(session_id: str, text: str, role: str, model: str | None) -> None:
    """Append one message to a CLI harness session."""
    settings = get_settings()
    _configure_cli_logging(settings)
    payload = _bridge_request(
        settings,
        "POST",
        f"/api/v1/sessions/{session_id}/messages",
        {"text": text, "role": role, "model": model},
    )
    click.echo(f"Message ID: {payload['message_id']} ({payload['status']})")


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

        try:
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

            services = await build_services(settings, db)
            set_services(services)

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
        finally:
            set_services(None)
        return failures

    failures = asyncio.run(run())
    if failures:
        raise SystemExit(1)
    click.secho("\nAll checks passed.", fg="green", bold=True)


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
            runner = ImportRunner(services)
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
            set_services(None)

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
@click.option("--limit", type=int, default=50, help="Maximum objects to reclassify.")
def reclassify(limit: int) -> None:
    """Retry classification for objects a classifier outage left pending."""
    settings = get_settings()
    _configure_cli_logging(settings)

    from subzero.importers.reclassify import drain_reclassification_queue

    async def run() -> None:
        services = await _with_services(settings)
        try:
            done, total = await drain_reclassification_queue(services, limit=limit)
            if total == 0:
                click.echo("Nothing queued for reclassification.")
                return
            click.echo(f"Reclassified {done} of {total} queued objects.")
        finally:
            await services.aclose()
            set_services(None)

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
                services, limit=limit, provider=provider, include_current=include_current
            )
            click.echo(f"{len(ids)} conversation(s) to reclassify.")
            if dry_run or not ids:
                return
            report = await reclassify_conversations(
                services, ids, concurrency=concurrency, use_cache=not no_cache
            )
            click.echo(json.dumps(summarise(report), indent=2))
        finally:
            await services.aclose()
            set_services(None)

    asyncio.run(run())


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
            return await fill_vault(services)
        finally:
            await services.aclose()
            set_services(None)

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
            result = await action(WikiService(services))
            click.echo(json.dumps(result, indent=2, default=str))
        finally:
            await services.aclose()
            set_services(None)

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
            set_services(None)

    text = asyncio.run(run())
    if output:
        output.write_text(text, encoding="utf-8")
        click.echo(f"Wrote {output} ({len(text)} characters).")
    else:
        click.echo(text)


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
            set_services(None)
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
            set_services(None)

    asyncio.run(run())


register_agent_commands(main)


if __name__ == "__main__":  # pragma: no cover
    main()


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
            report = await recover(services, dry_run=not apply_, limit=limit, redo=redo)
            return report.as_dict()
        finally:
            await services.aclose()
            set_services(None)

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
            return await write_structured(services)
        finally:
            await services.aclose()
            set_services(None)

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
            set_services(None)

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
            targets = await extraction_targets(services, limit=limit, redo=redo)
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
            extractor = DeepExtractor(services)
            try:
                report = await extractor.run(
                    [c.conversation_id for c in targets],
                    concurrency=concurrency,
                    destinations={c.conversation_id: c.destination for c in targets},
                )
            finally:
                await extractor.aclose()
            marked = await mark_extracted(services, [c.conversation_id for c in targets])
            click.echo(json.dumps({**report.as_dict(), "marked_extracted": marked}, indent=2))
        finally:
            await services.aclose()
            set_services(None)

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
            return (await honcho_sync(services, dry_run=not apply_, limit=limit)).as_dict()
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc
        finally:
            await services.aclose()
            set_services(None)

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
            return await honcho_snapshot(services)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc
        finally:
            await services.aclose()
            set_services(None)

    click.echo(f"Queued {asyncio.run(run())} for the vault.")
