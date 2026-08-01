"""Temporary-database coverage for the Stone HOLD management interface."""

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
from services.transaction_workflows import (
    WorkflowConflict,
    create_active_memo,
    create_direct_invoice,
    eligible_stones_for_client,
    place_hold,
)


PASSWORD_HASH = generate_password_hash("HoldInterfacePassword!123")


class HoldInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "holds.db")
        self.saved_environment = {
            name: os.environ.get(name)
            for name in ("DSMS_DB_PATH", "DSMS_TESTING", "DSMS_SECRET_KEY", "DSMS_ENV")
        }
        os.environ.update(
            {
                "DSMS_DB_PATH": self.db_path,
                "DSMS_TESTING": "1",
                "DSMS_SECRET_KEY": "holds-test-secret",
                "DSMS_ENV": "development",
            }
        )
        init_db(self.db_path)
        self.users = {
            role: self._insert_user(f"{role.lower()}@example.test", role)
            for role in ("ADMIN", "MANAGER", "SALES", "ACCOUNTING")
        }
        self.client_a = self._insert_client("HOLD-A", "Hold Client A")
        self.client_b = self._insert_client("HOLD-B", "Hold Client B")

        from app import app

        app.config.update(
            TESTING=True,
            SECRET_KEY="holds-test-secret",
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
                VALUES (?, ?, 'Hold', 'User', ?, 1)
                """,
                (email, PASSWORD_HASH, role),
            ).lastrowid
            connection.commit()
            return user_id

    def _insert_client(self, code, name):
        with closing(self._connect()) as connection:
            client_id = connection.execute(
                """
                INSERT INTO clients (code, name, address, tax_id, sales_tax_id)
                VALUES (?, ?, '1 Client Way', ?, ?)
                """,
                (code, name, f"{code}-TAX", f"{code}-SALES"),
            ).lastrowid
            connection.commit()
            return client_id

    def _stone(self, stock_number, status="Y", weight=1.25):
        with closing(self._connect()) as connection:
            stone_id = connection.execute(
                "INSERT INTO stones (stock_number, status) VALUES (?, ?)",
                (stock_number, status),
            ).lastrowid
            connection.execute(
                """
                INSERT INTO grading_reports
                    (stone_id, report_number, lab, shape, weight, color, clarity,
                     cut, polish, symmetry, fluorescence_intensity,
                     size, measurements, price_per_carat, total_price, active)
                VALUES (?, ?, 'GIA', 'ROUND', ?, 'D', 'VS1',
                        'EX', 'EX', 'EX', 'NONE', '1.25 CT', '6x6x4',
                        2000, 2500, 1)
                """,
                (stone_id, f"REPORT-{stock_number}", weight),
            )
            connection.commit()
            return stone_id

    def _row(self, stone_id):
        with closing(self._connect()) as connection:
            return connection.execute(
                "SELECT * FROM stones WHERE id = ?", (stone_id,)
            ).fetchone()

    def _browser(self, role=None):
        browser = self.app.test_client()
        if role:
            with browser.session_transaction() as session:
                session["user_id"] = self.users[role]
        return browser

    def _csrf(self, browser, path="/holds"):
        response = browser.get(path)
        self.assertEqual(response.status_code, 200)
        token = re.search(rb'name="csrf-token" content="([^"]+)"', response.data)
        self.assertIsNotNone(token)
        return token.group(1).decode("utf-8")

    def _post(self, browser, path, payload, csrf=True):
        headers = {}
        if csrf:
            headers["X-CSRFToken"] = self._csrf(browser)
        return browser.post(path, json=payload, headers=headers)

    def _transaction_payload(self, client_id, stone_ids):
        return {
            "client_id": client_id,
            "date": "2026-07-31",
            "terms": "NET 30",
            "carrier": "FEDEX",
            "shipment_type": "DELIVERY",
            "ship_charge": "0",
            "person": "Hold Contact",
            "ship_to_address": "10 Hold Lane",
            "stone_ids": stone_ids,
        }

    def test_page_access_role_visibility_and_unauthenticated_behavior(self):
        self.assertEqual(self._browser().get("/holds").status_code, 302)
        for role in ("ADMIN", "MANAGER", "SALES"):
            response = self._browser(role).get("/holds")
            self.assertEqual(response.status_code, 200, role)
            self.assertIn(b"Place Stones on Hold", response.data)
            self.assertIn(b"Release Selected Holds", response.data)
        accounting = self._browser("ACCOUNTING").get("/holds")
        self.assertEqual(accounting.status_code, 200)
        self.assertNotIn(b"Place Stones on Hold", accounting.data)
        self.assertNotIn(b"Release Selected Holds", accounting.data)

    def test_available_search_is_server_filtered_and_validated(self):
        available = self._stone("SEARCH-AVAILABLE", "Y", 1.2)
        for stock, status in (
            ("SEARCH-HOLD", "H"),
            ("SEARCH-MEMO", "M"),
            ("SEARCH-SOLD", "S"),
            ("SEARCH-PENDING", "RETURN_PENDING"),
        ):
            stone_id = self._stone(stock, status, 1.2)
            if status == "H":
                with closing(self._connect()) as connection:
                    connection.execute(
                        "UPDATE stones SET hold_client_id = ? WHERE id = ?",
                        (self.client_a, stone_id),
                    )
                    connection.commit()

        response = self._browser("SALES").get(
            "/api/holds/available-stones?lab=GIA&shape=ROUND&min_weight=1&max_weight=2"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.get_json()["stones"]], [available])
        invalid_range = self._browser("ADMIN").get(
            "/api/holds/available-stones?min_weight=2&max_weight=1"
        )
        self.assertEqual(invalid_range.status_code, 400)
        too_many = ",".join(f"S-{index}" for index in range(26))
        self.assertEqual(
            self._browser("ADMIN").get(
                "/api/holds/available-stones?stock_numbers=" + too_many
            ).status_code,
            400,
        )

    def test_batch_placement_stores_client_and_ignores_crafted_authority(self):
        first = self._stone("PLACE-1")
        second = self._stone("PLACE-2")
        response = self._post(
            self._browser("ADMIN"),
            "/api/holds",
            {
                "client_id": self.client_a,
                "stone_ids": [first, second],
                "status": "SOLD",
                "client_name": "Crafted Client",
                "stock_numbers": ["NOT-AUTHORITATIVE"],
            },
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.get_json()["held"], 2)
        self.assertEqual(response.get_json()["stock_numbers"], ["PLACE-1", "PLACE-2"])
        for stone_id in (first, second):
            stone = self._row(stone_id)
            self.assertEqual(stone["status"], "H")
            self.assertEqual(stone["hold_client_id"], self.client_a)

    def test_placement_validation_and_mixed_batch_are_atomic(self):
        valid = self._stone("ATOMIC-VALID")
        invalid_states = [
            self._stone("ATOMIC-MEMO", "M"),
            self._stone("ATOMIC-SOLD", "S"),
            self._stone("ATOMIC-PENDING", "RETURN_PENDING"),
        ]
        held = self._stone("ATOMIC-HELD")
        connection = get_db()
        try:
            place_hold(connection, self.client_a, [held])
        finally:
            connection.close()

        browser = self._browser("MANAGER")
        for payload in (
            {"client_id": None, "stone_ids": [valid]},
            {"client_id": 999999, "stone_ids": [valid]},
            {"client_id": self.client_a, "stone_ids": []},
            {"client_id": self.client_a, "stone_ids": [valid, valid]},
        ):
            self.assertEqual(self._post(browser, "/api/holds", payload).status_code, 400)
        for invalid in (*invalid_states, held):
            response = self._post(
                browser,
                "/api/holds",
                {"client_id": self.client_a, "stone_ids": [valid, invalid]},
            )
            self.assertEqual(response.status_code, 409)
            self.assertEqual(self._row(valid)["status"], "Y")

    def test_hold_exclusivity_and_same_client_memo_and_invoice(self):
        memo_stone = self._stone("EXCLUSIVE-MEMO")
        invoice_stone = self._stone("EXCLUSIVE-INVOICE")
        connection = get_db()
        try:
            place_hold(connection, self.client_a, [memo_stone, invoice_stone])
            own_ids = {row["id"] for row in eligible_stones_for_client(connection, self.client_a)}
            other_ids = {row["id"] for row in eligible_stones_for_client(connection, self.client_b)}
            self.assertTrue({memo_stone, invoice_stone}.issubset(own_ids))
            self.assertTrue({memo_stone, invoice_stone}.isdisjoint(other_ids))
            with self.assertRaises(WorkflowConflict):
                create_direct_invoice(
                    connection,
                    self._transaction_payload(self.client_b, [invoice_stone]),
                )
            create_active_memo(
                connection, self._transaction_payload(self.client_a, [memo_stone])
            )
            create_direct_invoice(
                connection, self._transaction_payload(self.client_a, [invoice_stone])
            )
        finally:
            connection.close()
        self.assertEqual(self._row(memo_stone)["status"], "M")
        self.assertEqual(self._row(invoice_stone)["status"], "S")
        self.assertIsNone(self._row(memo_stone)["hold_client_id"])
        self.assertIsNone(self._row(invoice_stone)["hold_client_id"])

    def test_release_batch_is_atomic_and_clears_client(self):
        first = self._stone("RELEASE-1")
        second = self._stone("RELEASE-2")
        connection = get_db()
        try:
            place_hold(connection, self.client_a, [first, second])
        finally:
            connection.close()
        response = self._post(
            self._browser("SALES"),
            "/api/holds/release",
            {"stone_ids": [first, second], "status": "SOLD", "client_id": self.client_b},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["released"], 2)
        for stone_id in (first, second):
            stone = self._row(stone_id)
            self.assertEqual(stone["status"], "Y")
            self.assertIsNone(stone["hold_client_id"])

    def test_release_duplicate_nonhold_and_mixed_batch_roll_back(self):
        held = self._stone("RELEASE-HELD")
        other = self._stone("RELEASE-NOT-HELD")
        connection = get_db()
        try:
            place_hold(connection, self.client_a, [held])
        finally:
            connection.close()
        browser = self._browser("ADMIN")
        duplicate = self._post(
            browser, "/api/holds/release", {"stone_ids": [held, held]}
        )
        self.assertEqual(duplicate.status_code, 400)
        mixed = self._post(
            browser, "/api/holds/release", {"stone_ids": [held, other]}
        )
        self.assertEqual(mixed.status_code, 409)
        self.assertEqual(self._row(held)["status"], "H")
        self.assertEqual(self._row(held)["hold_client_id"], self.client_a)

    def test_security_csrf_methods_and_accounting_authorization(self):
        stone = self._stone("SECURITY-HOLD")
        anonymous = self._browser()
        anonymous_token = self._csrf(anonymous, "/login")
        self.assertEqual(
            anonymous.post(
                "/api/holds",
                json={"client_id": self.client_a, "stone_ids": [stone]},
                headers={"X-CSRFToken": anonymous_token},
            ).status_code,
            401,
        )
        accounting = self._browser("ACCOUNTING")
        token = self._csrf(accounting)
        for path, payload in (
            ("/api/holds", {"client_id": self.client_a, "stone_ids": [stone]}),
            ("/api/holds/release", {"stone_ids": [stone]}),
        ):
            response = accounting.post(
                path, json=payload, headers={"X-CSRFToken": token}
            )
            self.assertEqual(response.status_code, 403)
            self.assertTrue(response.is_json)
            self.assertEqual(accounting.get(path).status_code, 405)
        self.assertEqual(self._row(stone)["status"], "Y")

    def test_missing_csrf_is_rejected_and_legacy_form_route_is_removed(self):
        stone = self._stone("CSRF-HOLD")
        browser = self._browser("ADMIN")
        response = self._post(
            browser,
            "/api/holds",
            {"client_id": self.client_a, "stone_ids": [stone]},
            csrf=False,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(browser.post("/hold-stones", data={}).status_code, 404)
        self.assertEqual(self._row(stone)["status"], "Y")

    def test_inventory_client_and_current_hold_views_show_ownership(self):
        stone = self._stone("INTEGRATION-HOLD")
        connection = get_db()
        try:
            place_hold(connection, self.client_a, [stone])
        finally:
            connection.close()
        accounting = self._browser("ACCOUNTING")
        holds_page = accounting.get("/holds?client=HOLD-A")
        self.assertEqual(holds_page.status_code, 200)
        self.assertIn(b"INTEGRATION-HOLD", holds_page.data)
        self.assertIn(b"Hold Client A", holds_page.data)
        inventory = accounting.get("/inventory?status=H")
        self.assertIn(b"HOLD-A", inventory.data)
        self.assertIn(b"Hold Client A", inventory.data)
        clients = accounting.get("/clients")
        self.assertIn(b"Held Stones", clients.data)
        self.assertRegex(clients.data, rb"holds\?client=HOLD-A")


if __name__ == "__main__":
    unittest.main()
