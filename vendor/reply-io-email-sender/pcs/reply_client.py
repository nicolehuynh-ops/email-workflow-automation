import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from pcs import config


class ReplyClient:
    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = "https://api.reply.io/v3"

    def request(self, method, route, body=None):
        data = None
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(
            f"{self.base_url}{route}",
            data=data,
            headers=headers,
            method=method,
        )

        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    text = response.read().decode("utf-8")
                    parsed = json.loads(text) if text else None
                    return {"ok": 200 <= response.status < 300, "status": response.status, "data": parsed}
            except urllib.error.HTTPError as exc:
                text = exc.read().decode("utf-8")
                try:
                    parsed = json.loads(text) if text else None
                except json.JSONDecodeError:
                    parsed = text
                if exc.code == 429:
                    retry_after = exc.headers.get("retry-after", "not provided")
                    raise RuntimeError(f"Reply.io rate limit hit. Retry-After: {retry_after}")
                return {"ok": False, "status": exc.code, "data": parsed}
            except urllib.error.URLError:
                if attempt == 2:
                    raise
                time.sleep(1 + attempt)

    def assert_sequence_safe(self, sequence_id):
        response = self.request("GET", f"/sequences/{sequence_id}")
        assert_ok(response, "fetch sequence")
        if response["data"].get("isArchived"):
            raise RuntimeError(f"Sequence {sequence_id} is archived.")
        return response["data"]

    def list_sequence_steps(self, sequence_id):
        response = self.request("GET", f"/sequences/{sequence_id}/steps")
        assert_ok(response, "list sequence steps")
        return response["data"] or []

    def list_email_accounts(self):
        items = []
        skip = 0
        while True:
            response = self.request("GET", f"/email-accounts?top={config.PAGE_SIZE}&skip={skip}")
            assert_ok(response, "list email accounts")
            items.extend(response["data"].get("items", []))
            if not response["data"].get("hasMore"):
                break
            skip += config.PAGE_SIZE
        return items

    def get_email_account_by_email(self, email):
        target = email.strip().lower()
        matches = [account for account in self.list_email_accounts() if (account.get("email") or "").lower() == target]
        if not matches:
            raise RuntimeError(f"No Reply.io email account found for {email}.")
        connected = [account for account in matches if account.get("connectionStatus") == "connected"]
        return connected[0] if connected else matches[0]

    def list_sequence_contact_states(self, sequence_id):
        items = []
        skip = 0
        while True:
            response = self.request(
                "GET",
                f"/sequences/{sequence_id}/contacts?top={config.PAGE_SIZE}&skip={skip}",
            )
            assert_ok(response, "list sequence contacts")
            items.extend(normalize_sequence_contacts(response["data"].get("items", [])))
            if not response["data"].get("hasMore"):
                break
            skip += config.PAGE_SIZE
        return items

    def get_sequence_contact(self, sequence_id, contact_id):
        response = self.request("GET", f"/sequences/{sequence_id}/contacts/{contact_id}")
        assert_ok(response, f"get sequence contact {contact_id}")
        return response["data"]

    def get_sequence_contacts_by_ids(self, sequence_id, contact_ids):
        with ThreadPoolExecutor(max_workers=4) as executor:
            return list(
                executor.map(
                    lambda contact_id: self.get_sequence_contact(sequence_id, contact_id),
                    contact_ids,
                )
            )

    def get_contact(self, contact_id):
        response = self.request("GET", f"/contacts/{contact_id}")
        assert_ok(response, f"get contact {contact_id}")
        return response["data"]

    def get_contact_by_email(self, email):
        encoded = urllib.parse.quote(email)
        response = self.request("GET", f"/contacts?email={encoded}")
        assert_ok(response, f"get contact by email {email}")
        for contact in response["data"].get("items", []):
            if (contact.get("email") or "").lower() == email.lower():
                return contact
        raise RuntimeError(f"No Reply.io contact found for email {email}.")

    def list_contact_sequences(self, contact_id):
        response = self.request("GET", f"/contacts/{contact_id}/sequences")
        assert_ok(response, f"list sequences for contact {contact_id}")
        return response["data"] or []

    def list_contact_activities(self, contact_id, top=100):
        """Return the complete activity history available for one contact."""
        items = []
        skip = 0
        while True:
            response = self.request("GET", f"/contacts/{contact_id}/activities?top={top}&skip={skip}")
            assert_ok(response, f"list activities for contact {contact_id}")
            payload = response["data"] or {}
            page = payload.get("items", payload if isinstance(payload, list) else [])
            items.extend(page)
            if not isinstance(payload, dict) or not payload.get("hasMore") or not page:
                return items
            skip += top

    def list_reporting_emails(self, filters, top=100):
        """Return every paginated record from Reply.io's email reporting endpoint.

        Reporting records are one record per sequence email rather than one record
        per contact.  They provide the supported delivery/open/click/reply flags
        without issuing a separate activity-history request for every contact.
        """
        items = []
        skip = 0
        while True:
            response = self.request(
                "POST",
                f"/reporting/emails?top={top}&skip={skip}",
                {"filters": filters},
            )
            assert_ok(response, "list reporting email activity")
            payload = response["data"] or {}
            page = payload.get("items", [])
            items.extend(page)
            if not payload.get("hasMore") or not page:
                return items
            skip += top

    def get_contact_activities_by_ids(self, contact_ids):
        with ThreadPoolExecutor(max_workers=4) as executor:
            return list(executor.map(self.list_contact_activities, contact_ids))

    def get_contacts_by_ids(self, contact_ids):
        # Sequence snapshots can contain hundreds of contacts. A small bounded
        # pool keeps the read-only snapshot phase practical without flooding Reply.io.
        with ThreadPoolExecutor(max_workers=4) as executor:
            return list(executor.map(self.get_contact, contact_ids))

    def set_sequence_status(self, sequence_id, contact_ids, status_in_sequence):
        results = []
        for ids in chunks(contact_ids, config.WRITE_CHUNK_SIZE):
            response = self.request(
                "POST",
                f"/sequences/{sequence_id}/contacts/set-status-in-sequence",
                {"contactIds": ids, "statusInSequence": status_in_sequence},
            )
            assert_ok(response, f"set {status_in_sequence}")
            failures = response["data"] or {}
            if failures:
                sample = dict(list(failures.items())[:10])
                raise RuntimeError(f"Failed to set {status_in_sequence}: {json.dumps(sample)}")
            results.append(failures)
        return results

    def update_contact_custom_field(self, contact_ids, field_id, value):
        updated = []
        for contact_id in contact_ids:
            contact = self.get_contact(contact_id)
            custom_fields = merge_custom_field_value(contact.get("customFields") or [], field_id, value)
            response = self.request(
                "PATCH",
                f"/contacts/{contact_id}",
                {"customFields": custom_fields},
            )
            assert_ok(response, f"update custom field for contact {contact_id}")
            updated.append(response["data"])
        return updated

    def move_contacts_to_step(self, sequence_id, contact_ids, step_id):
        results = []
        for ids in chunks(contact_ids, config.WRITE_CHUNK_SIZE):
            remove = self.request(
                "POST",
                f"/sequences/{sequence_id}/contact-links/bulk-delete",
                {"contactIds": ids},
            )
            assert_ok(remove, "bulk remove contacts from sequence")

            add = self.request(
                "POST",
                f"/sequences/{sequence_id}/contact-links/bulk",
                {
                    "contactIds": ids,
                    "removeFromExisting": False,
                    "startStepId": step_id,
                    "ignoreStepDelay": False,
                },
            )
            assert_ok(add, "bulk add contacts to sequence step")
            failures = (add["data"] or {}).get("notProcessed", {})
            if failures:
                sample = dict(list(failures.items())[:10])
                raise RuntimeError(f"Failed to move contacts: {json.dumps(sample)}")

            results.append(
                {
                    "requested": len(ids),
                    "removed": (remove["data"] or {}).get("removed", 0),
                    "added": len((add["data"] or {}).get("added", [])),
                }
            )
        return results


def assert_ok(response, label):
    if not response["ok"]:
        raise RuntimeError(f"Reply.io {label} failed ({response['status']}): {json.dumps(response['data'])}")


def chunks(items, size):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def merge_custom_field_value(existing_fields, field_id, value):
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


def normalize_sequence_contacts(items):
    normalized = []
    for item in items:
        current_step = item.get("currentStep") or {}
        email_disposition = item.get("emailDisposition") or {}
        step_number = current_step.get("displayName") or ""
        normalized.append(
            {
                "contactId": item["contactId"],
                "email": item.get("email") or "",
                "firstName": item.get("firstName") or "",
                "lastName": item.get("lastName") or "",
                "company": item.get("company") or "",
                "title": item.get("title") or "",
                "currentStep": {
                    "stepId": current_step.get("stepId"),
                    "displayStepNumber": step_number,
                    "stepNumber": step_number,
                },
                "sequenceAddedAt": item.get("addingDate") or "",
                "status": {
                    "status": display_status(item.get("statusInSequence")),
                    "replied": bool(email_disposition.get("isReplied")),
                    "bounced": bool(email_disposition.get("isBounced")),
                    "opened": False,
                    "clicked": False,
                },
            }
        )
    return normalized


def display_status(status):
    if not status:
        return ""
    key = "".join(char for char in str(status).lower() if char.isalnum())
    if key == "outofoffice":
        return "OutOfOffice"
    return str(status).replace("_", " ").title().replace(" ", "")


def settle():
    time.sleep(config.SETTLE_SECONDS)
