"""Campaign RSVP exports used as contact-level chase exclusions."""

import csv
import re

from pcs import config
from pcs.campaign import normalize_email


def load_rsvp_entries():
    """Load every CSV with RSVP in its name from the selected campaign folder."""
    paths = sorted(
        path
        for path in config.CAMPAIGN_SUPPRESSION_DIR.glob("*.csv")
        if "rsvp" in path.name.lower()
    )
    entries = []
    for path in paths:
        with path.open(newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            fieldnames = {name.strip().lower(): name for name in reader.fieldnames or []}
            email_field = fieldnames.get("email")
            name_field = fieldnames.get("name")
            first_name_field = fieldnames.get("first_name") or fieldnames.get("first name")
            last_name_field = fieldnames.get("last_name") or fieldnames.get("last name")
            if not email_field and not (name_field or (first_name_field and last_name_field)):
                raise RuntimeError(f"RSVP file must include email or name fields: {path}")
            for row in reader:
                email = normalize_email(row.get(email_field)) if email_field else ""
                name = row.get(name_field, "") if name_field else ""
                first_name = row.get(first_name_field, "") if first_name_field else ""
                last_name = row.get(last_name_field, "") if last_name_field else ""
                if email or normalize_name(name) or (normalize_name(first_name) and normalize_name(last_name)):
                    entries.append(
                        {
                            "email": email,
                            "name": name.strip(),
                            "firstName": first_name.strip(),
                            "lastName": last_name.strip(),
                            "source": path.name,
                        }
                    )
    return entries


def find_rsvp_matches(rows, entries=None):
    """Return contacts matching an RSVP by email or by both first and last name."""
    matches = []
    seen_contact_ids = set()
    for row in rows:
        email = normalize_email(row.get("email"))
        first_name = normalize_name(row.get("firstName"))
        last_name = normalize_name(row.get("lastName"))
        full_name = normalize_name(f"{row.get('firstName', '')} {row.get('lastName', '')}")
        for entry in entries if entries is not None else load_rsvp_entries():
            email_match = bool(email and email == entry["email"])
            name_match = bool(
                first_name
                and last_name
                and first_name == normalize_name(entry["firstName"])
                and last_name == normalize_name(entry["lastName"])
            ) or bool(full_name and full_name == normalize_name(entry["name"]))
            if not (email_match or name_match):
                continue
            if row["contactId"] not in seen_contact_ids:
                match_type = "email and name" if email_match and name_match else "email" if email_match else "name"
                matches.append(
                    {
                        **row,
                        "action": "Excluded from chase",
                        "reason": f"RSVP {match_type} match in {entry['source']}",
                        "rsvpName": entry["name"] or f"{entry['firstName']} {entry['lastName']}".strip(),
                        "rsvpEmail": entry["email"],
                    }
                )
                seen_contact_ids.add(row["contactId"])
            break
    return matches


def normalize_name(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())
