import hashlib
import json
from dataclasses import asdict
from typing import Dict, List, Optional

from outreach.config import normalize_company_name, normalize_domain
from outreach.models import Campaign, Contact, Signal
from outreach.reply.issuer_blocking import ReplyContactState, find_domain_company_blocks


# These labels are useful evidence, but never safe grounds for an automatic
# sequence change.  They remain in the review queue so an operator can see the
# source message and decide whether a separate follow-up is appropriate.
REVIEW_ONLY_CLASSIFIER_LABELS = {"out_of_office", "unclear"}


def resolve_match_key(contact: Optional[Contact]) -> Optional[str]:
    """Domain/company grouping key, in priority order: company name, then
    website domain (Reply.io's "Issuer ID" custom field -- its value is a
    company website domain, not an opaque id), then plain email domain."""
    if contact is None:
        return None
    if contact.company_name:
        normalized = normalize_company_name(contact.company_name)
        if normalized:
            return normalized
    if contact.issuer_id:
        normalized = normalize_domain(contact.issuer_id)
        if normalized:
            return normalized
    return contact.company_key


def build_decisions(campaign: Campaign, contacts: List[Contact], signals: List[Signal]) -> List[Dict]:
    by_email = {contact.email: contact for contact in contacts}
    decisions = []
    labels_by_email: Dict[str, set] = {}
    for signal in signals:
        if signal.email and signal.classifier_label:
            labels_by_email.setdefault(signal.email, set()).add(signal.classifier_label)
    conflicting_emails = {email for email, labels in labels_by_email.items() if len(labels) > 1}
    contact_states = [
        ReplyContactState(contact.reply_contact_id or "", contact.email, resolve_match_key(contact), contact.sequence_status or "", contact.sequence_step_id, contact.replied, contact.bounced, contact.opted_out, contact.auto_reply, contact.sender_email)
        for contact in contacts
    ]
    blocks = find_domain_company_blocks(contact_states, campaign)
    contact_by_id = {contact.reply_contact_id: contact for contact in contacts if contact.reply_contact_id}
    for state in blocks["related_rows_to_finish"]:
        if not _matches_configured_domain(state.match_key, campaign.domain_suppression_domains):
            continue
        item = contact_by_id.get(state.reply_contact_id)
        if item:
            reason = "; ".join(blocks["block_reasons"].get(state.match_key, ["Reply.io suppression state"]))
            decisions.append(_decision(campaign, item, "domain_company", state.match_key, reason, None, [item], False))
    for signal in signals:
        scope = campaign.outcome_scopes.get(signal.outcome)
        if not scope:
            continue
        contact = by_email.get(signal.email or "")
        if scope == "exact_contact":
            if not contact:
                continue
            match_key = contact.email
            impacted = [contact]
        else:
            match_key = resolve_match_key(contact) or signal.company_key
            if not match_key:
                continue
            impacted = [item for item in contacts if resolve_match_key(item) == match_key]
        for item in impacted:
            decisions.append(_decision(campaign, item, scope, match_key, None, signal, [item], item.email in conflicting_emails))
    return decisions


def _matches_configured_domain(match_key: Optional[str], domains) -> bool:
    return bool(match_key) and any(match_key == domain or match_key.endswith("." + domain) for domain in domains)


def _decision(campaign: Campaign, item: Contact, scope: str, match_key: str, deterministic_reason: Optional[str], signal: Optional[Signal], impacted: List[Contact], has_conflicting_classifier_signals: bool) -> Dict:
    expected_sender = campaign.expected_senders.get(item.sequence_step_id or "")
    allowed_senders = {expected_sender, *campaign.sender_aliases.get(item.sequence_step_id or "", ())} - {""}
    sender_matches = not allowed_senders or item.sender_email in allowed_senders
    insufficient_confidence = signal is not None and signal.classifier_confidence is not None and (signal.classifier_label == "unclear" or signal.classifier_confidence < campaign.classifier_confidence_threshold)
    review_only_label = signal.classifier_label if signal else None
    if has_conflicting_classifier_signals:
        action, reason = "hold_for_review", "conflicting classifier signals require manual review"
    elif review_only_label in REVIEW_ONLY_CLASSIFIER_LABELS:
        action, reason = "hold_for_review", f"{review_only_label} requires manual review"
    elif not sender_matches:
        action, reason = "hold_for_review", f"sender mismatch for {signal.outcome if signal else deterministic_reason}"
    elif insufficient_confidence:
        action, reason = "hold_for_review", signal.classifier_label or "low-confidence classification"
    else:
        action, reason = "finish", deterministic_reason or signal.classifier_label or signal.outcome
    source = [signal.source_type, signal.source_id] if signal else ["reply_state", item.reply_contact_id]
    fingerprint = json.dumps([campaign.slug, item.email, scope, match_key, *source], sort_keys=True)
    return {
        "id": hashlib.sha256(fingerprint.encode()).hexdigest()[:24], "contact_email": item.email,
        "suppression_type": scope, "match_key": match_key, "reason": reason, "proposed_action": action,
        "confidence": signal.classifier_confidence if signal and signal.classifier_confidence is not None else 1.0,
        "idempotency_key": hashlib.sha256((fingerprint + ":action").encode()).hexdigest(),
        "decision_json": json.dumps({"contact": asdict(item), "impacted_contact_ids": [contact.reply_contact_id for contact in impacted if contact.reply_contact_id], "reviewed_sequence_step_id": item.sequence_step_id, "reviewed_sender_email": item.sender_email, "signal": {key: value for key, value in asdict(signal).items() if key != "content"} if signal else None}, sort_keys=True),
    }
