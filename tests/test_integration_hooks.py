"""The CLI and Claude Code hooks against a live server, end to end."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from conftest import requires_db
from subzero import client, prompt
from subzero.cli import main

pytestmark = requires_db


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config, state, claude = tmp_path / "config", tmp_path / "state", tmp_path / "claude"
    monkeypatch.setattr(client, "CONFIG_DIR", config)
    monkeypatch.setattr(client, "STATE_DIR", state)
    monkeypatch.setattr(prompt, "CONFIG_DIR", config)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    monkeypatch.delenv("SUBZERO_CLIENT_URL", raising=False)
    monkeypatch.delenv("SUBZERO_CLIENT_TOKEN", raising=False)
    monkeypatch.delenv("SUBZERO_AGENT_SESSION", raising=False)
    return tmp_path


async def _run(*args: str, stdin: str | None = None, cwd: Path | None = None):
    def go():
        runner = CliRunner()
        if cwd is None:
            return runner.invoke(main, list(args), input=stdin, catch_exceptions=False)
        with runner.isolated_filesystem(temp_dir=cwd.parent):
            import os

            os.chdir(cwd)
            return runner.invoke(main, list(args), input=stdin, catch_exceptions=False)

    return await asyncio.to_thread(go)


@pytest.fixture
async def connected(services, live_server, home):
    await services.db.execute(
        "INSERT INTO pairing_codes (code, device_name, scopes) VALUES ('HOOKS42', 'mbp', '{ingest,agents}')"
    )
    result = await _run(
        "connect", str(live_server.base_url), "--code", "HOOKS42", "--machine", "mbp"
    )
    assert result.exit_code == 0, result.output
    return home


async def test_connect_saves_a_private_config_and_templates(connected) -> None:
    saved = json.loads((client.CONFIG_DIR / "client.json").read_text())
    assert saved["machine"] == "mbp" and saved["token"].startswith("pcd_")
    assert oct((client.CONFIG_DIR / "client.json").stat().st_mode)[-3:] == "600"
    assert (client.CONFIG_DIR / "MEMORY.md").is_file()
    assert (client.CONFIG_DIR / "MACHINES.md").is_file()


async def test_hooks_install_merges_and_is_repeatable(home) -> None:
    claude = home / "claude"
    claude.mkdir()
    other = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}}
    (claude / "settings.json").write_text(json.dumps({"model": "opus", **other}))
    assert (await _run("hooks", "install")).exit_code == 0
    assert (await _run("hooks", "install")).exit_code == 0
    settings = json.loads((claude / "settings.json").read_text())
    assert settings["model"] == "opus"
    stop = [h["command"] for e in settings["hooks"]["Stop"] for h in e["hooks"]]
    assert stop[0] == "say done" and sum("subzero hook stop" in c for c in stop) == 1
    assert set(settings["hooks"]) == {"SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"}
    assert (claude / "commands" / "dispatch.md").is_file()
    assert (claude / "settings.json.before-subzero").is_file()

    assert (await _run("hooks", "uninstall")).exit_code == 0
    settings = json.loads((claude / "settings.json").read_text())
    assert settings["hooks"] == other["hooks"]
    assert not (claude / "commands" / "dispatch.md").exists()


async def test_hooks_install_refuses_broken_json(home) -> None:
    (home / "claude").mkdir()
    (home / "claude" / "settings.json").write_text("{not json")
    result = await _run("hooks", "install")
    assert result.exit_code == 1
    assert (home / "claude" / "settings.json").read_text() == "{not json"


async def test_a_lesson_session_from_start_to_end(connected, services) -> None:
    lesson = connected / "zig-lessons"
    lesson.mkdir()
    (lesson / ".subzero").write_text("^zig #comptime\n")
    payload = {"session_id": "cc-42", "cwd": str(lesson), "hook_event_name": "SessionStart"}

    started = await _run("hook", "session-start", stdin=json.dumps(payload))
    assert started.exit_code == 0
    assert "# subzero" in started.output
    assert "registered as `claude-code@mbp:zig-lessons` in lesson/zig" in started.output
    assert "MEMORY.md" in started.output
    row = await services.db.fetchrow(
        "SELECT * FROM agent_sessions WHERE external_id = 'claude-code:cc-42'"
    )
    assert row["home"] == "lesson/zig"

    # Another session messages everyone in the lesson; it arrives on the next prompt.
    sent = await _run("msg", "^zig", "use", "0.15", "syntax")
    assert sent.exit_code == 0, sent.output
    turn = await _run("hook", "prompt", stdin=json.dumps({**payload, "prompt": "go on"}))
    assert "use 0.15 syntax" in turn.output

    # The agent hands the keyboard over; the end of its turn must not clear that.
    waiting = await _run(
        "agent", "status", "waiting", "Type the comptime fn in list.zig:30", cwd=lesson
    )
    assert waiting.exit_code == 0, waiting.output
    await _run("hook", "stop", stdin=json.dumps(payload))
    assert (
        await services.db.fetchval("SELECT status FROM agent_sessions WHERE id = $1", row["id"])
        == "waiting"
    )

    # The person replies: working again.
    await _run("hook", "prompt", stdin=json.dumps({**payload, "prompt": "done"}))
    assert (
        await services.db.fetchval("SELECT status FROM agent_sessions WHERE id = $1", row["id"])
        == "working"
    )

    await _run("hook", "session-end", stdin=json.dumps({**payload, "reason": "exit"}))
    assert (
        await services.db.fetchval("SELECT status FROM agent_sessions WHERE id = $1", row["id"])
        == "done"
    )


async def test_dispatch_claim_done_from_the_cli(connected) -> None:
    queued = await _run("dispatch", "^zig", "write", "the", "allocator", "test", ">claude")
    assert "for >claude as scout (sigil)" in queued.output
    runner_dir = connected / "runner"
    runner_dir.mkdir()
    session_id = (
        await _run("agent", "start", "--harness", "firstmate", cwd=runner_dir)
    ).output.strip()
    claimed = await _run("claim", "--as", "claude", "--session", session_id)
    job = json.loads(claimed.output)
    assert job["goal"].startswith("^zig write")
    holding = await _run("claim", "--as", "claude", "--session", session_id)
    assert "already holds job" in holding.output
    assert (await _run("done", job["id"], "--result", "PR #12")).exit_code == 0
    assert (await _run("claim", "--as", "claude", "--session", session_id)).output == ""
    listing = await _run("agents")
    assert "firstmate" in listing.output


async def test_hooks_never_fail_the_harness(home) -> None:
    # Not connected at all: the hook exits 0, prints nothing, logs the reason.
    result = await _run("hook", "prompt", stdin='{"session_id": "x"}')
    assert result.exit_code == 0 and result.output == ""
    assert "Not connected" in (client.STATE_DIR / "hook-errors.log").read_text()
    garbage = await _run("hook", "session-start", stdin="not json")
    assert garbage.exit_code == 0


def test_prompt_offline_has_the_rules_and_stays_small(home, tmp_path: Path) -> None:
    prompt.ensure_personal_files()
    (tmp_path / "AGENTS.md").write_text("# AGENTS.md\n\nRun the tests.\n" + "x" * 20000)
    text = prompt.build(cwd=str(tmp_path))
    assert text.startswith("# subzero")
    assert "Run the tests." in text and "^cfa2/fixed-income" in text
    assert len(text) <= prompt.MAX_CHARS + 20
    assert "## AGENTS.md" in text
    assert "## AGENTS.md" not in prompt.build(cwd=str(tmp_path), agents_md=False)
