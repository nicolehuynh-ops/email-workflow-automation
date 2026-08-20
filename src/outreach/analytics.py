"""Materialize the imported analytics project's input contract from SQLite."""

import csv
import json
import os
import subprocess
import tempfile
from pathlib import Path

from outreach.database import Database
from outreach.models import Campaign


REPLY_HEADERS = ["Contact Id", "PCS Issuer ID", "Contact email", "Sequence", "Sequence step", "Delivered", "Opened", "Clicked", "Replied", "OptedOut", "PCS Sender", "IssuerName", "Delivery date"]


def materialize_analytics_inputs(db: Database, campaign: Campaign, analytics_root: Path, environment=None) -> dict:
    run = db.latest_analytics_run(campaign.slug)
    if not run:
        raise ValueError(f"No completed review or dry-run data exists for campaign '{campaign.slug}'.")
    campaign_dir = analytics_root / campaign.slug
    input_dir = campaign_dir / "input"
    config_dir = campaign_dir / "config"
    input_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)
    overrides = config_dir / "issuer-overrides.json"
    if not overrides.exists():
        overrides.write_text("{}\n", encoding="utf-8")
    contacts = [json.loads(row["source_json"]) for row in db.contacts_for_run(run["id"])]
    contact_by_email = {item["email"]: item for item in contacts}
    report = input_dir / "Reply.io_Contact_Report.csv"
    with report.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPLY_HEADERS)
        writer.writeheader()
        for contact in contacts:
            writer.writerow({
                "Contact Id": contact.get("reply_contact_id") or "", "PCS Issuer ID": contact.get("issuer_id") or contact.get("company_key") or "",
                "Contact email": contact["email"], "Sequence": campaign.analytics_sequence or campaign.slug,
                "Sequence step": contact.get("sequence_step_id") or "", "Delivered": "", "Opened": "", "Clicked": "",
                "Replied": "1" if contact.get("replied") else "", "OptedOut": "1" if contact.get("opted_out") else "",
                "PCS Sender": contact.get("sender_email") or "", "IssuerName": contact.get("company_name") or "", "Delivery date": "",
            })
    positive_rows = []
    seen = set()
    for evidence in db.evidence_for_run(run["id"]):
        payload = json.loads(evidence["payload_json"])
        outcome = payload.get("outcome")
        email = payload.get("email")
        if outcome not in campaign.positive_response_outcomes or not email:
            continue
        key = (email, outcome, payload.get("source_type"), payload.get("source_id"))
        if key in seen:
            continue
        seen.add(key)
        contact = contact_by_email.get(email, {})
        positive_rows.append({
            "Issuer Name": contact.get("company_name") or "", "Domain": contact.get("issuer_id") or contact.get("company_key") or "",
            "PCS Sender": contact.get("sender_email") or payload.get("sender_email") or "", "Email Version": campaign.analytics_email_version,
            "Response": outcome.replace("_", " ").title(), "Contact Email": email, "Outcome Type": outcome,
            "Source": payload.get("source_type") or evidence["source_type"], "Evidence ID": payload.get("source_id") or evidence["source_id"] or "",
        })
    workbook = input_dir / "positive_response_generated.xlsx"
    _write_workbook(positive_rows, workbook, environment or os.environ)
    return {"run_id": run["id"], "contact_report": str(report), "positive_response_workbook": str(workbook), "issuer_overrides": str(overrides), "positive_response_count": len(positive_rows)}


def _write_workbook(rows, destination: Path, environment) -> None:
    node = environment.get("ARTIFACT_TOOL_NODE_BIN")
    modules = environment.get("ARTIFACT_TOOL_NODE_MODULES")
    if not node or not modules:
        raise RuntimeError("Analytics workbook creation requires ARTIFACT_TOOL_NODE_BIN and ARTIFACT_TOOL_NODE_MODULES.")
    script = Path(__file__).resolve().parents[2] / "scripts" / "write_positive_response_workbook.mjs"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
        json.dump(rows, handle)
        source = Path(handle.name)
    try:
        subprocess.run([node, str(script), str(source), str(destination)], check=True, env={**environment, "ARTIFACT_TOOL_NODE_MODULES": modules}, capture_output=True, text=True)
    finally:
        source.unlink(missing_ok=True)
