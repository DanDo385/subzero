"""Resolve where an exec job runs, without ever running anything.

The server stores intent; the runner on the user's machine executes. This
module turns a caller-supplied ``worktree`` into the absolute ``cwd`` the
runner will be pointed at, and refuses everything else. It mirrors the
discipline of ``obsidian/paths.py``, which solves the same problem for the
vault: textual rules first, then the filesystem, and the runner validates
again on arrival, because the cloud service and the laptop do not trust each
other's validation.

Three shapes are accepted:

* ``repo/slug``  a worktree: with the default roots, ~/Code/worktrees/repo/slug
* ``repo``       a checkout: with the default roots, ~/Code/repo
* ``/abs/path``  an absolute path, used as given

A relative shape is tried against every configured root and the first
candidate that exists wins. A one-segment spec is a checkout, so shallower
roots are tried first; a deeper spec is a worktree path, so deeper roots are
tried first. With the defaults (~/Code, ~/Code/worktrees) that makes a bare
"repo" find ~/Code/repo and "repo/slug" find ~/Code/worktrees/repo/slug,
falling back to ~/Code/repo/slug when the worktrees root has no such path.

Refused: ``..`` segments, control characters, ``~``-leading and empty or
``.`` segments in a relative shape, symlinks that resolve outside the roots,
paths outside the roots, and paths that do not exist. Errors name what was
refused and why; they never echo a full home path the caller did not supply.

``space`` is the other half of "where": an opaque herdr workspace label the
server cannot verify against herdr. It is checked only for what the server
can see (length, control characters), stored as given, and an unknown label
is the runner's problem to fail on.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from subzero.errors import ValidationError

#: A worktree spec is a short label, not a payload.
MAX_WORKTREE_LENGTH = 500
#: A herdr workspace label, checked for shape only: the server cannot ask
#: herdr whether it exists.
MAX_SPACE_LENGTH = 120


def _absolute_roots(roots: Sequence[str | Path]) -> list[Path]:
    """Expanded, absolute roots, in the configured order."""
    expanded: list[Path] = []
    for root in roots:
        candidate = Path(str(root).strip()).expanduser()
        if candidate.is_absolute():
            expanded.append(candidate)
    return expanded


def resolve_cwd(worktree: str, roots: Sequence[str | Path]) -> str:
    """Resolve a caller-supplied worktree into an absolute cwd under a root.

    Pure apart from filesystem reads (existence, symlinks); no database, no
    execution. Raises :class:`ValidationError` naming what was refused.
    """
    text = (worktree or "").strip()
    if not text:
        raise ValidationError("worktree is empty; give repo, repo/slug or an absolute path.")
    if len(text) > MAX_WORKTREE_LENGTH:
        raise ValidationError(f"worktree exceeds {MAX_WORKTREE_LENGTH} characters.")
    if "\x00" in text or any(ord(c) < 32 for c in text):
        raise ValidationError("worktree contains control characters.")
    if ".." in text.split("/"):
        raise ValidationError(f"worktree climbs out with '..': {text!r}")

    expanded = _absolute_roots(roots)
    if not expanded:
        raise ValidationError(
            "No usable exec roots are configured, so no worktree can be resolved."
        )

    # Resolved too: a root that is itself behind a symlink still contains
    # what it points at, and containment is decided on resolved paths only.
    resolved_roots = [root.resolve() for root in expanded]

    if PurePosixPath(text).is_absolute():
        candidates = [Path(text)]
    else:
        if text.startswith("~"):
            raise ValidationError(
                "worktree cannot start with '~'; give repo, repo/slug or an absolute path."
            )
        segments = text.split("/")
        if any(segment in ("", ".") for segment in segments):
            raise ValidationError(f"worktree has an empty or '.' segment: {text!r}")
        # A one-segment spec is a checkout: try shallower roots first, so a
        # bare "repo" finds ~/Code/repo rather than the worktrees root's
        # parent directory for it. A deeper spec is a worktree path: try
        # deeper roots first, so "repo/slug" finds ~/Code/worktrees/repo/slug.
        # sorted() is stable: equal depth keeps the configured order.
        order = sorted(
            range(len(expanded)),
            key=lambda i: len(resolved_roots[i].parts),
            reverse=len(segments) > 1,
        )
        candidates = [expanded[i] / text for i in order]

    for candidate in candidates:
        resolved = candidate.resolve()
        if not any(resolved.is_relative_to(root) for root in resolved_roots):
            # True for an absolute path outside the roots, and for a relative
            # one whose real path is elsewhere: a symlink that escapes.
            raise ValidationError(f"worktree {text!r} resolves outside every configured exec root.")
        if resolved.is_dir():
            return str(resolved)

    if PurePosixPath(text).is_absolute():
        if not Path(text).exists():
            raise ValidationError(f"worktree {text!r} does not exist.")
        raise ValidationError(f"worktree {text!r} is not a directory.")
    raise ValidationError(f"No directory {text!r} exists under the configured exec roots.")


def validate_space(space: str | None) -> str | None:
    """Normalise a herdr workspace label, or return None when there is none.

    The server cannot verify the label against herdr, so it refuses only
    what it can see: overlong labels and control characters. Whatever
    survives is stored as given, and herdr's answer is the truth.
    """
    if space is None:
        return None
    text = space.strip()
    if not text:
        return None
    if len(text) > MAX_SPACE_LENGTH:
        raise ValidationError(f"space exceeds {MAX_SPACE_LENGTH} characters.")
    if "\x00" in text or any(ord(c) < 32 for c in text):
        raise ValidationError("space contains control characters.")
    return text
