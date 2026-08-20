import json
import tempfile
import unittest
from pathlib import Path

from outreach.config import load_campaign
from outreach.decisions import build_decisions
from outreach.models import Contact, Signal


ROOT = Path(__file__).resolve().parents[1]


class ConfigReplyStepTests(unittest.TestCase):
    def test_alias_is_parsed_and_matches_a_decision_sender(self):
        raw = json.loads((ROOT / "config/campaigns/example-campaign.json").read_text())
        raw["sequenceSteps"][2]["senderAliases"] = ["alias@example.com"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "campaign.json"
            path.write_text(json.dumps(raw))
            campaign, _ = load_campaign(path)
        contact = Contact("contact-1", "alex@northstar.example", "northstar.example", "step-3", "alias@example.com")
        decision = build_decisions(campaign, [contact], [Signal("event", "1", "meeting_booked", contact.email, contact.company_key)])[0]
        self.assertEqual("finish", decision["proposed_action"])

    def test_domain_scope_requires_domains_and_initial_final_are_required(self):
        raw = json.loads((ROOT / "config/campaigns/example-campaign.json").read_text())
        raw["suppressionPolicy"].pop("domainSuppression")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "campaign.json"
            path.write_text(json.dumps(raw))
            with self.assertRaisesRegex(ValueError, "domainSuppression"):
                load_campaign(path)
        raw = json.loads((ROOT / "config/campaigns/example-campaign.json").read_text())
        raw["sequenceSteps"] = raw["sequenceSteps"][1:-1]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "campaign.json"
            path.write_text(json.dumps(raw))
            with self.assertRaisesRegex(ValueError, "exactly one 'initial'"):
                load_campaign(path)

    def test_hold_steps_are_non_sending(self):
        raw = json.loads((ROOT / "config/campaigns/example-campaign.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "campaign.json"
            path.write_text(json.dumps(raw))
            campaign, _ = load_campaign(path)
        self.assertEqual("", campaign.expected_senders["step-2"])
        self.assertEqual("", campaign.expected_senders["step-4"])
        raw["sequenceSteps"][1]["expectedSender"] = "should-not-exist@example.com"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "campaign.json"
            path.write_text(json.dumps(raw))
            with self.assertRaisesRegex(ValueError, "non-sending"):
                load_campaign(path)

    def test_gateway_input_scope_accepts_only_exact_bounded_allowlists(self):
        raw = json.loads((ROOT / "config/campaigns/example-campaign.json").read_text())
        raw["classification"]["gatewayInputScope"] = {
            "allowedUnmappedEmails": ["Forwarder@Example.com"],
            "allowedFrontConversationIds": ["cnv_exact"],
            "allowedFrontMessageIds": ["msg_exact"],
            "maxMessagesPerRun": 25,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "campaign.json"
            path.write_text(json.dumps(raw))
            campaign, _ = load_campaign(path)
        self.assertEqual(("forwarder@example.com",), campaign.gateway_allowed_unmapped_emails)
        self.assertEqual(("cnv_exact",), campaign.gateway_allowed_front_conversation_ids)
        self.assertEqual(("msg_exact",), campaign.gateway_allowed_front_message_ids)
        self.assertEqual(25, campaign.gateway_max_messages_per_run)

        raw["classification"]["gatewayInputScope"]["allowedFrontMessageIds"] = ["msg_*"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "campaign.json"
            path.write_text(json.dumps(raw))
            with self.assertRaisesRegex(ValueError, "wildcards are forbidden"):
                load_campaign(path)
