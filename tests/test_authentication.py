"""Temporary-database coverage for Flask browser authentication and CSRF."""

from __future__ import annotations

import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.serving import make_server
from flask import Flask

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from auth import VALID_ROLES, configure_session_security
from database.create_user import UserProvisioningError, provision_user
from database.init_db import init_db


ACTIVE_PASSWORD = "ActivePassword!123"
NEW_PASSWORD = "ChangedPassword!456"
GENERIC_LOGIN_ERROR = b"Invalid email or password."
ACTIVE_PASSWORD_HASH = generate_password_hash(ACTIVE_PASSWORD)


class AuthenticationInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "auth-test.db")
        self.saved_environment = {
            name: os.environ.get(name)
            for name in ("DSMS_DB_PATH", "DSMS_TESTING", "DSMS_SECRET_KEY", "DSMS_ENV")
        }
        os.environ.update(
            {
                "DSMS_DB_PATH": self.db_path,
                "DSMS_TESTING": "1",
                "DSMS_SECRET_KEY": "authentication-test-secret",
                "DSMS_ENV": "development",
            }
        )
        init_db(self.db_path)
        self.active_user_id = self._insert_user(
            "active@example.test", ACTIVE_PASSWORD, "SALES", active=1
        )
        self.inactive_user_id = self._insert_user(
            "inactive@example.test", ACTIVE_PASSWORD, "SALES", active=0
        )
        self.accounting_user_id = self._insert_user(
            "accounting@example.test", ACTIVE_PASSWORD, "ACCOUNTING", active=1
        )
        self.admin_user_id = self._insert_user(
            "admin@example.test", ACTIVE_PASSWORD, "ADMIN", active=1
        )
        self.manager_user_id = self._insert_user(
            "manager@example.test", ACTIVE_PASSWORD, "MANAGER", active=1
        )

        from app import app

        app.config.update(
            TESTING=True,
            SECRET_KEY="authentication-test-secret",
            SESSION_COOKIE_SECURE=False,
        )
        self.app = app
        self.client = app.test_client()

    def tearDown(self):
        for name, value in self.saved_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.tempdir.cleanup()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _insert_user(self, email, password, role, active):
        conn = self._connect()
        try:
            cursor = conn.execute(
                """
                INSERT INTO users
                    (email, password_hash, first_name, last_name, role, active)
                VALUES (?, ?, 'Test', 'User', ?, ?)
                """,
                (
                    email,
                    ACTIVE_PASSWORD_HASH
                    if password == ACTIVE_PASSWORD
                    else generate_password_hash(password),
                    role,
                    active,
                ),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def _csrf_token(self, path="/login"):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        match = re.search(rb'name="csrf-token" content="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode("utf-8")

    def _login(self, email="active@example.test", password=ACTIVE_PASSWORD, next_url=None):
        token = self._csrf_token("/login")
        data = {"csrf_token": token, "email": email, "password": password}
        if next_url is not None:
            data["next"] = next_url
        return self.client.post("/login", data=data, follow_redirects=False)

    def _set_identity(self, user_id):
        with self.client.session_transaction() as browser_session:
            browser_session["user_id"] = user_id

    def _legacy_unsupported_user(self):
        conn = self._connect()
        try:
            conn.execute("PRAGMA ignore_check_constraints = ON")
            cursor = conn.execute(
                """
                INSERT INTO users
                    (email, password_hash, first_name, last_name, role, active)
                VALUES ('legacy-inventory@example.test', ?, 'Legacy', 'User', 'INVENTORY', 1)
                """,
                (ACTIVE_PASSWORD_HASH,),
            )
            conn.commit()
            conn.execute("PRAGMA ignore_check_constraints = OFF")
            return cursor.lastrowid
        finally:
            conn.close()

    def _client_for_user(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as browser_session:
            browser_session["user_id"] = user_id
        return client

    def _client_csrf_token(self, client):
        response = client.get("/inventory")
        self.assertEqual(response.status_code, 200)
        match = re.search(rb'name="csrf-token" content="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode("utf-8")

    def test_only_four_roles_are_valid_and_inventory_decorators_are_absent(self):
        self.assertEqual(
            VALID_ROLES,
            {"ADMIN", "MANAGER", "SALES", "ACCOUNTING"},
        )
        routes_source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (BACKEND_ROOT / "routes").glob("*.py")
        )
        self.assertNotRegex(
            routes_source,
            r"require_roles\([^\n]*[\"']INVENTORY[\"']",
        )

    def test_legacy_inventory_role_cannot_login_or_use_an_existing_session(self):
        unsupported_user_id = self._legacy_unsupported_user()
        login = self._login(email="legacy-inventory@example.test")
        self.assertEqual(login.status_code, 401)
        self.assertIn(GENERIC_LOGIN_ERROR, login.data)

        self.client = self.app.test_client()
        self._set_identity(unsupported_user_id)
        response = self.client.get("/inventory")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])
        with self.client.session_transaction() as browser_session:
            self.assertNotIn("user_id", browser_session)

    def test_root_routes_logged_out_to_login_and_logged_in_to_inventory_without_looping(self):
        logged_out = self.client.get("/", follow_redirects=False)
        self.assertEqual(logged_out.status_code, 302)
        self.assertTrue(logged_out.headers["Location"].endswith("/login"))
        self.assertNotIn("next=", logged_out.headers["Location"])
        completed = self.client.get("/", follow_redirects=True)
        self.assertEqual(completed.status_code, 200)
        self.assertIn(b"Sign in to DSMS", completed.data)

        self._set_identity(self.active_user_id)
        logged_in = self.client.get("/", follow_redirects=False)
        self.assertEqual(logged_in.status_code, 302)
        self.assertTrue(logged_in.headers["Location"].endswith("/inventory"))

    def test_valid_active_email_login_is_normalized_and_session_is_minimal(self):
        response = self._login(email="  ACTIVE@EXAMPLE.TEST  ")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/inventory"))
        with self.client.session_transaction() as browser_session:
            self.assertEqual(browser_session["user_id"], self.active_user_id)
            self.assertTrue(
                set(browser_session).issubset({"user_id", "_permanent", "csrf_token"})
            )
            self.assertNotIn("password", browser_session)
            self.assertNotIn("role", browser_session)
            self.assertNotIn("email", browser_session)

    def test_login_csrf_survives_anonymous_requests_and_reaches_credentials(self):
        login_page = self.client.get("/login")
        self.assertEqual(login_page.status_code, 200)
        field = re.search(
            rb'name="csrf_token" value="([^"]+)"', login_page.data
        )
        self.assertIsNotNone(field)
        self.assertEqual(
            len(re.findall(rb'name="csrf_token"', login_page.data)), 1
        )
        token = field.group(1).decode("utf-8")
        cookie = login_page.headers.get("Set-Cookie", "")
        self.assertIn("dsms_session=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Path=/", cookie)
        self.assertNotIn("; Secure", cookie)

        with self.client.session_transaction() as browser_session:
            csrf_state = browser_session.get("csrf_token")
        self.assertTrue(csrf_state)

        # Browsers commonly request an unresolved favicon after rendering Login.
        self.assertEqual(self.client.get("/favicon.ico").status_code, 404)
        with self.client.session_transaction() as browser_session:
            self.assertEqual(browser_session.get("csrf_token"), csrf_state)

        invalid = self.client.post(
            "/login",
            data={
                "csrf_token": token,
                "email": "active@example.test",
                "password": "WrongPassword!123",
            },
        )
        self.assertEqual(invalid.status_code, 401)
        self.assertIn(GENERIC_LOGIN_ERROR, invalid.data)
        self.assertNotIn(b"security token was invalid", invalid.data)

    def test_login_token_survives_public_navigation_and_valid_login(self):
        login_page = self.client.get("/login")
        token = re.search(
            rb'name="csrf_token" value="([^"]+)"', login_page.data
        ).group(1).decode("utf-8")
        with self.client.session_transaction() as browser_session:
            csrf_state = browser_session["csrf_token"]

        root = self.client.get("/", follow_redirects=False)
        self.assertEqual(root.status_code, 302)
        with self.client.session_transaction() as browser_session:
            self.assertEqual(browser_session["csrf_token"], csrf_state)

        response = self.client.post(
            "/login",
            data={
                "csrf_token": token,
                "email": "active@example.test",
                "password": ACTIVE_PASSWORD,
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/inventory"))

    def test_real_http_server_login_invalid_password_and_logout(self):
        server = make_server("127.0.0.1", 0, self.app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        cookies = CookieJar()
        opener = build_opener(HTTPCookieProcessor(cookies))

        def get(path):
            return opener.open(base_url + path, timeout=10)

        def post(path, values):
            return opener.open(
                Request(
                    base_url + path,
                    data=urlencode(values).encode("utf-8"),
                    method="POST",
                ),
                timeout=10,
            )

        try:
            login = get("/login")
            login_html = login.read()
            token = re.search(
                rb'name="csrf_token" value="([^"]+)"', login_html
            ).group(1).decode("utf-8")
            cookie_header = login.headers.get("Set-Cookie", "")
            self.assertIn("dsms_session=", cookie_header)
            self.assertIn("SameSite=Lax", cookie_header)
            self.assertNotIn("; Secure", cookie_header)

            try:
                get("/favicon.ico")
            except HTTPError as error:
                self.assertEqual(error.code, 404)

            with self.assertRaises(HTTPError) as invalid_context:
                post(
                    "/login",
                    {
                        "csrf_token": token,
                        "email": "active@example.test",
                        "password": "WrongPassword!123",
                    },
                )
            self.assertEqual(invalid_context.exception.code, 401)
            invalid_html = invalid_context.exception.read()
            self.assertIn(GENERIC_LOGIN_ERROR, invalid_html)
            self.assertNotIn(b"security token was invalid", invalid_html)

            refreshed = get("/login").read()
            token = re.search(
                rb'name="csrf_token" value="([^"]+)"', refreshed
            ).group(1).decode("utf-8")
            authenticated = post(
                "/login",
                {
                    "csrf_token": token,
                    "email": "active@example.test",
                    "password": ACTIVE_PASSWORD,
                },
            )
            self.assertTrue(authenticated.geturl().endswith("/inventory"))
            inventory_html = authenticated.read()
            logout_token = re.search(
                rb'name="csrf_token" value="([^"]+)"', inventory_html
            ).group(1).decode("utf-8")
            logged_out = post("/logout", {"csrf_token": logout_token})
            self.assertTrue(logged_out.geturl().endswith("/login"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)

    def test_anonymous_current_user_and_stale_cleanup_preserve_csrf_state(self):
        from auth import current_user

        with self.app.test_request_context("/login"):
            from flask import session

            session["csrf_token"] = "anonymous-csrf-state"
            self.assertIsNone(current_user())
            self.assertEqual(session["csrf_token"], "anonymous-csrf-state")

        unsupported_user_id = self._legacy_unsupported_user()
        for user_id in (999999, self.inactive_user_id, unsupported_user_id, "malformed"):
            with self.subTest(user_id=user_id):
                client = self.app.test_client()
                with client.session_transaction() as browser_session:
                    browser_session["user_id"] = user_id
                    browser_session["csrf_token"] = "preserved-stale-csrf"
                response = client.get("/login")
                self.assertEqual(response.status_code, 200)
                with client.session_transaction() as browser_session:
                    self.assertNotIn("user_id", browser_session)
                    self.assertEqual(
                        browser_session.get("csrf_token"), "preserved-stale-csrf"
                    )

    def test_invalid_nonexistent_and_inactive_logins_use_the_same_generic_error(self):
        invalid = self._login(password="WrongPassword!123")
        missing = self._login(email="missing@example.test")
        inactive = self._login(email="inactive@example.test")
        for response in (invalid, missing, inactive):
            self.assertEqual(response.status_code, 401)
            self.assertIn(GENERIC_LOGIN_ERROR, response.data)
            self.assertNotIn(b"password_hash", response.data)

    def test_safe_next_is_accepted_and_external_or_protocol_relative_next_is_rejected(self):
        safe = self._login(next_url="/clients")
        self.assertTrue(safe.headers["Location"].endswith("/clients"))

        self.client = self.app.test_client()
        external = self._login(next_url="https://outside.example/steal")
        self.assertTrue(external.headers["Location"].endswith("/inventory"))
        self.assertNotIn("outside.example", external.headers["Location"])

        self.client = self.app.test_client()
        protocol_relative = self._login(next_url="//outside.example/steal")
        self.assertTrue(protocol_relative.headers["Location"].endswith("/inventory"))

    def test_already_authenticated_login_redirects_to_inventory(self):
        self._set_identity(self.active_user_id)
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/inventory"))

    def test_stale_and_inactive_sessions_render_login_instead_of_bypassing_it(self):
        for user_id in (999999, self.inactive_user_id):
            self.client = self.app.test_client()
            self._set_identity(user_id)
            response = self.client.get("/login")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Sign in to DSMS", response.data)
            with self.client.session_transaction() as browser_session:
                self.assertNotIn("user_id", browser_session)

    def test_browser_redirects_to_login_while_api_returns_json_401(self):
        browser = self.client.get("/inventory")
        self.assertEqual(browser.status_code, 302)
        self.assertIn("/login", browser.headers["Location"])
        self.assertIn("next=/inventory", browser.headers["Location"])

        api = self.client.get("/api/stone-by-stock/UNKNOWN")
        self.assertEqual(api.status_code, 401)
        self.assertTrue(api.is_json)
        self.assertEqual(api.get_json()["error"], "Authentication required")

    def test_forbidden_browser_and_api_responses_are_403_without_logout(self):
        self._set_identity(self.accounting_user_id)
        browser = self.client.get("/barcodes")
        self.assertEqual(browser.status_code, 403)
        self.assertIn(b"Access denied", browser.data)
        with self.client.session_transaction() as browser_session:
            self.assertEqual(browser_session["user_id"], self.accounting_user_id)

        self.client = self.app.test_client()
        self._set_identity(self.active_user_id)
        token = self._csrf_token("/inventory")
        api = self.client.post(
            "/api/transactions/invoices",
            json={},
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(api.status_code, 403)
        self.assertTrue(api.is_json)
        with self.client.session_transaction() as browser_session:
            self.assertEqual(browser_session["user_id"], self.active_user_id)

    def test_logout_is_post_only_requires_csrf_and_clears_authentication(self):
        self._set_identity(self.active_user_id)
        self.assertEqual(self.client.get("/logout").status_code, 405)
        missing_token = self.client.post("/logout")
        self.assertEqual(missing_token.status_code, 400)

        token = self._csrf_token("/inventory")
        response = self.client.post("/logout", data={"csrf_token": token})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/login"))
        with self.client.session_transaction() as browser_session:
            self.assertNotIn("user_id", browser_session)
        self.assertEqual(self.client.get("/inventory").status_code, 302)

    def test_deleted_or_inactive_session_user_is_cleared_and_must_reauthenticate(self):
        self._set_identity(self.active_user_id)
        conn = self._connect()
        try:
            conn.execute("UPDATE users SET active = 0 WHERE id = ?", (self.active_user_id,))
            conn.commit()
        finally:
            conn.close()

        response = self.client.get("/inventory")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])
        with self.client.session_transaction() as browser_session:
            self.assertNotIn("user_id", browser_session)

        self.client = self.app.test_client()
        self._set_identity(self.accounting_user_id)
        conn = self._connect()
        try:
            conn.execute("DELETE FROM users WHERE id = ?", (self.accounting_user_id,))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self.client.get("/inventory").status_code, 302)
        with self.client.session_transaction() as browser_session:
            self.assertNotIn("user_id", browser_session)

    def test_login_and_account_pages_are_private_and_navigation_shows_safe_user_data(self):
        login = self.client.get("/login")
        self.assertIn("no-store", login.headers["Cache-Control"])
        self.assertIn(b"Sign in to DSMS", login.data)
        self.assertNotIn(b"Register", login.data)

        self._set_identity(self.active_user_id)
        account = self.client.get("/account/change-password")
        self.assertEqual(account.status_code, 200)
        self.assertIn("no-store", account.headers["Cache-Control"])
        navigation = self.client.get("/inventory").data
        self.assertIn(b"Test User", navigation)
        self.assertIn(b"SALES", navigation)

    def test_change_password_requires_auth_and_rejects_bad_inputs_without_echoing_passwords(self):
        logged_out = self.client.get("/account/change-password")
        self.assertEqual(logged_out.status_code, 302)

        self._set_identity(self.active_user_id)
        cases = (
            ("WrongCurrent!123", NEW_PASSWORD, NEW_PASSWORD, b"Current password is incorrect."),
            (ACTIVE_PASSWORD, NEW_PASSWORD, "DifferentPassword!789", b"do not match"),
            (ACTIVE_PASSWORD, "short", "short", b"at least 12 characters"),
            (ACTIVE_PASSWORD, ACTIVE_PASSWORD, ACTIVE_PASSWORD, b"must be different"),
        )
        for current_password, new_password, confirmation, expected in cases:
            token = self._csrf_token("/account/change-password")
            response = self.client.post(
                "/account/change-password",
                data={
                    "csrf_token": token,
                    "current_password": current_password,
                    "new_password": new_password,
                    "confirm_password": confirmation,
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn(expected, response.data)
            self.assertNotIn(current_password.encode(), response.data)
            self.assertNotIn(new_password.encode(), response.data)

    def test_successful_password_change_rehashes_and_requires_login_with_new_password(self):
        self._set_identity(self.active_user_id)
        token = self._csrf_token("/account/change-password")
        response = self.client.post(
            "/account/change-password",
            data={
                "csrf_token": token,
                "current_password": ACTIVE_PASSWORD,
                "new_password": NEW_PASSWORD,
                "confirm_password": NEW_PASSWORD,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/login"))

        conn = self._connect()
        try:
            password_hash = conn.execute(
                "SELECT password_hash FROM users WHERE id = ?", (self.active_user_id,)
            ).fetchone()["password_hash"]
        finally:
            conn.close()
        self.assertTrue(check_password_hash(password_hash, NEW_PASSWORD))
        self.assertFalse(check_password_hash(password_hash, ACTIVE_PASSWORD))

        old_login = self._login(password=ACTIVE_PASSWORD)
        self.assertEqual(old_login.status_code, 401)
        self.assertIn(GENERIC_LOGIN_ERROR, old_login.data)
        new_login = self._login(password=NEW_PASSWORD)
        self.assertEqual(new_login.status_code, 302)

    def test_csrf_missing_invalid_form_and_valid_javascript_header_behavior(self):
        missing = self.client.post(
            "/login",
            data={"email": "active@example.test", "password": ACTIVE_PASSWORD},
        )
        invalid = self.client.post(
            "/login",
            data={
                "csrf_token": "not-a-valid-token",
                "email": "active@example.test",
                "password": ACTIVE_PASSWORD,
            },
        )
        for response in (missing, invalid):
            self.assertEqual(response.status_code, 400)
            self.assertIn(b"security token was invalid", response.data)

        self._set_identity(self.active_user_id)
        token = self._csrf_token("/inventory")
        javascript = self.client.post(
            "/auth/logout", headers={"X-CSRFToken": token}
        )
        self.assertEqual(javascript.status_code, 204)
        self.assertEqual(self.client.get("/login").status_code, 200)

    def test_safe_get_does_not_require_csrf_and_cookie_settings_are_securely_configured(self):
        self._set_identity(self.active_user_id)
        self.assertEqual(self.client.get("/inventory").status_code, 200)
        self.assertTrue(self.app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(self.app.config["SESSION_COOKIE_SAMESITE"], "Lax")
        self.assertFalse(self.app.config["SESSION_COOKIE_SECURE"])
        self.assertEqual(self.app.config["SECRET_KEY"], "authentication-test-secret")
        static_response = self.client.get("/static/css/style.css")
        try:
            self.assertNotIn("no-store", static_response.headers.get("Cache-Control", ""))
        finally:
            static_response.close()

        production_app = Flask("production-cookie-test")
        configure_session_security(production_app, "production")
        self.assertTrue(production_app.config["SESSION_COOKIE_SECURE"])

    def test_adjusted_business_role_matrix(self):
        users = {
            "ADMIN": self.admin_user_id,
            "MANAGER": self.manager_user_id,
            "SALES": self.active_user_id,
            "ACCOUNTING": self.accounting_user_id,
        }
        matrix = (
            ("inventory view", "GET", "/inventory", {"ADMIN", "MANAGER", "SALES", "ACCOUNTING"}, None),
            ("inventory import", "POST", "/add-stones", {"ADMIN", "MANAGER"}, "form"),
            ("receiving", "POST", "/receive-stones", {"ADMIN", "MANAGER"}, "json"),
            ("hold", "POST", "/api/holds", {"ADMIN", "MANAGER", "SALES"}, "json"),
            ("memo", "POST", "/create-memo", {"ADMIN", "MANAGER", "SALES"}, "form"),
            (
                "direct invoice",
                "POST",
                "/api/transactions/invoices",
                {"ADMIN", "MANAGER", "ACCOUNTING"},
                "json",
            ),
            ("barcode printing", "GET", "/barcodes", {"ADMIN", "MANAGER", "SALES"}, None),
            ("discount management", "POST", "/upload-discount", {"ADMIN", "MANAGER"}, "form"),
            ("Rapaport management", "POST", "/upload-rapaport", {"ADMIN", "MANAGER"}, "form"),
        )

        for label, method, path, allowed_roles, payload_kind in matrix:
            for role, user_id in users.items():
                with self.subTest(permission=label, role=role):
                    client = self._client_for_user(user_id)
                    if method == "GET":
                        response = client.get(path)
                    else:
                        token = self._client_csrf_token(client)
                        if payload_kind == "json":
                            response = client.post(
                                path,
                                json={},
                                headers={"X-CSRFToken": token},
                            )
                        else:
                            response = client.post(path, data={"csrf_token": token})
                    if role in allowed_roles:
                        self.assertNotEqual(response.status_code, 403)
                    else:
                        self.assertEqual(response.status_code, 403)

    def test_development_and_production_modes_both_require_login(self):
        for mode in ("development", "production"):
            with self.subTest(mode=mode):
                environment = os.environ.copy()
                environment.update(
                    {
                        "DSMS_ENV": mode,
                        "DSMS_SECRET_KEY": "mode-enforcement-test-secret",
                        "DSMS_DB_PATH": self.db_path,
                        "DSMS_TESTING": "1",
                    }
                )
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        (
                            "import sys; sys.path.insert(0, 'backend'); "
                            "from app import app; app.config['TESTING']=True; "
                            "response=app.test_client().get('/inventory'); "
                            "print(response.status_code); print(response.headers.get('Location', ''))"
                        ),
                    ],
                    cwd=PROJECT_ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                lines = result.stdout.strip().splitlines()
                self.assertEqual(lines[0], "302")
                self.assertIn("/login?next=/inventory", lines[1])

    def test_unexpected_authentication_database_error_is_a_safe_page(self):
        self._set_identity(self.active_user_id)
        os.environ["DSMS_DB_PATH"] = self.tempdir.name
        response = self.client.get("/inventory")
        os.environ["DSMS_DB_PATH"] = self.db_path
        self.assertEqual(response.status_code, 503)
        self.assertIn(b"Unable to authenticate", response.data)
        self.assertNotIn(self.tempdir.name.encode(), response.data)

    def test_production_import_rejects_missing_secret(self):
        environment = os.environ.copy()
        environment["DSMS_ENV"] = "production"
        environment.pop("DSMS_SECRET_KEY", None)
        environment["PYTHONPATH"] = str(BACKEND_ROOT)
        result = subprocess.run(
            [sys.executable, "-c", "import app"],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DSMS_SECRET_KEY must be set", result.stderr)
        self.assertNotIn("authentication-test-secret", result.stderr)


class InitialUserProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "provisioning.db")
        init_db(self.db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        self.conn.close()
        self.tempdir.cleanup()

    def test_provisioning_normalizes_email_and_role_and_hashes_password(self):
        provision_user(
            self.conn,
            "  NEW.USER@EXAMPLE.TEST ",
            "New",
            "User",
            " admin ",
            ACTIVE_PASSWORD,
        )
        row = self.conn.execute("SELECT * FROM users").fetchone()
        self.assertEqual(row["email"], "new.user@example.test")
        self.assertEqual(row["role"], "ADMIN")
        self.assertNotEqual(row["password_hash"], ACTIVE_PASSWORD)
        self.assertTrue(check_password_hash(row["password_hash"], ACTIVE_PASSWORD))
        schema = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'"
        ).fetchone()["sql"]
        self.assertNotIn("INVENTORY", schema)

    def test_provisioning_rejects_duplicate_email_and_invalid_role(self):
        provision_user(
            self.conn,
            "duplicate@example.test",
            "Duplicate",
            "User",
            "SALES",
            ACTIVE_PASSWORD,
        )
        with self.assertRaises(UserProvisioningError):
            provision_user(
                self.conn,
                " DUPLICATE@EXAMPLE.TEST ",
                "Duplicate",
                "Again",
                "SALES",
                ACTIVE_PASSWORD,
            )
        with self.assertRaises(UserProvisioningError):
            provision_user(
                self.conn,
                "inventory-role@example.test",
                "Unsupported",
                "Role",
                "INVENTORY",
                ACTIVE_PASSWORD,
            )
        with self.assertRaises(UserProvisioningError):
            provision_user(
                self.conn,
                "role@example.test",
                "Bad",
                "Role",
                "OWNER",
                ACTIVE_PASSWORD,
            )


if __name__ == "__main__":
    unittest.main()
