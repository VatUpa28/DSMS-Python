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

def cleanup_old_backups(backup_dir: Path) -> int:
    retention_value = os.environ.get(
        "DSMS_BACKUP_RETENTION_DAYS",
        "30",
    )

    try:
        retention_days = int(retention_value)
    except ValueError as error:
        raise ValueError(
            "DSMS_BACKUP_RETENTION_DAYS must be a whole number."
        ) from error

    if retention_days <= 0:
        return 0

    cutoff_timestamp = (
        datetime.now().timestamp()
        - retention_days * 24 * 60 * 60
    )

    deleted_count = 0

    for backup_path in backup_dir.glob("dsms-backup-*.db"):
        if backup_path.stat().st_mtime < cutoff_timestamp:
            backup_path.unlink()
            deleted_count += 1

    return deleted_count

def main() -> None:
    backup_path = create_backup()
    deleted_count = cleanup_old_backups(backup_path.parent)

    print(f"Backup created successfully: {backup_path}")
    print(f"Old backups removed: {deleted_count}")


if __name__ == "__main__":
    main()