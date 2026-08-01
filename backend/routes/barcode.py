from flask import Blueprint, render_template, request, send_file

from auth import require_roles
from database.db import get_db
from services.barcode_printing import BarcodePrintError, build_barcode_pdf, load_printable_stones

barcode_bp = Blueprint("barcode", __name__)
PRINT_ROLES = ("ADMIN", "MANAGER", "SALES")


@barcode_bp.route("/barcodes")
@require_roles(*PRINT_ROLES)
def barcodes():
    conn = get_db()
    try:
        stones = conn.execute(
            "SELECT id, stock_number FROM stones ORDER BY id ASC"
        ).fetchall()
        return render_template("barcodes.html", stones=stones)
    finally:
        conn.close()


def _barcode_pdf_response(raw_ids):
    conn = get_db()
    try:
        stones = load_printable_stones(conn.cursor(), raw_ids)
        pdf_buffer = build_barcode_pdf(stones)
    finally:
        conn.close()

    response = send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=False,
        download_name="barcode-labels.pdf",
        max_age=0,
    )
    response.headers["Cache-Control"] = "no-store, private, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@barcode_bp.route("/barcodes/<int:stone_id>/pdf")
@require_roles(*PRINT_ROLES)
def print_one(stone_id):
    try:
        return _barcode_pdf_response([stone_id])
    except BarcodePrintError as error:
        return str(error), error.status_code


@barcode_bp.route("/barcodes/pdf", methods=["POST"])
@require_roles(*PRINT_ROLES)
def generate_pdf():
    try:
        payload = request.get_json(silent=True) or request.form
        return _barcode_pdf_response(payload.get("stone_ids", []))
    except BarcodePrintError as error:
        return str(error), error.status_code
