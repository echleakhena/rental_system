"""Schema-driven SQLAlchemy models corresponding to the supplied 26-table PDF."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Index, UniqueConstraint, event, text
from sqlalchemy.orm.attributes import set_committed_value

from .identifiers import AUTO_REFERENCES, reference

db = SQLAlchemy()
SCHEMA = json.loads(Path(__file__).with_name("schema.json").read_text())
MODELS = {}
PAIRS = {
    "floors": ("property_id", "floor_number"),
    "rooms": ("floor_id", "room_number"),
    "contract_tenants": ("contract_id", "tenant_id"),
    "role_permissions": ("role_id", "permission_id"),
}
for table, fields in SCHEMA.items():
    attrs = {"__tablename__": table}
    for field in fields:
        name, t = field["name"], field["type"]
        if t.startswith(("BIGINT", "INTEGER", "SMALLINT")):
            kind = db.Integer
        elif t.startswith("VARCHAR"):
            kind = db.String(int(re.search(r"\d+", t)[0]))
        elif t.startswith("NUMERIC"):
            kind = db.Numeric(*map(int, re.search(r"\((.*?)\)", t)[1].split(",")))
        elif t == "BOOLEAN":
            kind = db.Boolean
        elif t == "DATE":
            kind = db.Date
        elif t == "TIMESTAMPTZ":
            kind = db.DateTime(timezone=True)
        elif t == "JSONB":
            kind = db.JSON
        else:
            kind = db.Text
        args = [kind]
        if field["fk"]:
            args.append(db.ForeignKey(field["fk"] + ".id"))
        options = {}
        automatic = AUTO_REFERENCES.get(table)
        if automatic and name == automatic[0] and table != "rental_contracts":
            options["default"] = lambda prefix=automatic[1]: reference(prefix)
        attrs[name] = db.Column(
            *args,
            primary_key=name == "id",
            unique="UQ" in t,
            nullable=name != "id",
            **options,
        )
    if table == "rooms":
        attrs["photos"] = db.Column(db.JSON, nullable=True)
    if table in PAIRS:
        attrs["__table_args__"] = (UniqueConstraint(*PAIRS[table]),)
    if "created_at" not in attrs:
        attrs["created_at"] = db.Column(
            db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
        )
    attrs["updated_at"] = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    MODELS[table] = type(
        "".join(x.title() for x in table.split("_")), (db.Model,), attrs
    )


@event.listens_for(MODELS["rental_contracts"], "after_insert")
def assign_contract_number(mapper, connection, contract):
    """Use the database-issued ID for unique, sequential contract numbers."""
    if contract.contract_no is None:
        number = f"{contract.id:03d}"
        connection.execute(
            mapper.local_table.update()
            .where(mapper.local_table.c.id == contract.id)
            .values(contract_no=number)
        )
        set_committed_value(contract, "contract_no", number)


Index(
    "uq_invoice_month",
    MODELS["invoices"].contract_id,
    MODELS["invoices"].billing_period,
    unique=True,
    sqlite_where=text("status != 'Voided'"),
    postgresql_where=text("status != 'Voided'"),
)
