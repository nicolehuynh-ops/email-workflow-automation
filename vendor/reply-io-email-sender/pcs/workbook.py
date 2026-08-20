from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from pcs import config


COLUMNS = [
    ("contactId", "Contact ID", 14),
    ("email", "Email", 34),
    ("firstName", "First Name", 18),
    ("lastName", "Last Name", 18),
    ("company", "Company", 28),
    ("title", "Title", 26),
    ("pcsIssuerId", "PCS Issuer ID", 24),
    ("pcsSender", "PCS Sender", 16),
    ("status", "Status", 14),
    ("replied", "Replied", 10),
    ("optedOut", "Opted Out", 11),
    ("autoReply", "Auto Reply / OOO", 16),
    ("bounced", "Bounced", 10),
    ("currentStepNumber", "Step #", 10),
    ("currentStepId", "Step ID", 12),
    ("action", "Action", 28),
    ("reason", "Reason", 42),
]


def write_run_workbook(
    script_name,
    eligible_rows,
    finished_rows,
    blocked_rows=None,
    notes=None,
    sequence_id=None,
    campaign_name=None,
    output_dir=None,
    eligible_sheet_name="Eligible for Push",
    finished_sheet_name="Marked Finished",
):
    blocked_rows = blocked_rows or []
    notes = notes or []
    output_dir = campaign_output_dir(output_dir or config.MULTI_SENDER_OUTPUT_DIR, sequence_id, campaign_name)
    output_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    file_path = output_dir / f"{stamp}-{slug(script_name)}.xlsx"

    workbook = Workbook()
    summary = workbook.active
    summary.title = "Summary"
    add_summary_sheet(summary, script_name, eligible_rows, finished_rows, blocked_rows, notes, sequence_id)
    add_rows_sheet(workbook, eligible_sheet_name, eligible_rows)
    add_rows_sheet(workbook, finished_sheet_name, finished_rows)
    if blocked_rows:
        add_rows_sheet(workbook, "Blocked", blocked_rows)

    workbook.save(file_path)
    return str(file_path.resolve())


def campaign_output_dir(base_output_dir, sequence_id, campaign_name):
    sequence_label = str(sequence_id or config.SEQUENCE_ID)
    name_label = slug(campaign_name or "unnamed-campaign") or "unnamed-campaign"
    return Path(base_output_dir) / f"{sequence_label}-{name_label}"


def add_summary_sheet(sheet, script_name, eligible_rows, finished_rows, blocked_rows, notes, sequence_id):
    sheet.append(["Metric", "Value"])
    style_header(sheet)
    rows = [
        ("Script", script_name),
        ("Run timestamp", datetime.now(timezone.utc).isoformat()),
        ("Eligible for push", len(eligible_rows)),
        ("Marked finished on this run", len(finished_rows)),
        ("Blocked rows", len(blocked_rows)),
        ("Sequence ID", sequence_id or config.SEQUENCE_ID),
    ]
    for row in rows:
        sheet.append(row)
    if notes:
        sheet.append([])
        sheet.append(["Notes", ""])
        for note in notes:
            sheet.append(["", note])
    sheet.column_dimensions["A"].width = 34
    sheet.column_dimensions["B"].width = 90


def add_rows_sheet(workbook, name, rows):
    sheet = workbook.create_sheet(name)
    sheet.append([header for _, header, _ in COLUMNS])
    style_header(sheet)
    for row in rows:
        sheet.append([row.get(key, "") for key, _, _ in COLUMNS])
    for index, (_, _, width) in enumerate(COLUMNS, start=1):
        sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = width
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions


def style_header(sheet):
    fill = PatternFill("solid", fgColor="1F4E78")
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = fill


def slug(value):
    cleaned = []
    previous_dash = False
    for char in value.lower():
        if char.isalnum():
            cleaned.append(char)
            previous_dash = False
        elif not previous_dash:
            cleaned.append("-")
            previous_dash = True
    return "".join(cleaned).strip("-")
