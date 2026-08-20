"""Live read-and-classify orchestration. This module never applies Reply actions."""

from dataclasses import replace
from typing import Dict, List, Tuple
from uuid import uuid4

from outreach.adapters import CalendlyReader, FrontReader, ReplyReader
from outreach.gateway import GatewayError, HiiveGatewayClassifierClient, config_from_env
from outreach.models import Campaign, Contact, Signal


def collect_live(campaign: Campaign, environment: Dict[str, str]) -> Tuple[List[Contact], List[Signal]]:
    # Validate the gateway before reading any vendor data. This guarantees a
    # broken route/model cannot cause Reply, Front, or Calendly synchronization.
    classifier = HiiveGatewayClassifierClient(config_from_env(environment))
    classifier.validate_configured_model()
    reply = ReplyReader(environment.get("REPLY_IO_API_KEY", ""))
    contacts, reply_signals = reply.snapshot(campaign)
    front_signals = FrontReader(environment.get("FRONT_API_TOKEN", "")).signals(campaign, contacts)
    calendly_signals = CalendlyReader(
        environment.get("CALENDLY_ACCESS_TOKEN", ""), environment.get("CALENDLY_ORGANIZATION_URI", "")
    ).signals(campaign, contacts)
    return contacts, [*reply_signals, *classify_front_signals(front_signals, classifier, campaign), *calendly_signals]


def classify_front_signals(signals: List[Signal], classifier: HiiveGatewayClassifierClient, campaign: Campaign = None) -> List[Signal]:
    classified = list(signals)
    pending = []
    for index, signal in enumerate(signals):
        scope_basis = _gateway_scope_basis(signal, campaign)
        if scope_basis is None:
            classified[index] = replace(
                signal,
                classifier_label="unclear",
                classifier_confidence=0.0,
                gateway_model=classifier.config.model,
                gateway_status="skipped_scope",
                gateway_attempts=0,
                gateway_scope_basis="not_mapped_or_allowlisted",
            )
            continue
        if not signal.content:
            classified[index] = replace(
                signal,
                classifier_label="unclear",
                classifier_confidence=0.0,
                gateway_model=classifier.config.model,
                gateway_status="skipped_empty",
                gateway_attempts=0,
                gateway_scope_basis=scope_basis,
            )
            continue
        pending.append((index, signal, scope_basis))

    max_messages = campaign.gateway_max_messages_per_run if campaign else 50
    if len(pending) > max_messages:
        for index, signal, scope_basis in pending:
            classified[index] = replace(
                signal,
                classifier_label="unclear",
                classifier_confidence=0.0,
                gateway_model=classifier.config.model,
                gateway_status="skipped_run_limit",
                gateway_attempts=0,
                gateway_scope_basis=scope_basis,
            )
        return classified

    batch_size = classifier.config.batch_size
    batches = [pending[offset:offset + batch_size] for offset in range(0, len(pending), batch_size)]
    for batch_number, batch in enumerate(batches):
        batch_id = str(uuid4())
        request_items = [(f"item-{index:06d}", signal.content or "") for index, signal, _scope_basis in batch]
        try:
            result = classifier.classify_batch(request_items)
            for index, signal, scope_basis in batch:
                item_id = f"item-{index:06d}"
                item_result = result.results[item_id]
                classified[index] = replace(
                    signal,
                    classifier_label=item_result.label,
                    classifier_confidence=item_result.confidence,
                    gateway_batch_id=batch_id,
                    gateway_item_id=item_id,
                    gateway_batch_size=len(batch),
                    gateway_response_id=result.response_id,
                    gateway_model=result.model,
                    gateway_attempts=result.attempts,
                    gateway_status="succeeded",
                    gateway_scope_basis=scope_basis,
                )
        except GatewayError as error:
            # Keep every message as review-only evidence when its batch cannot be
            # classified. Never guess which partial model result belongs to which
            # message, and never omit a reply because of a gateway failure.
            for index, signal, scope_basis in batch:
                classified[index] = replace(
                    signal,
                    classifier_label="unclear",
                    classifier_confidence=0.0,
                    gateway_batch_id=batch_id,
                    gateway_item_id=f"item-{index:06d}",
                    gateway_batch_size=len(batch),
                    gateway_model=classifier.config.model,
                    gateway_attempts=error.attempts,
                    gateway_status="failed",
                    gateway_http_status=error.status_code,
                    gateway_error_code=error.gateway_error_code,
                    gateway_cf_ray=error.cf_ray,
                    gateway_scope_basis=scope_basis,
                )
        if batch_number < len(batches) - 1 and classifier.config.batch_pause_seconds:
            classifier.sleeper(classifier.config.batch_pause_seconds)
    return classified


def _gateway_scope_basis(signal: Signal, campaign: Campaign = None):
    if signal.matched_reply_contact is True:
        return "matched_reply_contact"
    if campaign is None:
        return None
    if signal.email and signal.email in campaign.gateway_allowed_unmapped_emails:
        return "allowlisted_author"
    if signal.conversation_id and signal.conversation_id in campaign.gateway_allowed_front_conversation_ids:
        return "allowlisted_conversation"
    if signal.source_id and signal.source_id in campaign.gateway_allowed_front_message_ids:
        return "allowlisted_message"
    return None
