import json
import sqlite3
import uuid
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from outreach.models import Contact, Signal


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LockHeldError(RuntimeError):
    pass


class Database:
    def __init__(self, path: Path, migrations_dir: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._migrate(migrations_dir)

    def close(self) -> None:
        self.connection.close()

    def _migrate(self, migrations_dir: Path) -> None:
        self.connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        applied = {row[0] for row in self.connection.execute("SELECT version FROM schema_migrations")}
        for migration in sorted(migrations_dir.glob("*.sql")):
            if migration.name in applied:
                continue
            self.connection.executescript(migration.read_text(encoding="utf-8"))
            self.connection.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)", (migration.name, now()))
        self.connection.commit()

    def create_run(self, campaign_slug: str, configuration_digest: str, mode: str) -> str:
        run_id = str(uuid.uuid4())
        self.connection.execute(
            "INSERT INTO campaign_runs (id, campaign_slug, configuration_digest, mode, status, started_at) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, campaign_slug, configuration_digest, mode, "running", now()),
        )
        self.connection.commit()
        return run_id

    def persist_inputs(self, run_id: str, contacts: List[Contact], signals: List[Signal]) -> None:
        for contact in contacts:
            self.connection.execute(
                "INSERT INTO contacts (run_id, reply_contact_id, email, company_key, sequence_step_id, sender_email, source_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, contact.reply_contact_id, contact.email, contact.company_key, contact.sequence_step_id, contact.sender_email, json.dumps(contact.__dict__, sort_keys=True)),
            )
        for signal in signals:
            # A provider message ID is retained for traceability; do not copy full
            # email text into SQLite or reports.
            safe_signal = {key: value for key, value in signal.__dict__.items() if key != "content"}
            self.connection.execute(
                "INSERT INTO source_evidence (run_id, contact_email, source_type, source_id, payload_json) VALUES (?, ?, ?, ?, ?)",
                (run_id, signal.email, signal.source_type, signal.source_id, json.dumps(safe_signal, sort_keys=True)),
            )
        self.connection.commit()

    def persist_decisions(self, run_id: str, decisions: List[Dict], status: str) -> None:
        for decision in decisions:
            # Decisions are immutable records for one run. The embedded idempotency key
            # remains the stable action fingerprint; the database key is run-specific.
            stored_id = hashlib.sha256(f"{run_id}:{decision['id']}".encode()).hexdigest()[:24]
            stored_key = hashlib.sha256(f"{run_id}:{decision['idempotency_key']}".encode()).hexdigest()
            self.connection.execute(
                "INSERT INTO suppression_decisions (id, run_id, contact_email, suppression_type, match_key, reason, proposed_action, confidence, status, idempotency_key, decision_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (stored_id, run_id, decision["contact_email"], decision["suppression_type"], decision["match_key"], decision["reason"], decision["proposed_action"], decision["confidence"], status, stored_key, decision["decision_json"]),
            )
        self.connection.commit()

    def complete_run(self, run_id: str, status: str, summary: Dict) -> None:
        self.connection.execute(
            "UPDATE campaign_runs SET status = ?, completed_at = ?, summary_json = ? WHERE id = ?",
            (status, now(), json.dumps(summary, sort_keys=True), run_id),
        )
        self.connection.commit()

    def list_decisions(self, campaign_slug: str) -> List[sqlite3.Row]:
        return list(self.connection.execute(
            "SELECT d.id, d.contact_email, d.suppression_type, d.match_key, d.reason, d.proposed_action, d.status, r.id AS run_id "
            "FROM suppression_decisions d JOIN campaign_runs r ON r.id = d.run_id WHERE r.campaign_slug = ? ORDER BY r.started_at DESC, d.contact_email",
            (campaign_slug,),
        ))

    def decide(self, decision_id: str, reviewer: str, outcome: str, note: str) -> None:
        row = self.connection.execute("SELECT status FROM suppression_decisions WHERE id = ?", (decision_id,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown decision: {decision_id}")
        if row["status"] != "pending_review":
            raise ValueError(f"Decision {decision_id} is not pending review.")
        if outcome not in {"approve", "approved", "reject", "rejected"}:
            raise ValueError(f"Unsupported review outcome: {outcome}")
        new_status = "approved" if outcome in {"approve", "approved"} else "rejected"
        self.connection.execute("UPDATE suppression_decisions SET status = ? WHERE id = ?", (new_status, decision_id))
        self.connection.execute("INSERT INTO approvals (decision_id, reviewer, outcome, note, created_at) VALUES (?, ?, ?, ?, ?)", (decision_id, reviewer, new_status, note, now()))
        self.connection.commit()

    def approved_for_campaign(self, campaign_slug: str) -> List[sqlite3.Row]:
        return list(self.connection.execute(
            "SELECT d.* FROM suppression_decisions d JOIN campaign_runs r ON r.id = d.run_id "
            "WHERE r.campaign_slug = ? AND d.status = 'approved' ORDER BY r.started_at, d.contact_email",
            (campaign_slug,),
        ))

    def apply_candidates_for_campaign(self, campaign_slug: str) -> List[sqlite3.Row]:
        """Approved rows plus already-applied rows, which are retained to
        prove a repeat invocation will not issue another vendor write."""
        return list(self.connection.execute(
            "SELECT d.* FROM suppression_decisions d JOIN campaign_runs r ON r.id = d.run_id "
            "WHERE r.campaign_slug = ? AND d.status IN ('approved', 'applied') ORDER BY r.started_at, d.contact_email",
            (campaign_slug,),
        ))

    def acquire_apply_lock(self, campaign_slug: str, ttl_seconds: int = 900) -> str:
        current = self.connection.execute(
            "SELECT token, expires_at FROM apply_locks WHERE campaign_slug = ?", (campaign_slug,)
        ).fetchone()
        if current is not None and current["expires_at"] > now():
            raise LockHeldError(f"An apply is already in progress for campaign '{campaign_slug}'.")
        token = str(uuid.uuid4())
        expires_at = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() + ttl_seconds, tz=timezone.utc
        ).isoformat()
        self.connection.execute(
            "INSERT INTO apply_locks (campaign_slug, token, acquired_at, expires_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(campaign_slug) DO UPDATE SET token = excluded.token, acquired_at = excluded.acquired_at, expires_at = excluded.expires_at",
            (campaign_slug, token, now(), expires_at),
        )
        self.connection.commit()
        return token

    def release_apply_lock(self, campaign_slug: str, token: str) -> None:
        self.connection.execute(
            "DELETE FROM apply_locks WHERE campaign_slug = ? AND token = ?", (campaign_slug, token)
        )
        self.connection.commit()

    def reply_action_status(self, decision_id: str) -> Optional[str]:
        row = self.connection.execute(
            "SELECT status FROM reply_actions WHERE decision_id = ?", (decision_id,)
        ).fetchone()
        return row["status"] if row else None

    def record_reply_action_requested(self, decision_id: str, request_json: str) -> None:
        self.connection.execute(
            "INSERT INTO reply_actions (decision_id, requested_at, status, request_json) VALUES (?, ?, 'requested', ?) "
            "ON CONFLICT(decision_id) DO UPDATE SET requested_at = excluded.requested_at, status = 'requested', request_json = excluded.request_json",
            (decision_id, now(), request_json),
        )
        self.connection.commit()

    def record_reply_action_result(self, decision_id: str, status: str, response_json: str) -> None:
        self.connection.execute(
            "UPDATE reply_actions SET status = ?, response_json = ? WHERE decision_id = ?",
            (status, response_json, decision_id),
        )
        self.connection.commit()

    def set_decision_status(self, decision_id: str, status: str) -> None:
        self.connection.execute(
            "UPDATE suppression_decisions SET status = ? WHERE id = ?", (status, decision_id)
        )
        self.connection.commit()

    def decision_run_digest(self, decision_id: str) -> Optional[str]:
        row = self.connection.execute(
            "SELECT r.configuration_digest FROM suppression_decisions d JOIN campaign_runs r ON r.id = d.run_id WHERE d.id = ?",
            (decision_id,),
        ).fetchone()
        return row["configuration_digest"] if row else None

    def latest_analytics_run(self, campaign_slug: str) -> Optional[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM campaign_runs WHERE campaign_slug = ? AND status = 'completed' "
            "AND mode IN ('dry_run', 'review') ORDER BY completed_at DESC LIMIT 1", (campaign_slug,)
        ).fetchone()

    def contacts_for_run(self, run_id: str) -> List[sqlite3.Row]:
        return list(self.connection.execute("SELECT * FROM contacts WHERE run_id = ? ORDER BY email", (run_id,)))

    def evidence_for_run(self, run_id: str) -> List[sqlite3.Row]:
        return list(self.connection.execute("SELECT * FROM source_evidence WHERE run_id = ? ORDER BY id", (run_id,)))
