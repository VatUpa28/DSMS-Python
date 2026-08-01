from flask import Blueprint, request, render_template, redirect, url_for
from auth import require_roles
from database.db import get_db

contacts_bp = Blueprint("contacts", __name__)


@contacts_bp.route("/clients/<int:client_id>/contacts")
def contacts(client_id):

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM client_contacts
        WHERE client_id = ?
    """, (client_id,))

    contacts = cursor.fetchall()

    conn.close()

    return render_template("contacts/contacts.html", contacts=contacts, client_id=client_id)


@contacts_bp.route("/clients/<int:client_id>/contacts/create", methods=["POST"])
@require_roles("ADMIN", "MANAGER", "SALES")
def create_contact(client_id):

    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO client_contacts
            (client_id, name, phone, email, fax, cell)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            client_id,
            request.form.get("name"),
            request.form.get("phone"),
            request.form.get("email"),
            request.form.get("fax"),
            request.form.get("cell")
        ))

        conn.commit()
        conn.commit()
        return redirect(
            url_for("contacts.contacts", client_id=client_id)
        )

    finally:
        conn.close()
