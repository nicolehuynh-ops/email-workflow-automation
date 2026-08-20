"""Read-only live data adapters. No method in this module writes to a vendor."""

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from outreach.config import normalize_email
from outreach.models import Campaign, Contact, Signal
from outreach.reply.issuer_blocking import normalize_status_key
from outreach.snapshot import normalize_company


def get_custom_field_value(detail: Dict, field_id: Optional[str], field_name: str) -> Optional[str]:
    """Read a Reply.io custom-field value by id or name, case-insensitively.
    Read-only helper -- does not call Reply.io itself."""
    target_id = str(field_id).lower() if field_id else None
    target_name = field_name.lower()
    for entry in detail.get("customFields") or []:
        key = str(entry.get("key") or entry.get("name") or entry.get("id") or "").lower()
        if key and (key == target_id or key == target_name):
            return entry.get("value") or None
    return None


class SourceError(RuntimeError):
    pass


class FrontScopeError(RuntimeError):
    """A Front conversation is outside the campaign's confirmed inbox."""


def parse_timestamp(value) -> Optional[datetime]:
    """Parse Front Unix seconds or an ISO-8601 timestamp as UTC."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromtimestamp(float(text), tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def timestamp_is_on_or_after(value, lower_bound) -> bool:
    timestamp = parse_timestamp(value)
    boundary = parse_timestamp(lower_bound)
    return timestamp is not None and boundary is not None and timestamp >= boundary


def timestamp_as_utc_iso(value) -> Optional[str]:
    parsed = parse_timestamp(value)
    return parsed.isoformat().replace("+00:00", "Z") if parsed else None


class JsonClient:
    def __init__(self, base_url: str, headers: Dict[str, str], opener=None):
        self.base_url = base_url.rstrip("/")
        self.headers = headers
        self.opener = opener or urllib.request.urlopen

    def get(self, path: str, params: Optional[Dict] = None) -> Dict:
        url = path if path.startswith("http") else self.base_url + path
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
        request = urllib.request.Request(url, headers={**self.headers, "Accept": "application/json"})
        try:
            with self.opener(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as error:
            raise SourceError("Live source request failed.") from error


class ReplyReader:
    def __init__(self, api_key: str, opener=None):
        if not api_key:
            raise ValueError("REPLY_IO_API_KEY is required for --live.")
        self.client = JsonClient("https://api.reply.io/v3", {"Authorization": "Bearer " + api_key}, opener)

    def snapshot(self, campaign: Campaign) -> Tuple[List[Contact], List[Signal]]:
        states = self._all("/sequences/%s/contacts" % campaign.campaign_id, "items")
        accounts = self._all("/email-accounts", "items")
        account_emails = {str(item.get("id")): normalize_email(item.get("email")) for item in accounts}
        # One detail fetch per contact, to read company name, opt-out, and custom
        # fields (issuer id) not present on the sequence-contact state itself.
        # The legacy repo bounded this with a small thread pool for large
        # campaigns; kept sequential here for consistency with the rest of this
        # module -- revisit if snapshotting a large sequence proves slow.
        details_by_id: Dict[str, Dict] = {}
        for item in states:
            contact_id = str(item.get("contactId") or "")
            if contact_id and contact_id not in details_by_id:
                details_by_id[contact_id] = self.client.get("/contacts/%s" % contact_id)
        contacts, signals = [], []
        for item in states:
            disposition = item.get("emailDisposition") or {}
            step = item.get("currentStep") or {}
            email = normalize_email(item.get("email"))
            if not email:
                continue
            contact_id = str(item.get("contactId") or "")
            detail = details_by_id.get(contact_id, {})
            sequence_status = str(item.get("statusInSequence") or "")
            auto_reply = bool(item.get("autoReply")) or normalize_status_key(sequence_status) == "outofoffice"
            contact = Contact(
                reply_contact_id=contact_id or None,
                email=email,
                company_key=normalize_company(email.partition("@")[2]),
                sequence_step_id=str(step.get("stepId") or "") or None,
                sender_email=account_emails.get(str(item.get("emailAccountId") or "")) or None,
                company_name=detail.get("company") or None,
                issuer_id=get_custom_field_value(detail, campaign.issuer_id_field_id, campaign.issuer_id_field_name),
                sequence_status=sequence_status or None,
                replied=bool(disposition.get("isReplied")),
                bounced=bool(disposition.get("isBounced")),
                opted_out=bool(detail.get("isOptedOut")),
                auto_reply=auto_reply,
            )
            contacts.append(contact)
            # Reply.io may mark an automatic reply as both "replied" and
            # "autoReply".  Do not convert that state into a generic
            # reply_received finish proposal; Front classification, if any,
            # is retained as review-only evidence instead.
            if disposition.get("isReplied") and not auto_reply:
                signals.append(Signal("reply", contact_id, "reply_received", email, contact.company_key, contact.sender_email))
        return contacts, signals

    def _all(self, path: str, collection_key: str) -> List[Dict]:
        items, skip = [], 0
        while True:
            payload = self.client.get(path, {"top": 100, "skip": skip})
            page = payload.get(collection_key, []) if isinstance(payload, dict) else []
            items.extend(page)
            if not payload.get("hasMore") or not page:
                return items
            skip += len(page)


class FrontReader:
    def __init__(self, api_token: str, opener=None):
        if not api_token:
            raise ValueError("FRONT_API_TOKEN is required for --live.")
        self.client = JsonClient("https://api2.frontapp.com", {"Authorization": "Bearer " + api_token}, opener)

    def signals(self, campaign: Campaign, contacts: List[Contact]) -> List[Signal]:
        if len(campaign.front_inbox_ids) != 1:
            raise ValueError("Front reads require exactly one configured inbox ID.")
        expected_inbox_id = str(campaign.front_inbox_ids[0])
        known = {contact.email: contact for contact in contacts}
        signals = []
        # Scope the initial list request and independently verify every returned
        # conversation before its messages are ever fetched.  The latter guards
        # against shared/moved conversations and a token that can see siblings.
        conversations = self._all("/inboxes/%s/conversations" % expected_inbox_id)
        for conversation in conversations:
            conversation_id = str(conversation.get("id") or "")
            if not conversation_id or not self.conversation_is_in_expected_inbox(conversation_id, expected_inbox_id):
                continue
            for message in self._all("/conversations/%s/messages" % conversation_id):
                if message.get("is_inbound") is not True:
                    continue
                author = message.get("author") or {}
                email = normalize_email(author.get("email"))
                contact = known.get(email)
                created_at = message.get("created_at")
                if campaign.front_since and not timestamp_is_on_or_after(created_at, campaign.front_since):
                    continue
                signals.append(Signal(
                    "front", str(message.get("id") or conversation_id), "reply_received", email or None,
                    contact.company_key if contact else normalize_company(email.partition("@")[2]),
                    contact.sender_email if contact else None,
                    str(message.get("text") or message.get("body") or ""),
                    inbox_id=expected_inbox_id,
                    matched_reply_contact=contact is not None,
                    conversation_id=conversation_id,
                    occurred_at=timestamp_as_utc_iso(created_at),
                ))
        return signals

    def conversation_is_in_expected_inbox(self, conversation_id: str, expected_inbox_id: str) -> bool:
        """Fail closed when Front cannot prove this conversation's membership.

        Future Front mutation code must call this method immediately before a
        send/update and refuse the action when it returns false.
        """
        inboxes = self._all("/conversations/%s/inboxes" % conversation_id)
        return str(expected_inbox_id) in {str(inbox.get("id") or "") for inbox in inboxes}

    def require_conversation_in_expected_inbox(self, conversation_id: str, expected_inbox_id: str) -> None:
        """Fail closed before any future Front send or conversation mutation."""
        if not self.conversation_is_in_expected_inbox(conversation_id, expected_inbox_id):
            raise FrontScopeError(
                f"Refusing Front action for conversation {conversation_id}: it is not proven to belong to inbox {expected_inbox_id}."
            )

    def _all(self, path: str, params: Optional[Dict] = None) -> List[Dict]:
        items, next_path, next_params = [], path, params
        while next_path:
            payload = self.client.get(next_path, next_params)
            items.extend((payload.get("_results") or payload.get("results") or []))
            next_path = ((payload.get("_pagination") or {}).get("next"))
            next_params = None
        return items


class CalendlyReader:
    def __init__(self, access_token: str, organization_uri: str, opener=None):
        if not access_token or not organization_uri:
            raise ValueError("CALENDLY_ACCESS_TOKEN and CALENDLY_ORGANIZATION_URI are required for --live.")
        self.organization_uri = organization_uri
        self.client = JsonClient("https://api.calendly.com", {"Authorization": "Bearer " + access_token}, opener)

    def signals(self, campaign: Campaign, contacts: List[Contact]) -> List[Signal]:
        known = {contact.email: contact for contact in contacts}
        params = {"organization": self.organization_uri, "count": 100, "status": "active"}
        if campaign.calendly_report_start:
            params["min_start_time"] = campaign.calendly_report_start
        events = self._all("/scheduled_events", params)
        signals = []
        for event in events:
            if campaign.calendly_event_type_uris and event.get("event_type") not in campaign.calendly_event_type_uris:
                continue
            event_id = str(event.get("uri") or "").rstrip("/").split("/")[-1]
            for invitee in self._all("/scheduled_events/%s/invitees" % event_id, {"count": 100}):
                email = normalize_email(invitee.get("email"))
                contact = known.get(email)
                if contact:
                    signals.append(Signal("calendly", str(invitee.get("uri") or event_id), "meeting_booked", email, contact.company_key, contact.sender_email))
        return signals

    def _all(self, path: str, params: Optional[Dict] = None) -> List[Dict]:
        items, next_path, next_params = [], path, params
        while next_path:
            payload = self.client.get(next_path, next_params)
            items.extend(payload.get("collection") or [])
            next_path = (payload.get("pagination") or {}).get("next_page")
            next_params = None
        return items
