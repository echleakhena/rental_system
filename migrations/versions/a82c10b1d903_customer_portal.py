"""Add customer portal accounts and payment proof review.

Revision ID: a82c10b1d903
Revises: f4c7f272b478
"""

import sqlalchemy as sa
from alembic import op

revision = "a82c10b1d903"
down_revision = "f4c7f272b478"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "customer_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Integer(),
            sa.ForeignKey("tenants.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("phone", sa.String(30), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "customer_payment_proofs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey("customer_accounts.id"),
            nullable=False,
        ),
        sa.Column(
            "invoice_id", sa.Integer(), sa.ForeignKey("invoices.id"), nullable=False
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("reference", sa.String(100), nullable=False),
        sa.Column("photo_path", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "payment_id", sa.Integer(), sa.ForeignKey("payments.id"), unique=True
        ),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("customer_payment_proofs")
    op.drop_table("customer_accounts")
