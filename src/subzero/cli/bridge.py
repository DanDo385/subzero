"""Bridge commands for firstmate sessions."""

from __future__ import annotations

from typing import Any

import click
import httpx

from subzero.cli.app import _configure_cli_logging, main
from subzero.config import Settings, get_settings


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
