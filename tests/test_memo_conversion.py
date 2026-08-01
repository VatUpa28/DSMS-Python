"""Memo-to-Invoice conversion coverage using disposable SQLite databases."""

from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import unittest

from werkzeug.security import generate_password_hash


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from database.db import get_db
from database.init_db import init_db
from database.migrations import INVOICED_ITEM_MIGRATION, upgrade_database
from services.transaction_workflows import (
    WorkflowError,
    convert_memo_items_to_invoice,
    create_active_memo,
    create_memo_draft,
    return_memo_transaction_items,
)


PASSWORD_HASH = generate_password_hash("ConversionTestPassword!123")


class MemoConversionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "memo-conversion.db")
        self.saved_environment = {
            name: os.environ.get(name)
            for name in ("DSMS_DB_PATH", "DSMS_TESTING", "DSMS_SECRET_KEY", "DSMS_ENV")
        }
        os.environ.update(
            {
                "DSMS_DB_PATH": self.db_path,
                "DSMS_TESTING": "1",
                "DSMS_SECRET_KEY": "memo-conversion-test-secret",
                "DSMS_ENV": "development",
            }
        )
        init_db(self.db_path)
        self.users = {
            role: self._insert_user(f"{role.lower()}@example.test", role)
            for role in ("ADMIN", "MANAGER", "SALES", "ACCOUNTING")
        }
        self.client_id = self._insert_client()

        from app import app

        app.config.update(
            TESTING=True,
            SECRET_KEY="memo-conversion-test-secret",
            SESSION_COOKIE_SECURE=False,
        )
        self.app = app

    def tearDown(self):
        for name, value in self.saved_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.tempdir.cleanup()

    def _connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _insert_user(self, email, role):
        with closing(self._connect()) as connection:
            user_id = connection.execute(
                """
                INSERT INTO users
                    (email, password_hash, first_name, last_name, role, active)
                VALUES (?, ?, 'Conversion', 'User', ?, 1)
                """,
                (email, PASSWORD_HASH, role),
            ).lastrowid
            connection.commit()
            return user_id

    def _insert_client(self):
        with closing(self._connect()) as connection:
            client_id = connection.execute(
                """
                INSERT INTO clients (code, name, address, tax_id, sales_tax_id)
                VALUES ('CONVERT-CLIENT', 'Conversion Client', '1 Billing Way',
                        'CONVERT-TAX', 'CONVERT-SALES')
                """
            ).lastrowid
            connection.commit()
            return client_id

    def _stone(self, stock_number):
        with closing(self._connect()) as connection:
            stone_id = connection.execute(
                "INSERT INTO stones (stock_number, status) VALUES (?, 'Y')",
                (stock_number,),
            ).lastrowid
            connection.execute(
                """
                INSERT INTO grading_reports
                    (stone_id, report_number, lab, shape, weight, color, clarity,
                     cut, polish, symmetry, fluorescence_intensity, size,
                     measurements, price_per_carat, total_price, active)
                VALUES (?, ?, 'GIA', 'ROUND', 1.25, 'D', 'VS1',
                        'EX', 'EX', 'EX', 'NONE', '1.25 CT', '6x6x4',
                        2000.25, 2500.31, 1)
                """,
                (stone_id, f"REPORT-{stock_number}"),
            )
            connection.commit()
            return stone_id

    def _memo_payload(self, stone_ids):
        return {
            "client_id": self.client_id,
            "date": "2026-07-20",
            "terms": "NET 30",
            "carrier": "FEDEX",
            "shipment_type": "DELIVERY",
            "ship_charge": "12.34",
            "purchase_order_number": "PO-MEMO",
            "person": "Historical Contact",
            "phone": "555-1000",
            "fax": "555-1001",
            "ship_to_label": "Historic Store",
            "ship_to_manager": "Historic Manager",
            "ship_to_store_number": "S-1",
            "ship_to_address": "10 Historic Lane",
            "ship_to_city": "New York",
            "ship_to_state": "NY",
            "ship_to_country": "US",
            "ship_to_phone": "555-1002",
            "stone_ids": stone_ids,
        }

    def _conversion_payload(self, **overrides):
        payload = {
            "date": "2026-07-29",
            "terms": "NET 15",
            "carrier": "UPS",
            "shipment_type": "PICKUP",
            "ship_charge": "8.75",
            "purchase_order_number": "PO-INVOICE",
            "person": "Invoice Contact",
            "phone": "555-2000",
            "fax": "555-2001",
            "ship_to_label": "Invoice Store",
            "ship_to_manager": "Invoice Manager",
            "ship_to_store_number": "S-2",
            "ship_to_address": "20 Invoice Avenue",
            "ship_to_city": "Boston",
            "ship_to_state": "MA",
            "ship_to_country": "US",
            "ship_to_phone": "555-2002",
        }
        payload.update(overrides)
        return payload

    def _active_memo(self, count=3, prefix="CONVERT"):
        stone_ids = [self._stone(f"{prefix}-{index}") for index in range(count)]
        connection = get_db()
        try:
            result = create_active_memo(connection, self._memo_payload(stone_ids))
        finally:
            connection.close()
        with closing(self._connect()) as connection:
            item_ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM transaction_items WHERE transaction_id = ? ORDER BY id",
                    (result["id"],),
                )
            ]
        return result["id"], stone_ids, item_ids

    def _client_for(self, role=None):
        client = self.app.test_client()
        if role:
            with client.session_transaction() as session:
                session["user_id"] = self.users[role]
        return client

    def _csrf(self, client, path):
        response = client.get(path)
        self.assertEqual(response.status_code, 200)
        match = re.search(rb'name="csrf-token" content="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def _post_conversion(self, role, memo_id, item_ids, payload=None):
        client = self._client_for(role)
        path = f"/transactions/{memo_id}/convert-to-invoice"
        token = self._csrf(client, path)
        body = self._conversion_payload()
        if payload:
            body.update(payload)
        body["transaction_item_ids"] = item_ids
        return client.post(
            f"/api/transactions/memos/{memo_id}/convert-to-invoice",
            json=body,
            headers={"X-CSRFToken": token},
        )

    def test_access_roles_page_action_and_method_security(self):
        memo_id, _, item_ids = self._active_memo()
        path = f"/transactions/{memo_id}/convert-to-invoice"
        response = self._client_for().get(path)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

        for role in ("ADMIN", "MANAGER", "ACCOUNTING"):
            response = self._client_for(role).get(path)
            self.assertEqual(response.status_code, 200, role)
            self.assertIn(b"Create Invoice from Memo", response.data)
        self.assertEqual(self._client_for("SALES").get(path).status_code, 403)

        accounting = self._client_for("ACCOUNTING")
        detail = accounting.get(f"/transactions/{memo_id}")
        self.assertIn(b"Create Invoice from Memo", detail.data)
        sales_detail = self._client_for("SALES").get(f"/transactions/{memo_id}")
        self.assertNotIn(b"Create Invoice from Memo", sales_detail.data)

        sales = self._client_for("SALES")
        token = self._csrf(sales, f"/transactions/{memo_id}")
        response = sales.post(
            f"/api/transactions/memos/{memo_id}/convert-to-invoice",
            json={"transaction_item_ids": item_ids, **self._conversion_payload()},
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.is_json)
        self.assertEqual(
            accounting.get(
                f"/api/transactions/memos/{memo_id}/convert-to-invoice"
            ).status_code,
            405,
        )

    def test_draft_cancelled_completed_and_wrong_type_cannot_convert(self):
        draft_stone = self._stone("DRAFT-1")
        connection = get_db()
        try:
            draft = create_memo_draft(connection, self._memo_payload([draft_stone]))
        finally:
            connection.close()
        self.assertEqual(
            self._client_for("ADMIN").get(
                f"/transactions/{draft['id']}/convert-to-invoice"
            ).status_code,
            409,
        )

        cancelled_id, _, cancelled_items = self._active_memo(1, "CANCELLED")
        connection = get_db()
        try:
            return_memo_transaction_items(connection, cancelled_id, cancelled_items)
        finally:
            connection.close()
        completed_id, _, completed_items = self._active_memo(1, "COMPLETED")
        response = self._post_conversion(
            "ADMIN", completed_id, completed_items, {"date": "2026-07-27"}
        )
        self.assertEqual(response.status_code, 201)
        for transaction_id in (cancelled_id, completed_id, response.get_json()["id"]):
            self.assertEqual(
                self._client_for("ADMIN").get(
                    f"/transactions/{transaction_id}/convert-to-invoice"
                ).status_code,
                409,
            )

    def test_single_conversion_copies_historical_snapshots_and_ignores_crafted_values(self):
        memo_id, stone_ids, item_ids = self._active_memo(1)
        with closing(self._connect()) as connection:
            memo_item_before = dict(
                connection.execute(
                    "SELECT * FROM transaction_items WHERE id = ?", (item_ids[0],)
                ).fetchone()
            )
            connection.execute(
                """
                UPDATE grading_reports
                SET report_number = 'CURRENT-CHANGED', price_per_carat = 9999,
                    total_price = 9999
                WHERE stone_id = ?
                """,
                (stone_ids[0],),
            )
            connection.commit()

        response = self._post_conversion(
            "ADMIN",
            memo_id,
            item_ids,
            {
                "client_id": 99999,
                "transaction_number": "USER-NUMBER",
                "parent_transaction_id": 99999,
                "stone_ids": [99999],
                "price_per_carat": 1,
                "total_price": 1,
                "status": "cancelled",
            },
        )
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        result = response.get_json()
        self.assertRegex(result["transaction_number"], r"^INV-20260729-\d{4}$")

        with closing(self._connect()) as connection:
            invoice = connection.execute(
                "SELECT * FROM transactions WHERE id = ?", (result["id"],)
            ).fetchone()
            invoice_item = connection.execute(
                "SELECT * FROM transaction_items WHERE transaction_id = ?",
                (result["id"],),
            ).fetchone()
            memo = connection.execute(
                "SELECT * FROM transactions WHERE id = ?", (memo_id,)
            ).fetchone()
            source_item = connection.execute(
                "SELECT * FROM transaction_items WHERE id = ?", (item_ids[0],)
            ).fetchone()
            stone = connection.execute(
                "SELECT status, hold_client_id FROM stones WHERE id = ?",
                (stone_ids[0],),
            ).fetchone()

        self.assertEqual(invoice["client_id"], self.client_id)
        self.assertEqual(invoice["parent_transaction_id"], memo_id)
        self.assertEqual(invoice["person"], "Invoice Contact")
        self.assertEqual(invoice["ship_to_address_snapshot"], "20 Invoice Avenue")
        self.assertEqual(memo["person"], "Historical Contact")
        self.assertEqual(memo["ship_to_address_snapshot"], "10 Historic Lane")
        self.assertEqual(source_item["status"], "invoiced")
        self.assertEqual(stone["status"], "S")
        self.assertIsNone(stone["hold_client_id"])
        self.assertEqual(invoice_item["created_from_item_id"], item_ids[0])
        for field in (
            "stock_number",
            "report_number",
            "lab",
            "shape",
            "weight",
            "color",
            "clarity",
            "cut",
            "polish",
            "symmetry",
            "fluorescence_intensity",
            "price_per_carat",
            "total_price",
        ):
            self.assertEqual(invoice_item[field], memo_item_before[field], field)
        self.assertNotEqual(invoice_item["report_number"], "CURRENT-CHANGED")

    def test_partial_repeated_and_complete_conversion_relationships(self):
        memo_id, stone_ids, item_ids = self._active_memo(3, "PARTIAL")
        first = self._post_conversion(
            "MANAGER", memo_id, [item_ids[0]], {"date": "2026-07-28"}
        )
        second = self._post_conversion(
            "ACCOUNTING", memo_id, [item_ids[1]], {"date": "2026-07-28"}
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(
            first.get_json()["transaction_number"],
            second.get_json()["transaction_number"],
        )
        with closing(self._connect()) as connection:
            memo = connection.execute(
                "SELECT status FROM transactions WHERE id = ?", (memo_id,)
            ).fetchone()
            states = connection.execute(
                """
                SELECT ti.status AS item_status, s.status AS stone_status
                FROM transaction_items ti JOIN stones s ON s.id = ti.stone_id
                WHERE ti.transaction_id = ? ORDER BY ti.id
                """,
                (memo_id,),
            ).fetchall()
        self.assertEqual(memo["status"], "active")
        self.assertEqual(
            [(row["item_status"], row["stone_status"]) for row in states],
            [("invoiced", "S"), ("invoiced", "S"), ("active", "M")],
        )

        third = self._post_conversion("ADMIN", memo_id, [item_ids[2]])
        self.assertEqual(third.status_code, 201)
        with closing(self._connect()) as connection:
            memo = connection.execute(
                "SELECT status FROM transactions WHERE id = ?", (memo_id,)
            ).fetchone()
            children = connection.execute(
                """
                SELECT transaction_number, parent_transaction_id
                FROM transactions WHERE parent_transaction_id = ? ORDER BY id
                """,
                (memo_id,),
            ).fetchall()
        self.assertEqual(memo["status"], "completed")
        self.assertNotEqual(memo["status"], "cancelled")
        self.assertEqual(len(children), 3)
        self.assertTrue(all(row["parent_transaction_id"] == memo_id for row in children))
        detail = self._client_for("ADMIN").get(f"/transactions/{memo_id}")
        self.assertEqual(detail.status_code, 200)
        for child in children:
            self.assertIn(child["transaction_number"].encode(), detail.data)
        invoice_detail = self._client_for("ADMIN").get(
            f"/transactions/{third.get_json()['id']}"
        )
        self.assertIn(b"Source Memo", invoice_detail.data)

    def test_returned_and_invoiced_items_rejected_but_remaining_item_converts(self):
        memo_id, _, item_ids = self._active_memo(3, "MIXED")
        first = self._post_conversion("ADMIN", memo_id, [item_ids[0]])
        second = self._post_conversion("ADMIN", memo_id, [item_ids[1]])
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        connection = get_db()
        try:
            return_memo_transaction_items(connection, memo_id, [item_ids[2]])
        finally:
            connection.close()
        with closing(self._connect()) as connection:
            status = connection.execute(
                "SELECT status FROM transactions WHERE id = ?", (memo_id,)
            ).fetchone()["status"]
            item_states = [
                row["status"]
                for row in connection.execute(
                    "SELECT status FROM transaction_items WHERE transaction_id = ? ORDER BY id",
                    (memo_id,),
                )
            ]
        self.assertEqual(status, "completed")
        self.assertEqual(item_states, ["invoiced", "invoiced", "returned"])
        client = self._client_for("ADMIN")
        token = self._csrf(client, "/transactions")
        for forbidden in item_ids:
            response = client.post(
                f"/api/transactions/memos/{memo_id}/convert-to-invoice",
                json={
                    "transaction_item_ids": [forbidden],
                    **self._conversion_payload(),
                },
                headers={"X-CSRFToken": token},
            )
            self.assertEqual(response.status_code, 409)

    def test_atomic_validation_failures_leave_no_invoice_or_counter_consumption(self):
        memo_id, stone_ids, item_ids = self._active_memo(2, "ROLLBACK")
        other_id, _, other_items = self._active_memo(1, "OTHER")
        self.assertNotEqual(memo_id, other_id)
        with closing(self._connect()) as connection:
            before_invoices = connection.execute(
                "SELECT COUNT(*) FROM transactions WHERE type = 'invoice'"
            ).fetchone()[0]

        cases = (
            ([item_ids[0], item_ids[0]], {}, 400),
            ([item_ids[0], other_items[0]], {}, 400),
            ([], {}, 400),
            ([item_ids[0]], {"date": "not-a-date"}, 400),
            ([item_ids[0]], {"ship_charge": "-0.01"}, 400),
        )
        for selected, payload, expected in cases:
            response = self._post_conversion("ADMIN", memo_id, selected, payload)
            self.assertEqual(response.status_code, expected, response.get_data(as_text=True))

        with closing(self._connect()) as connection:
            connection.execute(
                "UPDATE stones SET status = 'RETURN_PENDING' WHERE id = ?",
                (stone_ids[0],),
            )
            connection.commit()
        response = self._post_conversion("ADMIN", memo_id, [item_ids[0]])
        self.assertEqual(response.status_code, 409)

        with closing(self._connect()) as connection:
            after_invoices = connection.execute(
                "SELECT COUNT(*) FROM transactions WHERE type = 'invoice'"
            ).fetchone()[0]
            counter = connection.execute(
                """
                SELECT last_value FROM transaction_number_counters
                WHERE transaction_type = 'invoice' AND transaction_date = '2026-07-29'
                """
            ).fetchone()
            item_states = [
                row["status"]
                for row in connection.execute(
                    "SELECT status FROM transaction_items WHERE transaction_id = ? ORDER BY id",
                    (memo_id,),
                )
            ]
        self.assertEqual(after_invoices, before_invoices)
        self.assertIsNone(counter)
        self.assertEqual(item_states, ["active", "active"])

    def test_missing_csrf_and_missing_or_malformed_selection_are_rejected(self):
        memo_id, _, item_ids = self._active_memo(1, "SECURITY")
        client = self._client_for("ADMIN")
        endpoint = f"/api/transactions/memos/{memo_id}/convert-to-invoice"
        response = client.post(
            endpoint,
            json={"transaction_item_ids": item_ids, **self._conversion_payload()},
        )
        self.assertEqual(response.status_code, 400)
        response = self._post_conversion(
            "ADMIN", memo_id, "not-a-list"
        )
        self.assertEqual(response.status_code, 400)

        client = self._client_for("ADMIN")
        token = self._csrf(client, "/transactions")
        response = client.post(
            "/api/transactions/memos/999999/convert-to-invoice",
            json={"transaction_item_ids": item_ids, **self._conversion_payload()},
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(response.status_code, 404)


class InvoicedItemMigrationTests(unittest.TestCase):
    def test_legacy_item_table_is_upgraded_with_backup_and_relationships_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "legacy-items.db"
            connection = sqlite3.connect(db_path)
            try:
                connection.executescript(
                    """
                    PRAGMA foreign_keys = ON;
                    CREATE TABLE schema_migrations (
                        name TEXT PRIMARY KEY,
                        applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    );
                    INSERT INTO schema_migrations(name) VALUES
                        ('001_workflow_foundation'),
                        ('002_remove_stored_barcode_path');
                    CREATE TABLE transactions (id INTEGER PRIMARY KEY);
                    CREATE TABLE stones (id INTEGER PRIMARY KEY);
                    CREATE TABLE grading_reports (id INTEGER PRIMARY KEY);
                    CREATE TABLE users (id INTEGER PRIMARY KEY);
                    CREATE TABLE transaction_items (
                        id INTEGER PRIMARY KEY,
                        transaction_id INTEGER NOT NULL,
                        stone_id INTEGER NOT NULL,
                        grading_report_id INTEGER NOT NULL,
                        created_from_item_id INTEGER,
                        status TEXT NOT NULL CHECK(status IN (
                            'draft','active','return','returned','credited'
                        )),
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
                        FOREIGN KEY(transaction_id) REFERENCES transactions(id) ON DELETE CASCADE,
                        FOREIGN KEY(stone_id) REFERENCES stones(id),
                        FOREIGN KEY(grading_report_id) REFERENCES grading_reports(id),
                        FOREIGN KEY(created_from_item_id) REFERENCES transaction_items(id),
                        UNIQUE(transaction_id, stone_id)
                    );
                    CREATE INDEX idx_transaction_items_tx
                        ON transaction_items(transaction_id);
                    CREATE TABLE receiving_events (
                        id INTEGER PRIMARY KEY,
                        source_transaction_item_id INTEGER,
                        FOREIGN KEY(source_transaction_item_id)
                            REFERENCES transaction_items(id)
                    );
                    INSERT INTO transactions VALUES (1);
                    INSERT INTO stones VALUES (1);
                    INSERT INTO grading_reports VALUES (1);
                    INSERT INTO users VALUES (1);
                    INSERT INTO transaction_items (
                        id, transaction_id, stone_id, grading_report_id, status,
                        stock_number, shape, weight, color, clarity,
                        price_per_carat, total_price
                    ) VALUES (
                        1, 1, 1, 1, 'active', 'LEGACY-ITEM', 'ROUND',
                        1.0, 'D', 'VS1', 1000, 1000
                    );
                    INSERT INTO receiving_events VALUES (1, 1);
                    """
                )
                connection.commit()
            finally:
                connection.close()

            backup = upgrade_database(str(db_path), backup=True)
            self.assertIsNotNone(backup)
            self.assertTrue(Path(backup).exists())

            connection = sqlite3.connect(db_path)
            try:
                migration = connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE name = ?",
                    (INVOICED_ITEM_MIGRATION,),
                ).fetchone()
                self.assertIsNotNone(migration)
                connection.execute(
                    "UPDATE transaction_items SET status = 'invoiced' WHERE id = 1"
                )
                connection.commit()
                self.assertEqual(
                    connection.execute(
                        "SELECT stock_number, status FROM transaction_items"
                    ).fetchone(),
                    ("LEGACY-ITEM", "invoiced"),
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT source_transaction_item_id FROM receiving_events"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
