import unittest
from pathlib import Path

from outreach.config import load_campaign
from outreach.preflight import live_acceptance_preflight


ROOT = Path(__file__).resolve().parents[1]


class PreflightTests(unittest.TestCase):
    def test_fixture_campaign_and_incomplete_environment_are_not_ready(self):
        campaign, _ = load_campaign(ROOT / "config/campaigns/example-campaign.json")
        result = live_acceptance_preflight(campaign, {"REPLY_IO_API_KEY": "set", "AI_GATEWAY_MODEL": "model"})
        self.assertFalse(result["ready"])
        self.assertIn("front.inboxIds", result["missing"])
        self.assertIn("FRONT_API_TOKEN", result["missing"])

    def test_complete_non_production_campaign_is_ready(self):
        campaign, _ = load_campaign(ROOT / "config/campaigns/example-campaign.json")
        campaign = campaign.__class__(**{**campaign.__dict__, "campaign_id": "test-sequence-123", "front_inbox_ids": ["inbox-1"], "calendly_report_start": "2026-01-01T00:00:00Z", "calendly_event_type_uris": ["event-1"]})
        environment = {key: "set" for key in ("REPLY_IO_API_KEY", "FRONT_API_TOKEN", "CALENDLY_ACCESS_TOKEN", "CALENDLY_ORGANIZATION_URI", "AI_GATEWAY_MODEL", "CF_ACCESS_TOKEN")}
        result = live_acceptance_preflight(campaign, environment, lambda _: {"ready": True, "model": "model", "available_models": 1})
        self.assertTrue(result["ready"])
        self.assertTrue(result["gateway"]["ready"])
