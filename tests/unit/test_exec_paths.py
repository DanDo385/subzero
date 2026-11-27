"""exec_paths: cwd resolution and space validation, without a database."""

from __future__ import annotations

from pathlib import Path

import pytest

from subzero.agents.exec_paths import MAX_SPACE_LENGTH, resolve_cwd, validate_space
from subzero.errors import ValidationError


@pytest.fixture
def code(tmp_path: Path) -> Path:
    """A stand-in for ~/Code: one checkout and one worktree."""
    root = tmp_path / "Code"
    (root / "subzero").mkdir(parents=True)
    (root / "worktrees" / "subzero" / "cli-exec").mkdir(parents=True)
    return root


@pytest.fixture
def roots(code: Path) -> list[str]:
    """The default shape: a code root with the worktrees root inside it."""
    return [str(code), str(code / "worktrees")]


def test_repo_slug_finds_the_worktree(code: Path, roots: list[str]) -> None:
    assert resolve_cwd("subzero/cli-exec", roots) == str(
        (code / "worktrees" / "subzero" / "cli-exec").resolve()
    )


def test_bare_repo_finds_the_checkout(code: Path, roots: list[str]) -> None:
    assert resolve_cwd("subzero", roots) == str((code / "subzero").resolve())


def test_deepest_root_wins_when_both_candidates_exist(code: Path, roots: list[str]) -> None:
    """repo/slug means the worktrees root, even when the checkout has the same subpath."""
    (code / "subzero" / "cli-exec").mkdir()
    assert resolve_cwd("subzero/cli-exec", roots) == str(
        (code / "worktrees" / "subzero" / "cli-exec").resolve()
    )


def test_a_two_segment_spec_falls_back_to_the_code_root(code: Path, roots: list[str]) -> None:
    """A real subdirectory of a checkout resolves when the worktrees root has none."""
    (code / "subzero" / "docs").mkdir()
    assert resolve_cwd("subzero/docs", roots) == str((code / "subzero" / "docs").resolve())


def test_absolute_path_inside_a_root_is_used_as_given(code: Path, roots: list[str]) -> None:
    given = code / "worktrees" / "subzero" / "cli-exec"
    assert resolve_cwd(str(given), roots) == str(given.resolve())


def test_traversal_segments_are_refused(roots: list[str]) -> None:
    for bad in ("../etc", "subzero/../../..", "/tmp/../etc/passwd"):
        with pytest.raises(ValidationError, match="climbs out"):
            resolve_cwd(bad, roots)


def test_a_symlink_that_escapes_is_refused(code: Path, roots: list[str], tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (code / "escape").symlink_to(outside)
    with pytest.raises(ValidationError, match="outside every configured exec root"):
        resolve_cwd("escape", roots)
    with pytest.raises(ValidationError, match="outside every configured exec root"):
        resolve_cwd(str(code / "escape"), roots)


def test_a_symlink_inside_the_roots_resolves_to_its_target(code: Path, roots: list[str]) -> None:
    (code / "link").symlink_to(code / "subzero")
    assert resolve_cwd("link", roots) == str((code / "subzero").resolve())


def test_a_path_outside_the_roots_is_refused(roots: list[str], tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(ValidationError, match="outside every configured exec root"):
        resolve_cwd(str(outside), roots)


def test_a_path_that_does_not_exist_is_refused(code: Path, roots: list[str]) -> None:
    with pytest.raises(ValidationError, match="No directory"):
        resolve_cwd("nope/missing", roots)
    with pytest.raises(ValidationError, match="does not exist"):
        resolve_cwd(str(code / "ghost"), roots)


def test_a_file_is_not_a_cwd(code: Path, roots: list[str]) -> None:
    (code / "README").write_text("x")
    with pytest.raises(ValidationError, match="not a directory"):
        resolve_cwd(str(code / "README"), roots)


def test_garbage_shapes_are_refused(roots: list[str]) -> None:
    for bad in ("", "   ", "~/Code/subzero", "subzero//cli-exec", "./subzero", "a" * 501):
        with pytest.raises(ValidationError):
            resolve_cwd(bad, roots)
    with pytest.raises(ValidationError, match="control characters"):
        resolve_cwd("sub\nzero", roots)


def test_no_usable_roots_refuses_everything() -> None:
    with pytest.raises(ValidationError, match="No usable exec roots"):
        resolve_cwd("subzero", [])
    with pytest.raises(ValidationError, match="No usable exec roots"):
        resolve_cwd("subzero", ["Code", ""])


def test_roots_may_be_given_with_a_tilde(code: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A root written as ~/... is expanded before anything is resolved."""
    monkeypatch.setenv("HOME", str(code.parent))
    assert resolve_cwd("subzero", ["~/Code"]) == str((code / "subzero").resolve())


def test_space_is_stored_as_given_but_shape_checked() -> None:
    assert validate_space(None) is None
    assert validate_space("   ") is None
    assert validate_space("  ws-12 ") == "ws-12"
    with pytest.raises(ValidationError, match="control characters"):
        validate_space("ws\t12")
    with pytest.raises(ValidationError, match=f"{MAX_SPACE_LENGTH} characters"):
        validate_space("w" * (MAX_SPACE_LENGTH + 1))
