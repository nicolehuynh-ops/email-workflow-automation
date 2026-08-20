"""Read-only human audit exports; these files never authorize actions."""

import csv
from pathlib import Path

from outreach.database import Database


def write_suppression_lists(db: Database, campaign_slug: str, output_dir: Path) -> dict:
    rows = db.connection.execute(
        "SELECT d.* FROM suppression_decisions d JOIN campaign_runs r ON r.id = d.run_id "
        "WHERE r.campaign_slug = ? AND d.status IN ('approved', 'applied') ORDER BY d.contact_email", (campaign_slug,)
    ).fetchall()
    destination = output_dir / campaign_slug
    destination.mkdir(parents=True, exist_ok=True)
    contacts_path, domains_path = destination / "suppression_contacts.csv", destination / "suppression_domains.csv"
    exact = [row for row in rows if row["suppression_type"] == "exact_contact"]
    domains = {row["match_key"]: row for row in rows if row["suppression_type"] == "domain_company"}
    with contacts_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["email", "reason", "notes"])
        writer.writeheader()
        writer.writerows({"email": row["contact_email"], "reason": row["reason"], "notes": f"decision:{row['id']}"} for row in exact)
    with domains_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["domain", "reason", "notes"])
        writer.writeheader()
        writer.writerows({"domain": key, "reason": row["reason"], "notes": f"decision:{row['id']}"} for key, row in domains.items())
    return {"contacts": str(contacts_path), "domains": str(domains_path), "exact_contact_count": len(exact), "domain_company_count": len(domains)}
