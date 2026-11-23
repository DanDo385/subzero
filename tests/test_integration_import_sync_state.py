"""An imported conversation tells the extension how current it is.

ZIP imports never set ``conversations.provider_updated_at``, so ``sync-state``
reported no time for them, the extension could not tell they were already up
to date, and periodic sync walked past every imported chat to capture it again.
"""

from __future__ import annotations

from datetime import UTC, datetime

from conftest import requires_db
from subzero.importers.base import ImportReport, NormalizedConversation, NormalizedMessage
from subzero.importers.runner import ImportRunner

pytestmark = requires_db


async def test_an_import_records_the_provider_update_time(services) -> None:
    updated = datetime(2026, 11, 1, 9, 30, tzinfo=UTC)
    conversation = NormalizedConversation(
        provider="claude",
        provider_conversation_id="zip-1",
        title="Exported chat",
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
        updated_at=updated,
        model=None,
        messages=[NormalizedMessage(role="user", content="hello", provenance="imported_metadata")],
    )

    await ImportRunner(services)._import_one(
        conversation, ImportReport(provider="claude", archive_sha256="")
    )

    assert (
        await services.db.fetchval(
            "SELECT provider_updated_at FROM conversations WHERE provider_conversation_id = 'zip-1'"
        )
        == updated
    )
