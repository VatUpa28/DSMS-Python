from flask import Blueprint, jsonify, render_template, request
from auth import login_required, require_roles, current_user
from database.db import get_db

inventory_bp = Blueprint("inventory", __name__)


def _context_filters():
    raw_numbers = request.args.get("stock_numbers", "")
    numbers = []
    seen = set()
    for value in raw_numbers.replace(",", " ").split():
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            numbers.append(value)
    if len(numbers) > 25:
        raise ValueError("A maximum of 25 stock numbers may be searched at once")
    minimum = request.args.get("min_weight", "").strip()
    maximum = request.args.get("max_weight", "").strip()
    try:
        if minimum and maximum and float(minimum) > float(maximum):
            raise ValueError("Minimum weight must not exceed maximum weight")
    except ValueError as error:
        if str(error).startswith("Minimum"):
            raise
        raise ValueError("Weight filters must be valid numbers") from error
    return {
        "stock_numbers": numbers,
        "stock_number": request.args.get("stock_number", "").strip(),
        "memo_number": request.args.get("memo_number", "").strip(),
        "lab": request.args.get("lab", "").strip(),
        "shape": request.args.get("shape", "").strip(),
        "min_weight": minimum,
        "max_weight": maximum,
        "color": request.args.get("color", "").strip(),
        "clarity": request.args.get("clarity", "").strip(),
        "cut": request.args.get("cut", "").strip(),
        "polish": request.args.get("polish", "").strip(),
        "symmetry": request.args.get("symmetry", "").strip(),
        "fluorescence": request.args.get("fluorescence", "").strip(),
    }


def _append_filters(sql, params, filters, alias, stock_alias=None):
    stock_alias = stock_alias or alias
    if filters["stock_numbers"]:
        marks = ",".join("?" for _ in filters["stock_numbers"])
        sql += f" AND {stock_alias}.stock_number IN ({marks})"
        params.extend(filters["stock_numbers"])
    if filters["stock_number"]:
        sql += f" AND {stock_alias}.stock_number LIKE ?"
        params.append(f"%{filters['stock_number']}%")
    for field in ("lab", "shape", "color", "clarity", "cut", "polish", "symmetry"):
        if filters[field]:
            sql += f" AND {alias}.{field} = ?"
            params.append(filters[field])
    if filters["fluorescence"]:
        sql += f" AND {alias}.fluorescence_intensity = ?"
        params.append(filters["fluorescence"])
    if filters["min_weight"]:
        sql += f" AND {alias}.weight >= ?"
        params.append(filters["min_weight"])
    if filters["max_weight"]:
        sql += f" AND {alias}.weight <= ?"
        params.append(filters["max_weight"])
    return sql


@inventory_bp.route("/api/inventory/client-context/<int:client_id>")
@require_roles("ADMIN", "MANAGER", "SALES", "ACCOUNTING")
def client_inventory_context(client_id):
    try:
        filters = _context_filters()
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    conn = get_db()
    try:
        client = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
        if client is None:
            return jsonify({"error": "Selected client does not exist"}), 404
        contacts = conn.execute(
            "SELECT id, name, phone, email, fax, cell FROM client_contacts WHERE client_id = ? ORDER BY name, id",
            (client_id,),
        ).fetchall()
        addresses = conn.execute(
            """SELECT id, label, manager, store_number, address, city, state, country, phone
               FROM shipping_addresses WHERE client_id = ? ORDER BY label, id""",
            (client_id,),
        ).fetchall()
        base = """
            SELECT s.id, s.stock_number, s.status, s.hold_client_id,
                   gr.report_number, gr.lab, gr.shape, gr.weight, gr.color, gr.clarity,
                   gr.cut, gr.polish, gr.symmetry, gr.fluorescence_intensity,
                   gr.price_per_carat, gr.total_price
            FROM stones s
            LEFT JOIN grading_reports gr ON gr.id = (
              SELECT g.id FROM grading_reports g WHERE g.stone_id=s.id AND g.active=1
              ORDER BY g.id DESC LIMIT 1)
            WHERE {condition}
        """
        available_params = []
        available_sql = _append_filters(base.format(condition="s.status IN ('Y','AVAILABLE')"), available_params, filters, "gr", "s")
        held_params = [client_id]
        held_sql = _append_filters(
            base.format(condition="s.status IN ('H','HOLD') AND s.hold_client_id = ?"),
            held_params, filters, "gr", "s")
        available = conn.execute(available_sql + " ORDER BY s.stock_number LIMIT 200", available_params).fetchall()
        held = conn.execute(held_sql + " ORDER BY s.stock_number LIMIT 200", held_params).fetchall()

        memo_params = [client_id]
        memo_sql = """
            SELECT ti.*, t.id AS memo_id, t.transaction_number AS memo_number,
                   t.date AS memo_date, t.status AS memo_status, t.client_id AS source_client_id
            FROM transactions t
            JOIN transaction_items ti ON ti.transaction_id = t.id
            JOIN stones s ON s.id = ti.stone_id
            WHERE t.client_id = ? AND t.type = 'memo' AND t.status = 'active'
              AND ti.status = 'active' AND s.status IN ('M','MEMO')
        """
        memo_sql = _append_filters(memo_sql, memo_params, filters, "ti")
        if filters["memo_number"]:
            memo_sql += " AND t.transaction_number LIKE ?"
            memo_params.append(f"%{filters['memo_number']}%")
        memo_items = conn.execute(memo_sql + " ORDER BY t.date, t.id, ti.stock_number LIMIT 300", memo_params).fetchall()
        groups = []
        by_memo = {}
        for row in memo_items:
            group = by_memo.get(row["memo_id"])
            if group is None:
                group = {"memo_id": row["memo_id"], "memo_number": row["memo_number"],
                         "memo_date": row["memo_date"], "memo_status": row["memo_status"], "items": []}
                by_memo[row["memo_id"]] = group
                groups.append(group)
            group["items"].append(dict(row))
        active_count = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE client_id=? AND type='memo' AND status='active'",
            (client_id,),).fetchone()[0]
        held_count = conn.execute(
            "SELECT COUNT(*) FROM stones WHERE hold_client_id=? AND status IN ('H','HOLD')",
            (client_id,),).fetchone()[0]
        return jsonify({
            "client": dict(client), "contacts": [dict(row) for row in contacts],
            "shipping_addresses": [dict(row) for row in addresses],
            "active_memo_count": active_count, "held_stone_count": held_count,
            "available_stones": [dict(row) for row in available],
            "held_stones": [dict(row) for row in held], "memo_groups": groups,
        })
    finally:
        conn.close()


@inventory_bp.route("/inventory", methods=["GET"])
@login_required
def inventory():
    conn = get_db()
    try:
        cursor = conn.cursor()

        filters = {
            "shape": request.args.getlist("shape"),
            "size": request.args.getlist("size"),
            "color": request.args.getlist("color"),
            "clarity": request.args.getlist("clarity"),
            "lab": request.args.getlist("lab"),
            "cut": request.args.getlist("cut"),
            "polish": request.args.getlist("polish"),
            "symmetry": request.args.getlist("symmetry"),
            "fluorescence_intensity": request.args.getlist("fluorescence_intensity"),
            "status": request.args.getlist("status"),
        }

        filter_columns = {
            "shape": "grading_reports.shape",
            "size": "grading_reports.size",
            "color": "grading_reports.color",
            "clarity": "grading_reports.clarity",
            "lab": "grading_reports.lab",
            "cut": "grading_reports.cut",
            "polish": "grading_reports.polish",
            "symmetry": "grading_reports.symmetry",
            "fluorescence_intensity": "grading_reports.fluorescence_intensity",
            "status": "stones.status",
        }

        ranges = {
            "weight": (request.args.get("weight_min"), request.args.get("weight_max")),
            "depth_percent": (request.args.get("depth_min"), request.args.get("depth_max")),
            "table_percent": (request.args.get("table_min"), request.args.get("table_max")),
            "price_per_carat": (request.args.get("ppc_min"), request.args.get("ppc_max")),
            "total_price": (request.args.get("total_min"), request.args.get("total_max")),
            "rapaport_discount": (request.args.get("discount_min"), request.args.get("discount_max"))
        }

        query = """
            SELECT
                stones.*,
                grading_reports.*,
                hold_client.code AS hold_client_code,
                hold_client.name AS hold_client_name

            FROM stones
            LEFT JOIN grading_reports
                ON grading_reports.stone_id = stones.id
            LEFT JOIN clients hold_client
                ON hold_client.id = stones.hold_client_id
            WHERE 1=1
        """

        params = []

        stock_numbers = request.args.get("stock_numbers", "").strip()

        if stock_numbers:
            stock_list = [
                s.strip()
                for s in stock_numbers.splitlines()
                if s.strip()
            ]

            if stock_list:
                placeholders = ",".join(["?"] * len(stock_list))

                query += f"""
                    AND stones.stock_number IN ({placeholders})
                """

                params.extend(stock_list)

        for field, values in filters.items():
            if values:
                placeholders = ",".join(["?"] * len(values))

                query += f" AND {filter_columns[field]} IN ({placeholders})"

                params.extend(values)

        for field, (min_value, max_value) in ranges.items():
            
            if min_value:
                query += f" AND grading_reports.{field} >= ?"
                params.append(min_value)

            if max_value:
                query += f" AND grading_reports.{field} <= ?"
                params.append(max_value)

        query += " ORDER BY stones.id"

        cursor.execute(query, params)

        stones = cursor.fetchall()

        rows = [dict(r) for r in stones]

        display_columns = [
            "stock_number",
            "status",
            "hold_client",
            "shape",
            "size",
            "weight",
            "color",
            "clarity",
            "rapaport_price_per_carat",
            "rapaport_discount",
            "price_per_carat",
            "total_price", 
            "lab", 
            "cut", 
            "polish", 
            "symmetry", 
            "fluorescence_intensity", 
            "fluorescence_color", 
            "depth_percent", 
            "table_percent", 
            "measurements", 
            "report_number", 
            "fancy_color", 
            "fancy_intensity", 
            "overtone", 
            "girdle_tn", 
            "girdle_thick", 
            "girdle_percent", 
            "girdle_condition", 
            "culet_size", 
            "picture_link", 
            "video_link", 
            "certificate_image_link", 
            "pair_number", 
            "pair_stock_number", 
            "pair_separable", 
            "shade", 
            "milky", 
            "eye_clean", 
            "bgm", 
            "black", 
            "open_inclusion", 
            "laser_inscription", 
            "lab_comments", 
            "key_to_symbols", 
            "internal_comments", 
            "current_country", 
            "current_state", 
            "current_city"
        ]

        return render_template(
            "inventory.html",
            stones=rows,
            display_columns=display_columns,
            can_create_memo=current_user()["role"] in {"ADMIN", "MANAGER", "SALES"},
            can_convert_memo=current_user()["role"] in {"ADMIN", "MANAGER", "ACCOUNTING"},
        )

    finally:
        conn.close()
