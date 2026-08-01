"""Business workflows translated from DSMS-Java into concise Flask/SQLite code."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
import sqlite3


# Preserve the Flask V1's stored status values while introducing an explicit
# RETURN_PENDING state.  Named values are accepted too, which helps imported
# records and makes validation readable without a destructive data rewrite.
AVAILABLE_STATUSES = {"Y", "AVAILABLE"}
HOLD_STATUSES = {"H", "HOLD"}
MEMO_STATUSES = {"M", "MEMO"}
SOLD_STATUSES = {"S", "SOLD"}
RETURN_PENDING = "RETURN_PENDING"

LEGACY_AVAILABLE = "Y"
LEGACY_HOLD = "H"
LEGACY_MEMO = "M"
LEGACY_SOLD = "S"


class WorkflowError(ValueError):
    status_code = 400


class WorkflowConflict(WorkflowError):
    status_code = 409


class WorkflowNotFound(WorkflowError):
    status_code = 404


@contextmanager
def atomic_workflow(conn: sqlite3.Connection):
    """Acquire SQLite's writer lock before workflow eligibility is checked."""
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    try:
        yield cursor
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


def _required_text(payload, key, label=None):
    value = payload.get(key)
    value = "" if value is None else str(value).strip()
    if not value:
        raise WorkflowError(f"{label or key} is required")
    return value


def _optional_text(payload, key):
    value = payload.get(key)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _optional_int(payload, key):
    value = payload.get(key)
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowError(f"{key} must be an integer") from exc


def _required_date(payload, key="date"):
    raw = _required_text(payload, key, "Transaction date")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise WorkflowError("Transaction date must use YYYY-MM-DD") from exc


def _required_nonnegative_number(payload, key):
    raw = payload.get(key)
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise WorkflowError(f"{key} must be a number") from exc
    if value < 0:
        raise WorkflowError(f"{key} must not be negative")
    return value


def _distinct_stone_ids(values):
    if not isinstance(values, (list, tuple)) or not values:
        raise WorkflowError("stone_ids must contain at least one Stone")
    ids = []
    seen = set()
    for value in values:
        try:
            stone_id = int(value)
        except (TypeError, ValueError) as exc:
            raise WorkflowError("stone_ids must contain integer Stone IDs") from exc
        if stone_id in seen:
            raise WorkflowError(f"Duplicate Stone ID {stone_id} is not allowed")
        seen.add(stone_id)
        ids.append(stone_id)
    return ids


def _require_client(cursor, client_id):
    client = cursor.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    if client is None:
        raise WorkflowError("Selected client does not exist")
    return client


def _load_active_stones(cursor, stone_ids):
    placeholders = ",".join("?" for _ in stone_ids)
    rows = cursor.execute(
        f"""
        SELECT
            s.id AS stone_id, s.stock_number, s.status AS stone_status, s.hold_client_id,
            g.id AS grading_report_id, g.report_number, g.lab, g.shape, g.weight,
            g.color, g.clarity, g.cut, g.polish, g.symmetry,
            g.fluorescence_intensity, g.price_per_carat, g.total_price
        FROM stones s
        JOIN grading_reports g ON g.stone_id = s.id AND g.active = 1
        WHERE s.id IN ({placeholders})
        """,
        stone_ids,
    ).fetchall()
    by_id = {row["stone_id"]: row for row in rows}
    missing = [stone_id for stone_id in stone_ids if stone_id not in by_id]
    if missing:
        raise WorkflowError(f"Stone {missing[0]} does not exist or has no active grading report")
    ordered = [by_id[stone_id] for stone_id in stone_ids]
    for stone in ordered:
        if stone["price_per_carat"] is None or stone["total_price"] is None:
            raise WorkflowError(
                f"Stone {stone['stock_number']} has no complete active pricing snapshot"
            )
    return ordered


def _validate_client_eligibility(stones, client_id):
    for stone in stones:
        if stone["stone_status"] in AVAILABLE_STATUSES:
            continue
        if stone["stone_status"] in HOLD_STATUSES and stone["hold_client_id"] == client_id:
            continue
        if stone["stone_status"] in HOLD_STATUSES:
            raise WorkflowConflict(
                f"Stone {stone['stock_number']} is held for a different client"
            )
        raise WorkflowConflict(
            f"Stone {stone['stock_number']} must be AVAILABLE or held for the selected client"
        )


def _contact_snapshot(cursor, payload, client_id):
    source_contact_id = _optional_int(payload, "source_contact_id")
    selected = None
    if source_contact_id is not None:
        selected = cursor.execute(
            "SELECT * FROM client_contacts WHERE id = ? AND client_id = ?",
            (source_contact_id, client_id),
        ).fetchone()
        if selected is None:
            raise WorkflowError("Selected contact does not belong to the client")

    person = _optional_text(payload, "person") or _optional_text(payload, "contact_name")
    phone = _optional_text(payload, "phone") or _optional_text(payload, "contact_phone")
    fax = _optional_text(payload, "fax") or _optional_text(payload, "contact_fax")
    email = _optional_text(payload, "contact_email")
    cell = _optional_text(payload, "contact_cell")
    if selected is not None:
        person = person or selected["name"]
        phone = phone or selected["phone"]
        fax = fax or selected["fax"]
        email = email or selected["email"]
        cell = cell or selected["cell"]
    if not person:
        raise WorkflowError("Contact name is required")
    return {
        "source_contact_id": source_contact_id,
        "person": person,
        "phone": phone,
        "fax": fax,
        "contact_email_snapshot": email,
        "contact_cell_snapshot": cell,
    }


def _shipping_snapshot(cursor, payload, client_id):
    source_id = _optional_int(payload, "source_shipping_address_id")
    if source_id is None:
        source_id = _optional_int(payload, "ship_to_address_id")

    selected = None
    if source_id is not None:
        selected = cursor.execute(
            "SELECT * FROM shipping_addresses WHERE id = ? AND client_id = ?",
            (source_id, client_id),
        ).fetchone()
        if selected is None:
            raise WorkflowError("Selected Ship To address does not belong to the client")

    mapping = {
        "ship_to_label": "label",
        "ship_to_manager": "manager",
        "ship_to_store_number": "store_number",
        "ship_to_address_snapshot": "address",
        "ship_to_city": "city",
        "ship_to_state": "state",
        "ship_to_country": "country",
        "ship_to_phone": "phone",
    }
    snapshot = {
        "source_shipping_address_id": source_id,
        "ship_to_address_id": source_id,
    }
    for transaction_key, address_key in mapping.items():
        request_key = "ship_to_address" if transaction_key == "ship_to_address_snapshot" else transaction_key
        submitted = _optional_text(payload, request_key)
        snapshot[transaction_key] = (
            submitted
            if submitted is not None
            else selected[address_key] if selected is not None else None
        )

    if not snapshot["ship_to_address_snapshot"]:
        raise WorkflowError("Ship To address is required")
    return snapshot


def generate_transaction_number(cursor, transaction_type, transaction_date):
    prefixes = {"memo": "MEMO", "invoice": "INV", "credit_invoice": "CR"}
    if transaction_type not in prefixes:
        raise WorkflowError("Unsupported transaction type")
    day = transaction_date.isoformat()
    cursor.execute(
        """
        INSERT OR IGNORE INTO transaction_number_counters
        (transaction_type, transaction_date, last_value)
        VALUES (?, ?, 0)
        """,
        (transaction_type, day),
    )
    cursor.execute(
        """
        UPDATE transaction_number_counters
        SET last_value = last_value + 1
        WHERE transaction_type = ? AND transaction_date = ?
        """,
        (transaction_type, day),
    )
    value = cursor.execute(
        """
        SELECT last_value FROM transaction_number_counters
        WHERE transaction_type = ? AND transaction_date = ?
        """,
        (transaction_type, day),
    ).fetchone()["last_value"]
    return f"{prefixes[transaction_type]}-{transaction_date.strftime('%Y%m%d')}-{value:04d}"


def _transaction_header(cursor, payload, client_id):
    transaction_date = _required_date(payload)
    contact = _contact_snapshot(cursor, payload, client_id)
    shipping = _shipping_snapshot(cursor, payload, client_id)
    return {
        "date": transaction_date,
        "terms": _required_text(payload, "terms", "Terms"),
        "carrier": _required_text(payload, "carrier", "Carrier"),
        "shipment_type": _required_text(payload, "shipment_type", "Shipment type"),
        "ship_charge": _required_nonnegative_number(payload, "ship_charge"),
        "purchase_order_number": _optional_text(payload, "purchase_order_number"),
        **contact,
        **shipping,
    }


def _insert_transaction(cursor, number, client_id, transaction_type, status, header, parent_id=None):
    cursor.execute(
        """
        INSERT INTO transactions (
            transaction_number, client_id, type, status, parent_transaction_id,
            person, phone, fax, date, terms, carrier, shipment_type, ship_charge,
            ship_to_address_id, source_shipping_address_id,
            ship_to_label, ship_to_manager, ship_to_store_number,
            ship_to_address_snapshot, ship_to_city, ship_to_state, ship_to_country,
            ship_to_phone, source_contact_id, contact_email_snapshot,
            contact_cell_snapshot, purchase_order_number
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            number,
            client_id,
            transaction_type,
            status,
            parent_id,
            header["person"],
            header["phone"],
            header["fax"],
            header["date"].isoformat(),
            header["terms"],
            header["carrier"],
            header["shipment_type"],
            header["ship_charge"],
            header["ship_to_address_id"],
            header["source_shipping_address_id"],
            header["ship_to_label"],
            header["ship_to_manager"],
            header["ship_to_store_number"],
            header["ship_to_address_snapshot"],
            header["ship_to_city"],
            header["ship_to_state"],
            header["ship_to_country"],
            header["ship_to_phone"],
            header["source_contact_id"],
            header["contact_email_snapshot"],
            header["contact_cell_snapshot"],
            header["purchase_order_number"],
        ),
    )
    return cursor.lastrowid


def _insert_snapshot_item(cursor, transaction_id, stone, status, created_from_item_id=None, total_price=None):
    cursor.execute(
        """
        INSERT INTO transaction_items (
            transaction_id, stone_id, grading_report_id, created_from_item_id, status,
            stock_number, report_number, lab, shape, weight, color, clarity,
            cut, polish, symmetry, fluorescence_intensity, price_per_carat, total_price
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            transaction_id,
            stone["stone_id"],
            stone["grading_report_id"],
            created_from_item_id,
            status,
            stone["stock_number"],
            stone["report_number"],
            stone["lab"],
            stone["shape"],
            stone["weight"],
            stone["color"],
            stone["clarity"],
            stone["cut"],
            stone["polish"],
            stone["symmetry"],
            stone["fluorescence_intensity"],
            abs(stone["price_per_carat"]),
            stone["total_price"] if total_price is None else total_price,
        ),
    )


def place_hold(conn, client_id, stone_ids):
    ids = _distinct_stone_ids(stone_ids)
    try:
        client_id = int(client_id)
    except (TypeError, ValueError) as exc:
        raise WorkflowError("client_id must be an integer") from exc
    with atomic_workflow(conn) as cursor:
        _require_client(cursor, client_id)
        stones = _load_active_stones(cursor, ids)
        for stone in stones:
            if stone["stone_status"] not in AVAILABLE_STATUSES:
                raise WorkflowConflict(
                    f"Stone {stone['stock_number']} must be AVAILABLE before it can be held"
                )
        cursor.executemany(
            "UPDATE stones SET status = ?, hold_client_id = ? WHERE id = ?",
            [(LEGACY_HOLD, client_id, stone["stone_id"]) for stone in stones],
        )
    return {
        "held": len(ids),
        "stone_ids": ids,
        "stock_numbers": [stone["stock_number"] for stone in stones],
        "client_id": client_id,
    }


def release_hold(conn, stone_ids):
    ids = _distinct_stone_ids(stone_ids)
    with atomic_workflow(conn) as cursor:
        stones = _load_active_stones(cursor, ids)
        for stone in stones:
            if stone["stone_status"] not in HOLD_STATUSES or stone["hold_client_id"] is None:
                raise WorkflowConflict(f"Stone {stone['stock_number']} is not currently held")
        cursor.executemany(
            "UPDATE stones SET status = ?, hold_client_id = NULL WHERE id = ?",
            [(LEGACY_AVAILABLE, stone["stone_id"]) for stone in stones],
        )
    return {
        "released": len(ids),
        "stone_ids": ids,
        "stock_numbers": [stone["stock_number"] for stone in stones],
    }


def search_available_stones(conn, filters=None, limit=100):
    """Return a bounded server-side search containing only AVAILABLE Stones."""

    filters = filters or {}
    try:
        limit = min(max(int(limit), 1), 100)
    except (TypeError, ValueError) as exc:
        raise WorkflowError("limit must be an integer") from exc

    clauses = ["s.status IN ('Y', 'AVAILABLE')"]
    parameters = []
    exact_numbers = filters.get("stock_numbers") or []
    if exact_numbers:
        if len(exact_numbers) > 25:
            raise WorkflowError("A maximum of 25 stock numbers may be searched at once")
        placeholders = ",".join("?" for _ in exact_numbers)
        clauses.append(f"s.stock_number IN ({placeholders})")
        parameters.extend(exact_numbers)
    elif filters.get("stock_number"):
        clauses.append("s.stock_number LIKE ?")
        parameters.append(f"%{filters['stock_number']}%")

    exact_fields = {
        "lab": "g.lab",
        "shape": "g.shape",
        "color": "g.color",
        "clarity": "g.clarity",
        "cut": "g.cut",
        "polish": "g.polish",
        "symmetry": "g.symmetry",
        "fluorescence": "g.fluorescence_intensity",
    }
    for key, column in exact_fields.items():
        value = filters.get(key)
        if value:
            clauses.append(f"{column} = ?")
            parameters.append(value)

    parsed_weights = {}
    for key, operator in (("min_weight", ">="), ("max_weight", "<=")):
        raw = filters.get(key)
        if raw not in (None, ""):
            try:
                weight = float(raw)
            except (TypeError, ValueError) as exc:
                raise WorkflowError(f"{key} must be a number") from exc
            if weight < 0:
                raise WorkflowError(f"{key} must not be negative")
            parsed_weights[key] = weight
            clauses.append(f"g.weight {operator} ?")
            parameters.append(weight)
    if (
        "min_weight" in parsed_weights
        and "max_weight" in parsed_weights
        and parsed_weights["min_weight"] > parsed_weights["max_weight"]
    ):
        raise WorkflowError("Minimum weight must not exceed maximum weight")

    parameters.append(limit)
    return conn.execute(
        f"""
        SELECT s.id, s.stock_number, s.status,
               g.report_number, g.lab, g.shape, g.weight, g.color, g.clarity,
               g.cut, g.polish, g.symmetry, g.fluorescence_intensity,
               g.price_per_carat, g.total_price
        FROM stones s
        JOIN grading_reports g ON g.stone_id = s.id AND g.active = 1
        WHERE {' AND '.join(clauses)}
        ORDER BY s.stock_number
        LIMIT ?
        """,
        parameters,
    ).fetchall()


def eligible_stones_for_client(conn, client_id):
    """Return the AVAILABLE inventory plus holds owned by one selected client."""
    try:
        client_id = int(client_id)
    except (TypeError, ValueError) as exc:
        raise WorkflowError("client_id must be an integer") from exc
    cursor = conn.cursor()
    _require_client(cursor, client_id)
    return cursor.execute(
        """
        SELECT s.id, s.stock_number, s.status, s.hold_client_id,
               g.id AS grading_report_id, g.report_number, g.lab, g.shape,
               g.weight, g.color, g.clarity, g.price_per_carat, g.total_price
        FROM stones s
        JOIN grading_reports g ON g.stone_id = s.id AND g.active = 1
        WHERE s.status IN ('Y', 'AVAILABLE')
           OR (s.status IN ('H', 'HOLD') AND s.hold_client_id = ?)
        ORDER BY s.stock_number
        """,
        (client_id,),
    ).fetchall()


def search_eligible_stones_for_client(conn, client_id, filters=None, limit=100):
    """Bounded, server-side search over client-eligible inventory."""

    try:
        client_id = int(client_id)
    except (TypeError, ValueError) as exc:
        raise WorkflowError("client_id must be an integer") from exc
    filters = filters or {}
    cursor = conn.cursor()
    _require_client(cursor, client_id)

    try:
        limit = min(max(int(limit), 1), 100)
    except (TypeError, ValueError) as exc:
        raise WorkflowError("limit must be an integer") from exc

    clauses = [
        "(s.status IN ('Y', 'AVAILABLE') "
        "OR (s.status IN ('H', 'HOLD') AND s.hold_client_id = ?))"
    ]
    parameters = [client_id]
    exact_numbers = filters.get("stock_numbers") or []
    if exact_numbers:
        if len(exact_numbers) > 25:
            raise WorkflowError("A maximum of 25 stock numbers may be searched at once")
        placeholders = ",".join("?" for _ in exact_numbers)
        clauses.append(f"s.stock_number IN ({placeholders})")
        parameters.extend(exact_numbers)
    elif filters.get("stock_number"):
        clauses.append("s.stock_number LIKE ?")
        parameters.append(f"%{filters['stock_number']}%")

    exact_fields = {
        "lab": "g.lab",
        "shape": "g.shape",
        "color": "g.color",
        "clarity": "g.clarity",
        "cut": "g.cut",
        "polish": "g.polish",
        "symmetry": "g.symmetry",
        "fluorescence": "g.fluorescence_intensity",
    }
    for key, column in exact_fields.items():
        value = filters.get(key)
        if value:
            clauses.append(f"{column} = ?")
            parameters.append(value)

    for key, operator in (("min_weight", ">="), ("max_weight", "<=")):
        raw = filters.get(key)
        if raw not in (None, ""):
            try:
                weight = float(raw)
            except (TypeError, ValueError) as exc:
                raise WorkflowError(f"{key} must be a number") from exc
            if weight < 0:
                raise WorkflowError(f"{key} must not be negative")
            clauses.append(f"g.weight {operator} ?")
            parameters.append(weight)
    if (
        filters.get("min_weight") not in (None, "")
        and filters.get("max_weight") not in (None, "")
        and float(filters["min_weight"]) > float(filters["max_weight"])
    ):
        raise WorkflowError("Minimum weight must not exceed maximum weight")

    parameters.append(limit)
    return cursor.execute(
        f"""
        SELECT s.id, s.stock_number, s.status, s.hold_client_id,
               g.id AS grading_report_id, g.report_number, g.lab, g.shape,
               g.weight, g.color, g.clarity, g.cut, g.polish, g.symmetry,
               g.fluorescence_intensity, g.price_per_carat, g.total_price
        FROM stones s
        JOIN grading_reports g ON g.stone_id = s.id AND g.active = 1
        WHERE {' AND '.join(clauses)}
        ORDER BY s.stock_number
        LIMIT ?
        """,
        parameters,
    ).fetchall()


def create_direct_invoice(conn, payload):
    client_id = _optional_int(payload, "client_id")
    if client_id is None:
        raise WorkflowError("client_id is required")
    stone_ids = _distinct_stone_ids(payload.get("stone_ids"))
    with atomic_workflow(conn) as cursor:
        _require_client(cursor, client_id)
        header = _transaction_header(cursor, payload, client_id)
        stones = _load_active_stones(cursor, stone_ids)
        _validate_client_eligibility(stones, client_id)
        number = generate_transaction_number(cursor, "invoice", header["date"])
        transaction_id = _insert_transaction(
            cursor, number, client_id, "invoice", "active", header, parent_id=None
        )
        total = 0.0
        for stone in stones:
            _insert_snapshot_item(cursor, transaction_id, stone, "active")
            total += float(stone["total_price"])
        cursor.executemany(
            "UPDATE stones SET status = ?, hold_client_id = NULL WHERE id = ?",
            [(LEGACY_SOLD, stone["stone_id"]) for stone in stones],
        )
    return {
        "id": transaction_id,
        "transaction_number": number,
        "type": "invoice",
        "status": "active",
        "parent_transaction_id": None,
        "stone_count": len(stones),
        "total_price": total,
    }


def create_memo_draft(conn, payload):
    client_id = _optional_int(payload, "client_id")
    if client_id is None:
        raise WorkflowError("client_id is required")
    stone_ids = _distinct_stone_ids(payload.get("stone_ids"))
    with atomic_workflow(conn) as cursor:
        _require_client(cursor, client_id)
        header = _transaction_header(cursor, payload, client_id)
        stones = _load_active_stones(cursor, stone_ids)
        _validate_client_eligibility(stones, client_id)
        number = generate_transaction_number(cursor, "memo", header["date"])
        transaction_id = _insert_transaction(cursor, number, client_id, "memo", "draft", header)
        for stone in stones:
            _insert_snapshot_item(cursor, transaction_id, stone, "draft")
    return {"id": transaction_id, "transaction_number": number, "status": "draft"}


def create_active_memo(conn, payload):
    """Create and activate a Memo as one indivisible SQLite workflow."""

    client_id = _optional_int(payload, "client_id")
    if client_id is None:
        raise WorkflowError("client_id is required")
    stone_ids = _distinct_stone_ids(payload.get("stone_ids"))
    with atomic_workflow(conn) as cursor:
        _require_client(cursor, client_id)
        header = _transaction_header(cursor, payload, client_id)
        stones = _load_active_stones(cursor, stone_ids)
        _validate_client_eligibility(stones, client_id)
        number = generate_transaction_number(cursor, "memo", header["date"])
        transaction_id = _insert_transaction(
            cursor, number, client_id, "memo", "active", header
        )
        for stone in stones:
            _insert_snapshot_item(cursor, transaction_id, stone, "active")
        cursor.executemany(
            "UPDATE stones SET status = ?, hold_client_id = NULL WHERE id = ?",
            [(LEGACY_MEMO, stone["stone_id"]) for stone in stones],
        )
    return {
        "id": transaction_id,
        "transaction_number": number,
        "type": "memo",
        "status": "active",
        "stone_count": len(stones),
    }


def activate_memo(conn, transaction_id):
    with atomic_workflow(conn) as cursor:
        memo = cursor.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
        if memo is None or memo["type"] != "memo" or memo["status"] != "draft":
            raise WorkflowConflict("Only a draft memo can be activated")
        rows = cursor.execute(
            "SELECT stone_id FROM transaction_items WHERE transaction_id = ? ORDER BY id", (transaction_id,)
        ).fetchall()
        if not rows:
            raise WorkflowError("A memo must contain at least one Stone")
        stone_ids = [row["stone_id"] for row in rows]
        stones = _load_active_stones(cursor, stone_ids)
        _validate_client_eligibility(stones, memo["client_id"])
        cursor.execute(
            "UPDATE transaction_items SET status = 'active' WHERE transaction_id = ?", (transaction_id,)
        )
        cursor.executemany(
            "UPDATE stones SET status = ?, hold_client_id = NULL WHERE id = ?",
            [(LEGACY_MEMO, stone["stone_id"]) for stone in stones],
        )
        cursor.execute("UPDATE transactions SET status = 'active' WHERE id = ?", (transaction_id,))
    return {"id": transaction_id, "status": "active"}


def _selected_active_items(cursor, transaction_id, stone_ids, expected_stone_statuses):
    ids = _distinct_stone_ids(stone_ids)
    placeholders = ",".join("?" for _ in ids)
    rows = cursor.execute(
        f"""
        SELECT ti.*, s.status AS stone_status, s.stock_number AS current_stock_number
        FROM transaction_items ti
        JOIN stones s ON s.id = ti.stone_id
        WHERE ti.transaction_id = ? AND ti.stone_id IN ({placeholders})
        """,
        [transaction_id, *ids],
    ).fetchall()
    by_stone = {row["stone_id"]: row for row in rows}
    if len(by_stone) != len(ids):
        raise WorkflowError("Every selected Stone must belong to the transaction")
    ordered = [by_stone[stone_id] for stone_id in ids]
    for item in ordered:
        if item["status"] != "active":
            raise WorkflowConflict(f"Stone {item['stock_number']} is no longer active on this transaction")
        if item["stone_status"] not in expected_stone_statuses:
            raise WorkflowConflict(f"Stone {item['stock_number']} is not in the required inventory state")
    return ordered


def _selected_active_transaction_items(
    cursor,
    transaction_id,
    transaction_item_ids,
    expected_stone_statuses,
    activity_label="returnable",
):
    ids = _distinct_stone_ids(transaction_item_ids)
    placeholders = ",".join("?" for _ in ids)
    rows = cursor.execute(
        f"""
        SELECT ti.*, s.status AS stone_status,
               s.stock_number AS current_stock_number
        FROM transaction_items ti
        JOIN stones s ON s.id = ti.stone_id
        WHERE ti.transaction_id = ? AND ti.id IN ({placeholders})
        """,
        [transaction_id, *ids],
    ).fetchall()
    by_id = {row["id"]: row for row in rows}
    if len(by_id) != len(ids):
        raise WorkflowError(
            "Every selected transaction item must belong to the transaction"
        )
    ordered = [by_id[item_id] for item_id in ids]
    for item in ordered:
        if item["status"] != "active":
            raise WorkflowConflict(
                f"Stone {item['stock_number']} is no longer {activity_label}"
            )
        if item["stone_status"] not in expected_stone_statuses:
            raise WorkflowConflict(
                f"Stone {item['stock_number']} is not in the required inventory state"
            )
    return ordered


def _recalculate_memo_status(cursor, memo_id):
    statuses = [
        row["status"]
        for row in cursor.execute(
            "SELECT status FROM transaction_items WHERE transaction_id = ?", (memo_id,)
        ).fetchall()
    ]
    if statuses and all(status == "returned" for status in statuses):
        status = "cancelled"
    elif statuses and all(status in {"returned", "invoiced"} for status in statuses) and "invoiced" in statuses:
        status = "completed"
    else:
        status = "active"
    cursor.execute("UPDATE transactions SET status = ? WHERE id = ?", (status, memo_id))


def return_memo_stones(conn, transaction_id, stone_ids):
    with atomic_workflow(conn) as cursor:
        memo = cursor.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
        if memo is None or memo["type"] != "memo" or memo["status"] != "active":
            raise WorkflowConflict("Only an active memo can be returned")
        items = _selected_active_items(cursor, transaction_id, stone_ids, MEMO_STATUSES)
        cursor.executemany(
            "UPDATE transaction_items SET status = 'returned' WHERE id = ?",
            [(item["id"],) for item in items],
        )
        cursor.executemany(
            "UPDATE stones SET status = ?, hold_client_id = NULL WHERE id = ?",
            [(RETURN_PENDING, item["stone_id"]) for item in items],
        )
        _recalculate_memo_status(cursor, transaction_id)
    return {"returned": len(items), "transaction_id": transaction_id}


def return_memo_transaction_items(conn, transaction_id, transaction_item_ids):
    """Return selected Memo items using authoritative transaction-item IDs."""

    with atomic_workflow(conn) as cursor:
        memo = cursor.execute(
            "SELECT * FROM transactions WHERE id = ?", (transaction_id,)
        ).fetchone()
        if memo is None or memo["type"] != "memo" or memo["status"] != "active":
            raise WorkflowConflict("Only an active memo can be returned")
        items = _selected_active_transaction_items(
            cursor, transaction_id, transaction_item_ids, MEMO_STATUSES
        )
        cursor.executemany(
            "UPDATE transaction_items SET status = 'returned' WHERE id = ?",
            [(item["id"],) for item in items],
        )
        cursor.executemany(
            "UPDATE stones SET status = ?, hold_client_id = NULL WHERE id = ?",
            [(RETURN_PENDING, item["stone_id"]) for item in items],
        )
        _recalculate_memo_status(cursor, transaction_id)
    return {
        "returned": len(items),
        "transaction_id": transaction_id,
        "stock_numbers": [item["stock_number"] for item in items],
    }


def _snapshot_header_from_transaction(transaction, transaction_date):
    return {
        "person": transaction["person"],
        "phone": transaction["phone"],
        "fax": transaction["fax"],
        "date": transaction_date,
        "terms": transaction["terms"],
        "carrier": transaction["carrier"],
        "shipment_type": transaction["shipment_type"],
        "ship_charge": transaction["ship_charge"],
        "purchase_order_number": transaction["purchase_order_number"],
        "ship_to_address_id": transaction["ship_to_address_id"],
        "source_shipping_address_id": transaction["source_shipping_address_id"],
        "ship_to_label": transaction["ship_to_label"],
        "ship_to_manager": transaction["ship_to_manager"],
        "ship_to_store_number": transaction["ship_to_store_number"],
        "ship_to_address_snapshot": transaction["ship_to_address_snapshot"],
        "ship_to_city": transaction["ship_to_city"],
        "ship_to_state": transaction["ship_to_state"],
        "ship_to_country": transaction["ship_to_country"],
        "ship_to_phone": transaction["ship_to_phone"],
        "source_contact_id": transaction["source_contact_id"],
        "contact_email_snapshot": transaction["contact_email_snapshot"],
        "contact_cell_snapshot": transaction["contact_cell_snapshot"],
    }


def _conversion_invoice_header(memo, payload):
    day = _required_date(payload)
    header = _snapshot_header_from_transaction(memo, day)

    required_fields = {
        "terms": "Terms",
        "carrier": "Carrier",
        "shipment_type": "Shipment type",
        "person": "Contact name",
        "ship_to_address": "Ship To address",
    }
    for key, label in required_fields.items():
        if key in payload:
            value = _required_text(payload, key, label)
        elif key == "ship_to_address":
            value = header["ship_to_address_snapshot"]
        else:
            value = header[key]
        if not value:
            raise WorkflowError(f"{label} is required")
        if key == "ship_to_address":
            header["ship_to_address_snapshot"] = value
        else:
            header[key] = value

    optional_mapping = {
        "phone": "phone",
        "fax": "fax",
        "purchase_order_number": "purchase_order_number",
        "ship_to_label": "ship_to_label",
        "ship_to_manager": "ship_to_manager",
        "ship_to_store_number": "ship_to_store_number",
        "ship_to_city": "ship_to_city",
        "ship_to_state": "ship_to_state",
        "ship_to_country": "ship_to_country",
        "ship_to_phone": "ship_to_phone",
    }
    for request_key, header_key in optional_mapping.items():
        if request_key in payload:
            header[header_key] = _optional_text(payload, request_key)

    if "ship_charge" in payload:
        header["ship_charge"] = _required_nonnegative_number(payload, "ship_charge")
    elif header["ship_charge"] is None or float(header["ship_charge"]) < 0:
        raise WorkflowError("ship_charge must not be negative")
    return header


def convert_memo_items_to_invoice(conn, memo_id, transaction_item_ids, payload):
    """Atomically convert selected Memo items into one child Invoice."""

    with atomic_workflow(conn) as cursor:
        memo = cursor.execute(
            "SELECT * FROM transactions WHERE id = ?", (memo_id,)
        ).fetchone()
        if memo is None:
            raise WorkflowNotFound("Memo not found")
        if memo["type"] != "memo" or memo["status"] != "active":
            raise WorkflowConflict("Only an active Memo can be converted")
        _require_client(cursor, memo["client_id"])
        items = _selected_active_transaction_items(
            cursor,
            memo_id,
            transaction_item_ids,
            MEMO_STATUSES,
            activity_label="convertible",
        )
        header = _conversion_invoice_header(memo, payload)
        number = generate_transaction_number(cursor, "invoice", header["date"])
        invoice_id = _insert_transaction(
            cursor,
            number,
            memo["client_id"],
            "invoice",
            "active",
            header,
            parent_id=memo_id,
        )
        for item in items:
            stone = dict(item)
            stone["grading_report_id"] = item["grading_report_id"]
            _insert_snapshot_item(
                cursor,
                invoice_id,
                stone,
                "active",
                created_from_item_id=item["id"],
            )
        cursor.executemany(
            "UPDATE transaction_items SET status = 'invoiced' WHERE id = ?",
            [(item["id"],) for item in items],
        )
        cursor.executemany(
            "UPDATE stones SET status = ?, hold_client_id = NULL WHERE id = ?",
            [(LEGACY_SOLD, item["stone_id"]) for item in items],
        )
        _recalculate_memo_status(cursor, memo_id)
        memo_status = cursor.execute(
            "SELECT status FROM transactions WHERE id = ?", (memo_id,)
        ).fetchone()["status"]
    return {
        "id": invoice_id,
        "transaction_number": number,
        "parent_transaction_id": memo_id,
        "client_id": memo["client_id"],
        "converted": len(items),
        "stock_numbers": [item["stock_number"] for item in items],
        "memo_status": memo_status,
    }


def credit_invoice(conn, invoice_id, stone_ids, transaction_date=None):
    with atomic_workflow(conn) as cursor:
        invoice = cursor.execute("SELECT * FROM transactions WHERE id = ?", (invoice_id,)).fetchone()
        if invoice is None or invoice["type"] != "invoice" or invoice["status"] != "active":
            raise WorkflowConflict("Only an active invoice can be credited")
        items = _selected_active_items(cursor, invoice_id, stone_ids, SOLD_STATUSES)
        day = transaction_date or date.today()
        header = _snapshot_header_from_transaction(invoice, day)
        number = generate_transaction_number(cursor, "credit_invoice", day)
        credit_id = _insert_transaction(cursor, number, invoice["client_id"], "credit_invoice", "completed", header, invoice_id)
        for item in items:
            stone = dict(item)
            stone["grading_report_id"] = item["grading_report_id"]
            _insert_snapshot_item(cursor, credit_id, stone, "credited", item["id"], -abs(item["total_price"]))
        cursor.executemany("UPDATE transaction_items SET status = 'returned' WHERE id = ?", [(item["id"],) for item in items])
        cursor.executemany(
            "UPDATE stones SET status = ?, hold_client_id = NULL WHERE id = ?",
            [(RETURN_PENDING, item["stone_id"]) for item in items],
        )
        remaining = cursor.execute(
            "SELECT COUNT(*) AS count FROM transaction_items WHERE transaction_id = ? AND status = 'active'",
            (invoice_id,),
        ).fetchone()["count"]
        if remaining == 0:
            cursor.execute("UPDATE transactions SET status = 'completed' WHERE id = ?", (invoice_id,))
    return {"id": credit_id, "transaction_number": number, "parent_transaction_id": invoice_id}


def credit_invoice_transaction_items(
    conn, invoice_id, transaction_item_ids, transaction_date=None
):
    """Create one Credit Invoice from authoritative Invoice item IDs."""

    with atomic_workflow(conn) as cursor:
        invoice = cursor.execute(
            "SELECT * FROM transactions WHERE id = ?", (invoice_id,)
        ).fetchone()
        if (
            invoice is None
            or invoice["type"] != "invoice"
            or invoice["status"] != "active"
        ):
            raise WorkflowConflict("Only an active invoice can be credited")
        items = _selected_active_transaction_items(
            cursor, invoice_id, transaction_item_ids, SOLD_STATUSES
        )
        day = transaction_date or date.today()
        header = _snapshot_header_from_transaction(invoice, day)
        number = generate_transaction_number(cursor, "credit_invoice", day)
        credit_id = _insert_transaction(
            cursor,
            number,
            invoice["client_id"],
            "credit_invoice",
            "completed",
            header,
            invoice_id,
        )
        for item in items:
            stone = dict(item)
            stone["grading_report_id"] = item["grading_report_id"]
            _insert_snapshot_item(
                cursor,
                credit_id,
                stone,
                "credited",
                item["id"],
                -abs(item["total_price"]),
            )
        cursor.executemany(
            "UPDATE transaction_items SET status = 'returned' WHERE id = ?",
            [(item["id"],) for item in items],
        )
        cursor.executemany(
            "UPDATE stones SET status = ?, hold_client_id = NULL WHERE id = ?",
            [(RETURN_PENDING, item["stone_id"]) for item in items],
        )
        remaining = cursor.execute(
            """
            SELECT COUNT(*) AS count
            FROM transaction_items
            WHERE transaction_id = ? AND status = 'active'
            """,
            (invoice_id,),
        ).fetchone()["count"]
        if remaining == 0:
            cursor.execute(
                "UPDATE transactions SET status = 'completed' WHERE id = ?",
                (invoice_id,),
            )
    return {
        "id": credit_id,
        "transaction_number": number,
        "parent_transaction_id": invoice_id,
        "returned": len(items),
        "stock_numbers": [item["stock_number"] for item in items],
    }


def receive_stones(conn, stone_ids, received_by_user_id, note=None):
    ids = _distinct_stone_ids(stone_ids)
    note = None if note is None else str(note).strip() or None
    with atomic_workflow(conn) as cursor:
        receiver = cursor.execute(
            "SELECT id FROM users WHERE id = ? AND active = 1", (received_by_user_id,)
        ).fetchone()
        if receiver is None:
            raise WorkflowError("Authenticated receiving user is not active")
        rows = cursor.execute(
            f"SELECT id, stock_number, status FROM stones WHERE id IN ({','.join('?' for _ in ids)})",
            ids,
        ).fetchall()
        by_id = {row["id"]: row for row in rows}
        if len(by_id) != len(ids):
            raise WorkflowError("Every scanned Stone must exist")
        stones = [by_id[stone_id] for stone_id in ids]
        for stone in stones:
            if stone["status"] != RETURN_PENDING:
                raise WorkflowConflict(
                    f"Stone {stone['stock_number']} is not awaiting physical receipt"
                )
        for stone in stones:
            source = cursor.execute(
                """
                SELECT ti.id, ti.transaction_id
                FROM transaction_items ti
                WHERE ti.stone_id = ? AND ti.status = 'returned'
                ORDER BY ti.id DESC LIMIT 1
                """,
                (stone["id"],),
            ).fetchone()
            cursor.execute(
                """
                INSERT INTO receiving_events (
                    stone_id, stock_number_snapshot, source_transaction_id,
                    source_transaction_item_id, received_by_user_id, note
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    stone["id"],
                    stone["stock_number"],
                    source["transaction_id"] if source else None,
                    source["id"] if source else None,
                    received_by_user_id,
                    note,
                ),
            )
        cursor.executemany(
            "UPDATE stones SET status = ?, hold_client_id = NULL WHERE id = ?",
            [(LEGACY_AVAILABLE, stone["id"]) for stone in stones],
        )
    return {
        "received": len(ids),
        "stone_ids": ids,
        "stock_numbers": [stone["stock_number"] for stone in stones],
    }
