"""Safe, explicit SQLite upgrades for the Flask application."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
import sqlite3

WORKFLOW_MIGRATION = "001_workflow_foundation"
BARCODE_MIGRATION = "002_remove_stored_barcode_path"
INVOICED_ITEM_MIGRATION = "003_add_invoiced_item_status"
MIGRATION_NAMES = (
    WORKFLOW_MIGRATION,
    BARCODE_MIGRATION,
    INVOICED_ITEM_MIGRATION,
)

TRANSACTION_COLUMNS = (
    "source_shipping_address_id INTEGER",
    "ship_to_label TEXT",
    "ship_to_manager TEXT",
    "ship_to_store_number TEXT",
    "ship_to_address_snapshot TEXT",
    "ship_to_city TEXT",
    "ship_to_state TEXT",
    "ship_to_country TEXT",
    "ship_to_phone TEXT",
    "source_contact_id INTEGER",
    "contact_email_snapshot TEXT",
    "contact_cell_snapshot TEXT",
)

STONE_COLUMNS_WITHOUT_BARCODE = (
    "id",
    "stock_number",
    "status",
    "hold_client_id",
    "shade",
    "milky",
    "eye_clean",
    "bgm",
    "black",
    "open_inclusion",
    "pair_number",
    "pair_stock_number",
    "pair_separable",
    "picture_link",
    "video_link",
    "current_country",
    "current_state",
    "current_city",
)


def _column_names(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")}


def _create_migration_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY,
            applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _add_missing_transaction_columns(conn: sqlite3.Connection) -> None:
    existing = _column_names(conn, "transactions")
    for definition in TRANSACTION_COLUMNS:
        name = definition.split(maxsplit=1)[0]
        if name not in existing:
            conn.execute(f"ALTER TABLE transactions ADD COLUMN {definition}")


def _create_workflow_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('ADMIN', 'MANAGER', 'SALES', 'ACCOUNTING', 'INVENTORY')),
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transaction_number_counters (
            transaction_type TEXT NOT NULL CHECK(transaction_type IN ('memo', 'invoice', 'credit_invoice')),
            transaction_date DATE NOT NULL,
            last_value INTEGER NOT NULL CHECK(last_value >= 0),
            PRIMARY KEY (transaction_type, transaction_date)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS receiving_events (
            id INTEGER PRIMARY KEY,
            stone_id INTEGER NOT NULL,
            stock_number_snapshot TEXT NOT NULL,
            source_transaction_id INTEGER,
            source_transaction_item_id INTEGER,
            received_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            received_by_user_id INTEGER NOT NULL,
            note TEXT,
            FOREIGN KEY (stone_id) REFERENCES stones(id),
            FOREIGN KEY (source_transaction_id) REFERENCES transactions(id),
            FOREIGN KEY (source_transaction_item_id) REFERENCES transaction_items(id),
            FOREIGN KEY (received_by_user_id) REFERENCES users(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_receiving_events_stone ON receiving_events(stone_id)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_receiving_events_received_at ON receiving_events(received_at)"
    )


def _apply_workflow_migration(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        _add_missing_transaction_columns(conn)
        _create_workflow_tables(conn)
        conn.execute("INSERT INTO schema_migrations (name) VALUES (?)", (WORKFLOW_MIGRATION,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _rebuild_stones_without_barcode_path(conn: sqlite3.Connection) -> None:
    """Remove the legacy NOT NULL/UNIQUE barcode column without losing Stones."""
    columns = ", ".join(STONE_COLUMNS_WITHOUT_BARCODE)
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            CREATE TABLE stones_without_barcode_path (
                id INTEGER PRIMARY KEY,
                stock_number TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                hold_client_id INTEGER,
                shade TEXT,
                milky TEXT,
                eye_clean TEXT,
                bgm TEXT,
                black TEXT,
                open_inclusion TEXT,
                pair_number TEXT,
                pair_stock_number TEXT,
                pair_separable INTEGER CHECK(pair_separable IN (0,1)),
                picture_link TEXT,
                video_link TEXT,
                current_country TEXT,
                current_state TEXT,
                current_city TEXT
            )
            """
        )
        conn.execute(
            f"INSERT INTO stones_without_barcode_path ({columns}) SELECT {columns} FROM stones"
        )
        conn.execute("DROP TABLE stones")
        conn.execute("ALTER TABLE stones_without_barcode_path RENAME TO stones")
        conn.execute("INSERT INTO schema_migrations (name) VALUES (?)", (BARCODE_MIGRATION,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")

    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError("Foreign-key validation failed after barcode-path migration")


def _apply_barcode_migration(conn: sqlite3.Connection) -> None:
    if "barcode_path" not in _column_names(conn, "stones"):
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("INSERT INTO schema_migrations (name) VALUES (?)", (BARCODE_MIGRATION,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return
    _rebuild_stones_without_barcode_path(conn)


def _transaction_items_support_invoiced(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'transaction_items'"
    ).fetchone()
    return bool(row and "'invoiced'" in (row[0] or "").lower())


def _apply_invoiced_item_migration(conn: sqlite3.Connection) -> None:
    transaction_items_exists = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'transaction_items'
        """
    ).fetchone()
    if transaction_items_exists is None or _transaction_items_support_invoiced(conn):
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT INTO schema_migrations (name) VALUES (?)",
                (INVOICED_ITEM_MIGRATION,),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return

    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            CREATE TABLE transaction_items_with_invoiced (
                id INTEGER PRIMARY KEY,
                transaction_id INTEGER NOT NULL,
                stone_id INTEGER NOT NULL,
                grading_report_id INTEGER NOT NULL,
                created_from_item_id INTEGER,
                status TEXT NOT NULL CHECK (
                    status IN (
                        'draft', 'active', 'return', 'returned',
                        'credited', 'invoiced'
                    )
                ),
                stock_number TEXT NOT NULL,
                report_number TEXT,
                lab TEXT,
                shape TEXT NOT NULL,
                weight REAL NOT NULL,
                color TEXT NOT NULL,
                clarity TEXT NOT NULL,
                cut TEXT,
                polish TEXT,
                symmetry TEXT,
                fluorescence_intensity TEXT,
                price_per_carat REAL NOT NULL,
                total_price REAL NOT NULL,
                FOREIGN KEY (transaction_id) REFERENCES transactions(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (stone_id) REFERENCES stones(id),
                FOREIGN KEY (grading_report_id) REFERENCES grading_reports(id),
                FOREIGN KEY (created_from_item_id)
                    REFERENCES transaction_items_with_invoiced(id),
                UNIQUE (transaction_id, stone_id)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO transaction_items_with_invoiced (
                id, transaction_id, stone_id, grading_report_id,
                created_from_item_id, status, stock_number, report_number, lab,
                shape, weight, color, clarity, cut, polish, symmetry,
                fluorescence_intensity, price_per_carat, total_price
            )
            SELECT
                id, transaction_id, stone_id, grading_report_id,
                created_from_item_id, status, stock_number, report_number, lab,
                shape, weight, color, clarity, cut, polish, symmetry,
                fluorescence_intensity, price_per_carat, total_price
            FROM transaction_items
            """
        )
        conn.execute("DROP TABLE transaction_items")
        conn.execute(
            "ALTER TABLE transaction_items_with_invoiced RENAME TO transaction_items"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_transaction_items_tx "
            "ON transaction_items(transaction_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_transaction_items_stone "
            "ON transaction_items(stone_id)"
        )
        conn.execute(
            "INSERT INTO schema_migrations (name) VALUES (?)",
            (INVOICED_ITEM_MIGRATION,),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")

    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(
            "Foreign-key validation failed after invoiced-item migration"
        )


def _pending_migrations(conn: sqlite3.Connection) -> list[str]:
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    if not has_table:
        return list(MIGRATION_NAMES)
    applied = {row[0] for row in conn.execute("SELECT name FROM schema_migrations")}
    return [name for name in MIGRATION_NAMES if name not in applied]


def _apply_pending_migrations(conn: sqlite3.Connection) -> None:
    _create_migration_table(conn)
    for migration in _pending_migrations(conn):
        if migration == WORKFLOW_MIGRATION:
            _apply_workflow_migration(conn)
        elif migration == BARCODE_MIGRATION:
            _apply_barcode_migration(conn)
        elif migration == INVOICED_ITEM_MIGRATION:
            _apply_invoiced_item_migration(conn)


def upgrade_database(db_path: str, backup: bool = True) -> str | None:
    """Apply pending schema upgrades and return the pre-upgrade backup path."""
    target = Path(db_path).resolve()
    if not target.exists():
        raise FileNotFoundError(f"Database does not exist: {target}")

    conn = sqlite3.connect(target, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        pending = _pending_migrations(conn)
    finally:
        conn.close()

    backup_path = None
    if pending and backup:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = target.with_name(f"{target.stem}.pre-{pending[0]}-{stamp}{target.suffix}")
        shutil.copy2(target, backup_path)

    conn = sqlite3.connect(target, timeout=30)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        _apply_pending_migrations(conn)
    finally:
        conn.close()
    return str(backup_path) if backup_path else None
