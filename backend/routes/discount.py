from flask import Blueprint, request, redirect, url_for
from auth import require_roles
from services.apply_discount_row import apply_discount_row
from database.db import get_db
import csv
import io
import sqlite3

from services.pricing_service import recalculate_stone_price

discount_bp = Blueprint("discount", __name__)

@discount_bp.route("/upload-discount", methods=["POST"])
@require_roles("ADMIN", "MANAGER")
def upload_discount():

    file = request.files.get("file")
    mode = request.form.get("mode")  # set / offset

    if not file:
        return redirect(url_for("inventory.inventory"))

    text = file.read().decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))

    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:

        for row in rows:
            apply_discount_row(cursor, row, mode)

        cursor.execute("SELECT * FROM stones")

        for stone in cursor.fetchall():
            recalculate_stone_price(cursor, stone)

        conn.commit()

    finally:
        conn.close()

    return redirect(url_for("inventory.inventory"))
