from flask import Blueprint, redirect, url_for
from auth import current_user

home_bp = Blueprint("home", __name__)


@home_bp.route("/")
def home():
    if current_user() is None:
        return redirect(url_for("auth.login"))
    return redirect(url_for("inventory.inventory"))
