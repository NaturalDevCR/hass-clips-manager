"""Built-in processing profiles."""

import json
from datetime import UTC, datetime

from .database import Database
from .profile_validation import ProcessingProfile

COMPATIBILITY_PROFILE_ID = "compatibility-4k-loudness"


def compatibility_4k_loudness_profile() -> ProcessingProfile:
    """Return the editable Compatibility 4K Loudness baseline."""
    return ProcessingProfile()


def seed_builtin_profiles(database: Database) -> None:
    """Make the editable compatibility baseline discoverable on every database."""

    now = datetime.now(UTC).isoformat()
    settings = json.dumps(
        compatibility_4k_loudness_profile().model_dump(mode="json"),
        separators=(",", ":"),
        sort_keys=True,
    )
    with database.transaction() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO profiles(id,name,settings,created_at,updated_at) "
            "VALUES(?,?,?,?,?)",
            (
                COMPATIBILITY_PROFILE_ID,
                "Compatibility 4K Loudness",
                settings,
                now,
                now,
            ),
        )
