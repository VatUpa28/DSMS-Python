"""Temporary-SQLite coverage for Flask V1 workflow foundations.

The tests use only unittest assertions, so they can also run with
`python -m unittest discover -s tests -v` when pytest is not yet installed.
"""

from __future__ import annotations

from datetime import date
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from database.init_db import init_db
from database.db import get_db
from services.transaction_workflows import (
    RETURN_PENDING,
    WorkflowConflict,
    WorkflowError,
    atomic_workflow,
    create_direct_invoice,
    create_memo_draft,
    credit_invoice,
    eligible_stones_for_client,
    generate_transaction_number,
    place_hold,
    receive_stones,
    release_hold,
    return_memo_stones,
    activate_memo,
)


class TransactionWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "workflow-test.db")
        self.previous_db_path = os.environ.get("DSMS_DB_PATH")
        self.previous_testing = os.environ.get("DSMS_TESTING")
        os.environ["DSMS_DB_PATH"] = self.db_path
        os.environ["DSMS_TESTING"] = "1"
        init_db(self.db_path)
        self.user_id = self._insert_user("manager@example.test", "MANAGER")
        self.sales_user_id = self._insert_user("sales@example.test", "SALES")

    def tearDown(self):
        if self.previous_db_path is None:
            os.environ.pop("DSMS_DB_PATH", None)
        else:
            os.environ["DSMS_DB_PATH"] = self.previous_db_path
        if self.previous_testing is None:
            os.environ.pop("DSMS_TESTING", None)
        else:
            os.environ["DSMS_TESTING"] = self.previous_testing
        self.tempdir.cleanup()

    def _connection(self):
        return get_db()

    def _insert_user(self, email, role):
        conn = self._connection()
        try:
            cursor = conn.execute(
                """
                INSERT INTO users (email, password_hash, first_name, last_name, role)
                VALUES (?, 'not-used-by-workflow-tests', 'Test', 'User', ?)
                """,
                (email, role),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def _client(self, code):
        conn = self._connection()
        try:
            cursor = conn.execute(
                """
                INSERT INTO clients (code, name, address, tax_id, sales_tax_id)
                VALUES (?, ?, '1 Billing Way', ?, ?)
                """,
                (code, f"{code} Client", f"TAX-{code}", f"SALES-{code}"),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def _shipping_address(self, client_id, address="1 Ship Way"):
        conn = self._connection()
        try:
            cursor = conn.execute(
                """
                INSERT INTO shipping_addresses
                (client_id, label, manager, store_number, address, city, state, country, phone)
                VALUES (?, 'Main', 'Manager', '42', ?, 'New York', 'NY', 'US', '555-0100')
                """,
                (client_id, address),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def _stone(self, stock, status="Y", hold_client_id=None, total=1000.0):
        conn = self._connection()
        try:
            cursor = conn.execute(
                """
                INSERT INTO stones (stock_number, status, hold_client_id)
                VALUES (?, ?, ?)
                """,
                (stock, status, hold_client_id),
            )
            stone_id = cursor.lastrowid
            conn.execute(
                """
                INSERT INTO grading_reports
                (stone_id, report_number, shape, weight, color, clarity, size, measurements,
                 price_per_carat, total_price, active)
                VALUES (?, ?, 'ROUND', 1.0, 'D', 'VS1', '1.00 CT', '6.5 x 6.5 x 4.0', ?, ?, 1)
                """,
                (stone_id, f"REPORT-{stock}", total, total),
            )
            conn.commit()
            return stone_id
        finally:
            conn.close()

    def _header(self, client_id, **overrides):
        payload = {
            "client_id": client_id,
            "person": "Contact Person",
            "phone": "555-1000",
            "contact_email": "contact@example.test",
            "date": "2026-07-29",
            "terms": "NET_30",
            "carrier": "FEDEX",
            "shipment_type": "DELIVERY",
            "ship_charge": 25.0,
            "purchase_order_number": "PO-42",
            "ship_to_address": "99 Manual Ship Lane",
            "ship_to_city": "New York",
            "ship_to_state": "NY",
            "ship_to_country": "US",
        }
        payload.update(overrides)
        return payload

    def _stone_row(self, stone_id):
        conn = self._connection()
        try:
            return conn.execute("SELECT * FROM stones WHERE id = ?", (stone_id,)).fetchone()
        finally:
            conn.close()

    def _transaction_count(self):
        conn = self._connection()
        try:
            return conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        finally:
            conn.close()

    def test_available_stone_can_be_held_and_release_requires_a_real_hold(self):
        client = self._client("HOLD-A")
        stone = self._stone("HOLD-1")
        conn = self._connection()
        try:
            place_hold(conn, client, [stone])
            self.assertEqual(self._stone_row(stone)["status"], "H")
            self.assertEqual(self._stone_row(stone)["hold_client_id"], client)
            release_hold(conn, [stone])
            self.assertEqual(self._stone_row(stone)["status"], "Y")
            self.assertIsNone(self._stone_row(stone)["hold_client_id"])
        finally:
            conn.close()

    def test_hold_requires_client_and_rejects_return_pending(self):
        stone = self._stone("HOLD-NO-CLIENT")
        conn = self._connection()
        try:
            with self.assertRaises(WorkflowError):
                place_hold(conn, None, [stone])
        finally:
            conn.close()

        client = self._client("HOLD-PENDING")
        pending = self._stone("HOLD-PENDING-1", RETURN_PENDING)
        conn = self._connection()
        try:
            with self.assertRaises(WorkflowConflict):
                place_hold(conn, client, [pending])
        finally:
            conn.close()

    def test_same_client_hold_moves_to_memo_and_clears_hold_client(self):
        client = self._client("MEMO-HOLD")
        stone = self._stone("MEMO-HOLD-1")
        payload = self._header(client, stone_ids=[stone])
        conn = self._connection()
        try:
            place_hold(conn, client, [stone])
            memo = create_memo_draft(conn, payload)
            activate_memo(conn, memo["id"])
        finally:
            conn.close()
        saved = self._stone_row(stone)
        self.assertEqual(saved["status"], "M")
        self.assertIsNone(saved["hold_client_id"])

    def test_same_client_hold_can_be_directly_invoiced_but_other_client_cannot(self):
        owner = self._client("INVOICE-OWNER")
        other = self._client("INVOICE-OTHER")
        held = self._stone("INVOICE-HELD")
        conn = self._connection()
        try:
            place_hold(conn, owner, [held])
            with self.assertRaises(WorkflowConflict):
                create_direct_invoice(conn, self._header(other, stone_ids=[held]))
            invoice = create_direct_invoice(conn, self._header(owner, stone_ids=[held]))
        finally:
            conn.close()
        self.assertEqual(invoice["parent_transaction_id"], None)
        self.assertEqual(self._stone_row(held)["status"], "S")
        self.assertIsNone(self._stone_row(held)["hold_client_id"])

    def test_client_eligible_inventory_includes_its_own_holds_only(self):
        owner = self._client("ELIGIBLE-OWNER")
        other = self._client("ELIGIBLE-OTHER")
        available = self._stone("ELIGIBLE-AVAILABLE")
        own_hold = self._stone("ELIGIBLE-OWN-HOLD")
        other_hold = self._stone("ELIGIBLE-OTHER-HOLD")
        conn = self._connection()
        try:
            place_hold(conn, owner, [own_hold])
            place_hold(conn, other, [other_hold])
            eligible = eligible_stones_for_client(conn, owner)
        finally:
            conn.close()
        self.assertEqual(
            {stone["id"] for stone in eligible},
            {available, own_hold},
        )

    def test_memo_return_requires_receipt_before_inventory_is_available(self):
        client = self._client("MEMO-RETURN")
        stone = self._stone("MEMO-RETURN-1")
        conn = self._connection()
        try:
            memo = create_memo_draft(conn, self._header(client, stone_ids=[stone]))
            activate_memo(conn, memo["id"])
            return_memo_stones(conn, memo["id"], [stone])
            self.assertEqual(self._stone_row(stone)["status"], RETURN_PENDING)
            with self.assertRaises(WorkflowConflict):
                place_hold(conn, client, [stone])
            receive_stones(conn, [stone], self.user_id, "Physically scanned")
            self.assertEqual(self._stone_row(stone)["status"], "Y")
            with self.assertRaises(WorkflowConflict):
                receive_stones(conn, [stone], self.user_id)
        finally:
            conn.close()

        conn = self._connection()
        try:
            event = conn.execute("SELECT * FROM receiving_events WHERE stone_id = ?", (stone,)).fetchone()
            self.assertEqual(event["stock_number_snapshot"], "MEMO-RETURN-1")
            self.assertEqual(event["received_by_user_id"], self.user_id)
        finally:
            conn.close()

    def test_invoice_return_creates_credit_snapshot_and_return_pending(self):
        client = self._client("INV-RETURN")
        stone = self._stone("INV-RETURN-1", total=1500.0)
        conn = self._connection()
        try:
            invoice = create_direct_invoice(conn, self._header(client, stone_ids=[stone]))
            credit = credit_invoice(conn, invoice["id"], [stone])
        finally:
            conn.close()
        self.assertEqual(self._stone_row(stone)["status"], RETURN_PENDING)
        conn = self._connection()
        try:
            original = conn.execute(
                "SELECT status FROM transaction_items WHERE transaction_id = ?", (invoice["id"],)
            ).fetchone()
            credit_item = conn.execute(
                "SELECT status, total_price FROM transaction_items WHERE transaction_id = ?", (credit["id"],)
            ).fetchone()
            self.assertEqual(original["status"], "returned")
            self.assertEqual(credit_item["status"], "credited")
            self.assertEqual(credit_item["total_price"], -1500.0)
        finally:
            conn.close()

    def test_transaction_numbers_have_independent_daily_sequences_and_are_unique(self):
        client = self._client("NUMBERS")
        memo_stone = self._stone("NUM-MEMO")
        invoice_stone = self._stone("NUM-INV")
        conn = self._connection()
        try:
            memo = create_memo_draft(conn, self._header(client, stone_ids=[memo_stone]))
            invoice = create_direct_invoice(conn, self._header(client, stone_ids=[invoice_stone]))
            credit = credit_invoice(
                conn, invoice["id"], [invoice_stone], date(2026, 7, 29)
            )
            self.assertEqual(memo["transaction_number"], "MEMO-20260729-0001")
            self.assertEqual(invoice["transaction_number"], "INV-20260729-0001")
            self.assertEqual(credit["transaction_number"], "CR-20260729-0001")
            with atomic_workflow(conn) as cursor:
                next_invoice = generate_transaction_number(
                    cursor, "invoice", date(2026, 7, 29)
                )
                self.assertEqual(next_invoice, "INV-20260729-0002")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO transactions (transaction_number, client_id, type, status, person, date, terms, carrier, shipment_type, ship_charge) VALUES (?, ?, 'invoice', 'active', 'A', '2026-07-29', 'NET', 'UPS', 'DELIVERY', 0)",
                    (invoice["transaction_number"], client),
                )
                conn.commit()
            conn.rollback()
        finally:
            conn.close()

    def test_shipping_snapshots_survive_saved_address_changes_and_manual_entry(self):
        client = self._client("SHIP-SNAPSHOT")
        source_address = self._shipping_address(client, "100 Original Road")
        source_stone = self._stone("SHIP-SOURCE")
        manual_stone = self._stone("SHIP-MANUAL")
        conn = self._connection()
        try:
            source = create_direct_invoice(
                conn,
                self._header(
                    client,
                    stone_ids=[source_stone],
                    source_shipping_address_id=source_address,
                    ship_to_address=None,
                ),
            )
            manual = create_direct_invoice(
                conn,
                self._header(client, stone_ids=[manual_stone], ship_to_address="44 Manual Way"),
            )
        finally:
            conn.close()
        conn = self._connection()
        try:
            conn.execute("UPDATE shipping_addresses SET address = 'Changed Later' WHERE id = ?", (source_address,))
            conn.commit()
            source_row = conn.execute("SELECT * FROM transactions WHERE id = ?", (source["id"],)).fetchone()
            manual_row = conn.execute("SELECT * FROM transactions WHERE id = ?", (manual["id"],)).fetchone()
            self.assertEqual(source_row["ship_to_address_snapshot"], "100 Original Road")
            self.assertEqual(manual_row["ship_to_address_snapshot"], "44 Manual Way")
            self.assertEqual(source_row["source_shipping_address_id"], source_address)
        finally:
            conn.close()

    def test_mixed_direct_invoice_batch_rolls_back_and_creates_snapshots_on_success(self):
        client = self._client("DIRECT-BATCH")
        available = self._stone("DIRECT-AVAILABLE")
        sold = self._stone("DIRECT-SOLD", "S")
        conn = self._connection()
        try:
            with self.assertRaises(WorkflowConflict):
                create_direct_invoice(conn, self._header(client, stone_ids=[available, sold]))
            self.assertEqual(self._transaction_count(), 0)
            self.assertEqual(self._stone_row(available)["status"], "Y")
            invoice = create_direct_invoice(conn, self._header(client, stone_ids=[available]))
        finally:
            conn.close()
        conn = self._connection()
        try:
            item = conn.execute("SELECT * FROM transaction_items WHERE transaction_id = ?", (invoice["id"],)).fetchone()
            parent = conn.execute("SELECT parent_transaction_id FROM transactions WHERE id = ?", (invoice["id"],)).fetchone()
            self.assertEqual(item["stock_number"], "DIRECT-AVAILABLE")
            self.assertEqual(item["total_price"], 1000.0)
            self.assertIsNone(parent["parent_transaction_id"])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
