import json
import tempfile
import unittest
from pathlib import Path

from outreach.config import load_campaign
from outreach.database import Database, LockHeldError
from outreach.reply.apply import apply_approved_decisions


ROOT = Path(__file__).resolve().parents[1]


class FakeReply:
    def __init__(self, step="step-2", account_id="account-1"):
        self.step = step
        self.account_id = account_id
        self.finished = []

    def list_email_accounts(self):
        return [{"id": "account-1", "email": "sender@example.com"}]

    def get_sequence_contact(self, _sequence_id, _contact_id):
        return {"currentStep": {"stepId": self.step}, "emailAccountId": self.account_id}

    def set_sequence_status(self, _sequence_id, contact_ids, status):
        self.finished.append((contact_ids, status))


class ApplyWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.directory.name) / "outreach.db", ROOT / "migrations")
        self.campaign, self.digest = load_campaign(ROOT / "config/campaigns/example-campaign.json")

    def tearDown(self):
        self.db.close()
        self.directory.cleanup()

    def _approved_decision(self):
        run_id = self.db.create_run(self.campaign.slug, self.digest, "review")
        decision_json = json.dumps({
            "contact": {"reply_contact_id": "contact-1", "sequence_step_id": "step-2"},
            "impacted_contact_ids": ["contact-1"],
            "reviewed_sequence_step_id": "step-2",
        })
        self.db.persist_decisions(run_id, [{
            "id": "decision-1", "contact_email": "alex@example.com", "suppression_type": "exact_contact",
            "match_key": "alex@example.com", "reason": "meeting_booked", "proposed_action": "finish",
            "confidence": 1.0, "idempotency_key": "key-1", "decision_json": decision_json,
        }], "pending_review")
        decision_id = self.db.list_decisions(self.campaign.slug)[0]["id"]
        self.db.decide(decision_id, "Nicole", "approve", "verified")
        return decision_id

    def test_apply_is_exactly_once(self):
        decision_id = self._approved_decision()
        reply = FakeReply()
        first = apply_approved_decisions(self.db, self.campaign, reply, self.campaign.slug, self.digest)
        second = apply_approved_decisions(self.db, self.campaign, reply, self.campaign.slug, self.digest)
        self.assertEqual("applied", first[0].status)
        self.assertEqual("skipped_already_applied", second[0].status)
        self.assertEqual([(["contact-1"], "finished")], reply.finished)
        self.assertEqual("succeeded", self.db.reply_action_status(decision_id))

    def test_stale_step_fails_without_vendor_write(self):
        self._approved_decision()
        reply = FakeReply(step="step-3")
        results = apply_approved_decisions(self.db, self.campaign, reply, self.campaign.slug, self.digest)
        self.assertEqual("failed", results[0].status)
        self.assertIn("sequence step changed", results[0].error)
        self.assertEqual([], reply.finished)

    def test_lock_prevents_overlapping_apply(self):
        self._approved_decision()
        token = self.db.acquire_apply_lock(self.campaign.slug)
        try:
            with self.assertRaises(LockHeldError):
                apply_approved_decisions(self.db, self.campaign, FakeReply(), self.campaign.slug, self.digest)
        finally:
            self.db.release_apply_lock(self.campaign.slug, token)
