"""ADMIN-only browser interface for managing DSMS user accounts."""

from __future__ import annotations

from contextlib import closing
import sqlite3

from flask import Blueprint, flash, redirect, render_template, request, url_for

from auth import current_user_id, require_roles
from database.create_user import UserProvisioningError, provision_user
from database.db import get_db
from services.user_management import (
    UserManagementError,
    change_user_role,
    deactivate_user,
    reactivate_user,
)


admin_users_bp = Blueprint("admin_users", __name__)
ROLE_OPTIONS = ("ADMIN", "MANAGER", "SALES", "ACCOUNTING")


@admin_users_bp.get("/admin/users")
@require_roles("ADMIN")
def user_list():
    with closing(get_db()) as connection:
        users = connection.execute(
            """
            SELECT id, email, first_name, last_name, role, active, created_at
            FROM users
            ORDER BY active DESC, LOWER(last_name), LOWER(first_name), LOWER(email)
            """
        ).fetchall()
    return render_template(
        "admin/users.html",
        users=users,
        role_options=ROLE_OPTIONS,
        actor_user_id=current_user_id(),
    )


@admin_users_bp.route("/admin/users/new", methods=["GET", "POST"])
@require_roles("ADMIN")
def new_user():
    values = {
        "email": "",
        "first_name": "",
        "last_name": "",
        "role": "SALES",
    }
    if request.method == "POST":
        values = {
            "email": request.form.get("email", "").strip(),
            "first_name": request.form.get("first_name", "").strip(),
            "last_name": request.form.get("last_name", "").strip(),
            "role": request.form.get("role", "").strip().upper(),
        }
        password = request.form.get("password", "")
        confirmation = request.form.get("confirm_password", "")
        try:
            if password != confirmation:
                raise UserProvisioningError("New password and confirmation must match.")
            with closing(get_db()) as connection:
                provision_user(
                    connection,
                    email=values["email"],
                    first_name=values["first_name"],
                    last_name=values["last_name"],
                    role=values["role"],
                    password=password,
                )
        except UserProvisioningError as exc:
            flash(str(exc), "danger")
        except sqlite3.Error:
            flash("The user could not be created. Please try again.", "danger")
        else:
            flash(
                "User created. Share the temporary password securely; "
                "it will not be shown again.",
                "success",
            )
            return redirect(url_for("admin_users.user_list"))

    return render_template(
        "admin/new_user.html",
        values=values,
        role_options=ROLE_OPTIONS,
    )


def _mutation_response(action, success_message: str, unchanged_message: str):
    try:
        changed = action()
    except (UserManagementError, UserProvisioningError) as exc:
        flash(str(exc), "danger")
    except sqlite3.Error:
        flash("The user could not be updated. Please try again.", "danger")
    else:
        flash(success_message if changed else unchanged_message, "success")
    return redirect(url_for("admin_users.user_list"))


@admin_users_bp.post("/admin/users/<int:user_id>/role")
@require_roles("ADMIN")
def update_role(user_id: int):
    def action():
        with closing(get_db()) as connection:
            return change_user_role(
                connection,
                target_user_id=user_id,
                new_role=request.form.get("role", ""),
                actor_user_id=current_user_id(),
            )

    return _mutation_response(
        action,
        "User role updated.",
        "The user already has that role.",
    )


@admin_users_bp.post("/admin/users/<int:user_id>/deactivate")
@require_roles("ADMIN")
def deactivate(user_id: int):
    def action():
        with closing(get_db()) as connection:
            return deactivate_user(
                connection,
                target_user_id=user_id,
                actor_user_id=current_user_id(),
            )

    return _mutation_response(
        action,
        "User deactivated.",
        "The user is already inactive.",
    )


@admin_users_bp.post("/admin/users/<int:user_id>/reactivate")
@require_roles("ADMIN")
def reactivate(user_id: int):
    def action():
        with closing(get_db()) as connection:
            return reactivate_user(connection, target_user_id=user_id)

    return _mutation_response(
        action,
        "User reactivated.",
        "The user is already active.",
    )
