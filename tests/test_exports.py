import json
import os
import tempfile
import unittest
from pathlib import Path

from outreach.analytics import materialize_analytics_inputs
from outreach.config import load_campaign
from outreach.database import Database
from outreach.models import Contact, Signal
from outreach.suppression_export import write_suppression_lists


ROOT = Path(__file__).resolve().parents[1]
NODE = "/Users/nicole/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
MODULES = "/Users/nicole/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.db = Database(self.root / "outreach.db", ROOT / "migrations")
        self.campaign, _ = load_campaign(ROOT / "config/campaigns/example-campaign.json")
        self.run_id = self.db.create_run(self.campaign.slug, "digest", "review")
        self.db.persist_inputs(self.run_id, [Contact("contact-1", "alex@northstar.example", "northstar.example", "step-2", "sender@example.com")], [Signal("calendly", "event-1", "meeting_booked", "alex@northstar.example", "northstar.example")])
        self.db.complete_run(self.run_id, "completed", {})

    def tearDown(self):
        self.db.close()
        self.directory.cleanup()

    def test_suppression_export_is_read_only_and_deduplicates_domains(self):
        self.db.persist_decisions(self.run_id, [
            {"id": "one", "contact_email": "alex@northstar.example", "suppression_type": "exact_contact", "match_key": "alex@northstar.example", "reason": "meeting_booked", "proposed_action": "finish", "confidence": 1, "idempotency_key": "one", "decision_json": "{}"},
            {"id": "two", "contact_email": "sam@northstar.example", "suppression_type": "domain_company", "match_key": "northstar.example", "reason": "event_rsvp", "proposed_action": "finish", "confidence": 1, "idempotency_key": "two", "decision_json": "{}"},
        ], "pending_review")
        for row in self.db.list_decisions(self.campaign.slug):
            self.db.decide(row["id"], "Nicole", "approve", "verified")
        result = write_suppression_lists(self.db, self.campaign.slug, self.root / "artifacts")
        self.assertEqual(1, result["exact_contact_count"])
        self.assertIn("alex@northstar.example", Path(result["contacts"]).read_text())
        self.assertIn("northstar.example", Path(result["domains"]).read_text())

    def test_analytics_materialization_writes_reporting_contract_inputs(self):
        result = materialize_analytics_inputs(self.db, self.campaign, self.root / "campaigns", {**os.environ, "ARTIFACT_TOOL_NODE_BIN": NODE, "ARTIFACT_TOOL_NODE_MODULES": MODULES})
        report = Path(result["contact_report"]).read_text()
        self.assertIn("Contact Id,PCS Issuer ID,Contact email,Sequence", report)
        self.assertIn("alex@northstar.example", report)
        workbook = Path(result["positive_response_workbook"])
        self.assertTrue(workbook.exists())
        self.assertEqual(b"PK", workbook.read_bytes()[:2])
        self.assertEqual(1, result["positive_response_count"])

    def test_gateway_audit_metadata_is_persisted_without_message_content(self):
        self.db.persist_inputs(self.run_id, [], [Signal(
            "front", "message-1", "reply_received", "alex@northstar.example", "northstar.example",
            content="sensitive reply body",
            gateway_batch_id="batch-1",
            gateway_item_id="item-000000",
            gateway_batch_size=4,
            gateway_response_id="response-1",
            gateway_model="openai/gpt-5.6-luna",
            gateway_attempts=2,
            gateway_status="succeeded",
            gateway_scope_basis="matched_reply_contact",
        )])
        row = self.db.connection.execute(
            "SELECT payload_json FROM source_evidence WHERE source_id = ?", ("message-1",)
        ).fetchone()
        payload = json.loads(row["payload_json"])
        self.assertNotIn("content", payload)
        self.assertNotIn("sensitive reply body", row["payload_json"])
        self.assertEqual("batch-1", payload["gateway_batch_id"])
        self.assertEqual("response-1", payload["gateway_response_id"])
        self.assertEqual(2, payload["gateway_attempts"])
        self.assertEqual("matched_reply_contact", payload["gateway_scope_basis"])
