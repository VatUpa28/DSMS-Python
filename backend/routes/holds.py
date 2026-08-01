"""Role-secured browser and JSON routes for Stone HOLD management."""

from flask import Blueprint, jsonify, render_template, request

from auth import current_user, require_roles
from database.db import get_db
from services.transaction_workflows import (
    WorkflowError,
    place_hold,
    release_hold,
    search_available_stones,
)


holds_bp = Blueprint("holds", __name__)
HOLD_ROLES = {"ADMIN", "MANAGER", "SALES"}


def _normalized_stock_numbers(raw):
    seen = set()
    values = []
    for value in (raw or "").replace(",", " ").split():
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            values.append(normalized)
    if len(values) > 25:
        raise WorkflowError("A maximum of 25 stock numbers may be searched at once")
    return values


def _filters_from_request(include_client=False):
    filters = {
        "stock_numbers": _normalized_stock_numbers(request.args.get("stock_numbers", "")),
        "stock_number": request.args.get("stock_number", "").strip(),
        "lab": request.args.get("lab", "").strip(),
        "shape": request.args.get("shape", "").strip(),
        "min_weight": request.args.get("min_weight", "").strip(),
        "max_weight": request.args.get("max_weight", "").strip(),
        "color": request.args.get("color", "").strip(),
        "clarity": request.args.get("clarity", "").strip(),
        "cut": request.args.get("cut", "").strip(),
        "polish": request.args.get("polish", "").strip(),
        "symmetry": request.args.get("symmetry", "").strip(),
        "fluorescence": request.args.get("fluorescence", "").strip(),
    }
    if include_client:
        filters["client"] = request.args.get("client", "").strip()
    return filters


def _current_holds(conn, filters):
    clauses = ["s.status IN ('H', 'HOLD')"]
    parameters = []
    if filters.get("client"):
        clauses.append("(c.code LIKE ? OR c.name LIKE ?)")
        parameters.extend([f"%{filters['client']}%", f"%{filters['client']}%"])
    if filters.get("stock_number"):
        clauses.append("s.stock_number LIKE ?")
        parameters.append(f"%{filters['stock_number']}%")
    for key, column in (("lab", "g.lab"), ("shape", "g.shape")):
        if filters.get(key):
            clauses.append(f"{column} = ?")
            parameters.append(filters[key])
    parsed = {}
    for key, operator in (("min_weight", ">="), ("max_weight", "<=")):
        raw = filters.get(key)
        if raw:
            try:
                parsed[key] = float(raw)
            except ValueError as exc:
                raise WorkflowError(f"{key} must be a number") from exc
            if parsed[key] < 0:
                raise WorkflowError(f"{key} must not be negative")
            clauses.append(f"g.weight {operator} ?")
            parameters.append(parsed[key])
    if parsed.get("min_weight", 0) > parsed.get("max_weight", float("inf")):
        raise WorkflowError("Minimum weight must not exceed maximum weight")
    return conn.execute(
        f"""
        SELECT s.id, s.stock_number, s.status, s.hold_client_id,
               c.code AS client_code, c.name AS client_name,
               g.report_number, g.lab, g.shape, g.weight, g.color, g.clarity,
               g.price_per_carat, g.total_price
        FROM stones s
        JOIN clients c ON c.id = s.hold_client_id
        LEFT JOIN grading_reports g ON g.stone_id = s.id AND g.active = 1
        WHERE {' AND '.join(clauses)}
        ORDER BY c.name, s.stock_number
        LIMIT 250
        """,
        parameters,
    ).fetchall()


@holds_bp.route("/holds")
@require_roles("ADMIN", "MANAGER", "SALES", "ACCOUNTING")
def holds_page():
    filters = _filters_from_request(include_client=True)
    conn = get_db()
    try:
        holds = _current_holds(conn, filters)
        return render_template(
            "holds/holds.html",
            holds=holds,
            hold_filters=filters,
            can_manage_holds=current_user()["role"] in HOLD_ROLES,
        )
    except WorkflowError as error:
        return render_template(
            "holds/holds.html",
            holds=[],
            hold_filters=filters,
            can_manage_holds=current_user()["role"] in HOLD_ROLES,
            page_error=str(error),
        ), error.status_code
    finally:
        conn.close()


@holds_bp.route("/api/holds/available-stones")
@require_roles("ADMIN", "MANAGER", "SALES")
def available_stones_api():
    try:
        filters = _filters_from_request()
        conn = get_db()
        try:
            stones = search_available_stones(conn, filters)
            return jsonify({"stones": [dict(stone) for stone in stones]})
        finally:
            conn.close()
    except WorkflowError as error:
        return jsonify({"error": str(error)}), error.status_code


@holds_bp.route("/api/holds", methods=["POST"])
@require_roles("ADMIN", "MANAGER", "SALES")
def place_hold_api():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "A JSON object is required"}), 400
    conn = get_db()
    try:
        return jsonify(place_hold(conn, data.get("client_id"), data.get("stone_ids")))
    except WorkflowError as error:
        return jsonify({"error": str(error)}), error.status_code
    finally:
        conn.close()


@holds_bp.route("/api/holds/release", methods=["POST"])
@require_roles("ADMIN", "MANAGER", "SALES")
def release_hold_api():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "A JSON object is required"}), 400
    conn = get_db()
    try:
        return jsonify(release_hold(conn, data.get("stone_ids")))
    except WorkflowError as error:
        return jsonify({"error": str(error)}), error.status_code
    finally:
        conn.close()
