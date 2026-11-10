"""The subzero client: how the CLI and coding-agent hooks talk to the server.

Configuration lives in ``~/.config/subzero/client.json`` (written by
``subzero connect``, readable only by you):

    {"url": "https://<mac>.<tailnet>.ts.net", "token": "pcd_...", "machine": "mbp"}

``SUBZERO_CLIENT_URL`` and ``SUBZERO_CLIENT_TOKEN`` override it, which is how a
firstmate crew or a CI box gets credentials without the file.

Hooks must never slow down or break the harness they run in, so every call
here has a short timeout and the hook commands swallow failures.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from subzero import sigils

CONFIG_DIR = Path(os.environ.get("SUBZERO_CONFIG_DIR", Path.home() / ".config" / "subzero"))
STATE_DIR = Path(os.environ.get("SUBZERO_STATE_DIR", Path.home() / ".cache" / "subzero"))
#: A file in a project or worktree whose first line holds its sigils
#: (``^zig #comptime`` for a lesson worktree, ``%Code/site`` for a project).
HOME_FILE = ".subzero"


class ClientError(RuntimeError):
    """Something the person can fix; the message says what."""


@dataclass
class ClientConfig:
    url: str
    token: str
    machine: str | None = None


def config_path() -> Path:
    return CONFIG_DIR / "client.json"


def load_config() -> ClientConfig:
    data: dict[str, Any] = {}
    if config_path().is_file():
        data = json.loads(config_path().read_text())
    url = os.environ.get("SUBZERO_CLIENT_URL") or data.get("url")
    token = os.environ.get("SUBZERO_CLIENT_TOKEN") or data.get("token")
    if not url or not token:
        raise ClientError(
            "Not connected. Run: subzero connect <server address> (with a code from "
            "`subzero pair-device` on the server)."
        )
    return ClientConfig(url=url.rstrip("/"), token=token, machine=data.get("machine"))


def save_config(config: ClientConfig) -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = config_path()
    # Created private: the token is never readable by others, not even briefly.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(json.dumps(config.__dict__, indent=2) + "\n")
    path.chmod(0o600)
    return path


def pair(url: str, code: str, machine: str) -> ClientConfig:
    """Trade a one-time pairing code for a device token."""
    url = url.rstrip("/")
    parsed = urlparse(url)
    local = parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")
    if not (parsed.scheme == "https" and parsed.hostname) and not local:
        raise ClientError("Use the server's https:// address (http only for localhost).")
    try:
        response = httpx.post(
            f"{url}/api/v1/auth/pair-device",
            json={"pairing_code": code, "device_name": machine},
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise ClientError(f"Could not reach {url}: {exc}") from exc
    if response.status_code != 200:
        raise ClientError("That pairing code is wrong or expired. Make a new one.")
    return ClientConfig(url=url, token=response.json()["device_token"], machine=machine)


def api(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 10,
    config: ClientConfig | None = None,
) -> Any:
    config = config or load_config()
    try:
        response = httpx.request(
            method,
            f"{config.url}{path}",
            json=json_body,
            params=params,
            headers={"authorization": f"Bearer {config.token}"},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise ClientError(f"Could not reach {config.url}: {exc}") from exc
    if response.status_code == 401:
        raise ClientError("The server refused this device. Run `subzero connect` again.")
    if response.status_code >= 400:
        try:
            detail = response.json().get("error")
        except ValueError:
            detail = response.text[:200]
        raise ClientError(str(detail or f"HTTP {response.status_code}"))
    return response.json()


# ---------------------------------------------------------------- local facts


def git(cwd: str, *args: str) -> str | None:
    program = shutil.which("git")
    if program is None:
        return None
    try:
        # Fixed program, no shell; the arguments are ours, cwd is a path.
        out = subprocess.run(  # noqa: S603
            [program, "-C", cwd, *args], capture_output=True, text=True, timeout=2, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def repo_root(cwd: str) -> Path | None:
    top = git(cwd, "rev-parse", "--show-toplevel")
    return Path(top) if top else None


def home_sigils(cwd: str) -> sigils.Sigils:
    """Sigils from the nearest ``.subzero`` file at or above ``cwd``."""
    here = Path(cwd).resolve()
    for folder in (here, *here.parents):
        candidate = folder / HOME_FILE
        if candidate.is_file():
            first = candidate.read_text(errors="replace").strip().splitlines()[:1]
            return sigils.parse(first[0] if first else "")
        if (folder / ".git").exists():
            break
    return sigils.parse("")


# ---------------------------------------------------------------- session cache


def _key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:24]


def remember_session(external_id: str, cwd: str, session: dict[str, Any]) -> None:
    folder = STATE_DIR / "sessions"
    folder.mkdir(parents=True, exist_ok=True)
    record = json.dumps({"id": session["id"], "name": session["name"], "cwd": cwd})
    (folder / f"ext-{_key(external_id)}.json").write_text(record)
    (folder / f"cwd-{_key(str(Path(cwd).resolve()))}.json").write_text(record)


def session_for_external(external_id: str) -> dict[str, Any] | None:
    path = STATE_DIR / "sessions" / f"ext-{_key(external_id)}.json"
    return json.loads(path.read_text()) if path.is_file() else None


def session_for_cwd(cwd: str) -> dict[str, Any] | None:
    """The session last started in ``cwd`` or a folder above it."""
    if env := os.environ.get("SUBZERO_AGENT_SESSION"):
        return {"id": env, "name": env}
    here = Path(cwd).resolve()
    for folder in (here, *here.parents):
        path = STATE_DIR / "sessions" / f"cwd-{_key(str(folder))}.json"
        if path.is_file():
            return json.loads(path.read_text())
    return None
