import csv

from pcs import config
from pcs.campaign import normalize_email


CONTACT_EXCLUSIONS_CSV_PATH = config.CAMPAIGN_SUPPRESSION_DIR / "contact_exclusions.csv"


def load_contact_exclusion_entries(path=CONTACT_EXCLUSIONS_CSV_PATH):
    """Load contacts that must never receive a chase, without blocking their issuer."""
    with open(path, newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))

    exclusions = []
    for row_number, row in enumerate(rows, start=2):
        email = normalize_email(row.get("email"))
        if not email or "@" not in email:
            raise RuntimeError(f"Contact exclusion row {row_number} has invalid email {email!r}.")
        exclusions.append({"email": email, "reason": (row.get("reason") or "").strip().lower()})
    return exclusions


def load_contact_exclusions(path=CONTACT_EXCLUSIONS_CSV_PATH):
    return {entry["email"] for entry in load_contact_exclusion_entries(path)}


def load_out_of_office_exclusions(path=CONTACT_EXCLUSIONS_CSV_PATH):
    """Load manual OOO contacts whose Reply.io reply flag must not block their issuer."""
    return {
        entry["email"]
        for entry in load_contact_exclusion_entries(path)
        if entry["reason"] == "out_of_office"
    }
