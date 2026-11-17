"""Menu groups, form fields and allowed statuses for management records."""

CUSTOMER_FIELDS = ("full_name", "gender", "phone")

GROUPS = {
    "Portfolio": ["properties", "floors", "rooms"],
    "Leasing": ["tenants", "reservations", "rental_contracts", "contract_tenants"],
    "Operations": [
        "utility_meters",
        "meter_readings",
        "maintenance_requests",
        "expenses",
    ],
    "Finance": [
        "invoices",
        "invoice_items",
        "payments",
        "payment_allocations",
        "deposits",
        "deposit_transactions",
    ],
    "Administration": [
        "users",
        "roles",
        "permissions",
        "role_permissions",
        "contract_renewals",
        "move_outs",
        "system_settings",
        "audit_logs",
    ],
}


EDITABLE = {
    "users",
    "roles",
    "permissions",
    "role_permissions",
    "system_settings",
    "contract_renewals",
    "move_outs",
    "properties",
    "floors",
    "rooms",
    "tenants",
    "reservations",
    "rental_contracts",
    "contract_tenants",
    "utility_meters",
    "meter_readings",
    "maintenance_requests",
    "room_inspections",
    "expenses",
}

ADMIN_TABLES = {"users", "roles", "permissions", "role_permissions", "system_settings"}


STATUSES = {
    "contract_renewals": ["Draft"],
    "move_outs": ["Draft"],
    "properties": ["Active", "Inactive"],
    "rooms": ["Vacant", "Maintenance"],
    "tenants": ["Active", "Inactive"],
    "reservations": ["Pending", "Cancelled"],
    "rental_contracts": ["Draft"],
    "utility_meters": ["Active", "Replaced", "Inactive"],
    "meter_readings": ["Draft", "Approved"],
    "maintenance_requests": [
        "Pending",
        "Assigned",
        "In Progress",
        "Completed",
        "Cancelled",
    ],
    "room_inspections": ["Draft", "Completed"],
    "expenses": ["Pending", "Approved", "Paid"],
}


REQUIRED = {
    "users": ["role_id", "name", "email"],
    "roles": ["name"],
    "permissions": ["code", "name", "module"],
    "role_permissions": ["role_id", "permission_id"],
    "system_settings": ["setting_key", "setting_value"],
    "contract_renewals": ["contract_id", "new_end_date", "new_rent", "renewal_date"],
    "move_outs": ["contract_id", "notice_date", "move_out_date"],
    "properties": ["property_code", "name", "address"],
    "floors": ["property_id", "floor_number", "name"],
    "rooms": ["floor_id", "room_number", "monthly_rent"],
    "tenants": ["full_name", "gender", "phone"],
    "reservations": ["reservation_no", "room_id", "tenant_id", "expected_move_in"],
    "rental_contracts": [
        "contract_no",
        "room_id",
        "start_date",
        "end_date",
        "monthly_rent",
        "deposit_required",
        "billing_day",
        "payment_due_day",
    ],
    "contract_tenants": ["contract_id", "tenant_id"],
    "utility_meters": ["room_id", "meter_type", "meter_number", "default_rate"],
    "meter_readings": [
        "meter_id",
        "contract_id",
        "reading_date",
        "previous_reading",
        "current_reading",
        "unit_rate",
    ],
    "maintenance_requests": ["request_no", "room_id", "title", "priority"],
    "room_inspections": [
        "contract_id",
        "room_id",
        "inspection_type",
        "inspection_date",
    ],
    "expenses": ["property_id", "category", "amount", "expense_date"],
}


HIDDEN = {
    "id",
    "created_at",
    "updated_at",
    "created_by",
    "recorded_by",
    "inspected_by",
    "reported_at",
    "completed_at",
    "usage_quantity",
    "total_amount",
    "signed_at",
    "password_hash",
    "identity_number",
    "identity_document_path",
    "photo_paths",
    "photo_path",
    "receipt_path",
}
