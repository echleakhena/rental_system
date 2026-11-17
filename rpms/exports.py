"""Excel rendering for the same customer report shown in the application."""

from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


def payment_report_workbook(report):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Customer payments"
    sheet.append(
        ["Customer", "Paid amount (USD)", "Outstanding (USD)", "Payment status"]
    )
    for customer in report["customers"]:
        sheet.append(
            [customer["name"], customer["paid"], customer["due"], customer["status"]]
        )
        # Customer text is literal, even when it starts with an Excel formula marker.
        for column in (1, 4):
            sheet.cell(sheet.max_row, column).data_type = "s"
    paid = sum((c["paid"] for c in report["customers"]), Decimal(0))
    due = sum((c["due"] for c in report["customers"]), Decimal(0))
    sheet.append(["TOTAL", paid, due, None])
    for row in sheet.iter_rows(min_row=2):
        for cell in row[1:3]:
            cell.number_format = '"$"#,##0.00'
    for cell in sheet[sheet.max_row]:
        cell.font = Font(bold=True)
    sheet.auto_filter.ref = f"A1:D{len(report['customers']) + 1}"
    sheet.freeze_panes = "A2"
    summary = workbook.create_sheet("Report notes")
    for row in [
        ["Report", "Customer payment report"],
        ["Generated (UTC)", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")],
        ["Scope", "All-time issued invoices grouped by primary customer"],
        ["Excluded", "Draft and voided invoices; customers without issued invoices"],
        ["Invoices without primary customer", report["unlinked"]],
        ["Customer table totals", "Include only invoices linked to primary customers"],
        ["Currency", "USD"],
    ]:
        summary.append(row)
    summary.column_dimensions["A"].width = 36
    summary.column_dimensions["B"].width = 80
    for row in summary:
        row[1].alignment = Alignment(wrap_text=True, vertical="top")
    for i, width in enumerate([32, 23, 23, 22], 1):
        sheet.column_dimensions[get_column_letter(i)].width = width
    for tab in (sheet, summary):
        tab.sheet_view.showGridLines = False
        for cell in tab[1]:
            cell.fill = PatternFill("solid", fgColor="176357")
            cell.font = Font(color="FFFFFF", bold=True)
        tab.row_dimensions[1].height = 25
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output
