"""Session authentication, browser login pages, and authorization helpers."""

from __future__ import annotations

from datetime import timedelta
from functools import wraps
import sqlite3
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.security import check_password_hash, generate_password_hash

from database.db import get_db

auth_bp = Blueprint("auth", __name__)
csrf = CSRFProtect()

VALID_ROLES = {"ADMIN", "MANAGER", "SALES", "ACCOUNTING"}
MIN_PASSWORD_LENGTH = 12
PUBLIC_ENDPOINTS = {
    "auth.login",
    "auth.login_api",
    "health.health",
    "home.home",
    "static",
}


class AuthenticationRequired(Exception):
    pass


class AuthorizationDenied(Exception):
    pass


class AuthenticationUnavailable(Exception):
    pass


def _request_expects_json():
    endpoint = request.endpoint or ""
    return (
        request.is_json
        or request.path.startswith("/api/")
        or request.path.startswith("/auth/")
        or request.path.startswith("/clients/by-code/")
        or request.path == "/receive-stones"
        or endpoint == "barcode.generate_pdf" and request.method != "GET"
    )


def _safe_next_url(candidate):
    """Return a local application path, or None for an unsafe redirect target."""
    if not candidate or not isinstance(candidate, str):
        return None
    candidate = candidate.strip()
    if (
        not candidate.startswith("/")
        or candidate.startswith("//")
        or "\\" in candidate
        or any(ord(character) < 32 for character in candidate)
    ):
        return None
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc:
        return None
    return candidate


def _current_request_path():
    path = request.full_path if request.query_string else request.path
    return _safe_next_url(path) or url_for("inventory.inventory")


def _load_current_user():
    if hasattr(g, "auth_user"):
        return g.auth_user

    g.auth_user = None
    if "user_id" not in session:
        return None

    user_id = session.get("user_id")
    if not isinstance(user_id, int):
        session.pop("user_id", None)
        session.pop("_permanent", None)
        g.invalid_auth_session = True
        return None

    conn = None
    try:
        conn = get_db()
        row = conn.execute(
            """
            SELECT id, email, first_name, last_name, role, active
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()
    except sqlite3.Error as error:
        g.auth_user = None
        raise AuthenticationUnavailable() from error
    finally:
        if conn is not None:
            conn.close()

    if row is None or not row["active"] or row["role"] not in VALID_ROLES:
        session.pop("user_id", None)
        session.pop("_permanent", None)
        g.invalid_auth_session = True
        return None

    g.auth_user = {
        "id": row["id"],
        "email": row["email"],
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "role": row["role"],
    }
    return g.auth_user


def current_user():
    """Return server-loaded identity data for this request."""
    return _load_current_user()


def _template_user():
    user = current_user()
    if user is None:
        return None
    display_name = " ".join(
        value for value in (user["first_name"], user["last_name"]) if value
    ).strip()
    return {
        "display_name": display_name or user["email"],
        "email": user["email"],
        "role": user["role"],
    }


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            raise AuthenticationRequired()
        return view(*args, **kwargs)

    return wrapped


def require_roles(*allowed_roles):
    """Require a current active user whose server-loaded role is allowed."""
    allowed = {role.strip().upper() for role in allowed_roles}
    if not allowed.issubset(VALID_ROLES):
        raise ValueError("Unknown role configured for route")

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if user is None:
                raise AuthenticationRequired()
            if user["role"] not in allowed:
                raise AuthorizationDenied()
            return view(*args, **kwargs)

        return wrapped

    return decorator


def current_user_id() -> int:
    user = current_user()
    if user is None:
        raise AuthenticationRequired()
    return user["id"]


def _find_login_user(email):
    conn = get_db()
    try:
        return conn.execute(
            """
            SELECT id, email, password_hash, first_name, last_name, role, active
            FROM users
            WHERE email = ?
            """,
            (email,),
        ).fetchone()
    finally:
        conn.close()


def _authenticate(email, password):
    try:
        user = _find_login_user(email)
    except sqlite3.Error:
        return None, True

    valid = (
        user is not None
        and bool(user["active"])
        and user["role"] in VALID_ROLES
        and check_password_hash(user["password_hash"], password)
    )
    return (user if valid else None), False


def _establish_session(user):
    session.clear()
    session["user_id"] = user["id"]
    session.permanent = False


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user() is not None:
        return redirect(url_for("inventory.inventory"))

    next_url = _safe_next_url(request.values.get("next"))
    email = (request.form.get("email") or "").strip().lower()
    error = None

    if request.method == "POST":
        password = request.form.get("password") or ""
        if not email or not password:
            error = "Email and password are required."
        else:
            user, unavailable = _authenticate(email, password)
            if unavailable:
                error = "Unable to sign in right now. Please try again."
            elif user is None:
                error = "Invalid email or password."
            else:
                _establish_session(user)
                return redirect(next_url or url_for("inventory.inventory"))

    return (
        render_template("auth/login.html", email=email, error=error, next_url=next_url),
        401 if request.method == "POST" and error else 200,
    )


@auth_bp.route("/auth/login", methods=["POST"])
def login_api():
    payload = request.get_json(silent=True) or request.form
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    if not email or not password:
        return jsonify({"error": "Email and password are required."}), 400

    user, unavailable = _authenticate(email, password)
    if unavailable:
        return jsonify({"error": "Unable to sign in right now. Please try again."}), 503
    if user is None:
        return jsonify({"error": "Invalid email or password."}), 401

    _establish_session(user)
    return jsonify(
        {
            "email": user["email"],
            "first_name": user["first_name"],
            "last_name": user["last_name"],
            "role": user["role"],
        }
    )


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/auth/logout", methods=["POST"])
@login_required
def logout_api():
    session.clear()
    return ("", 204)


@auth_bp.route("/account/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    error = None
    if request.method == "POST":
        current_password = request.form.get("current_password") or ""
        new_password = request.form.get("new_password") or ""
        confirmation = request.form.get("confirm_password") or ""

        if not current_password or not new_password or not confirmation:
            error = "All password fields are required."
        elif len(new_password) < MIN_PASSWORD_LENGTH:
            error = f"New password must contain at least {MIN_PASSWORD_LENGTH} characters."
        elif new_password != confirmation:
            error = "New password and confirmation do not match."
        else:
            conn = get_db()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT password_hash FROM users WHERE id = ? AND active = 1",
                    (current_user_id(),),
                ).fetchone()
                if row is None or not check_password_hash(
                    row["password_hash"], current_password
                ):
                    error = "Current password is incorrect."
                elif check_password_hash(row["password_hash"], new_password):
                    error = "New password must be different from the current password."
                else:
                    conn.execute(
                        """
                        UPDATE users
                        SET password_hash = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (generate_password_hash(new_password), current_user_id()),
                    )
                    conn.commit()
                if error is not None:
                    conn.rollback()
            except sqlite3.Error:
                conn.rollback()
                error = "Unable to change the password right now. Please try again."
            finally:
                conn.close()

            if error is None:
                session.clear()
                flash("Password changed. Sign in with your new password.", "success")
                return redirect(url_for("auth.login"))

    return render_template("auth/change_password.html", error=error)


@auth_bp.route("/access-denied")
@login_required
def access_denied():
    return render_template("errors/access_denied.html"), 403


def configure_authentication(app):
    """Register request identity, access handling, CSRF errors, and safe caching."""

    @app.before_request
    def load_identity_and_require_login():
        if request.endpoint == "static":
            return None
        user = current_user()
        if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
            return None
        if user is None:
            raise AuthenticationRequired()
        return None

    @app.context_processor
    def authentication_template_context():
        return {
            "current_user": _template_user(),
            "known_roles": tuple(sorted(VALID_ROLES)),
        }

    @app.errorhandler(AuthenticationRequired)
    def authentication_required(_error):
        if _request_expects_json():
            return jsonify({"error": "Authentication required"}), 401
        if getattr(g, "invalid_auth_session", False):
            flash("Your session is no longer valid. Please sign in again.", "warning")
        return redirect(url_for("auth.login", next=_current_request_path()))

    @app.errorhandler(AuthorizationDenied)
    def authorization_denied(_error):
        if _request_expects_json():
            return jsonify({"error": "You do not have permission for this action"}), 403
        return render_template("errors/access_denied.html"), 403

    @app.errorhandler(AuthenticationUnavailable)
    def authentication_unavailable(_error):
        message = "Authentication is temporarily unavailable. Please try again."
        if _request_expects_json():
            return jsonify({"error": message}), 503
        return render_template("errors/authentication_error.html", message=message), 503

    @app.errorhandler(CSRFError)
    def csrf_error(_error):
        message = "Your form session expired or the security token was invalid. Please try again."
        if _request_expects_json():
            return jsonify({"error": message}), 400
        return render_template("errors/csrf_error.html", message=message), 400

    @app.after_request
    def private_page_cache_control(response):
        if (
            getattr(g, "auth_user", None) is not None
            or request.blueprint == "auth"
            or response.status_code in {401, 403, 503}
        ):
            response.headers["Cache-Control"] = "no-store, private"
            response.headers["Pragma"] = "no-cache"
        return response


def configure_session_security(app, environment, secure_cookie=None):
    secure_default = environment != "development"
    app.config.update(
        SESSION_COOKIE_NAME="dsms_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=secure_default if secure_cookie is None else secure_cookie,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    )
