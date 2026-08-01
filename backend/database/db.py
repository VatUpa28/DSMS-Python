import sqlite3
import os


BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

DEFAULT_DB_PATH = os.path.join(BASE_DIR, "app.db")


def get_db_path():
    """Return the configured database path without ever printing it to stdout."""
    return os.environ.get("DSMS_DB_PATH", DEFAULT_DB_PATH)


def _is_default_database_path(path):
    return os.path.normcase(os.path.abspath(path)) == os.path.normcase(
        os.path.abspath(DEFAULT_DB_PATH)
    )


def get_db():
    db_path = get_db_path()
    if os.environ.get("DSMS_TESTING") == "1" and _is_default_database_path(db_path):
        raise RuntimeError("Tests must set DSMS_DB_PATH to a temporary SQLite database.")

    conn = sqlite3.connect(
        db_path,
        timeout=30,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn
