"""Vercel entrypoint. Vercel resolves ``main:app`` from the repository root,
where the ``src/`` layout is not on the import path unless the project itself
was installed, so add it before importing the real module."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from subzero.asgi import app  # noqa: E402

__all__ = ["app"]
