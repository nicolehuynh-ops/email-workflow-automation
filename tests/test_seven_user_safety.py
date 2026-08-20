import json
import unittest
from pathlib import Path
import tempfile

from outreach.config import load_campaign
from outreach.decisions import build_decisions
from outreach.models import Contact, Signal


ROOT = Path(__file__).resolve().parents[1]


class SevenUserSafetyTests(unittest.TestCase):
    def setUp(self):
        self.campaign, _ = load_campaign(ROOT / "config/campaigns/example-campaign.json")
        self.harbor_one = Contact("one", "one@example.test", "example.test", "step-2", "sender@example.com", company_name="Harbor Test Group")
        self.harbor_two = Contact("two", "two@example.test", "example.test", "step-2", "sender@example.com", company_name="Harbor Test Group")

    def test_high_confidence_out_of_office_is_never_finishable(self):
        signal = Signal(
            "front", "message-ooo", "reply_received", self.harbor_one.email,
            self.harbor_one.company_key, classifier_label="out_of_office", classifier_confidence=0.99,
        )
        decisions = build_decisions(self.campaign, [self.harbor_one], [signal])
        self.assertEqual(1, len(decisions))
        self.assertEqual("hold_for_review", decisions[0]["proposed_action"])
        self.assertEqual("out_of_office requires manual review", decisions[0]["reason"])

    def test_conflicting_out_of_office_and_unsubscribe_are_all_review_only(self):
        signals = [
            Signal("front", "message-ooo", "reply_received", self.harbor_one.email, self.harbor_one.company_key,
                   classifier_label="out_of_office", classifier_confidence=0.99),
            Signal("front", "message-unsubscribe", "reply_received", self.harbor_one.email, self.harbor_one.company_key,
                   classifier_label="unsubscribe", classifier_confidence=0.99),
        ]
        decisions = build_decisions(self.campaign, [self.harbor_one], signals)
        self.assertEqual(2, len(decisions))
        self.assertTrue(all(decision["proposed_action"] == "hold_for_review" for decision in decisions))
        self.assertTrue(all(decision["reason"] == "conflicting classifier signals require manual review" for decision in decisions))

    def test_shared_company_reply_is_exact_contact_only(self):
        signal = Signal(
            "front", "message-one", "reply_received", self.harbor_one.email,
            self.harbor_one.company_key, classifier_label="interested", classifier_confidence=0.99,
        )
        decisions = build_decisions(self.campaign, [self.harbor_one, self.harbor_two], [signal])
        self.assertEqual([self.harbor_one.email], [decision["contact_email"] for decision in decisions])
        self.assertEqual("exact_contact", decisions[0]["suppression_type"])

    def test_unmapped_bounceback_signal_cannot_create_campaign_decision(self):
        signal = Signal(
            "front", "message-bounce", "reply_received", "mailer-daemon@example.test",
            "example.test", classifier_label="unclear", classifier_confidence=0.0,
        )
        self.assertEqual([], build_decisions(self.campaign, [self.harbor_one, self.harbor_two], [signal]))

    def test_seven_user_config_uses_exact_contact_outcomes_only(self):
        raw = json.loads((ROOT / "config/campaigns/Seven-User Classification Test - 2026-08-20/campaign.local.json.example").read_text())
        self.assertEqual({"reply_received", "meeting_booked"}, set(raw["suppressionPolicy"]["outcomeScopes"]))
        self.assertEqual([], raw["suppressionPolicy"]["domainSuppression"]["domains"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "seven-user-classification-test.local.json"
            path.write_text(json.dumps(raw))
            campaign, _ = load_campaign(path)
        self.assertEqual("seven-user-classification-test", campaign.slug)
        self.assertEqual((), campaign.domain_suppression_domains)
