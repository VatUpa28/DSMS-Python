from flask import Blueprint, request, render_template, redirect
from auth import require_roles
from database.db import get_db

shipping_bp = Blueprint("shipping", __name__)


@shipping_bp.route("/clients/<int:client_id>/shipping")
def shipping(client_id):

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM shipping_addresses
        WHERE client_id = ?
    """, (client_id,))

    addresses = cursor.fetchall()

    conn.close()

    return render_template("shipping/shipping.html", addresses=addresses, client_id=client_id)


@shipping_bp.route("/clients/<int:client_id>/shipping/create", methods=["POST"])
@require_roles("ADMIN", "MANAGER", "SALES")
def create_shipping(client_id):

    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO shipping_addresses
            (client_id, label, manager, store_number, address, city, state, country, phone)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            client_id,
            request.form.get("label"),
            request.form.get("manager"),
            request.form.get("store_number"),
            request.form.get("address"),
            request.form.get("city"),
            request.form.get("state"),
            request.form.get("country"),
            request.form.get("phone")
        ))

        conn.commit()
        return redirect(f"/clients/{client_id}/shipping")

    finally:
        conn.close()
