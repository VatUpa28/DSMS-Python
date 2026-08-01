"""Create a Flask V1 user without placing a password in shell history."""

from __future__ import annotations

import argparse
from getpass import getpass
import re
import sqlite3

from werkzeug.security import generate_password_hash

from auth import MIN_PASSWORD_LENGTH, VALID_ROLES
from database.db import get_db


class UserProvisioningError(ValueError):
    pass


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_role(value):
    role = (value or "").strip().upper()
    if role not in VALID_ROLES:
        raise UserProvisioningError("Invalid role")
    return role


def provision_user(conn, email, first_name, last_name, role, password):
    normalized_email = (email or "").strip().lower()
    normalized_role = normalize_role(role)
    if not EMAIL_PATTERN.fullmatch(normalized_email):
        raise UserProvisioningError("A valid email is required")
    if not (first_name or "").strip() or not (last_name or "").strip():
        raise UserProvisioningError("First and last name are required")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise UserProvisioningError(
            f"Password must contain at least {MIN_PASSWORD_LENGTH} characters"
        )

    try:
        conn.execute("BEGIN IMMEDIATE")
        duplicate = conn.execute(
            "SELECT 1 FROM users WHERE LOWER(email) = ?",
            (normalized_email,),
        ).fetchone()
        if duplicate is not None:
            raise UserProvisioningError("An account with that email already exists")
        cursor = conn.execute(
            """
            INSERT INTO users (email, password_hash, first_name, last_name, role)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                normalized_email,
                generate_password_hash(password),
                first_name.strip(),
                last_name.strip(),
                normalized_role,
            ),
        )
        conn.commit()
    except UserProvisioningError:
        conn.rollback()
        raise
    except sqlite3.IntegrityError as error:
        conn.rollback()
        raise UserProvisioningError("An account with that email already exists") from error
    return cursor.lastrowid


def parse_args():
    parser = argparse.ArgumentParser(description="Create an active DSMS Flask user")
    parser.add_argument("--email", required=True)
    parser.add_argument("--first-name", required=True)
    parser.add_argument("--last-name", required=True)
    parser.add_argument(
        "--role", required=True, type=normalize_role, choices=sorted(VALID_ROLES)
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    password = getpass("Password: ")
    confirmation = getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")

    conn = get_db()
    try:
        provision_user(
            conn,
            args.email,
            args.first_name,
            args.last_name,
            args.role,
            password,
        )
    except UserProvisioningError as error:
        raise SystemExit(str(error)) from error
    finally:
        conn.close()
    print(f"Created {args.role} user for {args.email.strip().lower()}.")
