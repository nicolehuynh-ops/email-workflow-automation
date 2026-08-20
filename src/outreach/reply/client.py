"""Write-capable Reply.io API client.

Unlike ``outreach.adapters`` (read-only; no method there ever mutates a
vendor), every write here corresponds to a real Reply.io mutation: finishing
contacts, moving them between sequence steps, and updating custom fields.
Ported from the vendored ``reply.io-email-sender`` repo's ``pcs/reply_client.py``,
adapted to this project's injectable-``opener`` testing convention (see
``outreach.adapters.JsonClient`` and ``tests/test_gateway.py``).
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict, List, Optional

PAGE_SIZE = 1000
WRITE_CHUNK_SIZE = 100


class ReplyWriteError(RuntimeError):
    """A Reply.io write request failed after the vendor accepted it."""


class ReplyRateLimitError(ReplyWriteError):
    """Reply.io returned HTTP 429. Never retried automatically."""

    def __init__(self, retry_after: str):
        super().__init__(f"Reply.io rate limit hit. Retry-After: {retry_after}")
        self.retry_after = retry_after


def chunks(items: List, size: int):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def merge_custom_field_value(existing_fields: List[Dict], field_id, value: str) -> List[Dict]:
    merged = []
    target_id = str(field_id)
    found = False
    for field in existing_fields:
        key = field.get("key") or field.get("id")
        if not key:
            continue
        item = {"id": int(key) if str(key).isdigit() else key, "value": field.get("value") or ""}
        if str(key) == target_id:
            item["value"] = value
            found = True
        merged.append(item)
    if not found:
        merged.append({"id": field_id, "value": value})
    return merged


class ReplyWriteClient:
    def __init__(self, api_key: str, opener: Optional[Callable] = None):
        if not api_key:
            raise ValueError("REPLY_IO_API_KEY is required.")
        self.api_key = api_key
        self.base_url = "https://api.reply.io/v3"
        self.opener = opener or urllib.request.urlopen

    def request(self, method: str, path: str, body: Optional[Dict] = None) -> Dict:
        headers = {"Authorization": f"Bearer {self.api_key}", "user-agent": "hiive-outreach-workflow/1.0"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        for attempt in range(3):
            try:
                with self.opener(request, timeout=60) as response:
                    text = response.read().decode("utf-8")
                    parsed = json.loads(text) if text else None
                    return {"ok": 200 <= response.status < 300, "status": response.status, "data": parsed}
            except urllib.error.HTTPError as error:
                text = error.read().decode("utf-8") if hasattr(error, "read") else ""
                try:
                    parsed = json.loads(text) if text else None
                except json.JSONDecodeError:
                    parsed = text
                if error.code == 429:
                    raise ReplyRateLimitError(error.headers.get("retry-after", "not provided")) from error
                return {"ok": False, "status": error.code, "data": parsed}
            except urllib.error.URLError:
                if attempt == 2:
                    raise
                time.sleep(1 + attempt)
        raise ReplyWriteError("Reply.io request failed after retries.")

    def assert_ok(self, response: Dict, label: str) -> None:
        if not response["ok"]:
            raise ReplyWriteError(f"Reply.io {label} failed ({response['status']}): {json.dumps(response['data'])}")

    def assert_sequence_safe(self, sequence_id: str) -> Dict:
        response = self.request("GET", f"/sequences/{sequence_id}")
        self.assert_ok(response, "fetch sequence")
        if response["data"].get("isArchived"):
            raise ReplyWriteError(f"Sequence {sequence_id} is archived.")
        return response["data"]

    def list_sequence_steps(self, sequence_id: str) -> List[Dict]:
        response = self.request("GET", f"/sequences/{sequence_id}/steps")
        self.assert_ok(response, "list sequence steps")
        return response["data"] or []

    def get_sequence_contact(self, sequence_id: str, contact_id: str) -> Dict:
        response = self.request("GET", f"/sequences/{sequence_id}/contacts/{contact_id}")
        self.assert_ok(response, f"get sequence contact {contact_id}")
        return response["data"]

    def list_email_accounts(self) -> List[Dict]:
        items, skip = [], 0
        while True:
            response = self.request("GET", f"/email-accounts?top={PAGE_SIZE}&skip={skip}")
            self.assert_ok(response, "list email accounts")
            items.extend(response["data"].get("items", []))
            if not response["data"].get("hasMore"):
                return items
            skip += PAGE_SIZE

    def set_sequence_status(self, sequence_id: str, contact_ids: List[str], status_in_sequence: str) -> None:
        for ids in chunks(contact_ids, WRITE_CHUNK_SIZE):
            response = self.request(
                "POST",
                f"/sequences/{sequence_id}/contacts/set-status-in-sequence",
                {"contactIds": ids, "statusInSequence": status_in_sequence},
            )
            self.assert_ok(response, f"set {status_in_sequence}")
            failures = response["data"] or {}
            if failures:
                sample = dict(list(failures.items())[:10])
                raise ReplyWriteError(f"Failed to set {status_in_sequence}: {json.dumps(sample)}")

    def update_contact_custom_field(self, contact_id: str, field_id, value: str) -> Dict:
        contact = self.request("GET", f"/contacts/{contact_id}")
        self.assert_ok(contact, f"get contact {contact_id}")
        custom_fields = merge_custom_field_value(contact["data"].get("customFields") or [], field_id, value)
        response = self.request("PATCH", f"/contacts/{contact_id}", {"customFields": custom_fields})
        self.assert_ok(response, f"update custom field for contact {contact_id}")
        return response["data"]

    def move_contacts_to_step(self, sequence_id: str, contact_ids: List[str], step_id: str) -> Dict:
        results = []
        for ids in chunks(contact_ids, WRITE_CHUNK_SIZE):
            remove = self.request("POST", f"/sequences/{sequence_id}/contact-links/bulk-delete", {"contactIds": ids})
            self.assert_ok(remove, "bulk remove contacts from sequence")
            add = self.request(
                "POST",
                f"/sequences/{sequence_id}/contact-links/bulk",
                {"contactIds": ids, "removeFromExisting": False, "startStepId": step_id, "ignoreStepDelay": False},
            )
            self.assert_ok(add, "bulk add contacts to sequence step")
            failures = (add["data"] or {}).get("notProcessed", {})
            if failures:
                sample = dict(list(failures.items())[:10])
                raise ReplyWriteError(f"Failed to move contacts: {json.dumps(sample)}")
            results.append({
                "requested": len(ids),
                "removed": (remove["data"] or {}).get("removed", 0),
                "added": len((add["data"] or {}).get("added", [])),
            })
        return {"chunks": results}
