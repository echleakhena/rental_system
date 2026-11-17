# RPMS — Rental Property Management System

A Python/Flask rental management application with Jinja templates, SQLAlchemy and SQLite.
## Start the existing project
```sh
.venv/bin/python app.py
```
Open **http://127.0.0.1:5001**. The current installation uses SQLite at **`instance/rpms.db`**, selected through private `instance/database.json`. `DATABASE_URL`, when set, overrides that file.
Use your installation's staff or customer credentials. Demo credentials are not
published here; change seeded passwords before using real customer data.

Do not rerun database setup or seed commands to start an existing installation.

## Where the code lives

| File / directory | Purpose |
|---|---|
| `app.py` | Starts the local development server |
| `rpms/__init__.py` | Creates and configures the Flask application |
| `rpms/routes/` | Page handlers, grouped by feature |
| `rpms/web.py` | Shared blueprint, login checks, CSRF and template helpers |
| `rpms/routes/__init__.py` | Registers route modules on the blueprint |
| `rpms/record_config.py` | Menu groups, form fields and allowed statuses |
| `rpms/models.py`, `rpms/schema.json` | SQLAlchemy models and the 26-table schema |
| `rpms/services.py` | Lease activation, billing, payment and audit rules |
| `rpms/customer.py`, `rpms/customer_models.py` | Mobile customer routes, staff review, accounts and payment proofs |
| `rpms/templates/customer/`, `rpms/static/customer.css` | Mobile customer pages and styles |
| `rpms/policies.py` | Rules controlling which records can change |
| `rpms/reporting.py` | Summary cards, chart data and MIS recommendations |
| `rpms/exports.py` | Excel report generation |
| `rpms/photos.py` | Image validation and resizing |
| `rpms/seed.py` | Administrator and demo-data commands |
| `rpms/templates/` | HTML/Jinja pages and reusable components |
| `rpms/static/style.css` | Shared layout, forms, tables and sidebar styles |
| `rpms/static/dashboard.css` | Dashboard, room board and MIS styles |
| `rpms/static/theme.css`, `theme.js` | Light/dark mode |
| `migrations/` | Database migration history and configuration |
| `tests/test_app.py` | Automated verification using isolated SQLite databases |
| `instance/` | Private settings, SQLite backups and uploaded photos; excluded from Git |

Route modules are `auth.py`, `dashboard.py`, `records.py`, `rooms.py`, `billing.py` and `reports.py`. For example, room upload handlers are in `rpms/routes/rooms.py`, while their page is `rpms/templates/room.html`.

Application code belongs outside `.venv/`, which contains installed Python packages.

## Features and workflows

- **Portfolio:** property → floor → room. Rooms support four categorized photos (Room, Bathroom, Kitchen and Toilet), status totals, search and detailed views.
- **Leasing:** tenant → draft contract → primary tenant link → activate lease. Activation checks overlapping leases and conflicting reservations.
- **Utilities:** add a meter and increasing readings. Approved readings become charges on that month's invoice.
- **Billing:** generate an invoice from an active lease. Duplicate monthly invoices are blocked.
- **Payments:** open an invoice and record Cash, Transfer or KHQR payments, including partial payments. KHQR is a recorded method, not a banking integration.
- **Reports:** customer payment totals and Excel download. MIS & DSS adds charts and explainable next-step suggestions. The pricing simulator uses your assumptions rather than a statistical forecast.
- **Access:** sign-in is required. Admin/Manager roles can change permitted records; other active roles can view records and receive module write permissions through Settings. Deletion requires confirmation and is blocked for linked or protected records. Financial and system records use dedicated workflows.

Record codes are generated automatically and hidden from create/edit forms. Physical room numbers, floor numbers, meter serials and bank references remain user supplied.

Room photos accept JPEG, PNG and WebP, up to 5 MB and 20 megapixels each. They are resized to JPEG files under `instance/room_photos`. Seeded stock photos are labelled as samples.

## Dependencies and code formatting

Install runtime packages only when setting up a new environment or changing dependencies:

```sh
.venv/bin/python -m pip install -r requirements.txt
```

Optional development tools and checks:

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/ruff check app.py rpms scripts tests migrations
.venv/bin/ruff format app.py rpms scripts tests migrations
.venv/bin/djlint rpms/templates --reformat
.venv/bin/python scripts/format_frontend.py
.venv/bin/python -m unittest discover -s tests -v
```

Formatting settings are in `pyproject.toml`. Static file URLs automatically refresh when a file changes, so manual cache-version numbers are unnecessary.

## Database maintenance

Apply future migrations with:

```sh
.venv/bin/flask --app app db upgrade
```

To transfer a PostgreSQL installation to SQLite while preserving records:

```sh
.venv/bin/python scripts/configure_sqlite.py
```

The script reads PostgreSQL without changing it, creates a migrated SQLite database,
normalizes timestamps to UTC, verifies every row and SQLite integrity, backs up any
existing SQLite file and settings, then switches the local configuration. Stop the
app while transferring and restart it afterward so no writes are missed. The current
installation has already completed this transfer (67 records across 28 tables).

For optional legacy PostgreSQL tools, install `requirements-postgres.txt` first.
To configure a **different empty** PostgreSQL installation and optionally transfer SQLite records:

```sh
.venv/bin/python scripts/configure_postgres.py
```

The script uses database name `rental_system`, prompts for connection details, backs up SQLite, copies data in a transaction, verifies row counts, resets ID sequences and saves private settings. It refuses to overwrite an installation containing application records. The local installation previously used PostgreSQL and has now been transferred back to SQLite.

Back up `instance/rpms.db`, `instance/room_photos`, and `instance/customer_uploads` together. Keep `instance/database.json` and `instance/secret.key` private.

For a completely new empty installation only:

```sh
.venv/bin/flask --app app db upgrade
.venv/bin/flask --app app create-admin
# Or create illustrative demo records instead of an empty installation:
.venv/bin/flask --app app seed-demo
.venv/bin/flask --app app seed-room-photos
```

Without local database settings the application defaults to `instance/rpms.db`. The seed command refuses to seed a populated installation; photo seeding only fills empty slots.

## Remaining scope

See [requirements review](docs/requirements-review.md) for the original schema comparison. Dedicated deposit/refund settlement, reservation confirmation/expiry, move-out and renewal workflows, payment reversal, private tenant-document storage and recurring billing remain unfinished.

Lease activation currently marks a room occupied immediately, including future leases. Billing uses each utility reading's month. Reports aggregate all issued invoices and group customers by their primary lease link.

The Flask server is for local development. A production deployment also needs HTTPS, secure cookies, a WSGI server, login throttling, backups and concurrent PostgreSQL workflow testing.

## Mobile customer portal

Open **http://127.0.0.1:5001/customer/** for the customer experience. It is designed
for phones, with Home, Room, My Rental and Profile navigation, and stays centered
on larger screens. Customers can register with a name, phone and password, browse
available rooms, request or cancel pending bookings, see rental and booking
history, submit water/electric meter numbers with photos, and track monthly bills.
Domestic Cambodian phone numbers and their `+855` forms use the same login.

Open **Customer requests** in the management sidebar (`/customer-management`) to:

1. Confirm a pending booking. This reserves the room; create a lease from that
   reservation and activate it through the existing leasing workflow.
2. Review the customer's meter photo and reading. For a first reading, verify and
   correct its starting value through the edit link before approving it; otherwise
   the provisional starting value is zero. Confirm the utility rate as well.
3. Approve all readings for the month, then generate the lease's monthly invoice.
   Rent and approved utilities are calculated together. The issued bill immediately
   appears in the customer's My Rental tab. Readings cannot be submitted for months
   that have already been billed; management must handle corrections.
4. Upload the property's bank payment QR. Customers can save/scan it, transfer the
   amount in their bank app, and upload a payment screenshot and transaction reference.
5. Verify the actual bank receipt, then confirm or reject the proof. Confirmation
   uses the existing payment/allocation workflow, supports partial payments, and
   updates the customer's bill. Repeated confirmation cannot charge twice.

Payment-provider integration is deferred. This version uses an uploaded bank QR
and manual verification; it does not generate dynamic KHQR codes or receive bank
callbacks. No QR or merchant account is preconfigured.

Customer accounts are separate from staff accounts and cannot access management
routes. Each account only sees its linked tenant's rental and financial records.
Meter photos and payment proofs are private, stored in `instance/customer_uploads`;
include that directory in backups. Room photos are public only for available rooms,
or accessible to the resident who has a booking or lease for the room.

New registrations deliberately create a new tenant record. Management can verify
an existing resident and link their account to the correct tenant from Customer
requests, before that account has bookings or rental history. Knowing a phone
number alone never grants access to an existing resident's records. Phone ownership
verification, SMS password recovery, automatic bank confirmation, and production
login throttling are not implemented.

For another installation, apply the customer-account/payment-proof migration before
starting the new code:

```sh
.venv/bin/python -m flask --app app db upgrade
```

Use `.venv/bin/python -m flask` if the virtual environment was moved and the `flask`
launcher still references the previous folder. Customer workflow tests are in
`tests/test_customer.py` and use temporary uploads and isolated SQLite databases.

## Focused management workspace

The main sidebar contains Overview, Customer requests, Rooms, Customers, Rentals,
Invoices & payments, Utilities, Maintenance, and Reports. Less frequent setup,
financial detail, and administration screens are available in the closed
**More tools** menu in the top bar. Existing routes and permissions still apply.
The dashboard prioritizes real pending booking, utility-reading, and payment-proof
counts; room status filters and financial summaries remain visible. Portfolio
charts are available in the expandable Portfolio insights section.

## Settings: utility prices and staff access

Open **Settings** in the management sidebar (`/settings`). Authorized utility staff
can update each active meter's default water/electricity price, including meters
with reading history. Changes apply to future readings; saved reading rates and
existing invoices are preserved.

Only Admin can create staff users, assign staff roles, and save role permissions.
Admin and Manager retain full operational write access; staff administration stays
Admin-only. Other active roles can view management records and can receive these
module write permissions from the Role permissions controls:

- `portfolio.manage`: properties, floors, rooms and room photo uploads.
- `leasing.manage`: customers, bookings, rental contracts and resident linking.
- `utilities.manage`: meters, future utility prices and reading review.
- `billing.manage`: invoices, expenses, payments, bank QR and payment-proof review.
- `maintenance.manage`: maintenance requests and room inspections.

Changes are enforced on server requests immediately. Unchecking a module removes
its write access. Other permission codes remain available as records but do not
automatically add new application capabilities. Customer accounts remain separate
from staff accounts.

Settings submenus open separate pages: Utility prices at `/settings/utilities`,
Users at `/records/users`, Roles at `/records/roles`, and Permissions at
`/settings/permissions`. Each page shows only its own management information.
Saving prices or permissions stays on the corresponding page.

The management Customers page (`/records/tenants`) uses only Name, Gender, Phone
and Password. Creating a customer also creates their linked portal account.
Passwords are stored as hashes and shown only as masked placeholders. When editing
an existing account, leave Password blank to preserve it; phone edits update the
portal login phone. Existing extra tenant fields are retained in storage for
compatibility but are hidden from the customer form, list and detail page.

Rental detail pages (`/records/rental_contracts/<id>`) show the rental period,
linked customers, room information, deposit and payment totals, and billing
history. Draft activation and active-lease invoicing use the existing workflows
and return to the same rental detail page. Totals exclude draft and voided invoices.

The sidebar groups Rentals, Invoices and Payments under the expandable
**Rentals & payments** menu. It opens automatically on related record/detail
pages and highlights the selected section. Each section retains its own page.

## Combined invoices and utility readings

Open **Rentals & payments → Invoices & utilities** (`/records/invoices`). The old
`/records/meter_readings` list opens the same workspace. Select a rental and billing
month, review customer-submitted readings or enter staff readings, approve drafts,
then issue one invoice for rent plus utilities. First readings require the actual
starting meter value. Later readings continue from the previous reading. Staff
readings use the meter's saved Settings rate and are approved when saved.

The server calculates all charges. Issuing is blocked while draft readings remain,
and an existing monthly invoice is opened rather than recalculated. Issued bills
show the saved previous/current readings, usage, rate and amount on both the staff
and customer sides. Customers receive the bill automatically in My Rental. Staff
can also print the invoice. No email, SMS or banking notification is sent.

Utility write permission is required to enter/approve readings; billing write
permission is required to issue an invoice. An issued month cannot accept further
readings through this workspace.

The combined workspace waits for a monthly reading from every active room meter
and for all draft readings to be approved before issuing. This prevents issuing
a bill with missing water/electricity charges. Rental detail pages now open the
workspace via Prepare invoice so the readings can be reviewed before issue.

## Production configuration

Set `RPMS_ENV=production` and a persistent, randomly generated `SECRET_KEY` in your
hosting environment. Production mode requires an explicit secret, enables secure
session cookies (HTTPS required), and disables automatic template reloading. Generate
a secret with `.venv/bin/python -c "import secrets; print(secrets.token_hex(32))"`.
Keep the same secret across server workers and restarts. Replacing it logs users out.
The `.env.example` file documents variables; the app does not automatically load
`.env` files. Export variables in the shell or configure them through your host.

Run the WSGI application `app:app` using your deployment's WSGI server behind HTTPS.
`python app.py` remains a local development command. Apply database migrations before
starting a new release, and back up the database and both upload directories together.
Test backup restoration before entering real data.

The automated suite covers SQLite. PostgreSQL concurrency, login throttling, phone
verification and the unfinished workflows listed above still need work before a
public production rollout. Do not treat this cleanup as production certification.
