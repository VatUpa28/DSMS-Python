"""Command-line entry point for the safe live SQLite upgrade."""

from database.db import get_db_path
from database.migrations import upgrade_database


if __name__ == "__main__":
    backup = upgrade_database(get_db_path(), backup=True)
    if backup:
        print(f"Upgrade completed. Backup created at: {backup}")
    else:
        print("Database already has all configured migrations.")
