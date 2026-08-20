"""Live sender-account re-verification.

Ported from the vendored ``reply.io-email-sender`` repo's chase scripts,
which re-check each candidate contact's live ``emailAccountId`` against the
sequence step's expected sender both before AND after moving contacts to a
new step (chase 1) or before only (chase 2 send). This module provides the
shared check; callers (``reply.apply``) decide when to call it and whether a
mismatch aborts the batch.
"""

from typing import Dict, List

from outreach.reply.client import ReplyWriteClient


def verify_senders(
    reply: ReplyWriteClient,
    sequence_id: str,
    contact_ids: List[str],
    expected_account_id: str,
) -> List[Dict]:
    """Return mismatches only. An empty list means every contact verified."""
    mismatches = []
    for contact_id in contact_ids:
        contact = reply.get_sequence_contact(sequence_id, contact_id)
        actual_account_id = contact.get("emailAccountId")
        if str(actual_account_id) != str(expected_account_id):
            mismatches.append({
                "contact_id": contact_id,
                "expected_account_id": expected_account_id,
                "actual_account_id": actual_account_id,
            })
    return mismatches
