import json
from pathlib import Path
from typing import Optional, Tuple

from outreach.config import normalize_email
from outreach.models import Contact, Signal


def load_snapshot(path: Path) -> Tuple[list[Contact], list[Signal]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    contacts = [
        Contact(
            reply_contact_id=item.get("replyContactId"),
            email=normalize_email(item.get("email")),
            company_key=normalize_company(item.get("companyKey")),
            sequence_step_id=item.get("sequenceStepId"),
            sender_email=normalize_email(item.get("senderEmail")),
        )
        for item in raw.get("contacts", [])
    ]
    if not contacts or any(not contact.email for contact in contacts):
        raise ValueError("Snapshot must contain contacts with email addresses.")
    signals = [
        Signal(
            source_type=str(item.get("sourceType") or "unknown"),
            source_id=item.get("sourceId"),
            outcome=str(item.get("outcome") or ""),
            email=normalize_email(item.get("email")) or None,
            company_key=normalize_company(item.get("companyKey")),
            sender_email=normalize_email(item.get("senderEmail")) or None,
            content=item.get("content"),
            classifier_label=item.get("classifierLabel"),
            classifier_confidence=item.get("classifierConfidence"),
        )
        for item in raw.get("signals", [])
    ]
    return contacts, signals


def normalize_company(value: Optional[str]) -> Optional[str]:
    value = str(value or "").strip().lower().lstrip("@")
    return value or None
