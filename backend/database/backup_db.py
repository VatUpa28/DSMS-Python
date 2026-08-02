import os
import sqlite3
from datetime import datetime
from pathlib import Path
from contextlib import closing

from config import DB_PATH


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "backups"


def create_backup() -> Path:
    source_path = Path(DB_PATH).resolve()
    backup_dir = Path(
        os.environ.get("DSMS_BACKUP_DIR", str(DEFAULT_BACKUP_DIR))
    ).resolve()

    if not source_path.exists():
        raise FileNotFoundError(f"Database not found: {source_path}")

    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    final_path = backup_dir / f"dsms-backup-{timestamp}.db"
    temporary_path = backup_dir / f".dsms-backup-{timestamp}.tmp"

    try:
        with closing(sqlite3.connect(source_path)) as source:
            with closing(sqlite3.connect(temporary_path)) as destination:
                source.backup(destination)
                destination.commit()

        with closing(sqlite3.connect(temporary_path)) as verification:
            integrity = verification.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0]

            foreign_key_errors = verification.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()

        if integrity != "ok":
            raise RuntimeError(
                f"Backup integrity check failed: {integrity}"
            )

        if foreign_key_errors:
            raise RuntimeError(
                f"Backup contains foreign-key errors: {foreign_key_errors}"
            )

        temporary_path.replace(final_path)
        return final_path

    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def main() -> None:
    backup_path = create_backup()
    print(f"Backup created successfully: {backup_path}")


if __name__ == "__main__":
    main()