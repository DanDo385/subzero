"""Worker configuration.

The worker runs on the user's machine and talks OUTBOUND only. It needs no
inbound port, no tunnel, no public hostname and no filesystem exposure.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        # .env.local is where your own settings go; it overrides .env.
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
        # Deliberately specific: a bare OBSIDIAN_ prefix collides with
        # variables other tools already export (OBSIDIAN_VAULT_PATH is a
        # common one), and an unrelated export must not redirect where
        # this worker writes.
        env_prefix="OBSIDIAN_SYNC_",
    )

    #: Base URL of the Subzero service (outbound target).
    api_url: str = "http://localhost:8080"
    api_key: SecretStr = SecretStr("")
    worker_id: str = "macbook"

    vault_path: Path = Path("~/Obsidian/Vault")
    poll_interval_seconds: float = 20.0
    batch_size: int = 10

    #: Push human edits from the vault back to the canonical store.
    watch_for_edits: bool = True
    #: Roots the worker will write inside. Must match the server's config;
    #: the worker validates independently rather than trusting the job.
    vault_roots: dict[str, str] = {
        "inbox": "00-Inbox",
        "daily": "10-Daily",
        "project": "20-Projects",
        "research": "30-Research",
        "wiki": "40-Wiki",
        "concept": "50-Concepts",
        "resource": "60-Resources",
        "atlas": "70-Atlas",
        "shared": "80-Shared",
        "profile": "85-Profiles",
        "system": "90-System",
        "archive": "99-Archive",
        "area": "25-Areas",
        "permanent": "Permanent",
        "experiments": "Experiments",
    }

    max_file_bytes: int = 32 * 1024 * 1024
    dry_run: bool = False
    #: Where the helper keeps its record of what it wrote, so a human edit is
    #: still recognised after a restart. Outside the vault on purpose: a vault
    #: synced between devices would otherwise carry one machine's record to
    #: the others. Defaults to $XDG_STATE_HOME/subzero/obsidian-sync.
    state_dir: Path | None = None

    @property
    def resolved_vault(self) -> Path:
        return self.vault_path.expanduser().resolve()

    @property
    def state_file(self) -> Path:
        """One file per vault, so two vaults on one machine never share a record."""
        base = self.state_dir or (
            Path(os.environ.get("XDG_STATE_HOME") or "~/.local/state") / "subzero" / "obsidian-sync"
        )
        key = hashlib.sha256(str(self.resolved_vault).encode()).hexdigest()[:16]
        return base.expanduser() / f"written-{key}.json"
