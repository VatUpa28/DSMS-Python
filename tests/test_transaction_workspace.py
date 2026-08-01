"""HTTP coverage for the manual Memo and Invoice workspace."""

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

from database.init_db import init_db


PASSWORD_HASH = generate_password_hash("WorkspacePassword!123")


class TransactionWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "workspace.db")
        self.saved_environment = {
            name: os.environ.get(name)
            for name in ("DSMS_DB_PATH", "DSMS_TESTING", "DSMS_SECRET_KEY", "DSMS_ENV")
        }
        os.environ.update(
            {
                "DSMS_DB_PATH": self.db_path,
                "DSMS_TESTING": "1",
                "DSMS_SECRET_KEY": "workspace-test-secret",
                "DSMS_ENV": "development",
            }
        )
        init_db(self.db_path)
        self.users = {
            role: self._insert_user(role.lower() + "@example.test", role)
            for role in ("ADMIN", "MANAGER", "SALES", "ACCOUNTING")
        }
        self.client_a = self._insert_client("CLIENT-A", "Alpha Diamonds")
        self.client_b = self._insert_client("CLIENT-B", "Alpha Diamond Group")
        self.contact_a = self._insert_contact(self.client_a, "Alice Contact")
        self.contact_b = self._insert_contact(self.client_b, "Bob Contact")
        self.address_a = self._insert_address(self.client_a, "10 Original Avenue")
        self.address_b = self._insert_address(self.client_b, "20 Other Avenue")

        self.available = self._insert_stone("AVAILABLE-1", "Y")
        self.held_a = self._insert_stone("HELD-A", "H", self.client_a)
        self.held_b = self._insert_stone("HELD-B", "H", self.client_b)
        self.memo_stone = self._insert_stone("MEMO-1", "M")
        self.sold_stone = self._insert_stone("SOLD-1", "S")
        self.pending_stone = self._insert_stone("PENDING-1", "RETURN_PENDING")

        from app import app

        app.config.update(
            TESTING=True,
            SECRET_KEY="workspace-test-secret",
            SESSION_COOKIE_SECURE=False,
        )
        self.app = app

    def tearDown(self):
        for name, old_value in self.saved_environment.items():
            if old_value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old_value
        self.tempdir.cleanup()

    def _connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _insert_user(self, email, role):
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT INTO users
                    (email, password_hash, first_name, last_name, role, active)
                VALUES (?, ?, 'Test', ?, ?, 1)
                """,
                (email, PASSWORD_HASH, role.title(), role),
            )
            connection.commit()
            return cursor.lastrowid

    def _insert_client(self, code, name):
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT INTO clients (code, name, address, tax_id, sales_tax_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (code, name, f"{code} Billing", f"TAX-{code}", f"SALES-{code}"),
            )
            connection.commit()
            return cursor.lastrowid

    def _insert_contact(self, client_id, name):
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT INTO client_contacts (client_id, name, phone, email, fax, cell)
                VALUES (?, ?, '555-1000', 'contact@example.test', '555-1001', '555-1002')
                """,
                (client_id, name),
            )
            connection.commit()
            return cursor.lastrowid

    def _insert_address(self, client_id, address):
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT INTO shipping_addresses
                    (client_id, label, manager, store_number, address, city, state, country, phone)
                VALUES (?, 'Main', 'Manager', '10', ?, 'New York', 'NY', 'US', '555-2000')
                """,
                (client_id, address),
            )
            connection.commit()
            return cursor.lastrowid

    def _insert_stone(self, stock_number, status, hold_client_id=None):
        with closing(self._connect()) as connection:
            stone_id = connection.execute(
                "INSERT INTO stones (stock_number, status, hold_client_id) VALUES (?, ?, ?)",
                (stock_number, status, hold_client_id),
            ).lastrowid
            connection.execute(
                """
                INSERT INTO grading_reports
                    (stone_id, report_number, lab, shape, weight, color, clarity,
                     cut, polish, symmetry, fluorescence_intensity, size, measurements,
                     price_per_carat, total_price, active)
                VALUES (?, ?, 'GIA', 'ROUND', 1.25, 'D', 'VS1',
                        'EX', 'EX', 'EX', 'NONE', '1.25 CT', '7 x 7 x 4',
                        1000.00, 1250.00, 1)
                """,
                (stone_id, f"REPORT-{stock_number}"),
            )
            connection.commit()
            return stone_id

    def _client_for(self, role):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = self.users[role]
        return client

    def _csrf(self, client):
        response = client.get("/transactions/new")
        self.assertEqual(response.status_code, 200)
        match = re.search(rb'name="csrf-token" content="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def _payload(self, stone_ids, **overrides):
        payload = {
            "client_id": self.client_a,
            "date": "2026-07-29",
            "terms": "NET 30",
            "carrier": "FEDEX",
            "shipment_type": "DELIVERY",
            "ship_charge": "25.50",
            "purchase_order_number": "PO-100",
            "source_contact_id": self.contact_a,
            "person": "Edited Contact",
            "phone": "555-3000",
            "fax": "555-3001",
            "source_shipping_address_id": self.address_a,
            "ship_to_label": "Transaction Copy",
            "ship_to_manager": "Snapshot Manager",
            "ship_to_store_number": "99",
            "ship_to_address": "99 Edited Snapshot Lane",
            "ship_to_city": "Brooklyn",
            "ship_to_state": "NY",
            "ship_to_country": "US",
            "ship_to_phone": "555-4000",
            "stone_ids": stone_ids,
        }
        payload.update(overrides)
        return payload

    def _post_json(self, client, path, payload, csrf=True):
        headers = {}
        if csrf:
            headers["X-CSRFToken"] = self._csrf(client)
        return client.post(path, json=payload, headers=headers)

    def _row(self, sql, parameters=()):
        with closing(self._connect()) as connection:
            return connection.execute(sql, parameters).fetchone()

    def test_workspace_access_and_role_specific_transaction_types(self):
        logged_out = self.app.test_client().get("/transactions/new")
        self.assertEqual(logged_out.status_code, 302)
        self.assertIn("/login", logged_out.headers["Location"])

        expected = {
            "ADMIN": (True, True),
            "MANAGER": (True, True),
            "SALES": (True, False),
            "ACCOUNTING": (False, True),
        }
        for role, (memo, invoice) in expected.items():
            response = self._client_for(role).get("/transactions/new")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b'data-transaction-type="memo"' in response.data, memo)
            self.assertEqual(b'data-transaction-type="invoice"' in response.data, invoice)
            self.assertNotIn(b"INVENTORY", response.data)

    def test_backend_mutations_enforce_memo_and_invoice_role_matrix(self):
        accounting = self._client_for("ACCOUNTING")
        sales = self._client_for("SALES")
        memo = self._post_json(
            accounting, "/api/transactions/memos", self._payload([self.available])
        )
        invoice = self._post_json(
            sales, "/api/transactions/invoices", self._payload([self.available])
        )
        self.assertEqual(memo.status_code, 403)
        self.assertEqual(invoice.status_code, 403)

    def test_client_search_and_context_are_safe_and_client_scoped(self):
        client = self._client_for("MANAGER")
        search = client.get("/api/transaction-workspace/clients?q=Alpha")
        self.assertEqual(search.status_code, 200)
        self.assertEqual(len(search.get_json()["clients"]), 2)
        self.assertNotIn("tax_id", search.get_json()["clients"][0])

        context = client.get(f"/api/transaction-workspace/clients/{self.client_a}")
        body = context.get_json()
        self.assertEqual([item["id"] for item in body["contacts"]], [self.contact_a])
        self.assertEqual(
            [item["id"] for item in body["shipping_addresses"]], [self.address_a]
        )
        self.assertEqual(
            client.get("/api/transaction-workspace/clients/999999").status_code, 404
        )

    def test_eligibility_endpoint_includes_only_available_and_same_client_hold(self):
        client = self._client_for("MANAGER")
        response = client.get(f"/api/clients/{self.client_a}/eligible-stones")
        ids = {stone["id"] for stone in response.get_json()["stones"]}
        self.assertIn(self.available, ids)
        self.assertIn(self.held_a, ids)
        self.assertTrue(
            ids.isdisjoint(
                {self.held_b, self.memo_stone, self.sold_stone, self.pending_stone}
            )
        )

    def test_stone_search_filters_and_stock_number_limit(self):
        client = self._client_for("MANAGER")
        filtered = client.get(
            f"/api/clients/{self.client_a}/eligible-stones"
            "?lab=GIA&shape=ROUND&min_weight=1&max_weight=2&stock_numbers=AVAILABLE-1,HELD-A"
        )
        self.assertEqual(
            {stone["id"] for stone in filtered.get_json()["stones"]},
            {self.available, self.held_a},
        )
        invalid_range = client.get(
            f"/api/clients/{self.client_a}/eligible-stones?min_weight=2&max_weight=1"
        )
        self.assertEqual(invalid_range.status_code, 400)
        too_many = ",".join(f"STONE-{index}" for index in range(26))
        response = client.get(
            f"/api/clients/{self.client_a}/eligible-stones?stock_numbers={too_many}"
        )
        self.assertEqual(response.status_code, 400)

    def test_draft_memo_returns_generated_number_and_preserves_inventory_states(self):
        client = self._client_for("SALES")
        response = self._post_json(
            client,
            "/api/transactions/memos",
            self._payload([self.available, self.held_a]),
        )
        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertRegex(body["transaction_number"], r"^MEMO-20260729-\d{4}$")
        self.assertEqual(
            self._row("SELECT status FROM stones WHERE id = ?", (self.available,))["status"],
            "Y",
        )
        held = self._row(
            "SELECT status, hold_client_id FROM stones WHERE id = ?", (self.held_a,)
        )
        self.assertEqual(held["status"], "H")
        self.assertEqual(held["hold_client_id"], self.client_a)
        self.assertEqual(
            self._row(
                "SELECT COUNT(*) AS count FROM transaction_items WHERE transaction_id = ?",
                (body["id"],),
            )["count"],
            2,
        )

    def test_active_memo_is_atomic_and_clears_same_client_hold(self):
        client = self._client_for("MANAGER")
        failed = self._post_json(
            client,
            "/api/transactions/memos/active",
            self._payload([self.available, self.sold_stone]),
        )
        self.assertEqual(failed.status_code, 409)
        self.assertEqual(
            self._row("SELECT COUNT(*) AS count FROM transactions")["count"], 0
        )
        self.assertEqual(
            self._row("SELECT status FROM stones WHERE id = ?", (self.available,))["status"],
            "Y",
        )

        success = self._post_json(
            client,
            "/api/transactions/memos/active",
            self._payload([self.available, self.held_a]),
        )
        self.assertEqual(success.status_code, 201)
        for stone_id in (self.available, self.held_a):
            stone = self._row(
                "SELECT status, hold_client_id FROM stones WHERE id = ?", (stone_id,)
            )
            self.assertEqual(stone["status"], "M")
            self.assertIsNone(stone["hold_client_id"])

    def test_direct_invoice_is_atomic_and_has_no_parent(self):
        client = self._client_for("ACCOUNTING")
        response = self._post_json(
            client,
            "/api/transactions/invoices",
            self._payload([self.available, self.held_a], transaction_number="CRAFTED"),
        )
        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertRegex(body["transaction_number"], r"^INV-20260729-\d{4}$")
        transaction = self._row(
            "SELECT * FROM transactions WHERE id = ?", (body["id"],)
        )
        self.assertIsNone(transaction["parent_transaction_id"])
        self.assertNotEqual(transaction["transaction_number"], "CRAFTED")
        for stone_id in (self.available, self.held_a):
            stone = self._row(
                "SELECT status, hold_client_id FROM stones WHERE id = ?", (stone_id,)
            )
            self.assertEqual(stone["status"], "S")
            self.assertIsNone(stone["hold_client_id"])

    def test_edited_contact_and_ship_to_snapshots_remain_historical(self):
        client = self._client_for("MANAGER")
        response = self._post_json(
            client, "/api/transactions/memos", self._payload([self.available])
        )
        transaction_id = response.get_json()["id"]
        with closing(self._connect()) as connection:
            connection.execute(
                "UPDATE client_contacts SET name = 'Changed Saved Contact' WHERE id = ?",
                (self.contact_a,),
            )
            connection.execute(
                "UPDATE shipping_addresses SET address = 'Changed Saved Address' WHERE id = ?",
                (self.address_a,),
            )
            connection.commit()
        transaction = self._row(
            "SELECT * FROM transactions WHERE id = ?", (transaction_id,)
        )
        self.assertEqual(transaction["person"], "Edited Contact")
        self.assertEqual(transaction["ship_to_address_snapshot"], "99 Edited Snapshot Lane")
        self.assertEqual(transaction["source_contact_id"], self.contact_a)
        self.assertEqual(transaction["source_shipping_address_id"], self.address_a)

    def test_csrf_and_validation_failures_leave_no_transaction(self):
        client = self._client_for("MANAGER")
        missing_csrf = self._post_json(
            client, "/api/transactions/memos", self._payload([self.available]), csrf=False
        )
        self.assertEqual(missing_csrf.status_code, 400)
        invalid_client = self._post_json(
            client,
            "/api/transactions/memos",
            self._payload([self.available], client_id=999999),
        )
        self.assertEqual(invalid_client.status_code, 400)
        empty = self._post_json(
            client, "/api/transactions/memos", self._payload([])
        )
        duplicate = self._post_json(
            client,
            "/api/transactions/memos",
            self._payload([self.available, self.available]),
        )
        bad_charge = self._post_json(
            client,
            "/api/transactions/memos",
            self._payload([self.available], ship_charge="-1"),
        )
        for response in (empty, duplicate, bad_charge):
            self.assertEqual(response.status_code, 400)
        self.assertEqual(
            self._row("SELECT COUNT(*) AS count FROM transactions")["count"], 0
        )

    def test_inventory_client_searches_related_records_without_duplicates(self):
        client = self._client_for("ADMIN")
        for query in ("CLIENT-A", "Alpha Diamonds", "Billing", "Alice Contact",
                      "555-1000", "contact@example.test", "Manager", "10"):
            response = client.get(
                "/api/transaction-workspace/clients", query_string={"q": query}
            )
            self.assertEqual(response.status_code, 200)
            ids = [row["id"] for row in response.get_json()["clients"]]
            self.assertIn(self.client_a, ids)
            self.assertEqual(len(ids), len(set(ids)))

    def test_inventory_client_context_is_scoped_and_uses_active_memo_snapshots(self):
        client = self._client_for("MANAGER")
        response = client.get(f"/api/inventory/client-context/{self.client_a}")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertIn(self.available, [row["id"] for row in body["available_stones"]])
        self.assertIn(self.held_a, [row["id"] for row in body["held_stones"]])
        self.assertNotIn(self.held_b, [row["id"] for row in body["held_stones"]])

        created = self._post_json(
            client, "/api/transactions/memos/active", self._payload([self.available])
        ).get_json()
        context = client.get(f"/api/inventory/client-context/{self.client_a}").get_json()
        self.assertNotIn(self.available, [row["id"] for row in context["available_stones"]])
        self.assertEqual(context["memo_groups"][0]["memo_id"], created["id"])
        self.assertEqual(context["memo_groups"][0]["items"][0]["stock_number"], "AVAILABLE-1")
        item_id = context["memo_groups"][0]["items"][0]["id"]
        invoice_count = self._row(
            "SELECT COUNT(*) AS count FROM transactions WHERE type='invoice'"
        )["count"]
        conversion = client.get(
            f"/transactions/{created['id']}/convert-to-invoice",
            query_string={"item_ids": item_id},
        )
        self.assertEqual(conversion.status_code, 200)
        self.assertIn(f'value="{item_id}"'.encode(), conversion.data)
        self.assertIn(b"checked", conversion.data)
        self.assertEqual(self._row(
            "SELECT COUNT(*) AS count FROM transactions WHERE type='invoice'"
        )["count"], invoice_count)
        sales = self._client_for("SALES")
        self.assertEqual(sales.get(
            f"/transactions/{created['id']}/convert-to-invoice",
            query_string={"item_ids": item_id},
        ).status_code, 403)

    def test_inventory_prefills_are_validated_non_mutating_and_role_secured(self):
        manager = self._client_for("MANAGER")
        before = self._row("SELECT COUNT(*) AS count FROM transactions")["count"]
        page = manager.get(
            "/transactions/new",
            query_string={"type": "memo", "client_id": self.client_a,
                          "stone_ids": f"{self.available},{self.held_a}"},
        )
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'"client_id":', page.data)
        self.assertEqual(self._row("SELECT COUNT(*) AS count FROM transactions")["count"], before)
        rejected = manager.get(
            "/transactions/new",
            query_string={"type": "memo", "client_id": self.client_a,
                          "stone_ids": self.held_b},
        )
        self.assertEqual(rejected.status_code, 409)
        accounting = self._client_for("ACCOUNTING")
        self.assertEqual(accounting.get(
            "/transactions/new",
            query_string={"type": "memo", "client_id": self.client_a,
                          "stone_ids": self.available},
        ).status_code, 403)


if __name__ == "__main__":
    unittest.main()
