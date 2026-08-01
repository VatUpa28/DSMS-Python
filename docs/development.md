# DSMS Flask V1 development

Run every command below from the project root (`C:\Users\vatsa\Desktop\DSMS`).

## First-time setup

Use Python 3.12 (or another supported Python 3 release if 3.12 is unavailable). Do not reuse the legacy `venv` directory; the active environment is `.venv`.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell activation is blocked, invoke the environment directly instead:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Start the development application

Set a local development session secret for each shell. Never use this placeholder in production.

```powershell
$env:DSMS_ENV = "development"
$env:DSMS_SECRET_KEY = "replace-with-a-local-development-secret"
.\.venv\Scripts\python.exe backend\app.py
```

The default development URL is `http://127.0.0.1:5000`. Optional settings are `DSMS_HOST`, `DSMS_PORT`, and `DSMS_DEBUG`. Production must set `DSMS_SECRET_KEY` and should set `DSMS_ENV` to a non-development value; production startup details are deployment-specific.

Development uses the configured database path, which defaults to `backend\database\app.db`. Do not point `DSMS_DB_PATH` at a production database when running locally.

## Authentication and sessions

The application entry point is `http://127.0.0.1:5000/`. Logged-out users are redirected to `/login`; logged-in users are redirected to `/inventory`. Opening `/inventory` without a valid active session redirects to `/login?next=/inventory`, and a successful login safely returns there.

The browser login page is `http://127.0.0.1:5000/login`. Accounts use normalized email addresses and are created administratively; there is no public registration or password-reset page. Development mode still requires a valid login and never bypasses authentication.

Authenticated navigation displays the current user and role. Change a password at `/account/change-password`. The existing password policy is at least 12 characters. A successful change clears the session and requires sign-in with the new password. Logout is a CSRF-protected `POST` to `/logout`; it is not a state-changing link or GET request.

The application stores only the user ID and Flask security state in its signed session cookie. User details and roles are loaded from SQLite for each request and cached only for that request. Deleted, inactive, or invalid session users must sign in again.

Session cookies are `HttpOnly` and `SameSite=Lax`. `Secure` is off by default only when `DSMS_ENV=development` serves local HTTP; it is required outside development. `DSMS_SESSION_COOKIE_SECURE` accepts an explicit true/false value for local configuration, but a false value is rejected outside development. Production must use HTTPS and a strong, private `DSMS_SECRET_KEY`.

The established roles are:

- `ADMIN`
- `MANAGER`
- `SALES`
- `ACCOUNTING`

`INVENTORY` is not a valid role and is rejected by provisioning and login. Backend role checks are authoritative. Navigation visibility is only a usability aid.

Permission summary:

| Capability | Allowed roles |
| --- | --- |
| View Inventory and company-data pages | All authenticated roles |
| Create/import Stones and correct catalog data | `ADMIN`, `MANAGER` |
| Receive returned Stones | `ADMIN`, `MANAGER` |
| Place or release HOLDs | `ADMIN`, `MANAGER`, `SALES` |
| Create Memos | `ADMIN`, `MANAGER`, `SALES` |
| Create direct Invoices | `ADMIN`, `MANAGER`, `ACCOUNTING` |
| Print barcodes | `ADMIN`, `MANAGER`, `SALES` |
| Rapaport, discount, and price management | `ADMIN`, `MANAGER` |

Login rate limiting is deferred for V1. A process-local counter would be unreliable across production workers, and the deployment topology is not yet defined. Add a shared-store-backed limiter when the production hosting design is selected; no permanent account lockout is implemented.

## CSRF protection

Flask-WTF protects every unsafe request (`POST`, `PUT`, `PATCH`, and `DELETE`). Jinja forms must include:

```jinja2
<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
```

Existing browser JavaScript uses `dsmsFetch`, which adds the current token as:

```text
X-CSRFToken: <signed token>
```

Safe `GET` requests do not require a token. A missing, expired, or invalid token returns a readable HTTP `400`; CSRF must not be disabled in production.

## Run tests

Tests create temporary SQLite databases and set `DSMS_TESTING=1`; they are guarded from falling back to `backend\database\app.db`.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Upgrade the development database

The application does not run migrations on startup. Run the explicit safe upgrade command only when needed. It preserves the existing database and creates a timestamped backup before an unapplied migration changes it.

```powershell
Push-Location backend
..\.venv\Scripts\python.exe -m database.upgrade_db
Pop-Location
```

## Create an initial user

This command prompts twice for a password, so no password is placed in shell history. Use a strong unique password; do not paste one into documentation or a script.

```powershell
Push-Location backend
..\.venv\Scripts\python.exe -m database.create_user --email "admin@example.test" --first-name "Admin" --last-name "User" --role ADMIN
Pop-Location
```

Email is normalized to lowercase, role input is normalized to uppercase and checked against the fixed role list, duplicate emails are rejected, and the prompted password is stored only as a Werkzeug hash.

## ADMIN User Management

Active `ADMIN` users can open `/admin/users` from the authenticated navigation. The page lists active accounts before inactive accounts and supports:

- Creating an active account at `/admin/users/new`.
- Assigning exactly `ADMIN`, `MANAGER`, `SALES`, or `ACCOUNTING`.
- Changing a user's role.
- Deactivating an account without deleting it or its history.
- Reactivating an account without changing its role or password hash.

`INVENTORY` and arbitrary role names are invalid. New-user email addresses are
trimmed, converted to lowercase, and checked for case-insensitive duplicates.
First and last names are required. Temporary passwords must contain at least 12
characters and match their confirmation; password fields are never redisplayed.
Share a temporary password securely because DSMS does not show it again.

All User Management mutations are CSRF-protected `POST` requests and use short
SQLite write transactions. An administrator cannot remove their own
administrator access while signed in, and DSMS will not allow the last active
administrator to be demoted or deactivated. Accounts are never permanently
deleted.

There is currently no email password-reset workflow or separate safe ADMIN
password-reset CLI command. An administrator needing that capability is a
follow-up item; existing password hashes must never be displayed or replaced
with a password supplied as a visible command-line argument.

## Manual Memo and Invoice workspace

Open `/transactions/new` to create manual transactions:

| Transaction | Allowed roles |
| --- | --- |
| Memo draft or active Memo | `ADMIN`, `MANAGER`, `SALES` |
| Direct Invoice | `ADMIN`, `MANAGER`, `ACCOUNTING` |

`ADMIN` and `MANAGER` see both transaction types. `SALES` sees only Memo and
`ACCOUNTING` sees only Invoice. The backend applies the same authorization to
every mutation endpoint.

Search for a client by code or name and explicitly choose the correct result.
The workspace then loads only that client's contacts and shipping addresses.
Choosing either copies its values into editable transaction snapshot fields;
editing those fields does not change the saved client record. Manual contact
and Ship To values are also supported. The final snapshot remains attached to
the transaction even if saved client information changes later.

Stone searches are bounded and run in SQLite. Filters include stock number,
up to 25 pasted stock numbers, Lab, shape, weight range, color, clarity, cut,
polish, symmetry, and fluorescence. Eligible Stones are:

```text
AVAILABLE
OR HOLD assigned to the selected client
```

Another client's HOLD, `MEMO`, `SOLD`, and `RETURN_PENDING` are excluded.
Eligibility is rechecked inside the write transaction; browser selections are
never authoritative.

- `POST /api/transactions/memos` creates a draft Memo. Stone status and an
  existing same-client HOLD remain unchanged.
- `POST /api/transactions/memos/active` creates an active Memo atomically.
  Selected Stones become `MEMO` and any same-client HOLD assignment is cleared.
- `POST /api/transactions/invoices` creates a direct Invoice with no parent
  Memo. Selected Stones become `SOLD` and any same-client HOLD assignment is
  cleared.

Transaction numbers are generated server-side. Browser requests cannot choose
the number, final status, parent transaction, item snapshots, or Stone status.
All JSON mutations use the existing `X-CSRFToken` header. Action buttons are
disabled during submission, and meaningful unsaved transaction changes trigger
a leave-page warning. Search-filter changes alone do not trigger that warning.

## Returns and physical receiving

Open an active Memo or Invoice transaction detail page and choose `Return Items`
when at least one item is returnable. Return permissions are:

| Operation | Allowed roles |
| --- | --- |
| Return active Memo items | `ADMIN`, `MANAGER`, `SALES` |
| Return Invoice items and create a Credit Invoice | `ADMIN`, `MANAGER`, `ACCOUNTING` |
| Physically receive returned Stones | `ADMIN`, `MANAGER` |

The return page uses transaction-item IDs, not submitted stock numbers, prices,
or statuses. Partial returns leave unselected items active. Returning every
active Memo item changes the Memo to the existing terminal `cancelled` status.
Returning Invoice items creates one linked Credit Invoice with a generated
`CR-YYYYMMDD-####` number and preserved transaction and item snapshots.

Both return types move the Stone to:

```text
RETURN_PENDING
```

This means the item has been recorded as returned but has not yet been
physically checked in. It remains unavailable for HOLD, Memo, or Invoice until
Receiving completes.

`ADMIN` and `MANAGER` users can open `/receiving`. The queue contains only
`RETURN_PENDING` Stones and supports:

- Partial stock-number filtering through the page URL.
- Exact stock-number scan lookup.
- Pasted stock numbers separated by whitespace or commas.
- Deduplication in stable input order.
- Up to 100 pasted stock numbers per selection.
- Atomic checkbox batch receiving with an optional receiving note.

Receiving posts Stone IDs to `/receive-stones` with the standard
`X-CSRFToken` header. SQLite reloads every Stone and accepts only
`RETURN_PENDING`; a mixed valid/invalid batch receives none. Successful receipt
changes the Stone to AVAILABLE, clears stale HOLD ownership, and inserts one
`receiving_events` row per Stone containing the Stone ID, stock-number
snapshot, source transaction/item when available, authenticated user, timestamp,
and optional note.

Basic receiving history is available at `/receiving/history`, newest first.
The return form currently accepts an optional operational note for the active
request, but the frozen schema has no return-note field or return-event table;
that note is therefore not historical. Receiving notes are persisted.

## Memo-to-Invoice conversion

`ADMIN`, `MANAGER`, and `ACCOUNTING` users can open an active Memo and choose
`Create Invoice from Memo`. `SALES` may view the Memo but cannot open or submit
the conversion workflow.

- Page: `GET /transactions/<memo_id>/convert-to-invoice`
- Mutation: `POST /api/transactions/memos/<memo_id>/convert-to-invoice`

Only active Memo transaction items whose linked Stones are still in the
`MEMO` inventory state are selectable. Draft, cancelled, and completed Memos
cannot be converted. Returned or previously invoiced items cannot be selected
again. The mutation accepts transaction-item IDs and permitted Invoice header
and snapshot corrections; it does not accept authoritative client, Stone,
price, status, transaction-number, or parent-transaction values.

Each submission creates one child Invoice with a generated
`INV-YYYYMMDD-####` number and `parent_transaction_id` pointing to the source
Memo. Invoice contact and Ship To values begin with the Memo's historical
snapshots. Approved edits affect only the new Invoice. Invoice item values are
copied from the Memo transaction-item snapshots, so conversion does not reload
current grading data or reprice Stones.

Selected Memo items become `invoiced`, and their Stones move from `MEMO` to
`SOLD`. Unselected items and Stones remain active on the Memo, allowing later
partial conversions. The Memo remains active while any active item remains.
When all items have been resolved through conversion and/or return, a Memo with
at least one invoiced item becomes `completed`, not `cancelled`. Transaction
detail pages link a child Invoice back to its source Memo and list all child
Invoices on the source Memo.

The conversion uses one short `BEGIN IMMEDIATE` SQLite transaction. Number
allocation, Invoice and item inserts, source-item changes, Stone changes, and
Memo completion are rolled back together on any validation or database error.
JSON submission uses the standard `X-CSRFToken` header and prevents duplicate
browser submission.

Legacy databases require migration `003_add_invoiced_item_status`, which
extends the existing transaction-item status constraint to allow the already
established `invoiced` workflow state. The safe upgrade command above creates
a timestamped database backup before rebuilding that constrained table.
Migrations `001_workflow_foundation` and `002_remove_stored_barcode_path`
remain unchanged.

## Stone HOLD management

All authenticated roles may open `/holds` to view current reservations.
`ADMIN`, `MANAGER`, and `SALES` may place and release HOLDs; `ACCOUNTING` has a
read-only view. Inventory and Client pages link to the same HOLD workspace.

Placing a HOLD uses the existing client search by code or name and a bounded,
server-side AVAILABLE-Stone search. Search supports stock number, up to 25
pasted stock numbers, Lab, shape, weight range, color, clarity, cut, polish,
symmetry, and fluorescence. Only Stones currently in `Y`/`AVAILABLE` are
returned. Browser selection uses Stone IDs, but the service reloads the client
and every Stone after `BEGIN IMMEDIATE` before applying any change.

- `POST /api/holds` accepts a client ID and Stone IDs, then atomically changes
  every selected Stone from AVAILABLE to HOLD and assigns `hold_client_id`.
- `POST /api/holds/release` accepts only Stone IDs, then atomically changes
  every valid HOLD to AVAILABLE and clears `hold_client_id`.

Duplicate IDs, missing clients, mixed valid/invalid batches, stale statuses,
and non-HOLD releases fail without partial changes. HOLDs do not create a
transaction or consume a transaction number. Both JSON mutations require the
standard `X-CSRFToken` header and backend role authorization.

A held Stone remains eligible for a Memo or direct Invoice only for its
assigned client. Other clients cannot select or submit it. Moving a same-client
HOLD onto an active Memo or Invoice clears `hold_client_id` in the same atomic
transaction.

There is no HOLD event/audit table in the current schema. The page therefore
shows current HOLD state only; historical HOLD/release reporting is deferred
until a separately approved audit-history design and migration.

## Client-context Inventory

`GET /inventory` retains the general Inventory view until an authenticated user
explicitly selects a client. Client search reuses
`GET /api/transaction-workspace/clients?q=...` and searches client code, name,
address, Polygon/JBT/RapNet/tax IDs, contact name/phone/email/fax/cell, and saved
shipping label/manager/store/address/city/state/country/phone. Results are
bounded, de-duplicated by authoritative client ID, and are never selected
automatically.

After selection, `GET /api/inventory/client-context/<client_id>` returns the
authoritative client details, contacts, shipping addresses, all AVAILABLE
Stones, only that client's HOLD Stones, and only active/convertible Memo items
for that client. Memo items use historical transaction-item snapshots and are
grouped by source Memo. Server-side filters cannot broaden these eligibility
boundaries.

`ADMIN` and `MANAGER` may start either handoff. `SALES` may select AVAILABLE or
same-client HOLD Stones and open the Memo workspace, but cannot start Memo
conversion. `ACCOUNTING` may open a Memo-to-Invoice conversion, but cannot
start a Memo. The UI keeps new-Memo Stone selection separate from Memo-item
selection and permits Memo items from only one source Memo per Invoice.

- Memo handoff: `GET /transactions/new?type=memo&client_id=<id>&stone_ids=<ids>`
- Conversion handoff:
  `GET /transactions/<memo_id>/convert-to-invoice?item_ids=<item-ids>`

Both handoffs are read-only. They de-duplicate and cap selections, reload every
record from SQLite, enforce roles and client ownership, and visibly preselect
only eligible records. Final creation still uses the existing CSRF-protected,
atomic workflow endpoints, including generated numbers and historical
snapshots. Changing or clearing the selected client clears incompatible
selection after warning the user.
