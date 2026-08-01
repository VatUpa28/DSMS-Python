# Flask V1 workflow foundation

## Safe database upgrade

Run the Flask upgrade from the `backend` directory:

```powershell
python -m database.upgrade_db
```

`database/upgrade_db.py` creates a timestamped `app.pre-<migration>-*.db` backup before applying pending migrations. It adds nullable transaction snapshot fields plus `users`, `transaction_number_counters`, `receiving_events`, and `schema_migrations`, then safely rebuilds the SQLite `stones` table to remove the obsolete persistent `barcode_path` column while preserving Stone records. It never recreates the database file.

## Stone status behavior

Existing Flask status values remain in storage for compatibility:

| Meaning | Stored value |
|---|---|
| Available | `Y` |
| Hold | `H` |
| Memo | `M` |
| Sold | `S` |
| Returned, awaiting receipt | `RETURN_PENDING` |

`GET /api/clients/<client_id>/eligible-stones` returns only Available Stones plus Holds belonging to that client. The same eligibility rule is rechecked inside every direct-invoice and memo workflow transaction.

## Direct invoice API

`POST /api/transactions/invoices` requires a logged-in `ADMIN`, `MANAGER`, or `ACCOUNTING` user. It accepts JSON:

```json
{
  "client_id": 12,
  "person": "Jane Buyer",
  "phone": "555-0100",
  "contact_email": "jane@example.com",
  "source_contact_id": 8,
  "source_shipping_address_id": 4,
  "date": "2026-07-29",
  "terms": "NET_30",
  "carrier": "FEDEX",
  "shipment_type": "DELIVERY",
  "ship_charge": 25.0,
  "purchase_order_number": "PO-1007",
  "stone_ids": [101, 102]
}
```

For manual Ship To entry, omit `source_shipping_address_id` and provide `ship_to_address` (required) with any of `ship_to_label`, `ship_to_manager`, `ship_to_store_number`, `ship_to_city`, `ship_to_state`, `ship_to_country`, and `ship_to_phone`. A selected saved address is loaded from SQLite and copied to the transaction, so submitted values cannot impersonate a different saved address.

The server generates the active invoice number, copies item snapshots, and moves every selected eligible Stone to `SOLD` (`S` in the legacy database). It rejects all invalid requests before changing any Stone. Direct invoices always have `parent_transaction_id = NULL`.

## Number allocation and rollback

Numbers use `MEMO-YYYYMMDD-####`, `INV-YYYYMMDD-####`, and `CR-YYYYMMDD-####`. `transaction_number_counters` has an independent counter for each transaction type and date. Allocation happens within the same SQLite `BEGIN IMMEDIATE` transaction as transaction creation. Therefore a failed workflow rolls the counter back: failed creation does **not** consume a number.

## Receiving and authorization

`POST /receive-stones` requires `ADMIN` or `MANAGER`; it accepts `{"stone_ids": [101], "note": "optional"}`. Only `RETURN_PENDING` Stones can be received. Receipt writes a `receiving_events` row with the authenticated session user, time, stock-number snapshot, optional source transaction/item, and note before restoring the Stone to `Y`.

`POST /auth/login` establishes the Flask session from the `users` table and validates a Werkzeug password hash. Provision the first user without putting a password in shell history with `python -m database.create_user --email admin@example.com --first-name Admin --last-name User --role ADMIN`; it securely prompts for a password. A user-management screen and login template are deliberately outside this task. A production `DSMS_SECRET_KEY` must be supplied through the environment. Existing legacy forms have no CSRF token infrastructure, so CSRF protection is a required follow-up before exposing those session-authenticated form routes outside a trusted environment.
## Memo-to-Invoice conversion

An active Memo may be converted partially or completely by `ADMIN`, `MANAGER`,
or `ACCOUNTING`. The conversion page is
`GET /transactions/<memo_id>/convert-to-invoice`, and its CSRF-protected JSON
mutation is
`POST /api/transactions/memos/<memo_id>/convert-to-invoice`.

The server derives the client and parent Memo from the route, reloads selected
transaction-item IDs and their Stones inside `BEGIN IMMEDIATE`, generates the
Invoice number, copies the Memo's historical header and item snapshots, marks
the source items `invoiced`, and changes their Stones from `MEMO` to `SOLD`.
No current grading or pricing data is consulted. Unselected items remain active
and may be converted by a later child Invoice. A Memo closes as `completed`
when no active items remain and at least one item was invoiced; an all-returned
Memo follows the existing `cancelled` behavior.

The `parent_transaction_id` relationship supports multiple child Invoices.
Migration `003_add_invoiced_item_status` safely adds the established
`invoiced` status to the SQLite transaction-item constraint. It backs up an
existing database before changing the constrained table and does not modify
frozen migrations 001 or 002.

## HOLD placement and release

The authenticated HOLD workspace is `/holds`. `ADMIN`, `MANAGER`, and `SALES`
can mutate HOLDs; `ACCOUNTING` may view the current reservation table only.
Placement and release use `POST /api/holds` and
`POST /api/holds/release`, respectively, with Flask-WTF's `X-CSRFToken`
header.

Placement validates one authoritative client and every selected Stone inside a
single `BEGIN IMMEDIATE` transaction. All Stones must still be AVAILABLE before
the batch is changed to HOLD and assigned that client's ID. Release similarly
requires every selected Stone to still be HOLD with an assigned client before
the batch becomes AVAILABLE and its client IDs are cleared. Duplicate or mixed
invalid selections roll back completely.

Client-aware transaction eligibility remains AVAILABLE or HOLD owned by the
selected client. HOLDs owned by another client remain excluded and fail
server-side workflow validation. The current schema has no HOLD event-history
table, so history is intentionally deferred and no migration was added.
