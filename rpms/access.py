"""Supported staff permissions; customer access remains separately scoped."""

CAPABILITIES = {
    "portfolio.manage": (
        "Rooms & properties",
        "Rooms, floors, properties and room photos",
    ),
    "leasing.manage": (
        "Customers & rentals",
        "Customers, bookings, contracts and resident linking",
    ),
    "utilities.manage": ("Utilities", "Meters, utility rates and reading approval"),
    "billing.manage": (
        "Billing & payments",
        "Invoices, payments, expenses and payment QR",
    ),
    "maintenance.manage": ("Maintenance", "Maintenance requests and room inspections"),
}
TABLE_CAPABILITIES = {
    **dict.fromkeys(("properties", "floors", "rooms"), "portfolio.manage"),
    **dict.fromkeys(
        (
            "tenants",
            "reservations",
            "rental_contracts",
            "contract_tenants",
            "contract_renewals",
            "move_outs",
        ),
        "leasing.manage",
    ),
    **dict.fromkeys(("utility_meters", "meter_readings"), "utilities.manage"),
    **dict.fromkeys(
        (
            "invoices",
            "invoice_items",
            "payments",
            "payment_allocations",
            "deposits",
            "deposit_transactions",
            "expenses",
        ),
        "billing.manage",
    ),
    **dict.fromkeys(("maintenance_requests", "room_inspections"), "maintenance.manage"),
}
