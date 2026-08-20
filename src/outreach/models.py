from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class SequenceStep:
    id: str
    role: str  # "initial" | "hold" | "chase" | "final_hold" | "final"
    expected_sender: str
    sender_aliases: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Campaign:
    campaign_id: str
    slug: str
    expected_senders: Dict[str, str]
    outcome_scopes: Dict[str, str]
    front_inbox_ids: list
    front_since: Optional[str]
    calendly_report_start: Optional[str]
    calendly_event_type_uris: list
    classifier_confidence_threshold: float
    sender_aliases: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    sequence_steps: Tuple[SequenceStep, ...] = ()
    domain_suppression_domains: Tuple[str, ...] = ()
    manual_ooo_emails: Tuple[str, ...] = ()
    manual_exclusion_emails: Tuple[str, ...] = ()
    issuer_id_field_id: Optional[str] = None
    issuer_id_field_name: str = "Issuer ID"
    positive_response_outcomes: Tuple[str, ...] = ()
    analytics_sequence: Optional[str] = None
    analytics_email_version: str = ""
    gateway_allowed_unmapped_emails: Tuple[str, ...] = ()
    gateway_allowed_front_conversation_ids: Tuple[str, ...] = ()
    gateway_allowed_front_message_ids: Tuple[str, ...] = ()
    gateway_max_messages_per_run: int = 50


@dataclass(frozen=True)
class Contact:
    reply_contact_id: Optional[str]
    email: str
    company_key: Optional[str]
    sequence_step_id: Optional[str]
    sender_email: Optional[str]
    company_name: Optional[str] = None
    issuer_id: Optional[str] = None  # a company website domain, sourced from Reply.io's Issuer ID custom field
    sequence_status: Optional[str] = None
    replied: bool = False
    bounced: bool = False
    opted_out: bool = False
    auto_reply: bool = False


@dataclass(frozen=True)
class Signal:
    source_type: str
    source_id: Optional[str]
    outcome: str
    email: Optional[str]
    company_key: Optional[str]
    sender_email: Optional[str] = None
    content: Optional[str] = None
    classifier_label: Optional[str] = None
    classifier_confidence: Optional[float] = None
    inbox_id: Optional[str] = None
    matched_reply_contact: Optional[bool] = None
    conversation_id: Optional[str] = None
    occurred_at: Optional[str] = None
    gateway_batch_id: Optional[str] = None
    gateway_item_id: Optional[str] = None
    gateway_batch_size: Optional[int] = None
    gateway_response_id: Optional[str] = None
    gateway_model: Optional[str] = None
    gateway_attempts: Optional[int] = None
    gateway_status: Optional[str] = None
    gateway_http_status: Optional[int] = None
    gateway_error_code: Optional[str] = None
    gateway_cf_ray: Optional[str] = None
    gateway_scope_basis: Optional[str] = None
