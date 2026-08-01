"""Transactional business rules for administrator-managed user accounts."""

from __future__ import annotations

import sqlite3

from database.create_user import normalize_role


SELF_ADMIN_MESSAGE = (
    "You cannot remove your own administrator access while signed in."
)
LAST_ADMIN_MESSAGE = (
    "DSMS must always have at least one active administrator."
)


class UserManagementError(ValueError):
    """A safe, user-facing validation error."""


def _load_user(connection: sqlite3.Connection, user_id: int) -> sqlite3.Row:
    user = connection.execute(
        """
        SELECT id, email, role, active, password_hash
        FROM users
        WHERE id = ?
        """,
        (user_id,),
    ).fetchone()
    if user is None:
        raise UserManagementError("The selected user no longer exists.")
    return user


def _active_admin_count(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM users
        WHERE active = 1 AND UPPER(role) = 'ADMIN'
        """
    ).fetchone()
    return int(row["count"])


def change_user_role(
    connection: sqlite3.Connection,
    *,
    target_user_id: int,
    new_role: str,
    actor_user_id: int,
) -> bool:
    """Change a role atomically, returning False when no change was needed."""

    role = normalize_role(new_role)
    try:
        connection.execute("BEGIN IMMEDIATE")
        user = _load_user(connection, target_user_id)
        old_role = str(user["role"]).upper()

        if (
            bool(user["active"])
            and old_role == "ADMIN"
            and role != "ADMIN"
            and _active_admin_count(connection) <= 1
        ):
            raise UserManagementError(LAST_ADMIN_MESSAGE)
        if target_user_id == actor_user_id and old_role == "ADMIN" and role != "ADMIN":
            raise UserManagementError(SELF_ADMIN_MESSAGE)
        if old_role == role:
            connection.commit()
            return False

        connection.execute(
            """
            UPDATE users
            SET role = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (role, target_user_id),
        )
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise


def deactivate_user(
    connection: sqlite3.Connection,
    *,
    target_user_id: int,
    actor_user_id: int,
) -> bool:
    """Deactivate a user without deleting historical references."""

    try:
        connection.execute("BEGIN IMMEDIATE")
        user = _load_user(connection, target_user_id)
        if not bool(user["active"]):
            connection.commit()
            return False
        if (
            str(user["role"]).upper() == "ADMIN"
            and _active_admin_count(connection) <= 1
        ):
            raise UserManagementError(LAST_ADMIN_MESSAGE)
        if target_user_id == actor_user_id:
            raise UserManagementError(SELF_ADMIN_MESSAGE)

        connection.execute(
            """
            UPDATE users
            SET active = 0, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (target_user_id,),
        )
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise


def reactivate_user(
    connection: sqlite3.Connection,
    *,
    target_user_id: int,
) -> bool:
    """Reactivate an account while preserving its role and password hash."""

    try:
        connection.execute("BEGIN IMMEDIATE")
        user = _load_user(connection, target_user_id)
        normalize_role(user["role"])
        if bool(user["active"]):
            connection.commit()
            return False

        connection.execute(
            """
            UPDATE users
            SET active = 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (target_user_id,),
        )
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise
