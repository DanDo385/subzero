"""Start, watch and clean up one-command tmux sessions on a private socket.

The tmux commands follow the tmux skill: a detached ``new-session``,
``capture-pane`` to read it, and ``kill-session`` when the job is finished.
The socket (``tmux -L``) is the runner's alone, so a user's existing tmux
server is never attached to, listed or killed.

The user command runs as this process's macOS user, inside ``bash
--noprofile --norc``, with an environment built here. The server's
environment is not passed through.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Sequence
from pathlib import Path

from subzero.agents.exec_paths import resolve_cwd
from subzero.errors import NotFoundError, ValidationError

MAX_COMMAND_CHARS = 4000
MAX_TIMEOUT_S = 600
OUTPUT_LIMIT_DEFAULT = 16 * 1024
_JOB_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# A shell segment that starts with sudo (or its absolute path) is refused.
# ``echo sudo`` does not match: sudo has to be the command, not an argument.
_SUDO = re.compile(
    r"(?:^|[;&\n]|&&|\|\|)\s*(?:/usr/bin/sudo|/bin/sudo|sudo)(?:\s|$)",
    re.IGNORECASE,
)
_SOCKET = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _tail(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[-limit:], True


class CliHostRunner:
    """One private tmux server and the job directories that belong to it."""

    def __init__(
        self,
        *,
        roots: Sequence[str],
        state_dir: Path,
        socket_name: str = "subzero-cli",
        output_limit: int = OUTPUT_LIMIT_DEFAULT,
        sync_wait_s: float = 8.0,
        tmux_bin: str | None = None,
    ) -> None:
        if not _SOCKET.fullmatch(socket_name):
            raise ValidationError("The CLI runner socket name must be letters, digits, '_' or '-'.")
        self.roots = list(roots)
        self.state_dir = state_dir
        self.socket_name = socket_name
        self.output_limit = max(256, output_limit)
        self.sync_wait_s = max(0.2, sync_wait_s)
        self._tmux = tmux_bin or shutil.which("tmux") or ""

    def run(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout_s: int = 30,
        retain: bool = False,
    ) -> dict[str, object]:
        """Start a job and wait until it finishes or ``sync_wait_s`` elapses."""
        started = self.start(command, cwd=cwd, timeout_s=timeout_s, retain=retain)
        if started["status"] != "running":
            return started
        deadline = time.monotonic() + min(self.sync_wait_s, float(timeout_s) + 1.5)
        while time.monotonic() < deadline:
            current = self.status(str(started["job_id"]))
            if current["status"] != "running":
                return current
            time.sleep(0.1)
        return self.status(str(started["job_id"]))

    def start(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout_s: int = 30,
        retain: bool = False,
    ) -> dict[str, object]:
        """Create the session and return without waiting for the command."""
        self._refuse_root()
        checked = _validate_command(command)
        timeout = _validate_timeout(timeout_s)
        resolved = self._resolve(cwd)
        if not self._tmux:
            raise ValidationError(
                "tmux is not installed, so the CLI runner cannot start a session."
            )

        job_id = str(uuid.uuid4())
        session = "job-" + job_id.replace("-", "")[:12]
        job = self.state_dir / "jobs" / job_id
        job.mkdir(parents=True, mode=0o700)
        tmp = job / "tmp"
        tmp.mkdir(mode=0o700)
        host = socket.gethostname()
        meta = {
            "job_id": job_id,
            "session": session,
            "cwd": resolved,
            "host": host,
            "timeout_s": timeout,
            "retain": retain,
            "socket": self.socket_name,
            "command": checked,
        }
        (job / "meta.json").write_text(json.dumps(meta))
        (job / "command").write_text(checked)
        (job / "env.json").write_text(json.dumps(_job_env(resolved, tmp)))
        for path in (job / "meta.json", job / "command", job / "env.json"):
            path.chmod(0o600)

        command_line = " ".join(
            (
                shlex.quote(sys.executable),
                "-m",
                "subzero.cli_host.session",
                shlex.quote(str(job)),
            )
        )
        try:
            # One shell command, the form the tmux skill uses for a detached
            # session. shlex.quote keeps the paths as data for that shell.
            self._tmux_run(
                [
                    "new-session",
                    "-d",
                    "-s",
                    session,
                    "-c",
                    resolved,
                    "-x",
                    "200",
                    "-y",
                    "50",
                    command_line,
                ],
                check=True,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            detail = getattr(exc, "stderr", b"") or str(exc)
            if isinstance(detail, bytes):
                detail = detail.decode("utf-8", errors="replace")
            (job / "stderr").write_text(detail[-2000:])
            (job / "status").write_text("failed")
            (job / "exit").write_text("1")
            return self._payload(meta, "failed", 1, "", detail[-2000:], False, False)

        return self._payload(meta, "running", None, "", "", False, False)

    def status(self, job_id: str) -> dict[str, object]:
        """Read a job. Finished sessions are removed unless the job asked to keep them."""
        meta = self._meta(job_id)
        job = self._job_path(job_id)
        state = (job / "status").read_text().strip() if (job / "status").is_file() else ""
        if state not in {"done", "timed_out", "failed", "running"}:
            state = "running" if self._session_alive(str(meta["session"])) else "failed"
        exit_code = _read_exit(job)
        stdout_path = job / "stdout"
        stderr_path = job / "stderr"
        stdout, stdout_truncated = self._read_stream(stdout_path)
        stderr, stderr_truncated = self._read_stream(stderr_path)
        session = str(meta["session"])
        alive = self._session_alive(session)
        if alive:
            # Read the pane before any cleanup. Files are the split streams.
            # The pane fills stdout only when the command has not written a
            # file yet (still starting); an empty file is a real empty stream.
            pane = self.capture(session)
            if not stdout_path.is_file() and pane.strip():
                stdout, stdout_truncated = _tail(pane, self.output_limit)
        retain = bool(meta.get("retain"))
        if state in {"done", "timed_out", "failed"} and not retain and alive:
            self._kill(session)
            alive = False
        return self._payload(
            meta,
            state,
            exit_code,
            stdout,
            stderr,
            stdout_truncated,
            stderr_truncated,
            session_alive=alive,
        )

    def capture(self, session: str) -> str:
        """Read a session's pane without attaching to it."""
        if not _SOCKET.fullmatch(session) and not session.startswith("job-"):
            raise ValidationError("session name is not a CLI runner session.")
        completed = self._tmux_run(
            ["capture-pane", "-p", "-S", "-100", "-t", session],
            check=False,
        )
        if completed.returncode != 0:
            return ""
        return completed.stdout.decode("utf-8", errors="replace")

    def sessions(self) -> list[str]:
        completed = self._tmux_run(
            ["list-sessions", "-F", "#{session_name}"],
            check=False,
        )
        if completed.returncode != 0:
            return []
        return [
            line for line in completed.stdout.decode("utf-8", errors="replace").splitlines() if line
        ]

    def cleanup(self, job_id: str) -> None:
        """Stop a session even when the job asked to keep it."""
        meta = self._meta(job_id)
        self._kill(str(meta["session"]))

    def kill_server(self) -> None:
        """Stop this runner's tmux server. Does not touch any other socket."""
        self._tmux_run(["kill-server"], check=False)

    def _resolve(self, cwd: str | None) -> str:
        if cwd and cwd.strip():
            return resolve_cwd(cwd, self.roots)
        for root in self.roots:
            candidate = Path(str(root).strip()).expanduser()
            if candidate.is_dir():
                return resolve_cwd(str(candidate), self.roots)
        raise ValidationError(
            "No working directory was given, and none of the configured directories exist."
        )

    def _meta(self, job_id: str) -> dict[str, object]:
        path = self._job_path(job_id) / "meta.json"
        if not path.is_file():
            raise NotFoundError(f"No CLI job {job_id}.")
        loaded = json.loads(path.read_text())
        if not isinstance(loaded, dict):
            raise NotFoundError(f"No CLI job {job_id}.")
        return loaded

    def _job_path(self, job_id: str) -> Path:
        try:
            normalized = str(uuid.UUID(job_id))
        except ValueError as exc:
            raise ValidationError("job id must be the id run_cli_on_mbp returned.") from exc
        if not _JOB_ID.fullmatch(normalized):
            raise ValidationError("job id must be the id run_cli_on_mbp returned.")
        root = (self.state_dir / "jobs").resolve()
        path = (root / normalized).resolve()
        if not path.is_relative_to(root):
            raise ValidationError("job id must be the id run_cli_on_mbp returned.")
        return path

    def _session_alive(self, session: str) -> bool:
        return session in self.sessions()

    def _kill(self, session: str) -> None:
        self._tmux_run(["kill-session", "-t", session], check=False)

    def _read_stream(self, path: Path) -> tuple[str, bool]:
        if not path.is_file():
            return "", False
        return _tail(path.read_bytes().decode("utf-8", errors="replace"), self.output_limit)

    def _tmux_run(self, args: list[str], *, check: bool) -> subprocess.CompletedProcess[bytes]:
        if not self._tmux:
            raise ValidationError(
                "tmux is not installed, so the CLI runner cannot start a session."
            )
        return subprocess.run(  # noqa: S603
            [self._tmux, "-L", self.socket_name, *args],
            env=_tmux_env(),
            capture_output=True,
            timeout=15,
            check=check,
        )

    def _refuse_root(self) -> None:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            raise ValidationError("The CLI runner will not run as root.")

    def _payload(
        self,
        meta: dict[str, object],
        status: str,
        exit_code: int | None,
        stdout: str,
        stderr: str,
        stdout_truncated: bool,
        stderr_truncated: bool,
        *,
        session_alive: bool | None = None,
    ) -> dict[str, object]:
        retain = bool(meta.get("retain"))
        alive = session_alive
        if alive is None:
            alive = self._session_alive(str(meta["session"]))
        return {
            "job_id": meta["job_id"],
            "host": meta["host"],
            "cwd": meta["cwd"],
            "command": meta.get("command", ""),
            "exit_code": exit_code,
            "status": status,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "retained": retain and alive,
            "session": meta["session"],
            "socket": meta.get("socket", self.socket_name),
        }


def _validate_command(command: str) -> str:
    text = command.strip()
    if not text:
        raise ValidationError("Give the exact command to run.")
    if len(text) > MAX_COMMAND_CHARS:
        raise ValidationError(f"The command exceeds {MAX_COMMAND_CHARS} characters.")
    if "\x00" in text or any(ord(ch) < 32 and ch not in "\t\n\r" for ch in text):
        raise ValidationError(
            "The command contains a control character the runner will not pass on."
        )
    if _SUDO.search(text):
        raise ValidationError("The CLI runner will not run sudo.")
    return text


def _validate_timeout(timeout_s: int) -> int:
    if timeout_s < 1 or timeout_s > MAX_TIMEOUT_S:
        raise ValidationError(f"timeout_s must be between 1 and {MAX_TIMEOUT_S}.")
    return timeout_s


def _job_env(cwd: str, tmp: Path) -> dict[str, str]:
    """The only environment the user command inherits."""
    path = ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    for extra in ("/opt/homebrew/bin", "/usr/local/bin"):
        if Path(extra).is_dir():
            path.insert(0, extra)
    lang = os.environ.get("LANG", "")
    if not lang or any(ord(ch) < 32 for ch in lang):
        lang = "en_US.UTF-8"
    user = getpass.getuser()
    return {
        "PATH": ":".join(path),
        "HOME": str(Path.home()),
        "USER": user,
        "LOGNAME": user,
        "SHELL": "/bin/bash",
        "LANG": lang,
        "TERM": "xterm-256color",
        "PWD": cwd,
        "TMPDIR": str(tmp),
    }


def _tmux_env() -> dict[str, str]:
    """Environment for the tmux client and the server it creates.

    The socket stays in tmux's own short directory. ``-L`` is what makes it
    a private server; a custom ``TMUX_TMPDIR`` under a long temp path exceeds
    the macOS socket-path limit. The user command does not receive this
    mapping; it receives ``_job_env``.
    """
    return _job_env(str(Path.home()), Path("/tmp"))


def runner_for(settings: object) -> CliHostRunner:
    """Build the runner the MCP tools call when this process is the Mac host."""
    from subzero.config import Settings

    if not isinstance(settings, Settings):
        raise ValidationError("CLI runner settings are missing.")
    if settings.cli_runner_state_dir:
        state = Path(settings.cli_runner_state_dir).expanduser()
    else:
        base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
        state = Path(base).expanduser() / "subzero" / "cli-runner"
    return CliHostRunner(
        roots=settings.exec_roots,
        state_dir=state,
        socket_name=settings.cli_runner_socket,
        output_limit=settings.cli_runner_output_chars,
        sync_wait_s=settings.cli_runner_sync_wait_s,
    )


def runs_on_this_host(settings: object) -> bool:
    """True when this process should start tmux itself.

    The connector's server runs on the Mac, so ``auto`` executes here on
    Darwin and refuses elsewhere. ``local`` forces it. ``off`` never starts
    a shell. A non-Mac server does not get an outbound worker in this
    process: there is no second public endpoint to claim jobs from.
    """
    from subzero.config import Settings

    if not isinstance(settings, Settings):
        return False
    if settings.cli_runner == "off":
        return False
    if settings.cli_runner == "local":
        return True
    return sys.platform == "darwin"


def _read_exit(job: Path) -> int | None:
    path = job / "exit"
    if not path.is_file():
        return None
    text = path.read_text().strip()
    try:
        return int(text)
    except ValueError:
        return None
