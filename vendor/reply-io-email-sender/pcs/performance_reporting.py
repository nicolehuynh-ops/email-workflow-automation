"""Aggregation and workbook helpers for the read-only campaign performance report."""

import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from pcs.campaign import normalize_email


GROUP_PATTERN = re.compile(r"\bgroup\s*([ab])\b", re.IGNORECASE)
METRIC_KEYS = ("sent", "bounced", "hard_bounced", "soft_bounced", "unsubscribed", "complained", "opens", "clicks", "replies")


def campaign_group(sequence_name):
    match = GROUP_PATTERN.search(sequence_name or "")
    return f"Group {match.group(1).upper()}" if match else "Unclassified"


def activity_metrics(activities, chase_by_step):
    """Normalise Reply activity records without assuming a brittle API response shape."""
    result = {key: 0 for key in METRIC_KEYS}
    result.update({"first_open_at": "", "first_click_at": "", "first_reply_at": "", "by_chase": defaultdict(lambda: {key: 0 for key in METRIC_KEYS})})
    for activity in activities:
        event = classify_activity(activity)
        if not event:
            continue
        timestamp = activity_timestamp(activity)
        step_id = activity_value(activity, {"stepId", "sequenceStepId", "sequence_step_id"})
        chase = chase_by_step.get(str(step_id), "Unattributed")
        result[event] += 1
        result["by_chase"][chase][event] += 1
        if event in {"hard_bounced", "soft_bounced"}:
            result["bounced"] += 1
            result["by_chase"][chase]["bounced"] += 1
        first_key = {"opens": "first_open_at", "clicks": "first_click_at", "replies": "first_reply_at"}.get(event)
        if first_key and timestamp and (not result[first_key] or timestamp < result[first_key]):
            result[first_key] = timestamp
    result["by_chase"] = dict(result["by_chase"])
    return result


def classify_activity(activity):
    label = " ".join(str(value) for value in activity_labels(activity)).lower()
    compact = re.sub(r"[^a-z]", "", label)
    if "hardbounce" in compact:
        return "hard_bounced"
    if "softbounce" in compact:
        return "soft_bounced"
    if "bounce" in compact:
        return "bounced"
    if "spam" in compact or "complaint" in compact:
        return "complained"
    if "unsubscribe" in compact or "optout" in compact or "optingout" in compact:
        return "unsubscribed"
    if "click" in compact:
        return "clicks"
    if "open" in compact:
        return "opens"
    if "repl" in compact or "respond" in compact:
        return "replies"
    if "sent" in compact or "emailsend" in compact:
        return "sent"
    return ""


def activity_labels(value):
    labels = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in {"type", "activitytype", "eventtype", "action", "actiontype", "name"}:
                labels.append(item)
            labels.extend(activity_labels(item))
    elif isinstance(value, list):
        for item in value:
            labels.extend(activity_labels(item))
    return labels


def activity_value(value, names):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in names:
                return item
            found = activity_value(item, names)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for item in value:
            found = activity_value(item, names)
            if found not in (None, ""):
                return found
    return ""


def activity_timestamp(activity):
    return str(activity_value(activity, {"createdAt", "created", "timestamp", "date", "occurredAt", "eventDate"}) or "")


def build_report(sequence_snapshots, calendly_invitees, suppression_entries):
    bookings_by_email = defaultdict(list)
    for invitee in calendly_invitees:
        email = normalize_email(invitee.get("email"))
        if email:
            bookings_by_email[email].append(invitee)
    validated_responses = {normalize_email(entry["email"]) for entry in suppression_entries if entry.get("reason") == "manual_response"}
    validated_bookings = {normalize_email(entry["email"]) for entry in suppression_entries if entry.get("reason") == "meeting_booked"}

    detail_rows, validation_rows, chase_rows = [], [], []
    sequence_rows, groups = [], defaultdict(dict)
    for snapshot in sequence_snapshots:
        group = snapshot["group"]
        contacts = {}
        for contact in snapshot["contacts"]:
            email = normalize_email(contact.get("email"))
            if not email:
                continue
            contacts[email] = contact
            groups[group][email] = contact
            booking = (bookings_by_email[email] or [None])[0]
            validated_response = email in validated_responses
            manual_booking = email in validated_bookings
            raw_reply = bool(contact["metrics"]["replies"]) or bool(contact.get("replied"))
            status = "Booked meeting" if booking or manual_booking else "Validated positive response" if validated_response else "Pending human validation" if raw_reply else "No reply"
            detail_rows.append(contact_detail(snapshot, contact, booking, validated_response, manual_booking, status))
            if raw_reply or validated_response or manual_booking or booking:
                validation_rows.append({"Campaign group": group, "Sequence ID": snapshot["sequence_id"], "PCS Issuer ID": issuer_key(contact), "Email": contact.get("email"), "Reply.io response": raw_reply, "Validation status": status, "Calendly meeting": bool(booking), "Manual meeting backfill": manual_booking, "Calendly event": (booking or {}).get("eventName", ""), "Calendly start": (booking or {}).get("eventStartTime", "")})
            for chase, metrics in contact["metrics"]["by_chase"].items():
                chase_rows.append({"Campaign group": group, "Sequence ID": snapshot["sequence_id"], "Sequence name": snapshot["sequence_name"], "Chase": chase, **metrics, "Audience contact": email})
        sequence_rows.append(metric_row(group, snapshot["sequence_id"], snapshot["sequence_name"], contacts, bookings_by_email, validated_responses, validated_bookings))

    group_rows = [metric_row(group, "All", group, contacts, bookings_by_email, validated_responses, validated_bookings) for group, contacts in sorted(groups.items())]
    return sequence_rows, group_rows, detail_rows, validation_rows, aggregate_chases(chase_rows)


def contact_detail(snapshot, contact, booking, validated_response, manual_booking, validation_status):
    metrics = contact["metrics"]
    conversion_at = (booking or {}).get("bookedAt", "")
    if validation_status == "Validated positive response":
        conversion_at = metrics["first_reply_at"]
    return {"Campaign group": snapshot["group"], "Sequence ID": snapshot["sequence_id"], "Sequence name": snapshot["sequence_name"], "Contact ID": contact.get("contactId"), "Email": contact.get("email"), "Company": contact.get("company"), "PCS Issuer ID": issuer_key(contact), "Status": contact.get("status"), "Sequence added": contact.get("sequenceAddedAt"), "Sent": metrics["sent"], "Opened": bool(metrics["opens"]), "Total opens": metrics["opens"], "Clicked": bool(metrics["clicks"]), "Total clicks": metrics["clicks"], "Reply.io reply": bool(metrics["replies"]) or bool(contact.get("replied")), "First open": metrics["first_open_at"], "First click": metrics["first_click_at"], "First reply": metrics["first_reply_at"], "Conversion timestamp": conversion_at, "Time to conversion (hours)": elapsed_hours(contact.get("sequenceAddedAt"), conversion_at), "Validation status": validation_status, "Scheduled Calendly meeting": bool(booking), "Validated positive response": validated_response, "Manual meeting backfill": manual_booking, "Calendly event": (booking or {}).get("eventName", ""), "Calendly start": (booking or {}).get("eventStartTime", "")}


def metric_row(group, sequence_id, sequence_name, contacts, bookings_by_email, validated_responses, validated_bookings):
    emails = set(contacts)
    totals = {key: sum(contact["metrics"][key] for contact in contacts.values()) for key in METRIC_KEYS}
    bounced_contacts = {email for email, contact in contacts.items() if contact["metrics"]["bounced"] or contact.get("bounced")}
    opened_contacts = {email for email, contact in contacts.items() if contact["metrics"]["opens"]}
    clicked_contacts = {email for email, contact in contacts.items() if contact["metrics"]["clicks"]}
    replied_contacts = {email for email, contact in contacts.items() if contact["metrics"]["replies"] or contact.get("replied")}
    unsubscribed = {email for email, contact in contacts.items() if contact["metrics"]["unsubscribed"] or contact.get("optedOut")}
    issuer_audience = {issuer_key(contact) for contact in contacts.values()}
    meetings = {issuer_key(contacts[email]) for email in emails if bookings_by_email[email]} | {issuer_key(contacts[email]) for email in emails & validated_bookings}
    positives = {issuer_key(contacts[email]) for email in emails & validated_responses}
    conversions = meetings | positives
    delivered = max(totals["sent"] - totals["bounced"], 0)
    conversion_evidence = {}
    for email in emails:
        booking = (bookings_by_email[email] or [None])[0]
        is_conversion = bool(booking) or email in validated_bookings or email in validated_responses
        if is_conversion:
            conversion_evidence.setdefault(issuer_key(contacts[email]), (contacts[email], booking))
    conversion_hours = []
    for contact, booking in conversion_evidence.values():
        timestamp = (booking or {}).get("bookedAt", "") or contact["metrics"]["first_reply_at"]
        hours = elapsed_hours(contact.get("sequenceAddedAt"), timestamp)
        if hours is not None:
            conversion_hours.append(hours)
    return {"Campaign group": group, "Sequence ID": sequence_id, "Sequence name": sequence_name, "Audience contacts": len(emails), "Audience issuers": len(issuer_audience), "Emails sent": totals["sent"], "Delivered": delivered, "Delivery rate": rate(delivered, totals["sent"]), "Bounced": len(bounced_contacts), "Bounce rate": rate(len(bounced_contacts), totals["sent"]), "Hard bounces": totals["hard_bounced"], "Soft bounces": totals["soft_bounced"], "Unsubscribed": len(unsubscribed), "Unsubscribe rate": rate(len(unsubscribed), totals["sent"]), "Spam complaints": totals["complained"], "Spam complaint rate": rate(totals["complained"], totals["sent"]), "Unique opens": len(opened_contacts), "Total opens": totals["opens"], "Open rate": rate(len(opened_contacts), delivered), "Unique clicks": len(clicked_contacts), "Total clicks": totals["clicks"], "CTR": rate(len(clicked_contacts), delivered), "CTOR": rate(len(clicked_contacts), len(opened_contacts)), "Reply.io replies": len(replied_contacts), "Reply rate": rate(len(replied_contacts), delivered), "Issuers with scheduled meetings": len(meetings), "Issuers with validated positive responses": len(positives), "Converted issuers": len(conversions), "Issuer conversion rate": rate(len(conversions), len(issuer_audience)), "Emails per converted issuer": rate(totals["sent"], len(conversions)), "Average time to conversion (hours)": sum(conversion_hours) / len(conversion_hours) if conversion_hours else ""}


def aggregate_chases(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["Campaign group"], row["Sequence ID"], row["Sequence name"], row["Chase"])].append(row)
    result = []
    for key, entries in sorted(grouped.items()):
        totals = {metric: sum(row[metric] for row in entries) for metric in METRIC_KEYS}
        audience = len({row["Audience contact"] for row in entries})
        delivered = max(totals["sent"] - totals["bounced"], 0)
        result.append({"Campaign group": key[0], "Sequence ID": key[1], "Sequence name": key[2], "Chase": key[3], "Audience contacts": audience, "Emails sent": totals["sent"], "Delivered": delivered, "Unique opens": sum(row["opens"] > 0 for row in entries), "Open rate": rate(sum(row["opens"] > 0 for row in entries), delivered), "Unique clicks": sum(row["clicks"] > 0 for row in entries), "CTR": rate(sum(row["clicks"] > 0 for row in entries), delivered), "Replies": sum(row["replies"] > 0 for row in entries), "Reply rate": rate(sum(row["replies"] > 0 for row in entries), delivered), "Unsubscribed": sum(row["unsubscribed"] > 0 for row in entries), "Spam complaints": totals["complained"]})
    return result


def rate(numerator, denominator):
    return numerator / denominator if denominator else 0


def issuer_key(contact):
    """Use the configured issuer ID; retain unmatched contacts as separate audit entries."""
    return str(contact.get("pcsIssuerId") or f"Unmapped contact: {normalize_email(contact.get('email'))}")


def elapsed_hours(start, end):
    if not start or not end:
        return None
    try:
        start_at = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        end_at = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        return round((end_at - start_at).total_seconds() / 3600, 2)
    except ValueError:
        return None


def write_performance_workbook(output_dir, sequence_rows, group_rows, detail_rows, validation_rows, chase_rows, notes):
    output_dir = Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    file_path = output_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-campaign-performance.xlsx"
    workbook = Workbook(); summary = workbook.active; summary.title = "Summary"; summary.append(["Metric", "Value"]); style_header(summary)
    for note in notes: summary.append(note)
    summary.column_dimensions["A"].width = 35; summary.column_dimensions["B"].width = 105
    for title, rows in (("Campaign rollup", group_rows), ("Sequence performance", sequence_rows), ("Chase performance", chase_rows), ("Contact outcomes", detail_rows), ("Response validation", validation_rows)):
        add_table_sheet(workbook, title, rows)
    workbook.save(file_path)
    return str(file_path.resolve())


def add_table_sheet(workbook, title, rows):
    sheet = workbook.create_sheet(title); headers = list(rows[0]) if rows else []
    if not headers: sheet.append(["No data"]); return
    sheet.append(headers); style_header(sheet)
    for row in rows: sheet.append([row.get(header, "") for header in headers])
    for index, header in enumerate(headers, start=1):
        sheet.column_dimensions[sheet.cell(1, index).column_letter].width = min(max(len(header) + 3, 16), 36)
        if "rate" in header.lower() or header in {"CTR", "CTOR"}:
            for cells in sheet.iter_cols(min_col=index, max_col=index, min_row=2):
                for cell in cells: cell.number_format = "0.0%"
    sheet.freeze_panes = "A2"; sheet.auto_filter.ref = sheet.dimensions


def style_header(sheet):
    fill = PatternFill("solid", fgColor="1F4E78")
    for cell in sheet[1]: cell.font = Font(bold=True, color="FFFFFF"); cell.fill = fill
