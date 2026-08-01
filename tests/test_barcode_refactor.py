"""Temporary-database and in-memory coverage for barcode printing."""

from __future__ import annotations

import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import unittest
import io

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from database.init_db import init_db
from database.migrations import upgrade_database
from services.barcode_printing import (
    BarcodeNotFound,
    BarcodePrintError,
    build_barcode_pdf,
    load_printable_stones,
    normalize_stone_ids,
)
from services.stone_service_helpers import insert_stone

try:
    import flask  # noqa: F401
except ModuleNotFoundError:
    HAS_FLASK = False
else:
    HAS_FLASK = True


class BarcodePrintServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "barcode-test.db")
        self.previous_db_path = os.environ.get("DSMS_DB_PATH")
        self.previous_testing = os.environ.get("DSMS_TESTING")
        os.environ["DSMS_DB_PATH"] = self.db_path
        os.environ["DSMS_TESTING"] = "1"
        init_db(self.db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.stone_id = self._insert_stone("PRINT-001")

    def tearDown(self):
        self.conn.close()
        if self.previous_db_path is None:
            os.environ.pop("DSMS_DB_PATH", None)
        else:
            os.environ["DSMS_DB_PATH"] = self.previous_db_path
        if self.previous_testing is None:
            os.environ.pop("DSMS_TESTING", None)
        else:
            os.environ["DSMS_TESTING"] = self.previous_testing
        self.tempdir.cleanup()

    def _insert_stone(self, stock_number):
        cursor = self.conn.execute(
            "INSERT INTO stones (stock_number, status) VALUES (?, 'Y')", (stock_number,)
        )
        self.conn.commit()
        return cursor.lastrowid

    def test_new_stones_have_no_barcode_path_column_or_stored_value(self):
        columns = [row[1] for row in self.conn.execute("PRAGMA table_info(stones)")]
        self.assertNotIn("barcode_path", columns)

        second = insert_stone(self.conn.cursor(), {"stock_number": "CREATE-002", "status": "Y"})
        self.conn.commit()
        self.assertEqual(self.conn.execute("SELECT stock_number FROM stones WHERE id = ?", (second,)).fetchone()[0], "CREATE-002")

    def test_one_and_multiple_labels_are_valid_in_memory_pdfs_without_database_updates(self):
        second = self._insert_stone("PRINT-002")
        before = self.conn.execute("SELECT * FROM stones WHERE id = ?", (self.stone_id,)).fetchone()

        one = build_barcode_pdf(load_printable_stones(self.conn.cursor(), [self.stone_id]))
        multiple = build_barcode_pdf(load_printable_stones(self.conn.cursor(), [self.stone_id, second]))

        self.assertEqual(one.read(5), b"%PDF-")
        self.assertEqual(multiple.read(5), b"%PDF-")
        self.assertGreater(len(one.getvalue()), 1000)
        after = self.conn.execute("SELECT * FROM stones WHERE id = ?", (self.stone_id,)).fetchone()
        self.assertEqual(tuple(before), tuple(after))

    def test_printing_never_creates_static_or_application_barcode_files(self):
        static_directory = PROJECT_ROOT / "frontend" / "static" / "barcodes"
        before = {path.name for path in static_directory.glob("*")} if static_directory.exists() else set()

        build_barcode_pdf(load_printable_stones(self.conn.cursor(), [self.stone_id]))

        after = {path.name for path in static_directory.glob("*")} if static_directory.exists() else set()
        self.assertEqual(before, after)
        self.assertFalse(any((BACKEND_ROOT / name).exists() for name in ("barcode-labels.pdf", "barcodes.pdf")))

    def test_missing_stone_and_blank_stock_number_are_rejected(self):
        with self.assertRaises(BarcodeNotFound) as missing:
            load_printable_stones(self.conn.cursor(), [999999])
        self.assertEqual(missing.exception.status_code, 404)

        blank_id = self._insert_stone("")
        with self.assertRaises(BarcodePrintError):
            load_printable_stones(self.conn.cursor(), [blank_id])

    def test_duplicate_batch_ids_are_normalized_and_malformed_or_large_batches_fail(self):
        self.assertEqual(normalize_stone_ids([self.stone_id, self.stone_id]), [self.stone_id])
        with self.assertRaises(BarcodePrintError):
            normalize_stone_ids([""])
        with self.assertRaises(BarcodePrintError):
            normalize_stone_ids(list(range(1, 102)))


class BarcodeMigrationTests(unittest.TestCase):
    def test_legacy_barcode_column_is_removed_with_a_backup_and_data_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "legacy.db"
            conn = sqlite3.connect(db_path)
            try:
                conn.executescript(
                    """
                    CREATE TABLE schema_migrations (name TEXT PRIMARY KEY, applied_at DATETIME);
                    INSERT INTO schema_migrations (name, applied_at) VALUES ('001_workflow_foundation', CURRENT_TIMESTAMP);
                    CREATE TABLE stones (
                        id INTEGER PRIMARY KEY,
                        stock_number TEXT NOT NULL UNIQUE,
                        barcode_path TEXT NOT NULL UNIQUE,
                        status TEXT NOT NULL,
                        hold_client_id INTEGER,
                        shade TEXT, milky TEXT, eye_clean TEXT, bgm TEXT, black TEXT,
                        open_inclusion TEXT, pair_number TEXT, pair_stock_number TEXT,
                        pair_separable INTEGER, picture_link TEXT, video_link TEXT,
                        current_country TEXT, current_state TEXT, current_city TEXT
                    );
                    INSERT INTO stones (stock_number, barcode_path, status) VALUES ('LEGACY-001', 'barcodes/legacy.png', 'Y');
                    """
                )
                conn.commit()
            finally:
                conn.close()

            backup = upgrade_database(str(db_path), backup=True)
            self.assertIsNotNone(backup)
            self.assertTrue(Path(backup).exists())

            conn = sqlite3.connect(db_path)
            try:
                columns = [row[1] for row in conn.execute("PRAGMA table_info(stones)")]
                self.assertNotIn("barcode_path", columns)
                self.assertEqual(conn.execute("SELECT stock_number, status FROM stones").fetchone(), ("LEGACY-001", "Y"))
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            finally:
                conn.close()


@unittest.skipUnless(HAS_FLASK, "Flask must be installed to run HTTP barcode route tests")
class BarcodeRouteTests(unittest.TestCase):
    """HTTP tests run after `pip install -r requirements.txt` restores Flask."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "barcode-routes.db")
        self.previous_db_path = os.environ.get("DSMS_DB_PATH")
        self.previous_testing = os.environ.get("DSMS_TESTING")
        self.previous_secret_key = os.environ.get("DSMS_SECRET_KEY")
        os.environ["DSMS_DB_PATH"] = self.db_path
        os.environ["DSMS_TESTING"] = "1"
        os.environ["DSMS_SECRET_KEY"] = "test-session-secret"
        init_db(self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            user = conn.execute(
                "INSERT INTO users (email, password_hash, first_name, last_name, role) VALUES ('print@test', 'unused', 'Print', 'User', 'MANAGER')"
            )
            stone = conn.execute("INSERT INTO stones (stock_number, status) VALUES ('HTTP-001', 'Y')")
            conn.commit()
            self.user_id = user.lastrowid
            self.stone_id = stone.lastrowid
        finally:
            conn.close()

        from app import app

        app.config.update(TESTING=True, SECRET_KEY="barcode-test-secret")
        self.client = app.test_client()

    def tearDown(self):
        if self.previous_db_path is None:
            os.environ.pop("DSMS_DB_PATH", None)
        else:
            os.environ["DSMS_DB_PATH"] = self.previous_db_path
        if self.previous_testing is None:
            os.environ.pop("DSMS_TESTING", None)
        else:
            os.environ["DSMS_TESTING"] = self.previous_testing
        if self.previous_secret_key is None:
            os.environ.pop("DSMS_SECRET_KEY", None)
        else:
            os.environ["DSMS_SECRET_KEY"] = self.previous_secret_key
        self.tempdir.cleanup()

    def _authenticate(self):
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_id

    def _csrf_token(self):
        response = self.client.get("/barcodes")
        self.assertEqual(response.status_code, 200)
        match = re.search(rb'name="csrf-token" content="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode("utf-8")

    def test_unauthorized_printing_is_rejected(self):
        response = self.client.get(f"/barcodes/{self.stone_id}/pdf")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_single_and_duplicate_batch_printing_return_private_pdfs(self):
        self._authenticate()
        conn = sqlite3.connect(self.db_path)
        try:
            before = conn.execute("SELECT stock_number, status FROM stones WHERE id = ?", (self.stone_id,)).fetchone()
        finally:
            conn.close()
        single = self.client.get(f"/barcodes/{self.stone_id}/pdf")
        batch = self.client.post(
            "/barcodes/pdf",
            json={"stone_ids": [self.stone_id, self.stone_id]},
            headers={"X-CSRFToken": self._csrf_token()},
        )
        self.assertEqual(single.status_code, 200)
        self.assertEqual(batch.status_code, 200)
        self.assertEqual(single.mimetype, "application/pdf")
        self.assertTrue(single.data.startswith(b"%PDF-"))
        self.assertIn("no-store", single.headers["Cache-Control"])
        conn = sqlite3.connect(self.db_path)
        try:
            after = conn.execute("SELECT stock_number, status FROM stones WHERE id = ?", (self.stone_id,)).fetchone()
        finally:
            conn.close()
        self.assertEqual(before, after)

    def test_missing_and_blank_stock_routes_fail_safely(self):
        self._authenticate()
        missing = self.client.get("/barcodes/999999/pdf")
        self.assertEqual(missing.status_code, 404)

        conn = sqlite3.connect(self.db_path)
        try:
            blank = conn.execute("INSERT INTO stones (stock_number, status) VALUES ('', 'Y')")
            conn.commit()
        finally:
            conn.close()
        blank_response = self.client.get(f"/barcodes/{blank.lastrowid}/pdf")
        self.assertEqual(blank_response.status_code, 400)

    def test_csv_import_has_no_barcode_rendering_or_stored_path(self):
        self._authenticate()
        token = self._csrf_token()
        static_directory = PROJECT_ROOT / "frontend" / "static" / "barcodes"
        before = {path.name for path in static_directory.glob("*")} if static_directory.exists() else set()
        csv_data = b"Shape,Weight,Color,Clarity,Measurements\nRD,1.00,D,VS1,6.5 x 6.5 x 4.0\n"
        response = self.client.post(
            "/add-stones",
            data={
                "csrf_token": token,
                "file": (io.BytesIO(csv_data), "stones.csv"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        after = {path.name for path in static_directory.glob("*")} if static_directory.exists() else set()
        self.assertEqual(before, after)
        conn = sqlite3.connect(self.db_path)
        try:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(stones)")]
            self.assertNotIn("barcode_path", columns)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM stones").fetchone()[0], 2)
        finally:
            conn.close()
