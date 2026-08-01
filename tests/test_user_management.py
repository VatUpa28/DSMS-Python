"""ADMIN-only user-management coverage using a temporary SQLite database."""

from __future__ import annotations

import os
from contextlib import closing
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import unittest

from werkzeug.security import check_password_hash, generate_password_hash


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from database.init_db import init_db


PASSWORD = "TemporaryPassword!123"
PASSWORD_HASH = generate_password_hash(PASSWORD)


class UserManagementTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "users-test.db")
        self.saved_environment = {
            name: os.environ.get(name)
            for name in ("DSMS_DB_PATH", "DSMS_TESTING", "DSMS_SECRET_KEY", "DSMS_ENV")
        }
        os.environ.update(
            {
                "DSMS_DB_PATH": self.db_path,
                "DSMS_TESTING": "1",
                "DSMS_SECRET_KEY": "user-management-test-secret",
                "DSMS_ENV": "development",
            }
        )
        init_db(self.db_path)
        self.admin_id = self._insert_user("admin@example.test", "ADMIN")
        self.second_admin_id = self._insert_user("admin2@example.test", "ADMIN")
        self.manager_id = self._insert_user("manager@example.test", "MANAGER")
        self.sales_id = self._insert_user("sales@example.test", "SALES")
        self.accounting_id = self._insert_user("accounting@example.test", "ACCOUNTING")

        from app import app

        app.config.update(
            TESTING=True,
            SECRET_KEY="user-management-test-secret",
            SESSION_COOKIE_SECURE=False,
        )
        self.app = app
        self.client = self._client_for(self.admin_id)

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
        return connection

    def _insert_user(self, email, role, active=1):
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT INTO users
                    (email, password_hash, first_name, last_name, role, active)
                VALUES (?, ?, 'Test', 'User', ?, ?)
                """,
                (email, PASSWORD_HASH, role, active),
            )
            connection.commit()
            return cursor.lastrowid

    def _client_for(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
        return client

    def _csrf(self, client=None, path="/admin/users"):
        client = client or self.client
        response = client.get(path)
        self.assertEqual(response.status_code, 200)
        match = re.search(rb'name="csrf-token" content="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def _post(self, path, data=None, client=None):
        client = client or self.client
        payload = dict(data or {})
        payload["csrf_token"] = self._csrf(client)
        return client.post(path, data=payload, follow_redirects=True)

    def _user(self, user_id):
        with closing(self._connect()) as connection:
            return connection.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()

    def test_access_is_admin_only_and_forbidden_session_remains_authenticated(self):
        self.assertEqual(self.client.get("/admin/users").status_code, 200)
        self.assertIn(b"User Management", self.client.get("/admin/users").data)

        logged_out = self.app.test_client().get("/admin/users")
        self.assertEqual(logged_out.status_code, 302)
        self.assertIn("/login", logged_out.headers["Location"])

        for user_id in (self.manager_id, self.sales_id, self.accounting_id):
            client = self._client_for(user_id)
            response = client.get("/admin/users")
            self.assertEqual(response.status_code, 403)
            with client.session_transaction() as session:
                self.assertEqual(session.get("user_id"), user_id)

    def test_create_user_normalizes_values_hashes_password_and_supports_four_roles(self):
        for role in ("ADMIN", "MANAGER", "SALES", "ACCOUNTING"):
            email = f"  New-{role}@Example.Test  "
            response = self._post(
                "/admin/users/new",
                {
                    "email": email,
                    "first_name": "  New ",
                    "last_name": " User  ",
                    "role": role.lower(),
                    "password": PASSWORD,
                    "confirm_password": PASSWORD,
                },
            )
            self.assertIn(b"User created", response.data)
            with closing(self._connect()) as connection:
                user = connection.execute(
                    "SELECT * FROM users WHERE email = ?",
                    (f"new-{role.lower()}@example.test",),
                ).fetchone()
            self.assertEqual(user["role"], role)
            self.assertEqual(user["first_name"], "New")
            self.assertNotEqual(user["password_hash"], PASSWORD)
            self.assertTrue(check_password_hash(user["password_hash"], PASSWORD))

    def test_creation_rejects_invalid_input_without_partial_user(self):
        cases = [
            ("INVENTORY", PASSWORD, PASSWORD),
            ("OWNER", PASSWORD, PASSWORD),
            ("SALES", "short", "short"),
            ("SALES", PASSWORD, "DifferentPassword!123"),
        ]
        for index, (role, password, confirmation) in enumerate(cases):
            response = self._post(
                "/admin/users/new",
                {
                    "email": f"invalid-{index}@example.test",
                    "first_name": "Invalid",
                    "last_name": "User",
                    "role": role,
                    "password": password,
                    "confirm_password": confirmation,
                },
            )
            self.assertNotIn(b"User created.", response.data)
        with closing(self._connect()) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM users WHERE email LIKE 'invalid-%'"
            ).fetchone()[0]
        self.assertEqual(count, 0)
        self.assertNotIn(PASSWORD.encode(), response.data)

    def test_duplicate_email_is_case_insensitive(self):
        response = self._post(
            "/admin/users/new",
            {
                "email": " ADMIN@EXAMPLE.TEST ",
                "first_name": "Duplicate",
                "last_name": "User",
                "role": "SALES",
                "password": PASSWORD,
                "confirm_password": PASSWORD,
            },
        )
        self.assertIn(b"already exists", response.data)
        with closing(self._connect()) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM users WHERE LOWER(email) = ?",
                    ("admin@example.test",),
                ).fetchone()[0],
                1,
            )

    def test_role_changes_validate_roles_and_take_effect_on_next_request(self):
        for role in ("ADMIN", "MANAGER", "SALES", "ACCOUNTING"):
            previous_role = self._user(self.manager_id)["role"]
            response = self._post(
                f"/admin/users/{self.manager_id}/role", {"role": role.lower()}
            )
            expected = b"User role updated" if role != previous_role else b"already has"
            self.assertIn(expected, response.data)
            self.assertEqual(self._user(self.manager_id)["role"], role)
        self._post(f"/admin/users/{self.manager_id}/role", {"role": "ADMIN"})
        self.assertEqual(self._client_for(self.manager_id).get("/admin/users").status_code, 200)

        for invalid_role in ("INVENTORY", "OWNER"):
            response = self._post(
                f"/admin/users/{self.sales_id}/role", {"role": invalid_role}
            )
            self.assertIn(b"Invalid role", response.data)
            self.assertEqual(self._user(self.sales_id)["role"], "SALES")

    def test_self_demotion_and_self_deactivation_are_blocked(self):
        demotion = self._post(
            f"/admin/users/{self.admin_id}/role", {"role": "MANAGER"}
        )
        deactivation = self._post(f"/admin/users/{self.admin_id}/deactivate")
        for response in (demotion, deactivation):
            self.assertIn(b"cannot remove your own administrator access", response.data)
        user = self._user(self.admin_id)
        self.assertEqual(user["role"], "ADMIN")
        self.assertEqual(user["active"], 1)

    def test_last_active_admin_cannot_be_demoted_or_deactivated(self):
        self._post(f"/admin/users/{self.second_admin_id}/deactivate")
        demotion = self._post(
            f"/admin/users/{self.admin_id}/role", {"role": "MANAGER"}
        )
        self.assertIn(b"always have at least one active administrator", demotion.data)
        deactivation = self._post(f"/admin/users/{self.admin_id}/deactivate")
        self.assertIn(b"always have at least one active administrator", deactivation.data)
        with closing(self._connect()) as connection:
            active_admins = connection.execute(
                """
                SELECT COUNT(*) FROM users
                WHERE active = 1 AND role = 'ADMIN'
                """
            ).fetchone()[0]
        self.assertEqual(active_admins, 1)

    def test_deactivation_invalidates_login_and_existing_session_but_preserves_history(self):
        with closing(self._connect()) as connection:
            stone_id = connection.execute(
                "INSERT INTO stones (stock_number, status) VALUES ('HISTORY-1', 'AVAILABLE')"
            ).lastrowid
            connection.execute(
                """
                INSERT INTO receiving_events
                    (stone_id, stock_number_snapshot, received_by_user_id)
                VALUES (?, 'HISTORY-1', ?)
                """,
                (stone_id, self.sales_id),
            )
            connection.commit()

        existing_client = self._client_for(self.sales_id)
        first = self._post(f"/admin/users/{self.sales_id}/deactivate")
        second = self._post(f"/admin/users/{self.sales_id}/deactivate")
        self.assertIn(b"User deactivated", first.data)
        self.assertIn(b"already inactive", second.data)
        self.assertEqual(existing_client.get("/inventory").status_code, 302)

        login_client = self.app.test_client()
        token = self._csrf(login_client, "/login")
        login = login_client.post(
            "/login",
            data={
                "csrf_token": token,
                "email": "sales@example.test",
                "password": PASSWORD,
            },
        )
        self.assertEqual(login.status_code, 401)
        with closing(self._connect()) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM receiving_events WHERE received_by_user_id = ?",
                    (self.sales_id,),
                ).fetchone()[0],
                1,
            )

    def test_reactivation_preserves_role_and_hash_and_is_idempotent(self):
        self._post(f"/admin/users/{self.accounting_id}/deactivate")
        before = self._user(self.accounting_id)
        first = self._post(f"/admin/users/{self.accounting_id}/reactivate")
        second = self._post(f"/admin/users/{self.accounting_id}/reactivate")
        after = self._user(self.accounting_id)
        self.assertIn(b"User reactivated", first.data)
        self.assertIn(b"already active", second.data)
        self.assertEqual(after["role"], before["role"])
        self.assertEqual(after["password_hash"], before["password_hash"])
        login_client = self.app.test_client()
        token = self._csrf(login_client, "/login")
        login = login_client.post(
            "/login",
            data={
                "csrf_token": token,
                "email": after["email"],
                "password": PASSWORD,
            },
        )
        self.assertEqual(login.status_code, 302)

    def test_mutations_are_post_only_and_require_valid_csrf(self):
        path = f"/admin/users/{self.sales_id}/deactivate"
        self.assertEqual(self.client.get(path).status_code, 405)
        self.assertEqual(self.client.post(path).status_code, 400)
        self.assertEqual(
            self.client.post(path, data={"csrf_token": "invalid"}).status_code,
            400,
        )
        self.assertEqual(self._user(self.sales_id)["active"], 1)
        valid = self.client.post(
            path, data={"csrf_token": self._csrf()}, follow_redirects=True
        )
        self.assertEqual(valid.status_code, 200)
        self.assertEqual(self._user(self.sales_id)["active"], 0)


if __name__ == "__main__":
    unittest.main()
