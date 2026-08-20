import hashlib
import json
from pathlib import Path
from typing import Optional, Tuple

from outreach.models import Campaign, SequenceStep

VALID_SCOPES = {"exact_contact", "domain_company"}
VALID_ROLES = {"initial", "hold", "chase", "final_hold", "final"}
NON_SENDING_ROLES = {"hold", "final_hold"}
VALID_POSITIVE_OUTCOMES = {"reply_received", "meeting_booked", "event_rsvp"}


def load_campaign(path: Path) -> Tuple[Campaign, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    required = ("campaignId", "slug", "sequenceSteps", "suppressionPolicy")
    missing = [key for key in required if not raw.get(key)]
    if missing:
        raise ValueError(f"Campaign configuration is missing: {', '.join(missing)}")
    expected_senders = {}
    sender_aliases = {}
    sequence_steps = []
    role_counts: dict = {}
    for step in raw["sequenceSteps"]:
        if not step.get("id"):
            raise ValueError("Each sequence step requires an id.")
        role = step.get("role")
        if not role or role not in VALID_ROLES:
            raise ValueError(f"Each sequence step requires a role in {sorted(VALID_ROLES)}.")
        sender = normalize_email(step.get("expectedSender"))
        aliases = tuple(normalize_email(alias) for alias in (step.get("senderAliases") or []))
        if not sender and role not in NON_SENDING_ROLES:
            raise ValueError("Each sending sequence step requires expectedSender.")
        if role in NON_SENDING_ROLES and (sender or aliases):
            raise ValueError(f"The '{role}' step is non-sending and must not declare expectedSender or senderAliases.")
        role_counts[role] = role_counts.get(role, 0) + 1
        step_id = str(step["id"])
        expected_senders[step_id] = sender
        sender_aliases[step_id] = aliases
        sequence_steps.append(SequenceStep(step_id, role, sender, aliases))
    for role in ("initial", "final"):
        if role_counts.get(role, 0) != 1:
            raise ValueError(f"A campaign must declare exactly one '{role}' sequence step.")
    scopes = raw["suppressionPolicy"].get("outcomeScopes", {})
    if not scopes:
        raise ValueError("suppressionPolicy.outcomeScopes is required.")
    invalid = {outcome: scope for outcome, scope in scopes.items() if scope not in VALID_SCOPES}
    if invalid:
        raise ValueError(f"Invalid suppression scope(s): {invalid}")
    domain_suppression = raw["suppressionPolicy"].get("domainSuppression") or {}
    domain_suppression_domains = tuple(
        normalize_domain(domain) for domain in (domain_suppression.get("domains") or [])
    )
    if "domain_company" in scopes.values() and not domain_suppression_domains:
        raise ValueError(
            "suppressionPolicy.domainSuppression.domains is required when an outcome uses domain_company."
        )
    manual_overrides = raw["suppressionPolicy"].get("manualOverrides") or {}
    manual_ooo_emails = tuple(normalize_email(email) for email in (manual_overrides.get("outOfOffice") or []))
    manual_exclusion_emails = tuple(normalize_email(email) for email in (manual_overrides.get("excluded") or []))
    digest = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
    front = raw.get("front") or {}
    front_inbox_ids = list(front.get("inboxIds") or [])
    if front_inbox_ids:
        if len(front_inbox_ids) != 1:
            raise ValueError("front.inboxIds must contain exactly one inbox ID for a campaign.")
        confirmed_inbox_id = str(front.get("confirmedInboxId") or "")
        if confirmed_inbox_id != str(front_inbox_ids[0]):
            raise ValueError("front.confirmedInboxId must match the sole front.inboxIds value.")
        if not str(front.get("confirmation") or "").strip():
            raise ValueError("front.confirmation is required to acknowledge inbox-level scope.")
    calendly = raw.get("calendly") or {}
    reply = raw.get("reply") or {}
    classification = raw.get("classification") or {}
    threshold = float(classification.get("confidenceThreshold", 0.8))
    if not 0 <= threshold <= 1:
        raise ValueError("classification.confidenceThreshold must be between 0 and 1.")
    gateway_scope = classification.get("gatewayInputScope") or {}
    if not isinstance(gateway_scope, dict):
        raise ValueError("classification.gatewayInputScope must be an object.")
    allowed_scope_keys = {
        "allowedUnmappedEmails", "allowedFrontConversationIds", "allowedFrontMessageIds", "maxMessagesPerRun"
    }
    unknown_scope_keys = set(gateway_scope) - allowed_scope_keys
    if unknown_scope_keys:
        raise ValueError(f"Unknown classification.gatewayInputScope field(s): {sorted(unknown_scope_keys)}")
    for field_name in ("allowedUnmappedEmails", "allowedFrontConversationIds", "allowedFrontMessageIds"):
        if field_name in gateway_scope and not isinstance(gateway_scope[field_name], list):
            raise ValueError(f"classification.gatewayInputScope.{field_name} must be a list.")
    gateway_allowed_unmapped_emails = tuple(
        normalize_email(email) for email in (gateway_scope.get("allowedUnmappedEmails") or [])
    )
    if any(not email or "*" in email for email in gateway_allowed_unmapped_emails):
        raise ValueError(
            "classification.gatewayInputScope.allowedUnmappedEmails requires exact non-empty emails; wildcards are forbidden."
        )
    gateway_allowed_front_conversation_ids = _exact_ids(
        gateway_scope.get("allowedFrontConversationIds") or [], "allowedFrontConversationIds"
    )
    gateway_allowed_front_message_ids = _exact_ids(
        gateway_scope.get("allowedFrontMessageIds") or [], "allowedFrontMessageIds"
    )
    try:
        gateway_max_messages_per_run = int(gateway_scope.get("maxMessagesPerRun", 50))
    except (TypeError, ValueError) as error:
        raise ValueError("classification.gatewayInputScope.maxMessagesPerRun must be an integer.") from error
    if not 1 <= gateway_max_messages_per_run <= 500:
        raise ValueError("classification.gatewayInputScope.maxMessagesPerRun must be between 1 and 500.")
    issuer_id_field_id = reply.get("issuerIdFieldId")
    positive_response_outcomes = tuple(raw.get("positiveResponseDefinition") or [])
    if not positive_response_outcomes or not set(positive_response_outcomes) <= VALID_POSITIVE_OUTCOMES:
        raise ValueError(f"positiveResponseDefinition must contain only {sorted(VALID_POSITIVE_OUTCOMES)}.")
    analytics = raw.get("analytics") or {}
    return Campaign(
        str(raw["campaignId"]), str(raw["slug"]), expected_senders, scopes,
        front_inbox_ids, front.get("since"),
        calendly.get("reportStart"), list(calendly.get("eventTypeUris") or []), threshold,
        sender_aliases=sender_aliases,
        sequence_steps=tuple(sequence_steps),
        domain_suppression_domains=domain_suppression_domains,
        manual_ooo_emails=manual_ooo_emails,
        manual_exclusion_emails=manual_exclusion_emails,
        issuer_id_field_id=str(issuer_id_field_id) if issuer_id_field_id is not None else None,
        issuer_id_field_name=reply.get("issuerIdFieldName") or "Issuer ID",
        positive_response_outcomes=positive_response_outcomes,
        analytics_sequence=analytics.get("sequence") or str(raw["slug"]),
        analytics_email_version=str(analytics.get("emailVersion") or ""),
        gateway_allowed_unmapped_emails=gateway_allowed_unmapped_emails,
        gateway_allowed_front_conversation_ids=gateway_allowed_front_conversation_ids,
        gateway_allowed_front_message_ids=gateway_allowed_front_message_ids,
        gateway_max_messages_per_run=gateway_max_messages_per_run,
    ), digest


def normalize_email(value: Optional[str]) -> str:
    return str(value or "").strip().lower()


def normalize_company_name(value: Optional[str]) -> Optional[str]:
    normalized = "".join(char for char in str(value or "").strip().lower() if char.isalnum())
    return normalized or None


def normalize_domain(value: Optional[str]) -> Optional[str]:
    normalized = str(value or "").strip().lower().lstrip("@")
    return normalized or None


def _exact_ids(values, field_name: str) -> Tuple[str, ...]:
    normalized = tuple(str(value or "").strip() for value in values)
    if any(not value or "*" in value for value in normalized):
        raise ValueError(f"classification.gatewayInputScope.{field_name} requires exact non-empty IDs; wildcards are forbidden.")
    return normalized
