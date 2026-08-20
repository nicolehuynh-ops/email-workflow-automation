from pcs import config


def get_custom_field_value(contact, field_id, field_name):
    target_id = str(field_id).lower()
    target_name = field_name.lower()
    for field in contact.get("customFields") or []:
        key = str(field.get("key") or field.get("name") or field.get("id") or "").lower()
        if key == target_id or key == target_name:
            return field.get("value") or ""
    return ""


def merge_contact_state_and_details(states, details):
    detail_by_id = {contact["id"]: contact for contact in details}
    rows = []
    for state in states:
        detail = detail_by_id.get(state["contactId"], {})
        status = state.get("status") or {}
        current_step = state.get("currentStep") or {}
        rows.append(
            {
                "contactId": state["contactId"],
                "email": state.get("email") or detail.get("email") or "",
                "firstName": state.get("firstName") or detail.get("firstName") or "",
                "lastName": state.get("lastName") or detail.get("lastName") or "",
                "company": state.get("company") or detail.get("company") or "",
                "title": state.get("title") or detail.get("title") or "",
                "status": status.get("status") or "",
                "replied": bool(status.get("replied")),
                "bounced": bool(status.get("bounced")),
                "optedOut": bool(detail.get("isOptedOut")),
                "autoReply": bool(state.get("autoReply")) or is_out_of_office_status(status.get("status")),
                "opened": bool(status.get("opened")),
                "clicked": bool(status.get("clicked")),
                "currentStepId": current_step.get("stepId") or "",
                "currentStepNumber": current_step.get("displayStepNumber") or current_step.get("stepNumber") or "",
                "sequenceAddedAt": state.get("sequenceAddedAt") or "",
                "pcsIssuerId": get_custom_field_value(
                    detail,
                    config.CUSTOM_FIELDS["pcs_issuer_id"],
                    config.CUSTOM_FIELD_NAMES["pcs_issuer_id"],
                ),
                "pcsSender": get_custom_field_value(
                    detail,
                    config.CUSTOM_FIELDS["pcs_sender"],
                    config.CUSTOM_FIELD_NAMES["pcs_sender"],
                ),
            }
        )
    return rows


def find_issuer_blocks(
    rows,
    booked_emails=None,
    responded_emails=None,
    suppression_contacts=None,
    suppression_domains=None,
    contact_exclusion_emails=None,
    manual_ooo_emails=None,
):
    booked_emails = normalize_emails(booked_emails or [])
    responded_emails = normalize_emails(responded_emails or [])
    contact_exclusion_emails = normalize_emails(contact_exclusion_emails or [])
    manual_ooo_emails = normalize_emails(manual_ooo_emails or [])
    rows_by_issuer = {}
    rows_missing_issuer = []
    for row in rows:
        issuer_id = row["pcsIssuerId"]
        if not issuer_id:
            rows_missing_issuer.append(row)
            continue
        rows_by_issuer.setdefault(issuer_id, []).append(row)

    email_rows = {normalize_email(row["email"]): row for row in rows if normalize_email(row["email"])}
    manual_blockers = []
    manual_inputs = [
        (email, "Manual Calendly booking", True) for email in booked_emails
    ] + [
        (email, "Manual response", True) for email in responded_emails
    ]
    for entry in suppression_contacts or []:
        reason_label = "Suppression list: meeting booked" if entry["reason"] == "meeting_booked" else "Suppression list: manual response"
        manual_inputs.append((normalize_email(entry["email"]), reason_label, False))

    for domain_entry in suppression_domains or []:
        domain = normalize_domain(domain_entry["domain"])
        for row in rows:
            if email_matches_domain(row["email"], domain):
                manual_inputs.append((normalize_email(row["email"]), f"Suppression domain: {domain}", False))

    for email, label, require_sequence_match in manual_inputs:
        row = email_rows.get(email)
        if not row:
            if require_sequence_match:
                raise RuntimeError(f"{label} email is not in this sequence: {email}")
            continue
        if not row["pcsIssuerId"]:
            raise RuntimeError(f"{label} email is missing PCS Issuer ID: {email}")
        manual_blockers.append({**row, "blockReason": f"{label}: {row['email']}"})

    manual_ooo_issuer_ids = {
        row["pcsIssuerId"]
        for row in rows
        if normalize_email(row["email"]) in manual_ooo_emails and row["pcsIssuerId"]
    }
    blocker_rows = []
    for row in rows:
        # A manually verified OOO may be represented by Reply.io as Finished/replied.
        if normalize_email(row["email"]) in manual_ooo_emails:
            continue
        # OOO remains an exception even when Reply.io exposes it as a reply.
        if row.get("autoReply"):
            continue
        if row["replied"]:
            blocker_rows.append({**row, "blockReason": "Reply.io response"})
        # A bounce or opt-out alone is never an issuer blocker. A response above takes precedence.
        elif row.get("bounced") or row.get("optedOut"):
            continue
        elif is_finished_status(row["status"]) and normalize_email(row["email"]) not in contact_exclusion_emails:
            blocker_rows.append({**row, "blockReason": "Finished for a non-bounce, non-opt-out, non-OOO reason"})
    # A manual OOO override is authoritative for legacy Reply.io Finished flags.
    # Do not let one create an issuer block; genuine replies and manual inputs remain blockers.
    blocker_rows = [
        row
        for row in blocker_rows
        if not (
            row["pcsIssuerId"] in manual_ooo_issuer_ids
            and row["blockReason"] == "Finished for a non-bounce, non-opt-out, non-OOO reason"
        )
    ]
    blocker_rows.extend(manual_blockers)
    blocked_issuer_ids = {row["pcsIssuerId"] for row in blocker_rows if row["pcsIssuerId"]}
    block_reasons = {}
    for row in blocker_rows:
        issuer_id = row["pcsIssuerId"]
        if issuer_id:
            block_reasons.setdefault(issuer_id, []).append(row["blockReason"])
    related_rows_to_finish = []

    for issuer_id in blocked_issuer_ids:
        for row in rows_by_issuer.get(issuer_id, []):
            if (
                not is_finished_status(row["status"])
                and not row.get("optedOut")
                and not row.get("bounced")
                and not row.get("autoReply")
            ):
                related_rows_to_finish.append(row)

    return {
        "rows_by_issuer": rows_by_issuer,
        "rows_missing_issuer": rows_missing_issuer,
        "blocker_rows": blocker_rows,
        "block_reasons": {issuer_id: sorted(set(reasons)) for issuer_id, reasons in block_reasons.items()},
        "blocked_issuer_ids": blocked_issuer_ids,
        "related_rows_to_finish": related_rows_to_finish,
    }


def eligible_for_step(rows, blocked_issuer_ids, required_step_id, excluded_emails=None):
    excluded_emails = normalize_emails(excluded_emails or [])
    eligible = []
    for row in rows:
        if normalize_email(row["email"]) in excluded_emails:
            continue
        if not row["pcsIssuerId"]:
            continue
        if row["pcsIssuerId"] in blocked_issuer_ids:
            continue
        if not is_active_status(row["status"]):
            continue
        if row["replied"] or row["bounced"] or row.get("autoReply") or row.get("optedOut"):
            continue
        if int(row["currentStepId"] or 0) != int(required_step_id):
            continue
        eligible.append(row)
    return eligible


def workbook_rows(rows, extra=None):
    extra = extra or {}
    return [{**row, **extra} for row in rows]


def annotate_auto_replies(reply, sequence_id, rows, required_step_id=None):
    _ = (reply, sequence_id, required_step_id)
    for row in rows:
        row["autoReply"] = bool(row.get("autoReply")) or is_out_of_office_status(row["status"])
    return rows


def is_active_status(status):
    return normalize_status_key(status) == "active"


def is_finished_status(status):
    return normalize_status_key(status) == "finished"


def is_out_of_office_status(status):
    return normalize_status_key(status) == "outofoffice"


def normalize_status_key(status):
    return "".join(char for char in str(status or "").lower() if char.isalnum())


def normalize_email(email):
    return str(email or "").strip().lower()


def normalize_emails(emails):
    return {normalize_email(email) for email in emails if normalize_email(email)}


def normalize_domain(domain):
    return str(domain or "").strip().lower().lstrip("@")


def email_matches_domain(email, domain):
    email_domain = normalize_email(email).partition("@")[2]
    domain = normalize_domain(domain)
    return bool(email_domain and domain and (email_domain == domain or email_domain.endswith(f".{domain}")))
