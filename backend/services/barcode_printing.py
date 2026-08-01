"""In-memory Code 128 barcode labels for authenticated print responses."""

from __future__ import annotations

import io

from reportlab.graphics.barcode.code128 import Code128
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen.canvas import Canvas

MAX_BARCODE_LABELS = 100


class BarcodePrintError(ValueError):
    status_code = 400


class BarcodeNotFound(BarcodePrintError):
    status_code = 404


def normalize_stone_ids(raw_ids) -> list[int]:
    """Parse a batch safely, preserving order while normalizing duplicates."""
    if isinstance(raw_ids, str):
        raw_ids = raw_ids.split(",")
    if not isinstance(raw_ids, (list, tuple)):
        raise BarcodePrintError("stone_ids must contain at least one Stone ID")

    ids = []
    seen = set()
    for raw_id in raw_ids:
        value = str(raw_id).strip()
        if not value:
            raise BarcodePrintError("stone_ids must not contain blank values")
        try:
            stone_id = int(value)
        except (TypeError, ValueError) as exc:
            raise BarcodePrintError("stone_ids must contain integer Stone IDs") from exc
        if stone_id <= 0:
            raise BarcodePrintError("stone_ids must contain positive Stone IDs")
        if stone_id not in seen:
            seen.add(stone_id)
            ids.append(stone_id)

    if not ids:
        raise BarcodePrintError("stone_ids must contain at least one Stone ID")
    if len(ids) > MAX_BARCODE_LABELS:
        raise BarcodePrintError(f"A print request may contain at most {MAX_BARCODE_LABELS} Stones")
    return ids


def _validated_stock_number(value) -> str:
    stock_number = "" if value is None else str(value).strip()
    if not stock_number:
        raise BarcodePrintError("A requested Stone has a blank stock number")
    if len(stock_number) > 80 or not stock_number.isascii() or not stock_number.isprintable():
        raise BarcodePrintError("A requested Stone has an invalid stock number")
    return stock_number


def load_printable_stones(cursor, stone_ids):
    ids = normalize_stone_ids(stone_ids)
    placeholders = ",".join("?" for _ in ids)
    rows = cursor.execute(
        f"SELECT id, stock_number FROM stones WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    by_id = {row["id"]: row for row in rows}
    missing = [stone_id for stone_id in ids if stone_id not in by_id]
    if missing:
        raise BarcodeNotFound("One or more requested Stones were not found")
    return [
        {"id": stone_id, "stock_number": _validated_stock_number(by_id[stone_id]["stock_number"])}
        for stone_id in ids
    ]


def build_barcode_pdf(stones) -> io.BytesIO:
    """Render all labels into one PDF buffer without writing any file."""
    pdf_buffer = io.BytesIO()
    page_width, page_height = letter
    canvas = Canvas(pdf_buffer, pagesize=letter, pageCompression=1)
    margin = 36
    label_height = 72
    label_width = page_width - (margin * 2)
    y = page_height - margin

    for stone in stones:
        stock_number = _validated_stock_number(stone["stock_number"])
        if y - label_height < margin:
            canvas.showPage()
            y = page_height - margin

        bottom = y - label_height
        canvas.roundRect(margin, bottom, label_width, label_height - 6, 4, stroke=1, fill=0)
        canvas.setFont("Helvetica-Bold", 10)
        canvas.drawString(margin + 12, y - 16, stock_number)

        barcode = Code128(stock_number, barHeight=28, barWidth=0.42, humanReadable=False)
        barcode_x = margin + max(12, (label_width - barcode.width) / 2)
        barcode.drawOn(canvas, barcode_x, bottom + 12)
        y = bottom - 6

    canvas.save()
    pdf_buffer.seek(0)
    return pdf_buffer
