"""Domain/company-scope blocking precedence.

Ported, behavior-preserving, from the vendored ``reply.io-email-sender`` repo's
``pcs/campaign.py::find_issuer_blocks``. The legacy function blocked by an
opaque ``PCS Issuer ID`` (an implicit suppression scope); this version blocks
by whatever ``match_key`` the caller (``outreach.decisions``) has already
resolved for a ``domain_company`` outcome scope -- per BUILD_PLAN.md:167,
``PCS Issuer ID`` is never used as an implicit suppression scope here, only
retained on ``Contact`` as an analytics/correlation field.

The four-phase precedence below must not be reordered -- see
docs/REPLY_IO_MIGRATION_PLAN.md for why each phase exists and the failure
modes preserving the order guards against.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Set

from outreach.models import Campaign


@dataclass(frozen=True)
class ReplyContactState:
    reply_contact_id: str
    email: str
    match_key: Optional[str]
    sequence_status: str
    current_step_id: Optional[str]
    replied: bool
    bounced: bool
    opted_out: bool
    auto_reply: bool
    sender_email: Optional[str] = None


def normalize_status_key(status: Optional[str]) -> str:
    return "".join(char for char in str(status or "").lower() if char.isalnum())


def is_active_status(status: Optional[str]) -> bool:
    return normalize_status_key(status) == "active"


def is_finished_status(status: Optional[str]) -> bool:
    return normalize_status_key(status) == "finished"


def find_domain_company_blocks(rows: List[ReplyContactState], campaign: Campaign) -> Dict:
    """Compute which match_key groups are blocked and which sibling rows must be finished.

    The suppression rules applied here -- which emails are manually flagged
    out-of-office, and which are contact-level exclusions -- are sourced
    entirely from ``campaign`` (``campaign.manual_ooo_emails`` /
    ``campaign.manual_exclusion_emails``, both parsed once at campaign-config
    load time by ``outreach.config.load_campaign``). Nothing here reads a
    separate suppression file or accepts ad hoc overrides -- campaign
    configuration is the single source of which suppression rules apply.

    Exact precedence (do not reorder):
    1. Exemptions checked per-contact BEFORE any blocking check: a manually
       flagged out-of-office email, or Reply.io's own auto_reply flag, is
       never an automatic blocker -- even if Reply.io also reports it as a
       reply or a finished status.
    2. Automatic detection (if/elif -- one reason per contact, only for rows
       that passed step 1): replied=True blocks with reason "Reply.io
       response" (highest priority); bounced or opted_out ALONE never blocks;
       a finished status blocks with reason "Finished for a non-bounce,
       non-opt-out, non-OOO reason" (lowest priority), unless the contact is
       in campaign.manual_exclusion_emails.
    3. Strip pass: for any match_key group containing a manual-OOO contact,
       remove blockers in that group whose reason is specifically the
       "Finished..." string above -- but never strip a "Reply.io response"
       blocker from a different contact in the same group.
    4. (Manual/unconditional overrides -- booked/responded signals,
       configured suppression domains -- are appended by the caller,
       ``outreach.decisions``, as additional synthetic blocker rows on top of
       this function's output; they are never subject to the strip in step 3.)
    """
    manual_ooo_emails = set(campaign.manual_ooo_emails)
    contact_exclusion_emails = set(campaign.manual_exclusion_emails)

    rows_by_match_key: Dict[str, List[ReplyContactState]] = {}
    rows_missing_match_key = []
    for row in rows:
        if not row.match_key:
            rows_missing_match_key.append(row)
            continue
        rows_by_match_key.setdefault(row.match_key, []).append(row)

    manual_ooo_match_keys = {
        row.match_key for row in rows if row.email in manual_ooo_emails and row.match_key
    }

    blocker_rows = []
    for row in rows:
        # A manually verified OOO may be represented by Reply.io as Finished/replied.
        if row.email in manual_ooo_emails:
            continue
        # OOO remains an exception even when Reply.io exposes it as a reply.
        if row.auto_reply:
            continue
        if row.replied:
            blocker_rows.append((row, "Reply.io response"))
        # A bounce or opt-out alone is never an issuer blocker. A response above takes precedence.
        elif row.bounced or row.opted_out:
            continue
        elif is_finished_status(row.sequence_status) and row.email not in contact_exclusion_emails:
            blocker_rows.append((row, "Finished for a non-bounce, non-opt-out, non-OOO reason"))

    # A manual OOO override is authoritative for legacy Reply.io Finished flags.
    # Do not let one create a block; genuine replies remain blockers.
    blocker_rows = [
        (row, reason)
        for row, reason in blocker_rows
        if not (row.match_key in manual_ooo_match_keys and reason == "Finished for a non-bounce, non-opt-out, non-OOO reason")
    ]

    blocked_match_keys = {row.match_key for row, _ in blocker_rows if row.match_key}
    block_reasons: Dict[str, List[str]] = {}
    for row, reason in blocker_rows:
        if row.match_key:
            block_reasons.setdefault(row.match_key, []).append(reason)

    related_rows_to_finish = []
    for match_key in blocked_match_keys:
        for row in rows_by_match_key.get(match_key, []):
            if not is_finished_status(row.sequence_status) and not row.opted_out and not row.bounced and not row.auto_reply:
                related_rows_to_finish.append(row)

    return {
        "rows_by_match_key": rows_by_match_key,
        "rows_missing_match_key": rows_missing_match_key,
        "blocker_rows": [row for row, _ in blocker_rows],
        "block_reasons": {key: sorted(set(reasons)) for key, reasons in block_reasons.items()},
        "blocked_match_keys": blocked_match_keys,
        "related_rows_to_finish": related_rows_to_finish,
    }


def eligible_for_step(
    rows: List[ReplyContactState],
    blocked_match_keys: Set[str],
    match_key_of: Callable[[ReplyContactState], Optional[str]],
    required_step_id: str,
    campaign: Campaign,
    extra_excluded_emails: Optional[Set[str]] = None,
) -> List[ReplyContactState]:
    """Contact-level exclusions default to ``campaign.manual_exclusion_emails``
    (sourced from campaign config, same as ``find_domain_company_blocks``);
    ``extra_excluded_emails`` layers on per-run exclusions (e.g. RSVP matches)
    that aren't standing campaign configuration."""
    excluded_emails = set(campaign.manual_exclusion_emails) | (extra_excluded_emails or set())
    eligible = []
    for row in rows:
        if row.email in excluded_emails:
            continue
        match_key = match_key_of(row)
        if not match_key or match_key in blocked_match_keys:
            continue
        if not is_active_status(row.sequence_status):
            continue
        if row.replied or row.bounced or row.auto_reply or row.opted_out:
            continue
        if str(row.current_step_id or "") != str(required_step_id):
            continue
        eligible.append(row)
    return eligible
