"""System-issued record references, distinct from physical or external identifiers."""

from uuid import uuid4

AUTO_REFERENCES = {
    "properties": ("property_code", "PROP"),
    "tenants": ("tenant_code", "TEN"),
    "reservations": ("reservation_no", "RES"),
    "rental_contracts": ("contract_no", "LEASE"),
    "maintenance_requests": ("request_no", "MNT"),
    "invoices": ("invoice_no", "INV"),
    "payments": ("payment_no", "PAY"),
}


def reference(prefix):
    # Fits VARCHAR(30); independent requests need no shared counter or MAX(id).
    return f"{prefix}-{uuid4().hex[:24].upper()}"
