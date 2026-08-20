import json
import tempfile
import unittest
from pathlib import Path

from outreach.config import load_campaign
from outreach.database import Database
from outreach.decisions import build_decisions
from outreach.models import Contact, Signal
from outreach.snapshot import load_snapshot


ROOT = Path(__file__).resolve().parents[1]


class LocalWorkflowTests(unittest.TestCase):
    def test_exact_contact_suppression_only_targets_matching_email(self):
        campaign, _ = load_campaign(ROOT / "config/campaigns/example-campaign.json")
        contacts, signals = load_snapshot(ROOT / "fixtures/example_snapshot.json")
        decisions = build_decisions(campaign, contacts, signals)
        self.assertEqual(1, len(decisions))
        self.assertEqual("alex@northstar.example", decisions[0]["contact_email"])
        self.assertEqual("exact_contact", decisions[0]["suppression_type"])

    def test_domain_company_suppression_targets_all_company_contacts(self):
        campaign_path = ROOT / "config/campaigns/example-campaign.json"
        raw = json.loads(campaign_path.read_text())
        raw["suppressionPolicy"]["outcomeScopes"] = {"event_rsvp": "domain_company"}
        with tempfile.TemporaryDirectory() as directory:
            temp_config = Path(directory) / "campaign.json"
            temp_config.write_text(json.dumps(raw))
            campaign, _ = load_campaign(temp_config)
        contacts, _ = load_snapshot(ROOT / "fixtures/example_snapshot.json")
        decisions = build_decisions(campaign, contacts, [
            Signal("event", "rsvp-1", "event_rsvp", "alex@northstar.example", "northstar.example")
        ])
        self.assertEqual({"alex@northstar.example", "sam@northstar.example"}, {item["contact_email"] for item in decisions})

    def test_approval_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "outreach.db", ROOT / "migrations")
            run_id = db.create_run("example-campaign", "digest", "review")
            db.persist_decisions(run_id, [{
                "id": "decision-1", "contact_email": "alex@example.com", "suppression_type": "exact_contact",
                "match_key": "alex@example.com", "reason": "meeting_booked", "proposed_action": "finish",
                "confidence": 1.0, "idempotency_key": "key-1", "decision_json": "{}",
            }], "pending_review")
            decision_id = db.list_decisions("example-campaign")[0]["id"]
            db.decide(decision_id, "Nicole", "approved", "Meeting confirmed")
            self.assertEqual("approved", db.approved_for_campaign("example-campaign")[0]["status"])
            approval = db.connection.execute("SELECT outcome FROM approvals WHERE decision_id = ?", (decision_id,)).fetchone()
            self.assertEqual("approved", approval["outcome"])
            db.close()

    def test_reply_state_domain_block_is_created_before_signal_classification(self):
        campaign, _ = load_campaign(ROOT / "config/campaigns/example-campaign.json")
        contacts = [
            Contact("contact-1", "alex@northstar.example", "northstar.example", "step-2", "sender@example.com", replied=True),
            Contact("contact-2", "sam@northstar.example", "northstar.example", "step-2", "sender@example.com"),
        ]
        decisions = build_decisions(campaign, contacts, [])
        self.assertEqual({"alex@northstar.example", "sam@northstar.example"}, {item["contact_email"] for item in decisions})
        self.assertTrue(all(item["suppression_type"] == "domain_company" for item in decisions))
