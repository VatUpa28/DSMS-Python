import os
import sqlite3

from database.db import get_db_path
from database.migrations import MIGRATION_NAMES, upgrade_database

BASE_DIR = os.path.dirname(__file__)
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")


def init_db(db_path=None):
    """Create a new database or safely upgrade an existing one.

    Existing database files are never recreated.  `upgrade_database` makes a
    timestamped backup before changing an existing file.
    """
    target = db_path or get_db_path()
    if os.path.exists(target) and os.path.getsize(target) > 0:
        return upgrade_database(target, backup=True)

    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    conn = sqlite3.connect(target)
    try:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as schema_file:
            conn.executescript(schema_file.read())
        conn.executemany(
            "INSERT OR IGNORE INTO schema_migrations (name) VALUES (?)",
            [(name,) for name in MIGRATION_NAMES],
        )
        conn.commit()
    finally:
        conn.close()
    return None


if __name__ == "__main__":
    backup_path = init_db()
    if backup_path:
        print(f"Database upgraded. Backup created at: {backup_path}")
    else:
        print("Database initialized successfully.")
