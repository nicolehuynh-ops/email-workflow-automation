"""Guarded execution of reviewed Reply.io decisions."""

import json
from dataclasses import dataclass
from typing import Dict, List

from outreach.config import normalize_email
from outreach.database import Database
from outreach.models import Campaign
from outreach.reply.action_dispatch import dispatch
from outreach.reply.client import ReplyWriteClient


@dataclass(frozen=True)
class ApplyResult:
    decision_id: str
    status: str
    reply_action: str = ""
    error: str = ""


def apply_approved_decisions(
    db: Database,
    campaign: Campaign,
    reply: ReplyWriteClient,
    campaign_slug: str,
    configuration_digest: str,
) -> List[ApplyResult]:
    """Apply each approved decision once, retaining failures for audit/review."""
    token = db.acquire_apply_lock(campaign_slug)
    try:
        accounts = {normalize_email(item.get("email")): str(item.get("id")) for item in reply.list_email_accounts()}
        results = []
        for row in db.apply_candidates_for_campaign(campaign_slug):
            decision = dict(row)
            decision_id = decision["id"]
            if db.reply_action_status(decision_id) == "succeeded":
                results.append(ApplyResult(decision_id, "skipped_already_applied"))
                continue
            try:
                _validate_fresh(db, decision, campaign, reply, configuration_digest, accounts)
                request = json.dumps({"campaign_id": campaign.campaign_id, "decision": decision_id}, sort_keys=True)
                db.record_reply_action_requested(decision_id, request)
                outcome = dispatch(reply, campaign, decision)
                db.record_reply_action_result(decision_id, "succeeded", json.dumps(outcome, sort_keys=True))
                db.set_decision_status(decision_id, "applied")
                results.append(ApplyResult(decision_id, "applied", outcome["request"]["action"]))
            except Exception as error:
                # Record even pre-write validation failures, so operators can see
                # why an approved action was not sent.
                request = json.dumps({"campaign_id": campaign.campaign_id, "decision": decision_id}, sort_keys=True)
                db.record_reply_action_requested(decision_id, request)
                db.record_reply_action_result(decision_id, "failed", json.dumps({"error": str(error)}, sort_keys=True))
                db.set_decision_status(decision_id, "failed")
                results.append(ApplyResult(decision_id, "failed", error=str(error)))
        return results
    finally:
        db.release_apply_lock(campaign_slug, token)


def _validate_fresh(
    db: Database,
    decision: Dict,
    campaign: Campaign,
    reply: ReplyWriteClient,
    configuration_digest: str,
    accounts: Dict[str, str],
) -> None:
    if db.decision_run_digest(decision["id"]) != configuration_digest:
        raise RuntimeError("Campaign configuration changed since this decision was reviewed.")
    payload = json.loads(decision["decision_json"])
    contact = payload.get("contact") or {}
    contact_id = contact.get("reply_contact_id")
    if not contact_id:
        raise RuntimeError("Reviewed decision has no Reply.io contact id.")
    live = reply.get_sequence_contact(campaign.campaign_id, contact_id)
    live_step = str((live.get("currentStep") or {}).get("stepId") or live.get("currentStepId") or "")
    reviewed_step = str(payload.get("reviewed_sequence_step_id") or contact.get("sequence_step_id") or "")
    if live_step != reviewed_step:
        raise RuntimeError("Reply.io sequence step changed since review.")
    allowed_emails = (campaign.expected_senders.get(reviewed_step, ""), *campaign.sender_aliases.get(reviewed_step, ()))
    # Hold steps deliberately send no email. Their safety check is the exact
    # reviewed Reply step above; an email-account comparison would be
    # meaningless and could incorrectly block a reviewed contact.
    if not any(allowed_emails):
        return
    allowed_ids = {accounts[email] for email in allowed_emails if email in accounts}
    if not allowed_ids or str(live.get("emailAccountId") or "") not in allowed_ids:
        raise RuntimeError("Reply.io sender no longer matches the reviewed campaign step.")
