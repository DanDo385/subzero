"""SUBZERO_ settings take priority, with PC_ names supported during migration."""

from __future__ import annotations

from pathlib import Path

import pytest

from subzero.config import Settings, legacy_env_names


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in ("SUBZERO_LOG_LEVEL", "PC_LOG_LEVEL", "SUBZERO_PORT", "PC_PORT"):
        monkeypatch.delenv(name, raising=False)


def test_new_prefix_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUBZERO_PORT", "9001")
    assert Settings().port == 9001


def test_old_prefix_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PC_PORT", "9002")
    assert Settings().port == 9002


def test_new_prefix_wins_over_old(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PC_PORT", "9002")
    monkeypatch.setenv("SUBZERO_PORT", "9003")
    assert Settings().port == 9003


def test_old_names_in_env_local_still_work(tmp_path: Path) -> None:
    (tmp_path / ".env.local").write_text("PC_LOG_LEVEL=DEBUG\n")
    assert Settings().log_level == "DEBUG"


def test_new_name_in_env_local_wins_over_old(tmp_path: Path) -> None:
    (tmp_path / ".env.local").write_text("PC_LOG_LEVEL=DEBUG\nSUBZERO_LOG_LEVEL=WARNING\n")
    assert Settings().log_level == "WARNING"


def test_legacy_env_names_lists_old_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PC_PORT", "1")
    assert "PC_PORT" in legacy_env_names()
