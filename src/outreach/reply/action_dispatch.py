"""Maps one approved suppression_decisions row to a concrete Reply.io write.

Each decision (``outreach.decisions.build_decisions``) already carries exactly
one impacted contact per row -- for a ``domain_company`` scope, the fan-out
across every matched contact happens once, at decision-build time, producing
one decision row per contact (all sharing the same ``match_key``), not a
single row listing several contact ids. So dispatch never re-derives a
fan-out list at apply time; it always acts on the one contact embedded in
``decision_json``, exactly as it was reviewed and approved.
"""

import json
from typing import Dict

from outreach.models import Campaign
from outreach.reply.client import ReplyWriteClient


class UnsupportedActionError(RuntimeError):
    pass


def _next_step_id(campaign: Campaign, current_step_id: str) -> str:
    step_ids = [step.id for step in campaign.sequence_steps]
    if current_step_id not in step_ids:
        raise UnsupportedActionError(f"Sequence step {current_step_id} is not declared in campaign configuration.")
    index = step_ids.index(current_step_id)
    if index + 1 >= len(step_ids):
        raise UnsupportedActionError(f"Sequence step {current_step_id} has no configured next step to advance to.")
    return step_ids[index + 1]


def dispatch(reply: ReplyWriteClient, campaign: Campaign, decision: Dict) -> Dict:
    decision_json = json.loads(decision["decision_json"])
    contact = decision_json["contact"]
    sequence_id = campaign.campaign_id
    proposed_action = decision["proposed_action"]

    if proposed_action in {"finish", "hold_for_review"}:
        # A held decision reaches dispatch only after an operator explicitly approves it.
        contact_ids = decision_json.get("impacted_contact_ids") or [contact["reply_contact_id"]]
        if not contact_ids or any(not contact_id for contact_id in contact_ids):
            raise UnsupportedActionError("Approved decision has no Reply.io contact id.")
        reply.set_sequence_status(sequence_id, contact_ids, "finished")
        return {"request": {"action": "finish", "contact_ids": contact_ids}, "response": {"ok": True}}

    if proposed_action == "advance":
        next_step_id = _next_step_id(campaign, contact["sequence_step_id"])
        result = reply.move_contacts_to_step(sequence_id, [contact["reply_contact_id"]], next_step_id)
        return {"request": {"action": "advance", "contact_id": contact["reply_contact_id"], "to_step": next_step_id}, "response": result}

    raise UnsupportedActionError(
        f"proposed_action '{proposed_action}' should never reach dispatch -- only 'finish'/'advance' decisions are applied."
    )
