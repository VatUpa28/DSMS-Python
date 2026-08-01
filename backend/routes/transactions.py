from datetime import date

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from auth import AuthorizationDenied, current_user, current_user_id, require_roles
from database.db import get_db
from services.transaction_workflows import (
    WorkflowError,
    activate_memo,
    create_active_memo,
    convert_memo_items_to_invoice,
    create_direct_invoice as create_direct_invoice_workflow,
    create_memo_draft,
    credit_invoice_transaction_items,
    credit_invoice as credit_invoice_workflow,
    search_eligible_stones_for_client,
    receive_stones as receive_stones_workflow,
    return_memo_transaction_items,
)

transactions_bp = Blueprint("transactions", __name__)
MEMO_ROLES = {"ADMIN", "MANAGER", "SALES"}
INVOICE_ROLES = {"ADMIN", "MANAGER", "ACCOUNTING"}


def _form_payload():
    payload = request.form.to_dict(flat=True)
    payload["stone_ids"] = request.form.getlist("stone_ids")
    return payload


def _workflow_error(error, json_response=False):
    if json_response or request.is_json:
        return jsonify({"error": str(error)}), error.status_code
    return str(error), error.status_code


@transactions_bp.route("/create-memo", methods=["POST"])
@require_roles("ADMIN", "MANAGER", "SALES")
def create_memo():
    conn = get_db()
    try:
        create_memo_draft(conn, _form_payload())
    except WorkflowError as error:
        return _workflow_error(error)
    finally:
        conn.close()
    return redirect(url_for("transactions.transactions"))


@transactions_bp.route("/transactions/new", methods=["GET"])
@require_roles("ADMIN", "MANAGER", "SALES", "ACCOUNTING")
def new_transaction():
    role = current_user()["role"]
    requested_type = request.args.get("type", "").strip().lower()
    default_type = requested_type or ("memo" if role in MEMO_ROLES else "invoice")
    if default_type not in {"memo", "invoice"}:
        return "Unsupported transaction type", 400
    if default_type == "memo" and role not in MEMO_ROLES:
        raise AuthorizationDenied()
    if default_type == "invoice" and role not in INVOICE_ROLES:
        raise AuthorizationDenied()

    prefill = None
    client_id = request.args.get("client_id", "").strip()
    raw_ids = request.args.get("stone_ids", "").strip()
    if client_id or raw_ids:
        try:
            selected_client_id = int(client_id)
            stone_ids = []
            seen = set()
            for raw in raw_ids.replace(",", " ").split():
                stone_id = int(raw)
                if stone_id not in seen:
                    seen.add(stone_id)
                    stone_ids.append(stone_id)
        except (TypeError, ValueError):
            return "Invalid Inventory prefill selection", 400
        if default_type != "memo" or not stone_ids or len(stone_ids) > 25:
            return "Inventory Memo prefill requires 1 to 25 Stones", 400
        conn = get_db()
        try:
            client = conn.execute(
                "SELECT id, code, name, address FROM clients WHERE id = ?",
                (selected_client_id,),
            ).fetchone()
            if client is None:
                return "Selected client does not exist", 404
            placeholders = ",".join("?" for _ in stone_ids)
            rows = conn.execute(
                f"""
                SELECT s.id, s.stock_number, s.status, s.hold_client_id,
                       gr.report_number, gr.lab, gr.shape, gr.weight, gr.color,
                       gr.clarity, gr.cut, gr.polish, gr.symmetry,
                       gr.fluorescence_intensity, gr.price_per_carat, gr.total_price
                FROM stones s
                LEFT JOIN grading_reports gr ON gr.id = (
                    SELECT g.id FROM grading_reports g
                    WHERE g.stone_id = s.id AND g.active = 1
                    ORDER BY g.id DESC LIMIT 1
                )
                WHERE s.id IN ({placeholders})
                """,
                stone_ids,
            ).fetchall()
            by_id = {row["id"]: row for row in rows}
            invalid = [
                stone_id for stone_id in stone_ids
                if stone_id not in by_id
                or not (
                    by_id[stone_id]["status"] in ("Y", "AVAILABLE")
                    or (
                        by_id[stone_id]["status"] in ("H", "HOLD")
                        and by_id[stone_id]["hold_client_id"] == selected_client_id
                    )
                )
            ]
            if invalid:
                return "One or more selected Stones are no longer eligible for this client", 409
            prefill = {
                "client_id": selected_client_id,
                "stones": [dict(by_id[stone_id]) for stone_id in stone_ids],
            }
        finally:
            conn.close()
    return render_template(
        "transactions/new_transaction.html",
        can_create_memo=role in MEMO_ROLES,
        can_create_invoice=role in INVOICE_ROLES,
        default_transaction_type=default_type,
        prefill=prefill,
        today=date.today().isoformat(),
    )


def _json_workflow(workflow):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "A JSON object is required"}), 400
    conn = get_db()
    try:
        transaction = workflow(conn, payload)
        transaction["detail_url"] = url_for(
            "transactions.view_transaction",
            transaction_id=transaction["id"],
        )
        return jsonify(transaction), 201
    except WorkflowError as error:
        return _workflow_error(error, json_response=True)
    finally:
        conn.close()


@transactions_bp.route("/api/transactions/memos", methods=["POST"])
@require_roles("ADMIN", "MANAGER", "SALES")
def create_memo_draft_api():
    return _json_workflow(create_memo_draft)


@transactions_bp.route("/api/transactions/memos/active", methods=["POST"])
@require_roles("ADMIN", "MANAGER", "SALES")
def create_active_memo_api():
    return _json_workflow(create_active_memo)


@transactions_bp.route("/api/transactions/invoices", methods=["POST"])
@require_roles("ADMIN", "MANAGER", "ACCOUNTING")
def create_direct_invoice():
    return _json_workflow(create_direct_invoice_workflow)


@transactions_bp.route("/transactions", methods=["GET"])
def transactions():
    conn = get_db()
    try:
        cursor = conn.cursor()
        transactions = cursor.execute(
            """
            SELECT t.*, c.name AS client_name, COUNT(ti.id) AS stone_count
            FROM transactions t
            LEFT JOIN clients c ON c.id = t.client_id
            LEFT JOIN transaction_items ti ON ti.transaction_id = t.id
            WHERE t.status != 'cancelled'
            GROUP BY t.id
            ORDER BY t.id DESC
            """
        ).fetchall()
        available_stones = cursor.execute(
            """
            SELECT s.id, s.stock_number, gr.shape, gr.weight, gr.color, gr.clarity,
                   gr.price_per_carat, gr.total_price
            FROM stones s
            JOIN grading_reports gr ON gr.stone_id = s.id
            WHERE s.status = 'Y' AND gr.active = 1
            ORDER BY s.stock_number
            """
        ).fetchall()
        return render_template(
            "transactions/transactions.html",
            transactions=transactions,
            available_stones=available_stones,
        )
    finally:
        conn.close()


@transactions_bp.route("/clients/by-code/<client_code>")
def get_client_by_code(client_code):
    conn = get_db()
    try:
        client = conn.execute("SELECT * FROM clients WHERE code = ?", (client_code,)).fetchone()
        if not client:
            return jsonify({"error": "Client not found"}), 404
        addresses = conn.execute(
            "SELECT * FROM shipping_addresses WHERE client_id = ?", (client["id"],)
        ).fetchall()
        return jsonify({"client": dict(client), "shipping_addresses": [dict(row) for row in addresses]})
    finally:
        conn.close()


@transactions_bp.route("/api/clients/<int:client_id>/eligible-stones")
@require_roles("ADMIN", "MANAGER", "SALES", "ACCOUNTING")
def eligible_stones(client_id):
    stock_numbers = []
    raw_numbers = request.args.get("stock_numbers", "")
    if raw_numbers:
        seen = set()
        for value in raw_numbers.replace(",", " ").split():
            normalized = value.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                stock_numbers.append(normalized)
        if len(stock_numbers) > 25:
            return jsonify(
                {"error": "A maximum of 25 stock numbers may be searched at once"}
            ), 400
    filters = {
        "stock_numbers": stock_numbers,
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
    conn = get_db()
    try:
        stones = search_eligible_stones_for_client(conn, client_id, filters)
        return jsonify({"stones": [dict(stone) for stone in stones]})
    except WorkflowError as error:
        return _workflow_error(error, json_response=True)
    finally:
        conn.close()


@transactions_bp.route("/api/transaction-workspace/clients")
@require_roles("ADMIN", "MANAGER", "SALES", "ACCOUNTING")
def transaction_client_search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"clients": []})
    conn = get_db()
    try:
        pattern = f"%{query}%"
        clients = conn.execute(
            """
            SELECT DISTINCT c.id, c.code, c.name, c.address,
                   c.polygon_id, c.jbt_id, c.rapnet_id
            FROM clients c
            WHERE c.code LIKE ? OR c.name LIKE ? OR c.address LIKE ?
               OR CAST(c.polygon_id AS TEXT) LIKE ? OR CAST(c.jbt_id AS TEXT) LIKE ?
               OR CAST(c.rapnet_id AS TEXT) LIKE ? OR c.tax_id LIKE ? OR c.sales_tax_id LIKE ?
               OR EXISTS (
                 SELECT 1 FROM client_contacts cc WHERE cc.client_id = c.id
                   AND (cc.name LIKE ? OR cc.phone LIKE ? OR cc.email LIKE ?
                        OR cc.fax LIKE ? OR cc.cell LIKE ?)
               )
               OR EXISTS (
                 SELECT 1 FROM shipping_addresses sa WHERE sa.client_id = c.id
                   AND (sa.label LIKE ? OR sa.manager LIKE ? OR sa.store_number LIKE ?
                        OR sa.address LIKE ? OR sa.city LIKE ? OR sa.state LIKE ?
                        OR sa.country LIKE ? OR sa.phone LIKE ?)
               )
            ORDER BY c.name, c.code
            LIMIT 20
            """,
            (pattern,) * 21,
        ).fetchall()
        results = []
        for client in clients:
            item = dict(client)
            contact = conn.execute(
                """SELECT name, phone, email FROM client_contacts
                   WHERE client_id = ? AND (name LIKE ? OR phone LIKE ? OR email LIKE ?
                     OR fax LIKE ? OR cell LIKE ?) ORDER BY id LIMIT 1""",
                (client["id"],) + (pattern,) * 5,
            ).fetchone()
            shipping = conn.execute(
                """SELECT label, manager, store_number, city, state FROM shipping_addresses
                   WHERE client_id = ? AND (label LIKE ? OR manager LIKE ? OR store_number LIKE ?
                     OR address LIKE ? OR city LIKE ? OR state LIKE ? OR country LIKE ? OR phone LIKE ?)
                   ORDER BY id LIMIT 1""",
                (client["id"],) + (pattern,) * 8,
            ).fetchone()
            item["matched_contact"] = dict(contact) if contact else None
            item["matched_shipping"] = dict(shipping) if shipping else None
            results.append(item)
        return jsonify({"clients": results})
    finally:
        conn.close()


@transactions_bp.route("/api/transaction-workspace/clients/<int:client_id>")
@require_roles("ADMIN", "MANAGER", "SALES", "ACCOUNTING")
def transaction_client_context(client_id):
    conn = get_db()
    try:
        client = conn.execute(
            "SELECT id, code, name, address FROM clients WHERE id = ?",
            (client_id,),
        ).fetchone()
        if client is None:
            return jsonify({"error": "Selected client does not exist"}), 404
        contacts = conn.execute(
            """
            SELECT id, name, phone, fax, email, cell
            FROM client_contacts
            WHERE client_id = ?
            ORDER BY name, id
            """,
            (client_id,),
        ).fetchall()
        addresses = conn.execute(
            """
            SELECT id, label, manager, store_number, address, city, state,
                   country, phone
            FROM shipping_addresses
            WHERE client_id = ?
            ORDER BY label, address, id
            """,
            (client_id,),
        ).fetchall()
        return jsonify(
            {
                "client": dict(client),
                "contacts": [dict(row) for row in contacts],
                "shipping_addresses": [dict(row) for row in addresses],
            }
        )
    finally:
        conn.close()


@transactions_bp.route("/transactions/<int:transaction_id>/confirm", methods=["POST"])
@require_roles("ADMIN", "MANAGER", "SALES")
def confirm_transaction(transaction_id):
    conn = get_db()
    try:
        activate_memo(conn, transaction_id)
    except WorkflowError as error:
        return _workflow_error(error)
    finally:
        conn.close()
    return redirect(url_for("transactions.transactions"))


@transactions_bp.route("/transactions/<int:transaction_id>")
def view_transaction(transaction_id):
    conn = get_db()
    try:
        transaction = conn.execute(
            """
            SELECT t.*, c.name AS client_name,
                   parent.transaction_number AS parent_transaction_number
            FROM transactions t
            JOIN clients c ON c.id = t.client_id
            LEFT JOIN transactions parent ON parent.id = t.parent_transaction_id
            WHERE t.id = ?
            """,
            (transaction_id,),
        ).fetchone()
        if transaction is None:
            return "Transaction not found", 404
        stones = conn.execute(
            """
            SELECT ti.*, s.status AS stone_inventory_status,
                   child_transaction.id AS child_transaction_id,
                   child_transaction.transaction_number AS child_transaction_number
            FROM transaction_items ti
            JOIN stones s ON s.id = ti.stone_id
            LEFT JOIN transaction_items child_item
              ON child_item.created_from_item_id = ti.id
            LEFT JOIN transactions child_transaction
              ON child_transaction.id = child_item.transaction_id
            WHERE ti.transaction_id = ?
            ORDER BY ti.stock_number
            """,
            (transaction_id,),
        ).fetchall()
        child_invoices = []
        if transaction["type"] == "memo":
            child_invoices = conn.execute(
                """
                SELECT child.id, child.transaction_number, child.date, child.status,
                       COUNT(item.id) AS item_count,
                       COALESCE(SUM(item.total_price), 0) AS total_price
                FROM transactions child
                LEFT JOIN transaction_items item ON item.transaction_id = child.id
                WHERE child.parent_transaction_id = ?
                  AND child.type = 'invoice'
                GROUP BY child.id
                ORDER BY child.id
                """,
                (transaction_id,),
            ).fetchall()
        return render_template(
            "transactions/transaction_detail.html",
            transaction=transaction,
            stones=stones,
            child_invoices=child_invoices,
        )
    finally:
        conn.close()


def _require_return_permission(transaction_type):
    role = current_user()["role"]
    if transaction_type == "memo" and role not in MEMO_ROLES:
        raise AuthorizationDenied()
    if transaction_type == "invoice" and role not in INVOICE_ROLES:
        raise AuthorizationDenied()
    if transaction_type not in {"memo", "invoice"}:
        raise WorkflowError("This transaction type cannot be returned")


@transactions_bp.route(
    "/transactions/<int:transaction_id>/return", methods=["GET", "POST"]
)
@require_roles("ADMIN", "MANAGER", "SALES", "ACCOUNTING")
def return_items(transaction_id):
    conn = get_db()
    try:
        transaction = conn.execute(
            """
            SELECT t.*, c.name AS client_name
            FROM transactions t
            JOIN clients c ON c.id = t.client_id
            WHERE t.id = ?
            """,
            (transaction_id,),
        ).fetchone()
        if transaction is None:
            return render_template("errors/not_found.html", message="Transaction not found"), 404
        _require_return_permission(transaction["type"])
        items = conn.execute(
            """
            SELECT ti.*, s.status AS stone_inventory_status
            FROM transaction_items ti
            JOIN stones s ON s.id = ti.stone_id
            WHERE ti.transaction_id = ?
            ORDER BY ti.stock_number
            """,
            (transaction_id,),
        ).fetchall()
        returnable = [
            item
            for item in items
            if transaction["status"] == "active"
            and item["status"] == "active"
            and (
                transaction["type"] == "memo"
                and item["stone_inventory_status"] in {"M", "MEMO"}
                or transaction["type"] == "invoice"
                and item["stone_inventory_status"] in {"S", "SOLD"}
            )
        ]
        if request.method == "GET":
            return render_template(
                "transactions/return_items.html",
                transaction=transaction,
                items=items,
                returnable_items=returnable,
                selected_item_ids=set(),
                return_note="",
            )

        item_ids = request.form.getlist("transaction_item_ids")
        if transaction["type"] == "memo":
            result = return_memo_transaction_items(conn, transaction_id, item_ids)
            flash(
                f"Returned {result['returned']} item(s). The Stones are awaiting physical receipt.",
                "success",
            )
            return redirect(
                url_for("transactions.view_transaction", transaction_id=transaction_id)
            )
        result = credit_invoice_transaction_items(conn, transaction_id, item_ids)
        flash(
            f"Credit Invoice {result['transaction_number']} created for "
            f"{result['returned']} returned item(s).",
            "success",
        )
        return redirect(
            url_for("transactions.view_transaction", transaction_id=result["id"])
        )
    except WorkflowError as error:
        if request.method == "POST":
            flash(str(error), "danger")
            return (
                render_template(
                    "transactions/return_items.html",
                    transaction=transaction,
                    items=items,
                    returnable_items=returnable,
                    selected_item_ids={
                        value
                        for value in request.form.getlist("transaction_item_ids")
                    },
                    return_note=request.form.get("return_note", ""),
                ),
                error.status_code,
            )
        return _workflow_error(error)
    finally:
        conn.close()


@transactions_bp.route("/receive-stones", methods=["POST"])
@require_roles("ADMIN", "MANAGER")
def receive_stones():
    data = request.get_json(silent=True) or {}
    conn = get_db()
    try:
        result = receive_stones_workflow(
            conn,
            data.get("stone_ids"),
            current_user_id(),
            data.get("note"),
        )
        return jsonify({"success": True, **result})
    except WorkflowError as error:
        return jsonify({"success": False, "error": str(error)}), error.status_code
    finally:
        conn.close()


@transactions_bp.route("/receiving")
@require_roles("ADMIN", "MANAGER")
def receiving_queue():
    query = request.args.get("q", "").strip()
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT s.id, s.stock_number, s.status,
                   g.report_number, g.lab, g.shape, g.weight,
                   source.transaction_number AS source_transaction_number,
                   source.type AS source_transaction_type,
                   source.date AS return_date,
                   c.name AS client_name
            FROM stones s
            LEFT JOIN grading_reports g
              ON g.stone_id = s.id AND g.active = 1
            LEFT JOIN transaction_items returned_item
              ON returned_item.id = (
                SELECT latest.id
                FROM transaction_items latest
                WHERE latest.stone_id = s.id AND latest.status = 'returned'
                ORDER BY latest.id DESC
                LIMIT 1
              )
            LEFT JOIN transactions source
              ON source.id = returned_item.transaction_id
            LEFT JOIN clients c ON c.id = source.client_id
            WHERE s.status = 'RETURN_PENDING'
              AND (? = '' OR s.stock_number LIKE ?)
            ORDER BY s.stock_number
            """,
            (query, f"%{query}%"),
        ).fetchall()
        return render_template("receiving/queue.html", stones=rows, query=query)
    finally:
        conn.close()


@transactions_bp.route("/receiving/history")
@require_roles("ADMIN", "MANAGER")
def receiving_history():
    conn = get_db()
    try:
        events = conn.execute(
            """
            SELECT re.received_at, re.stock_number_snapshot, re.note,
                   t.transaction_number AS source_transaction_number,
                   u.first_name, u.last_name, u.email, u.active AS user_active
            FROM receiving_events re
            LEFT JOIN transactions t ON t.id = re.source_transaction_id
            LEFT JOIN users u ON u.id = re.received_by_user_id
            ORDER BY re.received_at DESC, re.id DESC
            LIMIT 250
            """
        ).fetchall()
        return render_template("receiving/history.html", events=events)
    finally:
        conn.close()


@transactions_bp.route("/api/receiving/stone-by-stock")
@require_roles("ADMIN", "MANAGER")
def receiving_stone_by_stock():
    stock_number = request.args.get("stock_number", "").strip()
    if not stock_number:
        return jsonify({"error": "A stock number is required"}), 400
    conn = get_db()
    try:
        stone = conn.execute(
            "SELECT id, stock_number, status FROM stones WHERE stock_number = ?",
            (stock_number,),
        ).fetchone()
        if stone is None:
            return jsonify({"error": "Stone not found"}), 404
        if stone["status"] != "RETURN_PENDING":
            return jsonify({"error": "Stone is not awaiting physical receipt"}), 409
        return jsonify(dict(stone))
    finally:
        conn.close()


@transactions_bp.route("/transactions/<int:memo_id>/convert-to-invoice")
@require_roles("ADMIN", "MANAGER", "ACCOUNTING")
def convert_memo_page(memo_id):
    conn = get_db()
    try:
        memo = conn.execute(
            """
            SELECT t.*, c.code AS client_code, c.name AS client_name
            FROM transactions t
            JOIN clients c ON c.id = t.client_id
            WHERE t.id = ?
            """,
            (memo_id,),
        ).fetchone()
        if memo is None:
            return render_template("errors/not_found.html", message="Memo not found"), 404
        if memo["type"] != "memo" or memo["status"] != "active":
            return "Only an active Memo can be converted", 409
        items = conn.execute(
            """
            SELECT ti.*, s.status AS stone_inventory_status
            FROM transaction_items ti
            JOIN stones s ON s.id = ti.stone_id
            WHERE ti.transaction_id = ?
              AND ti.status = 'active'
              AND s.status IN ('M', 'MEMO')
            ORDER BY ti.stock_number
            """,
            (memo_id,),
        ).fetchall()
        if not items:
            return "No convertible Memo items remain", 409
        requested_ids = []
        seen = set()
        raw_ids = request.args.get("item_ids", "").strip()
        if raw_ids:
            try:
                for raw in raw_ids.replace(",", " ").split():
                    item_id = int(raw)
                    if item_id not in seen:
                        seen.add(item_id)
                        requested_ids.append(item_id)
            except ValueError:
                return "Invalid Memo item prefill selection", 400
            if len(requested_ids) > 25:
                return "A maximum of 25 Memo items may be preselected", 400
            convertible_ids = {item["id"] for item in items}
            if any(item_id not in convertible_ids for item_id in requested_ids):
                return "A selected item does not belong to this Memo or is no longer convertible", 409
        return render_template(
            "transactions/convert_memo.html",
            memo=memo,
            items=items,
            today=date.today().isoformat(),
            preselected_item_ids=set(requested_ids),
        )
    finally:
        conn.close()


@transactions_bp.route(
    "/api/transactions/memos/<int:memo_id>/convert-to-invoice",
    methods=["POST"],
)
@require_roles("ADMIN", "MANAGER", "ACCOUNTING")
def convert_memo_api(memo_id):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "A JSON object is required"}), 400
    conn = get_db()
    try:
        result = convert_memo_items_to_invoice(
            conn,
            memo_id,
            payload.get("transaction_item_ids"),
            payload,
        )
        result["detail_url"] = url_for(
            "transactions.view_transaction", transaction_id=result["id"]
        )
        return jsonify(result), 201
    except WorkflowError as error:
        return _workflow_error(error, json_response=True)
    finally:
        conn.close()


@transactions_bp.route("/transactions/<int:transaction_id>/credit", methods=["POST"])
@require_roles("ADMIN", "MANAGER", "ACCOUNTING")
def credit_invoice(transaction_id):
    selected_date = request.form.get("date")
    try:
        credit_date = date.fromisoformat(selected_date) if selected_date else None
    except ValueError:
        return "Transaction date must use YYYY-MM-DD", 400
    conn = get_db()
    try:
        credit_invoice_workflow(conn, transaction_id, request.form.getlist("stone_ids"), credit_date)
    except WorkflowError as error:
        return _workflow_error(error)
    finally:
        conn.close()
    return redirect(url_for("transactions.view_transaction", transaction_id=transaction_id))
