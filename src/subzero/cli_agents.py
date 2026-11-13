"""CLI commands for working with agents: connect, prompt, hooks, messages, dispatch."""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import shutil
import socket
import sys
import time
from pathlib import Path
from typing import Any, NoReturn

import click

from subzero import client, prompt, sigils

HOOK_EVENTS = {
    "SessionStart": ("session-start", 15),
    "UserPromptSubmit": ("prompt", 5),
    "Stop": ("stop", 5),
    "SessionEnd": ("session-end", 5),
}

SLASH_COMMANDS = {
    "dispatch.md": """\
---
description: Queue a job for your agents. Sigils work - %project ^lesson >agent
argument-hint: <goal, e.g. ^zig write an arena allocator lesson >claude>
allowed-tools: Bash(subzero dispatch:*)
---
Queue this job with `subzero dispatch`, passing the goal below as one quoted argument.
Add `--ship` only if the user said to make the change; otherwise it is a scout job.

Goal: $ARGUMENTS

Then tell the user in one line which agent it went to and whether it is scout or ship.
""",
    "msg.md": """\
---
description: Message another session, a home (^zig, %Code/site) or me
argument-hint: <to> <message>
allowed-tools: Bash(subzero msg:*)
---
Send this with `subzero msg <to> "<message>"`. The first word is who it goes to.

$ARGUMENTS
""",
    "agents.md": """\
---
description: Show which agents are running, what they are doing, and open jobs
allowed-tools: Bash(subzero agents:*)
---
Run `subzero agents` and summarise it in a few lines. Call out anyone waiting on the user.
""",
}


def _fail(message: str) -> NoReturn:
    click.secho(message, fg="red", err=True)
    sys.exit(1)


def _call(*args: Any, **kwargs: Any) -> Any:
    try:
        return client.api(*args, **kwargs)
    except client.ClientError as exc:
        _fail(str(exc))


def _current_session(cwd: str | None = None) -> dict[str, Any] | None:
    return client.session_for_cwd(cwd or os.getcwd())


# ------------------------------------------------------------------ hooks


def _start_session(payload: dict[str, Any], harness: str) -> tuple[dict[str, Any], str]:
    cwd = str(payload.get("cwd") or os.getcwd())
    external = f"{harness}:{payload.get('session_id') or 'none'}"
    marks = client.home_sigils(cwd)
    config = client.load_config()
    session = client.api(
        "POST",
        "/api/v1/agents/start",
        json_body={
            "harness": harness,
            "external_id": external,
            "machine": config.machine or socket.gethostname().split(".")[0],
            "cwd": cwd,
            "branch": client.git(cwd, "rev-parse", "--abbrev-ref", "HEAD"),
            "home": marks.home,
        },
        timeout=4,
        config=config,
    )
    client.remember_session(external, cwd, session)
    return session, external


def hook_session_start(payload: dict[str, Any], harness: str) -> str:
    session, _ = _start_session(payload, harness)
    cwd = session.get("cwd") or os.getcwd()
    marks = client.home_sigils(cwd)
    root = client.repo_root(cwd)
    branch = session.get("branch") or ""
    query = marks.plain or " ".join(p for p in (root.name if root else "", branch) if p)
    package = None
    with contextlib.suppress(client.ClientError):
        package = client.api(
            "GET",
            "/api/v1/bootstrap",
            params={"q": query, "project": marks.project or "", "budget": 900},
            timeout=5,
        )
    beat = client.api(
        "POST", f"/api/v1/agents/{session['id']}/beat", json_body={"deliver": True}, timeout=3
    )
    return prompt.build(
        cwd=cwd,
        # Claude Code already loads the repo's CLAUDE.md (AGENTS.md here).
        agents_md=harness != "claude-code",
        package=package,
        messages=beat.get("messages"),
        session_name=session["name"],
        session_id=session["id"],
        home=session.get("home"),
    )


def hook_prompt(payload: dict[str, Any], harness: str) -> str | None:
    external = f"{harness}:{payload.get('session_id') or 'none'}"
    cached = client.session_for_external(external)
    if cached is None:
        cached, _ = _start_session(payload, harness)
    body: dict[str, Any] = {"status": "working", "deliver": True}
    # Only a prompt that opens with a home sigil ("^cfa2 next topic") moves the
    # session; a sigil further into the text is just being talked about.
    words = str(payload.get("prompt") or "").split(maxsplit=1)
    opening = sigils.parse(words[0]) if words else sigils.parse("")
    if opening.home:
        body["home"] = opening.home
    beat = client.api("POST", f"/api/v1/agents/{cached['id']}/beat", json_body=body, timeout=3)
    text = prompt.render_messages(beat.get("messages") or [])
    return f"## Messages for you (from other subzero sessions)\n\n{text}" if text else None


def hook_stop(payload: dict[str, Any], harness: str) -> None:
    cached = client.session_for_external(f"{harness}:{payload.get('session_id') or 'none'}")
    if cached:
        client.api(
            "POST", f"/api/v1/agents/{cached['id']}/beat", json_body={"status": "idle"}, timeout=3
        )


def hook_session_end(payload: dict[str, Any], harness: str) -> None:
    cached = client.session_for_external(f"{harness}:{payload.get('session_id') or 'none'}")
    if cached:
        client.api(
            "POST",
            f"/api/v1/agents/{cached['id']}/end",
            json_body={"summary": payload.get("reason")},
            timeout=3,
        )


HOOKS = {
    "session-start": hook_session_start,
    "prompt": hook_prompt,
    "stop": hook_stop,
    "session-end": hook_session_end,
}


#: Appended to every hook command (a shell comment), so reinstalling and
#: uninstalling find our hooks even when the executable's path has spaces.
HOOK_MARK = "# subzero-hook"


def _ours(hook: dict[str, Any]) -> bool:
    command = str(hook.get("command", ""))
    return HOOK_MARK in command or "subzero hook " in command


def _merge_hooks(settings: dict[str, Any], command: str) -> dict[str, Any]:
    hooks = settings.setdefault("hooks", {})
    for event, (name, timeout) in HOOK_EVENTS.items():
        kept = [
            entry
            for entry in hooks.get(event, [])
            if not any(_ours(h) for h in entry.get("hooks", []))
        ]
        kept.append(
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{command} hook {name} {HOOK_MARK}",
                        "timeout": timeout,
                    }
                ]
            }
        )
        hooks[event] = kept
    return settings


def _remove_hooks(settings: dict[str, Any]) -> dict[str, Any]:
    hooks = settings.get("hooks", {})
    for event in list(hooks):
        hooks[event] = [
            entry for entry in hooks[event] if not any(_ours(h) for h in entry.get("hooks", []))
        ]
        if not hooks[event]:
            del hooks[event]
    return settings


def _claude_dir(scope: str, project: str) -> Path:
    if scope == "user":
        return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
    root = client.repo_root(project) or Path(project)
    return root / ".claude"


# ------------------------------------------------------------------ commands


def register(main: click.Group) -> None:
    @main.command()
    @click.argument("url")
    @click.option("--code", prompt="Pairing code (from `subzero pair-device` on the server)")
    @click.option(
        "--machine",
        default=lambda: socket.gethostname().split(".")[0],
        help="Name this machine shows as on the board.",
    )
    def connect(url: str, code: str, machine: str) -> None:
        """Connect this machine to a subzero server (once per machine)."""
        try:
            config = client.pair(url, code, machine)
        except client.ClientError as exc:
            _fail(str(exc))
        path = client.save_config(config)
        made = prompt.ensure_personal_files()
        click.secho(f"Connected as {machine}. Saved to {path}.", fg="green")
        for created in made:
            click.echo(f"Created {created}: fill it in; every session starts with it.")
        click.echo("Next: subzero hooks install")

    @main.command(name="prompt")
    @click.option("--cwd", default=os.getcwd, help="Folder to build the prompt for.")
    @click.option("--query", default=None, help="Key terms or sigils, e.g. '^cfa2 duration'.")
    @click.option("--no-agents-md", is_flag=True, help="Leave out the repo's AGENTS.md.")
    @click.option("--offline", is_flag=True, help="Skip the server; files only.")
    def show_prompt(cwd: str, query: str | None, no_agents_md: bool, offline: bool) -> None:
        """Print the master prompt: subzero rules, AGENTS.md, MEMORY.md, MACHINES.md, live context."""
        package = None
        if not offline:
            marks = sigils.parse(query) if query else client.home_sigils(cwd)
            root = client.repo_root(cwd)
            q = query or marks.plain or (root.name if root else "")
            try:
                package = client.api(
                    "GET",
                    "/api/v1/bootstrap",
                    params={"q": q, "project": marks.project or "", "budget": 1200},
                )
            except client.ClientError as exc:
                click.secho(f"(no live context: {exc})", fg="yellow", err=True)
        click.echo(prompt.build(cwd=cwd, agents_md=not no_agents_md, package=package))

    @main.command()
    @click.argument("event", type=click.Choice(sorted(HOOKS)))
    @click.option("--harness", default=lambda: os.environ.get("SUBZERO_HARNESS", "claude-code"))
    def hook(event: str, harness: str) -> None:
        """Run from a coding harness's hooks. Never fails the harness."""
        try:
            raw = sys.stdin.read() if not sys.stdin.isatty() else ""
            payload = json.loads(raw) if raw.strip() else {}
        except (OSError, ValueError):
            payload = {}
        try:
            out = HOOKS[event](payload, harness)
            if out:
                click.echo(out)
        except Exception as exc:  # a hook must never break the session
            client.STATE_DIR.mkdir(parents=True, exist_ok=True)
            with (client.STATE_DIR / "hook-errors.log").open("a") as log:
                log.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {event}: {exc}\n")
        sys.exit(0)

    @main.group()
    def hooks() -> None:
        """Install subzero's hooks and slash commands into Claude Code."""

    @hooks.command(name="install")
    @click.option(
        "--scope",
        type=click.Choice(["user", "project"]),
        default="user",
        show_default=True,
        help="user: every Claude Code session on this machine; project: this repo only.",
    )
    @click.option("--project", default=os.getcwd, help="Repo for --scope project.")
    def hooks_install(scope: str, project: str) -> None:
        """Add the session hooks and /dispatch, /msg, /agents commands."""
        folder = _claude_dir(scope, project)
        settings_file = folder / "settings.json"
        settings: dict[str, Any] = {}
        if settings_file.is_file():
            try:
                settings = json.loads(settings_file.read_text() or "{}")
            except ValueError:
                _fail(f"{settings_file} is not valid JSON; fix it first. Nothing was changed.")
            backup = settings_file.with_name("settings.json.before-subzero")
            if not backup.exists():
                shutil.copyfile(settings_file, backup)
        executable = shutil.which("subzero") or str(Path(sys.argv[0]).resolve())
        _merge_hooks(settings, shlex.quote(executable))
        folder.mkdir(parents=True, exist_ok=True)
        settings_file.write_text(json.dumps(settings, indent=2) + "\n")
        commands = folder / "commands"
        commands.mkdir(exist_ok=True)
        for name, text in SLASH_COMMANDS.items():
            (commands / name).write_text(text)
        click.secho(f"Hooks written to {settings_file}", fg="green")
        click.echo(f"Commands /dispatch, /msg, /agents written to {commands}")
        click.echo("Start a new Claude Code session to load them.")

    @hooks.command(name="uninstall")
    @click.option("--scope", type=click.Choice(["user", "project"]), default="user")
    @click.option("--project", default=os.getcwd)
    def hooks_uninstall(scope: str, project: str) -> None:
        """Remove subzero's hooks and slash commands."""
        folder = _claude_dir(scope, project)
        settings_file = folder / "settings.json"
        if settings_file.is_file():
            settings = json.loads(settings_file.read_text() or "{}")
            settings_file.write_text(json.dumps(_remove_hooks(settings), indent=2) + "\n")
        for name in SLASH_COMMANDS:
            (folder / "commands" / name).unlink(missing_ok=True)
        click.secho("Removed.", fg="green")

    @main.command()
    def agents() -> None:
        """List running sessions, open jobs and messages for you."""
        board = _call("GET", "/api/v1/agents/board")
        live = [s for s in board["sessions"] if s["status"] != "done"]
        if not live:
            click.echo("No agents running.")
        for s in live:
            colour = {"waiting": "yellow", "working": "green"}.get(s["status"], "white")
            click.secho(f"{s['id'][:8]}  {s['status']:8}", fg=colour, nl=False)
            click.echo(f" {s['name']}  {s.get('home') or ''}  {s.get('task') or ''}")
        for j in board["jobs"]:
            if j["status"] in ("queued", "claimed"):
                click.echo(
                    f"job {j['id'][:8]}  {j['status']:8} >{j['target']} {j['mode']}: {j['goal']}"
                )
        for m in board["inbox"]:
            click.secho(f"for you, from {m['from_label']}: {m['body']}", fg="cyan")

    @main.group()
    def agent() -> None:
        """Register this terminal as an agent, or change its status."""

    @agent.command(name="start")
    @click.option("--harness", default="cli", show_default=True)
    @click.option("--name", default=None)
    @click.option("--home", default=None, help="A sigil: ^zig, %Code/site, &herdr.")
    def agent_start(harness: str, name: str | None, home: str | None) -> None:
        """Register a session (runners and scripts; hooks do this for Claude Code)."""
        cwd = os.getcwd()
        body = {
            "harness": harness,
            "name": name,
            "cwd": cwd,
            "home": sigils.parse(home).home if home else client.home_sigils(cwd).home,
        }
        session = _call("POST", "/api/v1/agents/start", json_body=body)
        client.remember_session(f"{harness}:{session['id']}", cwd, session)
        click.echo(session["id"])

    @agent.command(name="status")
    @click.argument("status", type=click.Choice(["working", "waiting", "idle", "done"]))
    @click.argument("task", required=False)
    @click.option("--session", "session_id", default=None, help="Session id (from your prompt).")
    def agent_status(status: str, task: str | None, session_id: str | None) -> None:
        """Set this session's status; 'waiting' puts a your-turn sign on the board."""
        session = {"id": session_id, "name": session_id} if session_id else _current_session()
        if session is None:
            _fail("No session for this folder. Start one with `subzero agent start`.")
        body: dict[str, Any] = {"status": status}
        if task is not None:
            body["task"] = task
        _call("POST", f"/api/v1/agents/{session['id']}/beat", json_body=body)
        click.echo(f"{session['name']}: {status}")

    @main.command()
    @click.argument("to")
    @click.argument("text", nargs=-1, required=True)
    @click.option("--session", "session_id", default=None, help="Send as this session id.")
    def msg(to: str, text: tuple[str, ...], session_id: str | None) -> None:
        """Message a session (name or id), a home (^zig) or 'me'."""
        session = {"id": session_id} if session_id else _current_session()
        _call(
            "POST",
            "/api/v1/messages",
            json_body={
                "to": to,
                "body": " ".join(text),
                "from_session": session["id"] if session else None,
            },
        )
        click.echo(f"Sent to {to}.")

    @main.command()
    def inbox() -> None:
        """Messages waiting for you."""
        messages = _call("GET", "/api/v1/messages/me")["messages"]
        if not messages:
            click.echo("Nothing for you.")
        for m in messages:
            click.echo(f"{m['created_at'][:16]}  {m['from_label']}: {m['body']}")

    @main.command()
    @click.argument("goal", nargs=-1, required=True)
    @click.option("--ship", is_flag=True, help="Make the change (default: scout - plan only).")
    @click.option("--to", "target", default=None, help="Agent name; same as >name in the goal.")
    def dispatch(goal: tuple[str, ...], ship: bool, target: str | None) -> None:
        """Queue a job for your agents. Sigils work: %project ^lesson >agent."""
        job = _call(
            "POST",
            "/api/v1/dispatch",
            json_body={
                "goal": " ".join(goal),
                "mode": "ship" if ship else "scout",
                "target": target,
                "created_by": "cli",
            },
        )
        click.echo(
            f"Queued {job['id'][:8]} for >{job['target']} as {job['mode']} ({job['routed_by']})."
        )
        for note in job.get("notes") or []:
            click.secho(note, fg="yellow")

    @main.command()
    @click.option(
        "--as", "targets", multiple=True, required=True, help="Agent name(s) to take jobs for."
    )
    @click.option(
        "--session", "session_id", default=None, help="Session id; defaults to this folder's."
    )
    def claim(targets: tuple[str, ...], session_id: str | None) -> None:
        """Take the oldest queued job for these agents (prints JSON, or nothing)."""
        session_id = session_id or (s["id"] if (s := _current_session()) else None)
        if not session_id:
            _fail("No session. Run `subzero agent start --harness <name>` first.")
        job = _call(
            "POST",
            "/api/v1/dispatch/claim",
            json_body={"session_id": session_id, "targets": list(targets)},
        )["job"]
        if job:
            click.echo(json.dumps(job))

    @main.command()
    @click.option("--port", default=7777, show_default=True, help="Local port (127.0.0.1 only).")
    @click.option("--no-open", is_flag=True, help="Do not open a browser.")
    def board(port: int, no_open: bool) -> None:
        """Open the 2D board: agents, lessons, jobs and vault notes, live."""
        import webbrowser

        from subzero.board.server import make_server

        try:
            config = client.load_config()
        except client.ClientError as exc:
            _fail(str(exc))
        try:
            server = make_server(port, config)
        except OSError:
            _fail(f"Port {port} is busy. Try: subzero board --port {port + 1}")
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        click.secho(f"Board at {url}  (Ctrl-C to stop)", fg="green")
        if not no_open:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            click.echo("Stopped.")
        finally:
            server.server_close()

    @main.command()
    @click.argument("job_id")
    @click.option("--failed", is_flag=True)
    @click.option("--result", default=None, help="One or two lines: what happened, PR link.")
    def done(job_id: str, failed: bool, result: str | None) -> None:
        """Mark a dispatched job done (or --failed)."""
        _call(
            "POST",
            f"/api/v1/dispatch/{job_id}/finish",
            json_body={"status": "failed" if failed else "done", "result": result},
        )
        click.echo("Recorded.")
