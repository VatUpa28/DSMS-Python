"""HTTP and workflow coverage for returns, credits, and physical receiving."""

from __future__ import annotations

from contextlib import closing
from datetime import date
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
from services.transaction_workflows import create_active_memo, create_direct_invoice


PASSWORD_HASH = generate_password_hash("ReturnTestPassword!123")


class ReturnsAndReceivingTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "returns.db")
        self.saved_environment = {
            name: os.environ.get(name)
            for name in ("DSMS_DB_PATH", "DSMS_TESTING", "DSMS_SECRET_KEY", "DSMS_ENV")
        }
        os.environ.update(
            {
                "DSMS_DB_PATH": self.db_path,
                "DSMS_TESTING": "1",
                "DSMS_SECRET_KEY": "returns-test-secret",
                "DSMS_ENV": "development",
            }
        )
        init_db(self.db_path)
        self.users = {
            role: self._insert_user(role.lower() + "@example.test", role)
            for role in ("ADMIN", "MANAGER", "SALES", "ACCOUNTING")
        }
        self.client_id = self._insert_client()

        from app import app

        app.config.update(
            TESTING=True,
            SECRET_KEY="returns-test-secret",
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
                VALUES (?, ?, 'Return', ?, ?, 1)
                """,
                (email, PASSWORD_HASH, role.title(), role),
            ).lastrowid
            connection.commit()
            return user_id

    def _insert_client(self):
        with closing(self._connect()) as connection:
            client_id = connection.execute(
                """
                INSERT INTO clients (code, name, address, tax_id, sales_tax_id)
                VALUES ('RETURN-CLIENT', 'Return Client', '1 Billing Way',
                        'RETURN-TAX', 'RETURN-SALES')
                """
            ).lastrowid
            connection.commit()
            return client_id

    def _stone(self, stock_number, status="Y"):
        with closing(self._connect()) as connection:
            stone_id = connection.execute(
                "INSERT INTO stones (stock_number, status) VALUES (?, ?)",
                (stock_number, status),
            ).lastrowid
            connection.execute(
                """
                INSERT INTO grading_reports
                    (stone_id, report_number, lab, shape, weight, color, clarity,
                     cut, polish, symmetry, fluorescence_intensity, size,
                     measurements, price_per_carat, total_price, active)
                VALUES (?, ?, 'GIA', 'ROUND', 1.1, 'D', 'VS1',
                        'EX', 'EX', 'EX', 'NONE', '1.10 CT', '6x6x4',
                        1200, 1320, 1)
                """,
                (stone_id, f"REPORT-{stock_number}"),
            )
            connection.commit()
            return stone_id

    def _payload(self, stone_ids):
        return {
            "client_id": self.client_id,
            "date": "2026-07-29",
            "terms": "NET 30",
            "carrier": "FEDEX",
            "shipment_type": "DELIVERY",
            "ship_charge": "10.00",
            "person": "Return Contact",
            "phone": "555-1000",
            "ship_to_address": "10 Snapshot Lane",
            "ship_to_city": "New York",
            "ship_to_state": "NY",
            "ship_to_country": "US",
            "stone_ids": stone_ids,
        }

    def _active_memo(self, count=2, prefix="MEMO-RETURN"):
        stone_ids = [self._stone(f"{prefix}-{index}") for index in range(count)]
        connection = get_db()
        try:
            transaction = create_active_memo(connection, self._payload(stone_ids))
        finally:
            connection.close()
        return transaction["id"], stone_ids, self._item_ids(transaction["id"])

    def _active_invoice(self, count=2, prefix="INVOICE-RETURN"):
        stone_ids = [self._stone(f"{prefix}-{index}") for index in range(count)]
        connection = get_db()
        try:
            transaction = create_direct_invoice(connection, self._payload(stone_ids))
        finally:
            connection.close()
        return transaction["id"], stone_ids, self._item_ids(transaction["id"])

    def _item_ids(self, transaction_id):
        with closing(self._connect()) as connection:
            return [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM transaction_items WHERE transaction_id = ? ORDER BY id",
                    (transaction_id,),
                )
            ]

    def _client_for(self, role):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = self.users[role]
        return client

    def _csrf(self, client, path):
        response = client.get(path)
        self.assertEqual(response.status_code, 200)
        match = re.search(rb'name="csrf-token" content="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def _return(self, client, transaction_id, item_ids, follow=False):
        path = f"/transactions/{transaction_id}/return"
        return client.post(
            path,
            data={
                "csrf_token": self._csrf(client, path),
                "transaction_item_ids": item_ids,
                "return_note": "Inspected return request",
            },
            follow_redirects=follow,
        )

    def _receive(self, client, stone_ids, note="Received in good condition", **extra):
        payload = {"stone_ids": stone_ids, "note": note, **extra}
        token = self._csrf(client, "/receiving")
        return client.post(
            "/receive-stones",
            json=payload,
            headers={"X-CSRFToken": token},
        )

    def _row(self, sql, parameters=()):
        with closing(self._connect()) as connection:
            return connection.execute(sql, parameters).fetchone()

    def test_return_page_access_and_role_matrix(self):
        memo_id, _, _ = self._active_memo()
        invoice_id, _, _ = self._active_invoice()
        self.assertEqual(
            self.app.test_client().get(f"/transactions/{memo_id}/return").status_code,
            302,
        )
        for role in ("ADMIN", "MANAGER", "SALES"):
            client = self._client_for(role)
            self.assertEqual(client.get(f"/transactions/{memo_id}/return").status_code, 200)
            detail = client.get(f"/transactions/{memo_id}")
            self.assertEqual(detail.status_code, 200)
            self.assertIn(b"Return Items", detail.data)
        self.assertEqual(
            self._client_for("ACCOUNTING")
            .get(f"/transactions/{memo_id}/return")
            .status_code,
            403,
        )
        for role in ("ADMIN", "MANAGER", "ACCOUNTING"):
            self.assertEqual(
                self._client_for(role)
                .get(f"/transactions/{invoice_id}/return")
                .status_code,
                200,
            )
        self.assertEqual(
            self._client_for("SALES")
            .get(f"/transactions/{invoice_id}/return")
            .status_code,
            403,
        )

    def test_partial_and_complete_memo_returns_preserve_unselected_items(self):
        memo_id, stones, items = self._active_memo()
        sales = self._client_for("SALES")
        first = self._return(sales, memo_id, [items[0]])
        self.assertEqual(first.status_code, 302)
        returned = self._row(
            "SELECT status FROM transaction_items WHERE id = ?", (items[0],)
        )
        remaining = self._row(
            "SELECT status FROM transaction_items WHERE id = ?", (items[1],)
        )
        self.assertEqual(returned["status"], "returned")
        self.assertEqual(remaining["status"], "active")
        self.assertEqual(
            self._row("SELECT status FROM stones WHERE id = ?", (stones[0],))["status"],
            "RETURN_PENDING",
        )
        self.assertEqual(
            self._row("SELECT status FROM stones WHERE id = ?", (stones[1],))["status"],
            "M",
        )
        self.assertEqual(
            self._row("SELECT status FROM transactions WHERE id = ?", (memo_id,))["status"],
            "active",
        )

        second = self._return(sales, memo_id, [items[1]])
        self.assertEqual(second.status_code, 302)
        self.assertEqual(
            self._row("SELECT status FROM transactions WHERE id = ?", (memo_id,))["status"],
            "cancelled",
        )
        self.assertEqual(
            self._row("SELECT status FROM stones WHERE id = ?", (stones[1],))["status"],
            "RETURN_PENDING",
        )

    def test_duplicate_and_mixed_memo_return_roll_back_atomically(self):
        memo_id, stones, items = self._active_memo()
        other_id, _, other_items = self._active_memo(1, "OTHER-MEMO")
        sales = self._client_for("SALES")
        duplicate = self._return(sales, memo_id, [items[0], items[0]])
        self.assertEqual(duplicate.status_code, 400)
        mixed = self._return(sales, memo_id, [items[0], other_items[0]])
        self.assertEqual(mixed.status_code, 400)
        for item_id, stone_id in zip(items, stones):
            self.assertEqual(
                self._row(
                    "SELECT status FROM transaction_items WHERE id = ?", (item_id,)
                )["status"],
                "active",
            )
            self.assertEqual(
                self._row("SELECT status FROM stones WHERE id = ?", (stone_id,))["status"],
                "M",
            )
        self.assertNotEqual(other_id, memo_id)

    def test_invoice_partial_return_creates_linked_credit_and_snapshots(self):
        invoice_id, stones, items = self._active_invoice()
        accounting = self._client_for("ACCOUNTING")
        response = self._return(accounting, invoice_id, [items[0]])
        self.assertEqual(response.status_code, 302)
        credit = self._row(
            """
            SELECT * FROM transactions
            WHERE type = 'credit_invoice' AND parent_transaction_id = ?
            """,
            (invoice_id,),
        )
        self.assertIsNotNone(credit)
        self.assertRegex(
            credit["transaction_number"],
            rf"^CR-{date.today().strftime('%Y%m%d')}-\d{{4}}$",
        )
        credit_item = self._row(
            "SELECT * FROM transaction_items WHERE transaction_id = ?", (credit["id"],)
        )
        original_item = self._row(
            "SELECT * FROM transaction_items WHERE id = ?", (items[0],)
        )
        self.assertEqual(original_item["status"], "returned")
        self.assertEqual(credit_item["status"], "credited")
        self.assertEqual(credit_item["stock_number"], original_item["stock_number"])
        self.assertEqual(credit_item["price_per_carat"], original_item["price_per_carat"])
        self.assertEqual(credit_item["total_price"], -abs(original_item["total_price"]))
        self.assertEqual(
            self._row("SELECT status FROM stones WHERE id = ?", (stones[0],))["status"],
            "RETURN_PENDING",
        )
        self.assertEqual(
            self._row("SELECT status FROM transaction_items WHERE id = ?", (items[1],))[
                "status"
            ],
            "active",
        )
        detail = accounting.get(f"/transactions/{credit['id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(credit["transaction_number"].encode(), detail.data)
        self.assertIn(b"Parent Invoice", detail.data)

    def test_invoice_duplicate_and_mixed_return_leave_no_partial_credit(self):
        invoice_id, stones, items = self._active_invoice()
        other_id, _, other_items = self._active_invoice(1, "OTHER-INVOICE")
        accounting = self._client_for("ACCOUNTING")
        duplicate = self._return(accounting, invoice_id, [items[0], items[0]])
        mixed = self._return(accounting, invoice_id, [items[0], other_items[0]])
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(mixed.status_code, 400)
        self.assertEqual(
            self._row(
                "SELECT COUNT(*) AS count FROM transactions WHERE type = 'credit_invoice'"
            )["count"],
            0,
        )
        for item_id, stone_id in zip(items, stones):
            self.assertEqual(
                self._row(
                    "SELECT status FROM transaction_items WHERE id = ?", (item_id,)
                )["status"],
                "active",
            )
            self.assertEqual(
                self._row("SELECT status FROM stones WHERE id = ?", (stone_id,))["status"],
                "S",
            )
        self.assertNotEqual(other_id, invoice_id)

    def test_receiving_role_access_queue_scan_and_history(self):
        memo_id, stones, items = self._active_memo(1)
        self._return(self._client_for("SALES"), memo_id, items)
        for role in ("ADMIN", "MANAGER"):
            client = self._client_for(role)
            queue = client.get("/receiving")
            self.assertEqual(queue.status_code, 200)
            self.assertIn(b"MEMO-RETURN-0", queue.data)
            scan = client.get(
                "/api/receiving/stone-by-stock?stock_number=MEMO-RETURN-0"
            )
            self.assertEqual(scan.status_code, 200)
            self.assertEqual(scan.get_json()["id"], stones[0])
            self.assertEqual(client.get("/receiving/history").status_code, 200)
        for role in ("SALES", "ACCOUNTING"):
            self.assertEqual(self._client_for(role).get("/receiving").status_code, 403)
            self.assertEqual(
                self._client_for(role).get("/receiving/history").status_code, 403
            )

    def test_receiving_records_event_and_preserves_grading_and_price(self):
        invoice_id, stones, items = self._active_invoice(1)
        self._return(self._client_for("ACCOUNTING"), invoice_id, items)
        before = self._row(
            "SELECT * FROM grading_reports WHERE stone_id = ?", (stones[0],)
        )
        manager = self._client_for("MANAGER")
        response = self._receive(
            manager,
            stones,
            note="Package inspected & accepted",
            status="AVAILABLE",
            stock_number="CRAFTED-DIFFERENT-STONE",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["stock_numbers"], ["INVOICE-RETURN-0"])
        self.assertEqual(
            self._row("SELECT status FROM stones WHERE id = ?", (stones[0],))["status"],
            "Y",
        )
        event = self._row(
            "SELECT * FROM receiving_events WHERE stone_id = ?", (stones[0],)
        )
        self.assertEqual(event["stock_number_snapshot"], "INVOICE-RETURN-0")
        self.assertEqual(event["source_transaction_id"], invoice_id)
        self.assertEqual(event["received_by_user_id"], self.users["MANAGER"])
        self.assertEqual(event["note"], "Package inspected & accepted")
        after = self._row(
            "SELECT * FROM grading_reports WHERE stone_id = ?", (stones[0],)
        )
        self.assertEqual(after["id"], before["id"])
        self.assertEqual(after["price_per_carat"], before["price_per_carat"])
        self.assertEqual(after["total_price"], before["total_price"])

    def test_duplicate_nonpending_and_mixed_receiving_are_atomic(self):
        memo_id, pending_stones, items = self._active_memo(2)
        self._return(self._client_for("SALES"), memo_id, items)
        available = self._stone("NOT-PENDING")
        manager = self._client_for("MANAGER")
        mixed = self._receive(manager, [pending_stones[0], available])
        self.assertEqual(mixed.status_code, 409)
        self.assertEqual(
            self._row(
                "SELECT COUNT(*) AS count FROM receiving_events"
            )["count"],
            0,
        )
        for stone_id in pending_stones:
            self.assertEqual(
                self._row("SELECT status FROM stones WHERE id = ?", (stone_id,))["status"],
                "RETURN_PENDING",
            )

        first = self._receive(manager, [pending_stones[0]])
        second = self._receive(manager, [pending_stones[0]])
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(
            self._row(
                "SELECT COUNT(*) AS count FROM receiving_events WHERE stone_id = ?",
                (pending_stones[0],),
            )["count"],
            1,
        )

    def test_receiving_history_is_newest_first_and_shows_source(self):
        invoice_id, stones, items = self._active_invoice(2)
        self._return(self._client_for("ACCOUNTING"), invoice_id, items)
        manager = self._client_for("MANAGER")
        self._receive(manager, [stones[0]], note="First")
        self._receive(manager, [stones[1]], note="Second")
        history = manager.get("/receiving/history")
        self.assertEqual(history.status_code, 200)
        self.assertLess(
            history.data.index(b"INVOICE-RETURN-1"),
            history.data.index(b"INVOICE-RETURN-0"),
        )
        invoice_number = self._row(
            "SELECT transaction_number FROM transactions WHERE id = ?", (invoice_id,)
        )["transaction_number"].encode()
        self.assertIn(invoice_number, history.data)

    def test_csrf_get_and_authorization_protect_mutations(self):
        memo_id, stones, items = self._active_memo(1)
        sales = self._client_for("SALES")
        self.assertEqual(sales.get("/receive-stones").status_code, 405)
        missing_return_csrf = sales.post(
            f"/transactions/{memo_id}/return",
            data={"transaction_item_ids": items},
        )
        self.assertEqual(missing_return_csrf.status_code, 400)

        manager = self._client_for("MANAGER")
        missing_receiving_csrf = manager.post(
            "/receive-stones", json={"stone_ids": stones}
        )
        self.assertEqual(missing_receiving_csrf.status_code, 400)
        forbidden = sales.post(
            "/receive-stones",
            json={"stone_ids": stones},
            headers={"X-CSRFToken": self._csrf(sales, f"/transactions/{memo_id}/return")},
        )
        self.assertEqual(forbidden.status_code, 403)


if __name__ == "__main__":
    unittest.main()
