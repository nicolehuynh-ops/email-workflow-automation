import os

from pcs import config
from pcs.campaign import get_custom_field_value, is_out_of_office_status
from pcs.reply_client import ReplyClient


def finish_issuer_from_reply(api_key, sequence_id=None, contact_id=None, contact_email=None, pcs_issuer_id=None):
    if not api_key:
        raise RuntimeError("api_key is required.")
    if not contact_id and not contact_email:
        raise RuntimeError("Either contact_id or contact_email is required.")

    reply = ReplyClient(api_key)
    trigger_contact = reply.get_contact(int(contact_id)) if contact_id else reply.get_contact_by_email(contact_email)
    trigger_contact_id = trigger_contact.get("contactId") or trigger_contact.get("id")
    trigger_details = reply.get_contact(trigger_contact_id)

    if not sequence_id:
        sequence_id = infer_single_active_sequence_id(reply, trigger_contact_id)
    else:
        sequence_id = int(sequence_id)

    issuer_id = pcs_issuer_id or ""

    if not issuer_id:
        issuer_id = get_custom_field_value(
            trigger_details,
            config.CUSTOM_FIELDS["pcs_issuer_id"],
            config.CUSTOM_FIELD_NAMES["pcs_issuer_id"],
        )

    if not issuer_id:
        raise RuntimeError(f"Trigger contact is missing {config.CUSTOM_FIELD_NAMES['pcs_issuer_id']}.")

    states = reply.list_sequence_contact_states(sequence_id)
    details = reply.get_contacts_by_ids([state["contactId"] for state in states])
    details_by_id = {contact["id"]: contact for contact in details}
    trigger_state = next((state for state in states if state["contactId"] == trigger_contact_id), None)
    trigger_status = (trigger_state or {}).get("status") or {}

    if (
        (trigger_status.get("bounced") and not trigger_status.get("replied"))
        or is_out_of_office_status(trigger_status.get("status"))
    ):
        return {
            "ok": True,
            "ignored": True,
            "reason": "Trigger contact is bounced or out of office; issuer was not suppressed.",
            "sequenceId": sequence_id,
            "issuerId": issuer_id,
            "triggerContactId": trigger_contact_id,
            "triggerContactEmail": trigger_contact.get("email") or contact_email,
            "finishedCount": 0,
            "finishedContactIds": [],
        }

    same_issuer_active = []
    for state in states:
        if (state.get("status") or {}).get("status") != "Active":
            continue
        detail = details_by_id.get(state["contactId"])
        if not detail or detail.get("isOptedOut"):
            continue
        row_issuer_id = get_custom_field_value(
            detail,
            config.CUSTOM_FIELDS["pcs_issuer_id"],
            config.CUSTOM_FIELD_NAMES["pcs_issuer_id"],
        )
        if row_issuer_id == issuer_id:
            same_issuer_active.append(state)

    if same_issuer_active:
        reply.set_sequence_status(sequence_id, [state["contactId"] for state in same_issuer_active], "finished")

    return {
        "ok": True,
        "sequenceId": sequence_id,
        "issuerId": issuer_id,
        "triggerContactId": trigger_contact_id,
        "triggerContactEmail": trigger_contact.get("email") or contact_email,
        "finishedCount": len(same_issuer_active),
        "finishedContactIds": [state["contactId"] for state in same_issuer_active],
    }


def infer_single_active_sequence_id(reply, contact_id):
    sequences = reply.list_contact_sequences(contact_id)
    active_sequences = [
        sequence
        for sequence in sequences
        if str(sequence.get("statusInSequence") or sequence.get("status") or "").lower() == "active"
    ]
    if len(active_sequences) == 0 and len(sequences) == 1:
        return sequences[0].get("sequenceId") or sequences[0].get("id")
    if len(active_sequences) != 1:
        sequence_summary = [
            {
                "sequenceId": sequence.get("sequenceId") or sequence.get("id"),
                "sequenceName": sequence.get("sequenceName") or sequence.get("name"),
                "statusInSequence": sequence.get("statusInSequence") or sequence.get("status"),
            }
            for sequence in sequences
        ]
        raise RuntimeError(
            f"Expected exactly one active sequence for contact {contact_id}, found {len(active_sequences)}. "
            f"Sequences: {sequence_summary}"
        )
    return active_sequences[0].get("sequenceId") or active_sequences[0].get("id")


def zapier_entry(input_data):
    return finish_issuer_from_reply(
        api_key=input_data.get("REPLY_IO_API_KEY") or os.getenv("REPLY_IO_API_KEY"),
        sequence_id=optional_int(input_data.get("sequenceId")),
        contact_id=input_data.get("contactId"),
        contact_email=input_data.get("contactEmail"),
        pcs_issuer_id=input_data.get("pcsIssuerId"),
    )


def optional_int(value):
    if value is None or str(value).strip() == "":
        return None
    return int(value)
