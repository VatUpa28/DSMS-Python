import os
import secrets

from flask import Flask
from auth import (
    auth_bp,
    configure_authentication,
    configure_session_security,
    csrf,
)
from routes.inventory import inventory_bp
from routes.home import home_bp
from routes.add import add_stone_bp, add_stones_bp
from routes.discount import discount_bp
from routes.upload_rapaport import upload_rapaport_bp
from routes.transactions import transactions_bp
from routes.clients import clients_bp
from routes.contacts import contacts_bp
from routes.shipping import shipping_bp
from routes.barcode import barcode_bp
from routes.api import api_bp
from routes.admin_users import admin_users_bp
from routes.holds import holds_bp


def _environment_flag(name, default):
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a true or false value.")

app = Flask(__name__, template_folder="../frontend/templates", static_folder="../frontend/static")
environment = os.environ.get("DSMS_ENV", "development").strip().lower()
secret_key = os.environ.get("DSMS_SECRET_KEY")
if not secret_key or not secret_key.strip():
    if environment != "development":
        raise RuntimeError(
            "DSMS_SECRET_KEY must be set unless DSMS_ENV is explicitly development."
        )
    # A random per-process key is intentionally limited to local development.
    secret_key = secrets.token_urlsafe(32)

app.config.update(
    SECRET_KEY=secret_key,
    DSMS_ENV=environment,
)
secure_cookie = _environment_flag(
    "DSMS_SESSION_COOKIE_SECURE", environment != "development"
)
if environment != "development" and not secure_cookie:
    raise RuntimeError("Secure session cookies cannot be disabled outside development.")
configure_session_security(
    app,
    environment,
    secure_cookie=secure_cookie,
)
csrf.init_app(app)

app.register_blueprint(home_bp)
app.register_blueprint(inventory_bp)
app.register_blueprint(add_stone_bp)
app.register_blueprint(add_stones_bp)
app.register_blueprint(upload_rapaport_bp)
app.register_blueprint(discount_bp)
app.register_blueprint(clients_bp)
app.register_blueprint(contacts_bp)
app.register_blueprint(shipping_bp)
app.register_blueprint(transactions_bp)
app.register_blueprint(barcode_bp)
app.register_blueprint(api_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(admin_users_bp)
app.register_blueprint(holds_bp)
configure_authentication(app)

if __name__ == "__main__":
    debug = os.environ.get("DSMS_DEBUG", "1" if environment == "development" else "0")
    port = int(os.environ.get("DSMS_PORT", "5000"))
    app.run(
        host=os.environ.get("DSMS_HOST", "127.0.0.1"),
        port=port,
        debug=debug.strip().lower() in {"1", "true", "yes", "on"},
    )
