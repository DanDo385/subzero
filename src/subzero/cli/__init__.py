"""subzero command line.

subzero serve
subzero migrate [--status]
subzero check
subzero probe <url> [--token TOKEN]
subzero import <provider|auto> <archive.zip>
subzero export-lists --out DIR [--format md|json|both]
subzero reclassify [--limit N]
subzero review
subzero vault-fill"""

from __future__ import annotations

from subzero.cli import bridge, classify, data, gemini_sync, memory_lab, ops, wiki  # noqa: F401
from subzero.cli.app import main
from subzero.cli_agents import register as register_agent_commands

# Each module above registers its commands on `main` when imported.
register_agent_commands(main)

__all__ = ["main"]
