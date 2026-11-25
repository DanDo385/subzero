"""Settings are read as SUBZERO_ names only, and a stale PC_ name is refused."""

from __future__ import annotations

from pathlib import Path

import pytest

from subzero.config import Settings, legacy_env_names


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # No .env/.env.local from the developer's checkout, no stray settings.
    monkeypatch.chdir(tmp_path)
    for name in (
        "SUBZERO_LOG_LEVEL",
        "PC_LOG_LEVEL",
        "SUBZERO_PORT",
        "PC_PORT",
        "SUBZERO_HARNESS_API_KEY",
        "PC_HARNESS_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_new_prefix_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUBZERO_PORT", "9001")
    assert Settings().port == 9001


def test_old_prefix_is_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pre-rename variable is ignored rather than quietly honoured.

    Honouring it is what let a deployment keep running on a name that nothing
    documented, so the day the fallback was removed no deployment would have
    noticed until it lost a setting. Ignoring it and refusing to boot says so
    out loud instead.
    """
    monkeypatch.setenv("PC_PORT", "9002")
    assert Settings().port != 9002


def test_old_names_in_env_local_are_not_read(tmp_path: Path) -> None:
    (tmp_path / ".env.local").write_text("PC_LOG_LEVEL=DEBUG\n")
    assert Settings().log_level != "DEBUG"


def test_legacy_env_names_lists_old_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PC_PORT", "1")
    assert "PC_PORT" in legacy_env_names()


def test_startup_names_a_stale_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """The refusal has to say which variable, on a machine nobody is watching."""
    monkeypatch.setenv("PC_HARNESS_API_KEY", "bridge-test-key")
    problems = Settings().validate_startup()
    assert any("PC_HARNESS_API_KEY" in problem for problem in problems)


def test_bridge_key_reads_the_new_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUBZERO_HARNESS_API_KEY", "bridge-test-key")
    assert Settings().harness_api_key.get_secret_value() == "bridge-test-key"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://postgres@/subzero?host=/srv/pg", "subzero on this computer (/srv/pg)"),
        ("postgresql://u:secret@db.example.com:6543/postgres", "postgres on db.example.com:6543"),
        ("postgresql://u:secret@localhost/subzero", "subzero on localhost"),
        ("", "(no database configured)"),
    ],
)
def test_database_target_names_the_database_without_credentials(url: str, expected: str) -> None:
    from pydantic import SecretStr

    target = Settings(database_url=SecretStr(url)).database_target()
    assert target == expected
    assert "secret" not in target
