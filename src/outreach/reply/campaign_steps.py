"""Multi-sender sequence-shape validation.

Ported from the vendored ``reply.io-email-sender`` repo's
``pcs/campaign_config.py``. Kept separate from the generic ``outreach.config``
module -- this 5-step "email/task/email/task/email" shape is specific to the
multi-sender campaign design, not a requirement every campaign shares.
"""

from typing import Dict, List

from outreach.models import Campaign

EXPECTED_MULTI_SENDER_STEP_TYPES = ["email", "task", "email", "task", "email"]


class SequenceShapeError(RuntimeError):
    pass


def ordered_sequence_steps(raw_steps: List[Dict]) -> List[Dict]:
    """Reconstruct the true step order via each step's parentId, since
    Reply.io's own returned display order is not reliable."""
    by_parent: Dict = {}
    for step in raw_steps:
        by_parent.setdefault(step.get("parentId"), []).append(step)

    ordered = []
    current = _only_child(by_parent, None, "root")
    while current:
        ordered.append(current)
        children = by_parent.get(current.get("id"), [])
        if len(children) > 1:
            raise SequenceShapeError(f"Step {current.get('id')} has {len(children)} children. Expected a linear sequence.")
        current = children[0] if children else None

    if len(ordered) != len(raw_steps):
        raise SequenceShapeError("Could not reconstruct a single linear sequence from parentId chains.")
    return ordered


def _only_child(by_parent: Dict, parent_id, label: str) -> Dict:
    children = by_parent.get(parent_id, [])
    if len(children) != 1:
        raise SequenceShapeError(f"Expected one {label} step, found {len(children)}.")
    return children[0]


def validate_multi_sender_step_pattern(ordered_steps: List[Dict]) -> None:
    if len(ordered_steps) != len(EXPECTED_MULTI_SENDER_STEP_TYPES):
        raise SequenceShapeError(f"Expected exactly {len(EXPECTED_MULTI_SENDER_STEP_TYPES)} sequence steps, found {len(ordered_steps)}.")
    actual_types = [step.get("type") for step in ordered_steps]
    if actual_types != EXPECTED_MULTI_SENDER_STEP_TYPES:
        raise SequenceShapeError(f"Expected step pattern {EXPECTED_MULTI_SENDER_STEP_TYPES}, found {actual_types}.")


def role_step_ids(campaign: Campaign) -> Dict[str, str]:
    """Map each declared step role to its Reply.io step id, e.g.
    {"initial": "step-1", "hold": "step-2", "chase": "step-3", ...}."""
    return {step.role: step.id for step in campaign.sequence_steps}
