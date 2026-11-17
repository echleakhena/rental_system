# Requirements review

Source: `RPMS_Python_Database_Schema.pdf`, supplied by the user. It is an 11-page proposed schema derived from another proposal, not the original proposal itself. Document technology recommendations were treated as design context; the user's explicit Python/Flask request governs implementation.

| Requirement | Current implementation |
|---|---|
| 26 named relational tables | SQLAlchemy schema with foreign keys and selected uniqueness constraints |
| Flask and Python frontend | Flask routes, Jinja templates, responsive CSS |
| PostgreSQL | Configurable URL and psycopg driver; local verification uses SQLite |
| Login and roles | Hashed passwords, sessions, CSRF, Admin/Manager writes, configurable module write permissions and other-role reads |
| Fine-grained permissions | Five module write permissions enforced through Settings; staff administration is Admin-only |
| Properties/floors/rooms/tenants | Creation, listing and selected edits |
| Reservations | Pending/cancelled record entry; confirmation and expiry workflow pending |
| Contracts | Draft entry, tenant linking, activation and overlap checks |
| Deposits | Pending balance created on activation; receipt/refund workflow pending |
| Inspections | Record entry; photos/private storage pending |
| Utilities | Meters, monotonic readings, rate snapshots and invoice incorporation |
| Monthly invoices | Rent + approved utilities, unique contract/month |
| Partial payments | Confirmed receipt and allocation with overpayment protection |
| Maintenance and expenses | Forms, listings, maintenance queue |
| Move-outs and renewals | Schema and read-only listings; workflow pending |
| MIS | Occupancy, all-time collected payments and outstanding balance |
| DSS | Pricing scenario calculator; forecast model pending |
| Audit | Successful writes logged; full before/after snapshots pending |
| Deployment | Local setup and migrations; Docker/Nginx/backup automation pending |

The original seven-page proposal is not available in this workspace. Local legal rules, approval responsibilities, currencies beyond USD, reservation hold policy, meter replacement policy and refund rules cannot be inferred from this schema alone.
