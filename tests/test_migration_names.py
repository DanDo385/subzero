"""Each new migration takes a number of its own.

Three numbers are shared by two applied files each. That is harmless (the
runner records a migration by file name and content hash, and applies in name
order), and renaming an applied file would make it look new. So those three
stay, and this refuses a fourth.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
#: Applied in production under these names; never renumber them.
GRANDFATHERED = {"0009", "0010", "0012"}


def test_no_new_migration_reuses_a_number() -> None:
    numbers = Counter(path.name.split("_", 1)[0] for path in MIGRATIONS.glob("*.sql"))
    shared = {number for number, count in numbers.items() if count > 1}
    assert shared <= GRANDFATHERED, f"reused migration number(s): {sorted(shared - GRANDFATHERED)}"
    assert all(numbers[n] == 2 for n in GRANDFATHERED)
