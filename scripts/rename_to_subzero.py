"""Rename personal-context-mcp to subzero across a checkout.

Re-runnable: it only rewrites old names, so running it twice changes nothing.
Run it on any branch that predates the rename (feat-server-skills,
feat-browser-extension, ...) after merging main into it, then fix conflicts:

    uv run python scripts/rename_to_subzero.py
    uv lock

What it leaves alone, on purpose:

- migrations/*.sql: applied migrations are hash-checked at startup, so editing
  even a comment in one makes the server refuse to boot.
- the live Vercel address (personal-context-mcp-seven.vercel.app) and the
  stopword list in retrieval/inference.py.
- `PC_` names inside the deliberate fallbacks (config.py, conftest.py, the
  deploy scripts), which a second run must not turn into SUBZERO_.
- `pc:` markers in rendered vault notes, which existing notes already carry.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve()

SKIP_PREFIXES = ("migrations/",)
SKIP_FILES = {"uv.lock"}
#: Files where only package/CLI names change, not prose like "personal context".
PROSE_EXEMPT = {"src/subzero/retrieval/inference.py", "src/personal_context_mcp/retrieval/inference.py"}

#: Files that read the old PC_ names on purpose, as a fallback.
ENV_EXEMPT = {
    "src/subzero/config.py",
    "tests/conftest.py",
    "tests/test_config_env_prefix.py",
    "tests/test_ingest.py",
    "deploy/go-live.sh",
    "deploy/preflight.sh",
    "deploy/vercel-env.sh",
}

PROTECT = {
    "personal-context-mcp-seven.vercel.app": "\x00VERCEL\x00",
    '("personal context mcp")': "\x00LOOSE\x00",
    'personal-context-mcp = "subzero.cli:main"': "\x00ALIAS\x00",
}

#: Ordered: longer and more specific names first.
LITERALS: list[tuple[str, str]] = [
    ("personal_context_mcp_test", "subzero_test"),
    ("personal_context_mcp", "subzero"),
    ("personal-context-mcp-archives", "subzero-archives"),
    ("personal-context-mcp-probe", "subzero-probe"),
    ("personal-context-mcp", "subzero"),
    ("personal-context-extension", "subzero-extension"),
    ("personal-context server", "subzero server"),
    ("--transport http personal-context ", "--transport http subzero "),
    ("**personal-context**", "**subzero**"),
    ("Personal Context MCP", "Subzero"),
    ("Personal Context", "Subzero"),
    ('env_prefix="PC_"', 'env_prefix="SUBZERO_"'),
]

PROSE: list[tuple[str, str]] = [
    ("my personal context", "subzero"),
    ("Check personal context", "Check subzero"),
    ("to personal context", "to subzero"),
    ("personal context", "subzero"),
]

ENV = re.compile(r"\bPC_(?=[A-Z0-9])")


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout
    return [line for line in out.splitlines() if line]


def rewrite(rel: str, text: str) -> str:
    for old, token in PROTECT.items():
        text = text.replace(old, token)
    for old, new in LITERALS:
        text = text.replace(old, new)
    if rel not in PROSE_EXEMPT:
        for old, new in PROSE:
            text = text.replace(old, new)
    if rel not in ENV_EXEMPT:
        text = ENV.sub("SUBZERO_", text)
    for old, token in PROTECT.items():
        text = text.replace(token, old)
    return text


def main() -> int:
    old_pkg = ROOT / "src" / "personal_context_mcp"
    if old_pkg.exists():
        subprocess.run(["git", "mv", "src/personal_context_mcp", "src/subzero"], cwd=ROOT, check=True)

    changed = 0
    for rel in tracked_files():
        path = ROOT / rel
        if path.resolve() == SELF or rel in SKIP_FILES or rel.startswith(SKIP_PREFIXES):
            continue
        if not path.is_file() or path.is_symlink():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        new = rewrite(rel, text)
        if new != text:
            path.write_text(new, encoding="utf-8")
            changed += 1
    print(f"rewrote {changed} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
