from flask import Blueprint, request, render_template, redirect, jsonify
from auth import require_roles
from database.db import get_db
import sqlite3

clients_bp = Blueprint("clients", __name__)


@clients_bp.route("/clients", methods=["GET"])
def clients():

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT c.*, COUNT(s.id) AS held_stone_count
        FROM clients c
        LEFT JOIN stones s
          ON s.hold_client_id = c.id AND s.status IN ('H', 'HOLD')
        GROUP BY c.id
        ORDER BY c.name
        """
    )
    clients = cursor.fetchall()

    conn.close()

    return render_template("clients/clients.html", clients=clients)


@clients_bp.route("/clients/create", methods=["GET", "POST"])
@require_roles("ADMIN", "MANAGER", "SALES")
def create_client():

    conn = get_db()
    cursor = conn.cursor()

    try:
        if request.method == "POST":

            cursor.execute("""
                INSERT INTO clients (
                    code,
                    name,
                    address,
                    polygon_id,
                    jbt_id,
                    rapnet_id,
                    tax_id,
                    sales_tax_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                request.form.get("code"),
                request.form.get("name"),
                request.form.get("address"),
                request.form.get("polygon_id"),
                request.form.get("jbt_id"),
                request.form.get("rapnet_id"),
                request.form.get("tax_id"),
                request.form.get("sales_tax_id")
            ))

            conn.commit()
            return redirect("/clients")

        return render_template("clients/create_client.html")

    finally:
        conn.close()

@clients_bp.route("/clients/by-code/<code>")
def get_client_by_code(code):

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM clients WHERE code = ?
    """, (code,))

    client = cursor.fetchone()

    if not client:
        return jsonify({"error": "not found"}), 404

    cursor.execute("""
        SELECT *
        FROM shipping_addresses
        WHERE client_id = ?
    """, (client["id"],))

    addresses = cursor.fetchall()

    conn.close()

    return jsonify({
        "client": dict(client),
        "shipping_addresses": [dict(a) for a in addresses]
    })

@clients_bp.route("/api/clients/<int:client_id>/contacts")
def get_contacts(client_id):

    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT name, phone, email
        FROM client_contacts
        WHERE client_id = ?
    """, (client_id,))

    rows = cursor.fetchall()
    conn.close()

    return jsonify({
        "contacts": [dict(r) for r in rows]
    })
