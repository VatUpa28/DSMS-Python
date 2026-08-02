import os
import socket
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from config import DB_PATH


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "backups"
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"

VALID_ENVIRONMENTS = {"development", "pilot", "production"}


def check_environment(errors: list[str]) -> None:
    environment = os.environ.get("DSMS_ENV", "development").strip().lower()

    if environment not in VALID_ENVIRONMENTS:
        errors.append(
            "DSMS_ENV must be development, pilot, or production."
        )

    secret_key = os.environ.get("DSMS_SECRET_KEY", "")

    if not secret_key:
        errors.append("DSMS_SECRET_KEY is not configured.")
    elif len(secret_key) < 32:
        errors.append("DSMS_SECRET_KEY must be at least 32 characters.")

    unsafe_keys = {
        "dsms-local-development-secret-change-before-production",
        "temporary-local-pilot-test-key-at-least-32-characters",
    }

    if environment != "development" and secret_key in unsafe_keys:
        errors.append(
            "The temporary development secret cannot be used in pilot or production."
        )


def check_database(errors: list[str]) -> None:
    database_path = Path(DB_PATH).resolve()

    if not database_path.exists():
        errors.append(f"Database does not exist: {database_path}")
        return

    try:
        with closing(sqlite3.connect(database_path)) as connection:
            integrity = connection.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0]

            foreign_key_errors = connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()

        if integrity != "ok":
            errors.append(
                f"Database integrity check failed: {integrity}"
            )

        if foreign_key_errors:
            errors.append(
                f"Database has foreign-key errors: {foreign_key_errors}"
            )

    except sqlite3.Error as error:
        errors.append(f"Database check failed: {error}")


def check_writable_directory(
    path: Path,
    label: str,
    errors: list[str],
) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)

        test_file = path / ".dsms-write-test"
        test_file.write_text("test", encoding="utf-8")
        test_file.unlink()

    except OSError as error:
        errors.append(f"{label} is not writable: {path}. {error}")


def check_port(errors: list[str]) -> None:
    host = os.environ.get("DSMS_HOST", "127.0.0.1")

    try:
        port = int(os.environ.get("DSMS_PORT", "8000"))
    except ValueError:
        errors.append("DSMS_PORT must be a whole number.")
        return

    if not 1 <= port <= 65535:
        errors.append("DSMS_PORT must be between 1 and 65535.")
        return

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
            server_socket.bind((host, port))
    except OSError as error:
        errors.append(
            f"Cannot use {host}:{port}. The address may already be in use. {error}"
        )


def main() -> int:
    errors: list[str] = []

    backup_directory = Path(
        os.environ.get(
            "DSMS_BACKUP_DIR",
            str(DEFAULT_BACKUP_DIR),
        )
    ).resolve()

    log_directory = Path(
        os.environ.get(
            "DSMS_LOG_DIR",
            str(DEFAULT_LOG_DIR),
        )
    ).resolve()

    check_environment(errors)
    check_database(errors)
    check_writable_directory(
        backup_directory,
        "Backup directory",
        errors,
    )
    check_writable_directory(
        log_directory,
        "Log directory",
        errors,
    )
    check_port(errors)

    if errors:
        print("DSMS preflight check failed:")

        for error in errors:
            print(f"  - {error}")

        return 1

    print("DSMS preflight check passed.")
    print(f"Database: {Path(DB_PATH).resolve()}")
    print(f"Backup directory: {backup_directory}")
    print(f"Log directory: {log_directory}")
    print(
        "Server address: "
        f"{os.environ.get('DSMS_HOST', '127.0.0.1')}:"
        f"{os.environ.get('DSMS_PORT', '8000')}"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())