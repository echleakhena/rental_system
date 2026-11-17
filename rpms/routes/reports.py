"""Reports page routes."""

from datetime import date
from decimal import Decimal, InvalidOperation

from flask import abort, render_template, request, send_file
from sqlalchemy import select

from ..exports import payment_report_workbook
from ..models import MODELS, db
from ..reporting import dashboard_reports, management_actions
from ..services import money
from ..web import bp, login_required


@bp.get("/simulator")
@login_required
def simulator():
    try:
        rent = money(request.args.get("rent", "150"))
        occupancy = Decimal(request.args.get("occupancy", "85"))
        count = int(request.args.get("rooms", "20"))
        cost = money(request.args.get("cost", "500"))
        if (
            not occupancy.is_finite()
            or not 0 <= occupancy <= 100
            or not 0 <= count <= 100000
        ):
            raise ValueError()
    except (ValueError, InvalidOperation):
        abort(400, "Invalid simulation inputs.")
    revenue = rent * count * occupancy / 100
    return render_template(
        "simulator.html",
        rent=rent,
        occupancy=occupancy,
        count=count,
        cost=cost,
        revenue=revenue,
    )


@bp.get("/reports")
@login_required
def reports():
    rooms, invoices, report = load_report_data()
    return render_template(
        "insights.html",
        page_title="Report",
        reports=report,
        mis=False,
    )


@bp.get("/mis-dss")
@login_required
def mis_dss():
    rooms, invoices, report = load_report_data()

    maintenance = db.session.scalars(select(MODELS["maintenance_requests"])).all()
    contracts = db.session.scalars(select(MODELS["rental_contracts"])).all()
    decisions = management_actions(
        rooms, invoices, maintenance, contracts, date.today()
    )
    return render_template(
        "insights.html",
        page_title="MIS & DSS",
        reports=report,
        mis=True,
        decisions=decisions,
    )


@bp.get("/reports/export.xlsx")
@login_required
def export_report():
    rooms, invoices, report = load_report_data()
    response = send_file(
        payment_report_workbook(report),
        as_attachment=True,
        download_name=f"RPMS-customer-payments-{date.today().isoformat()}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        max_age=0,
    )
    response.headers["Cache-Control"] = "no-store, private"
    return response


def load_report_data():
    """Use the same records for the report page, MIS and Excel download."""
    rooms = db.session.scalars(select(MODELS["rooms"])).all()
    invoices = db.session.scalars(
        select(MODELS["invoices"]).where(
            MODELS["invoices"].status.notin_(["Draft", "Voided"])
        )
    ).all()
    return rooms, invoices, dashboard_reports(rooms, invoices)
