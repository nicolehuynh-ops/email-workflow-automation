import json
import tempfile
import unittest
from pathlib import Path

from outreach.adapters import FrontReader, FrontScopeError, parse_timestamp, timestamp_is_on_or_after
from outreach.config import load_campaign
from outreach.models import Contact


ROOT = Path(__file__).resolve().parents[1]


class FakeFrontClient:
    def __init__(self):
        self.paths = []

    def get(self, path, params=None):
        self.paths.append(path)
        payloads = {
            "/inboxes/inb_allowed/conversations": {"_results": [{"id": "cnv_allowed"}, {"id": "cnv_wrong"}, {"id": "cnv_unproven"}], "_pagination": {}},
            "/conversations/cnv_allowed/inboxes": {"_results": [{"id": "inb_allowed"}], "_pagination": {}},
            "/conversations/cnv_wrong/inboxes": {"_results": [{"id": "inb_other"}], "_pagination": {}},
            "/conversations/cnv_unproven/inboxes": {"_results": [], "_pagination": {}},
            "/conversations/cnv_allowed/messages": {"_results": [
                {"id": "msg-1", "is_inbound": True, "author": {"email": "alex@example.com"}, "created_at": "2026-08-20T01:00:00Z", "text": "Test reply"},
                {"id": "msg-2", "is_inbound": True, "author": {"email": "forwarder@other.example"}, "created_at": "2026-08-20T01:01:00Z", "text": "Forwarded reply"},
                {"id": "msg-3", "is_inbound": True, "author": {"email": "bounce@other.example"}, "created_at": 1787184001, "text": "Numeric timestamp reply"},
                {"id": "msg-old", "is_inbound": True, "author": {"email": "old@other.example"}, "created_at": 1787183999, "text": "Before reporting window"},
            ], "_pagination": {}},
        }
        if path not in payloads:
            raise AssertionError(f"Unexpected Front request: {path}")
        return payloads[path]


class FrontInboxScopeTests(unittest.TestCase):
    def test_configuration_requires_one_confirmed_inbox(self):
        raw = json.loads((ROOT / "config/campaigns/example-campaign.json").read_text())
        raw["front"] = {"inboxIds": ["one", "two"], "confirmedInboxId": "one", "confirmation": "confirmed"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "campaign.json"
            path.write_text(json.dumps(raw))
            with self.assertRaisesRegex(ValueError, "exactly one"):
                load_campaign(path)

    def test_reader_fetches_messages_only_after_inbox_membership_proof(self):
        campaign, _ = load_campaign(ROOT / "config/campaigns/example-campaign.json")
        campaign = campaign.__class__(**{
            **campaign.__dict__,
            "front_inbox_ids": ["inb_allowed"],
            "front_since": "2026-08-20T00:00:00Z",
        })
        reader = FrontReader("token")
        fake = FakeFrontClient()
        reader.client = fake
        signals = reader.signals(campaign, [Contact("contact-1", "alex@example.com", "example.com", "step-2", "sender@example.com")])
        self.assertEqual(3, len(signals))
        self.assertTrue(all(signal.inbox_id == "inb_allowed" for signal in signals))
        self.assertTrue(signals[0].matched_reply_contact)
        self.assertFalse(signals[1].matched_reply_contact)
        self.assertEqual("forwarder@other.example", signals[1].email)
        self.assertTrue(all(signal.conversation_id == "cnv_allowed" for signal in signals))
        self.assertEqual("2026-08-20T00:00:01Z", signals[2].occurred_at)
        self.assertIn("/conversations/cnv_allowed/messages", fake.paths)
        self.assertNotIn("/conversations/cnv_wrong/messages", fake.paths)
        self.assertNotIn("/conversations/cnv_unproven/messages", fake.paths)

    def test_front_timestamps_accept_unix_and_iso_values(self):
        self.assertEqual("2026-08-20T00:00:01+00:00", parse_timestamp(1787184001).isoformat())
        self.assertTrue(timestamp_is_on_or_after(1787184001, "2026-08-20T00:00:00Z"))
        self.assertFalse(timestamp_is_on_or_after(1787183999, "2026-08-20T00:00:00Z"))
        self.assertFalse(timestamp_is_on_or_after("invalid", "2026-08-20T00:00:00Z"))

    def test_future_front_mutations_fail_closed_outside_confirmed_inbox(self):
        reader = FrontReader("token")
        reader.client = FakeFrontClient()
        with self.assertRaises(FrontScopeError):
            reader.require_conversation_in_expected_inbox("cnv_wrong", "inb_allowed")
