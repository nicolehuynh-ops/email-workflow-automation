import unittest

from outreach.config import load_campaign
from outreach.reply.issuer_blocking import ReplyContactState, find_domain_company_blocks
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def row(email, **overrides):
    values = dict(reply_contact_id=email, email=email, match_key="northstar.example", sequence_status="active", current_step_id="step-2", replied=False, bounced=False, opted_out=False, auto_reply=False)
    values.update(overrides)
    return ReplyContactState(**values)


class IssuerBlockingTests(unittest.TestCase):
    def setUp(self):
        self.campaign, _ = load_campaign(ROOT / "config/campaigns/example-campaign.json")

    def test_bounce_or_opt_out_alone_does_not_block(self):
        result = find_domain_company_blocks([row("a@example.com", bounced=True), row("b@example.com", opted_out=True)], self.campaign)
        self.assertEqual(set(), result["blocked_match_keys"])

    def test_manual_ooo_finished_row_is_not_a_blocker(self):
        campaign = self.campaign.__class__(**{**self.campaign.__dict__, "manual_ooo_emails": ("a@example.com",)})
        result = find_domain_company_blocks([row("a@example.com", sequence_status="finished"), row("b@example.com")], campaign)
        self.assertEqual(set(), result["blocked_match_keys"])

    def test_reply_wins_over_opt_out_and_blocks_related_active_contact(self):
        result = find_domain_company_blocks([row("a@example.com", replied=True, opted_out=True), row("b@example.com")], self.campaign)
        self.assertEqual({"northstar.example"}, result["blocked_match_keys"])
        # The replied/opted-out contact blocks the group, but an opted-out row
        # itself is already excluded from the finish action.
        self.assertEqual({"b@example.com"}, {item.email for item in result["related_rows_to_finish"]})
