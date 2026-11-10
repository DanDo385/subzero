"""Agent pieces that need no database."""

from __future__ import annotations

import pytest

from subzero import client
from subzero.agents.service import is_risky
from subzero.cli_agents import _merge_hooks, _remove_hooks


@pytest.mark.parametrize(
    "goal",
    [
        "git push --force to main",
        "deleting old branches",
        "rotate the API key",
        "lock down the tailscale ACL",
        "deploy to production",
        "drop the old table",
    ],
)
def test_risky_goals(goal: str) -> None:
    assert is_risky(goal)


@pytest.mark.parametrize(
    "goal", ["add a dark mode toggle", "write the arena allocator test", "explain comptime"]
)
def test_ordinary_goals(goal: str) -> None:
    assert not is_risky(goal)


def test_hooks_are_found_again_when_the_path_has_spaces() -> None:
    command = "'/Users/A B/.local/bin/subzero'"
    settings = _merge_hooks(_merge_hooks({}, command), command)
    assert all(len(entries) == 1 for entries in settings["hooks"].values())
    assert _remove_hooks(settings)["hooks"] == {}


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1.attacker.tld", "http://example.com", "ftp://x", "https://"]
)
def test_pairing_refuses_unsafe_addresses(url: str) -> None:
    with pytest.raises(client.ClientError):
        client.pair(url, "CODE", "mbp")
