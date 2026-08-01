from flask import Blueprint, request, redirect, url_for
from auth import require_roles
from database.db import get_db
from utils.process_rapaport_file import process_rapaport_file
from services.stone_service import recalculate_stone_price

upload_rapaport_bp = Blueprint("upload_rapaport", __name__)

@upload_rapaport_bp.route("/upload-rapaport", methods=["POST"])
@require_roles("ADMIN", "MANAGER")
def upload_rapaport():
    conn = get_db()

    try:
        cursor = conn.cursor()
        round_file = request.files["round_file"]
        pear_file = request.files["pear_file"]

        cursor.execute("DELETE FROM rapaport_prices")

        process_rapaport_file(round_file, cursor)
        process_rapaport_file(pear_file, cursor)

        cursor.execute("SELECT * FROM stones")
        stones = cursor.fetchall()

        for stone in stones:
            recalculate_stone_price(cursor, stone)

        conn.commit()
        return redirect(url_for("inventory.inventory"))

    finally:
        conn.close()
