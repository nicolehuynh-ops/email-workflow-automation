import csv

from pcs import config


SUPPRESSION_CONTACTS_CSV_PATH = config.CAMPAIGN_SUPPRESSION_DIR / "suppression_contacts.csv"
SUPPRESSION_DOMAINS_CSV_PATH = config.CAMPAIGN_SUPPRESSION_DIR / "suppression_domains.csv"
GLOBAL_SUPPRESSION_DOMAINS_CSV_PATH = config.PROJECT_ROOT / "global_suppression_domains.csv"
VALID_REASONS = {"meeting_booked", "manual_response"}


def load_suppression_contacts(path=SUPPRESSION_CONTACTS_CSV_PATH):
    """Load the selected campaign's manual response/booking registry."""
    with open(path, newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))

    entries = []
    for row_number, row in enumerate(rows, start=2):
        email = (row.get("email") or "").strip()
        reason = (row.get("reason") or "").strip().lower()
        if not email:
            raise RuntimeError(f"Suppression list row {row_number} is missing email.")
        if reason not in VALID_REASONS:
            raise RuntimeError(
                f"Suppression list row {row_number} has invalid reason {reason!r}. "
                f"Use one of: {', '.join(sorted(VALID_REASONS))}."
            )
        entries.append({"email": email, "reason": reason, "notes": (row.get("notes") or "").strip()})
    return entries


def _load_suppression_domains(path):
    with open(path, newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))

    domains = []
    for row_number, row in enumerate(rows, start=2):
        domain = (row.get("domain") or "").strip().lower().lstrip("@")
        if not domain or "." not in domain or "@" in domain:
            raise RuntimeError(f"Suppression domain row {row_number} in {path} has invalid domain {domain!r}.")
        domains.append({"domain": domain, "reason": (row.get("reason") or "").strip(), "notes": (row.get("notes") or "").strip()})
    return domains


def load_suppression_domains(path=SUPPRESSION_DOMAINS_CSV_PATH):
    """Load global and selected-campaign domains that suppress matching contacts."""
    global_domains = _load_suppression_domains(GLOBAL_SUPPRESSION_DOMAINS_CSV_PATH)
    campaign_domains = _load_suppression_domains(path)
    seen_domains = set()
    return [
        entry
        for entry in global_domains + campaign_domains
        if not (entry["domain"] in seen_domains or seen_domains.add(entry["domain"]))
    ]
