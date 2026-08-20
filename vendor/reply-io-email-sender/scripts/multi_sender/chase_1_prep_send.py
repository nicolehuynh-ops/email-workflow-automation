#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pcs.env import load_env

load_env()

import os

from pcs import config
from pcs.campaign import annotate_auto_replies, eligible_for_step, find_issuer_blocks, merge_contact_state_and_details, workbook_rows
from pcs.campaign_config import load_campaign_config
from pcs.cli import notes_with_sequence, prompt_run_options, require_live_confirmation
from pcs.contact_exclusions import load_contact_exclusions, load_out_of_office_exclusions
from pcs.reply_client import ReplyClient
from pcs.rsvp_list import find_rsvp_matches, load_rsvp_entries
from pcs.suppression_list import load_suppression_contacts, load_suppression_domains
from pcs.workbook import write_run_workbook


SCRIPT_NAME = "Chase 1 prep + send"


def main():
    sequence_id, dry_run, booked_emails, responded_emails, confirm_send = prompt_run_options(SCRIPT_NAME)
    campaign_config = load_campaign_config(sequence_id)
    reply = ReplyClient(os.environ["REPLY_IO_API_KEY"])
    reply.assert_sequence_safe(sequence_id)

    states = reply.list_sequence_contact_states(sequence_id)
    details = reply.get_contacts_by_ids([row["contactId"] for row in states])
    rows = merge_contact_state_and_details(states, details)
    annotate_auto_replies(reply, sequence_id, rows, campaign_config.step_2_hold_id)
    contact_exclusions = load_contact_exclusions()
    rsvp_matches = find_rsvp_matches(rows, load_rsvp_entries())
    contact_exclusions |= {row["email"].strip().lower() for row in rsvp_matches}
    manual_ooo_exclusions = load_out_of_office_exclusions()
    blocks = find_issuer_blocks(
        rows,
        booked_emails,
        responded_emails,
        load_suppression_contacts(),
        load_suppression_domains(),
        contact_exclusions,
        manual_ooo_exclusions,
    )

    if blocks["rows_missing_issuer"]:
        raise RuntimeError(f"Found {len(blocks['rows_missing_issuer'])} contacts missing PCS Issuer ID.")

    finished_rows = [
        {**row, "action": "Would mark finished" if dry_run else "Marked Finished", "reason": "; ".join(blocks["block_reasons"][row["pcsIssuerId"]])}
        for row in blocks["related_rows_to_finish"]
    ]
    candidate_rows = [
        {**row, "action": "Candidate for step 3", "reason": "Active, not replied, not bounced, not opted out, not OOO, issuer not blocked, currently step 2"}
        for row in eligible_for_step(
            rows, blocks["blocked_issuer_ids"], campaign_config.step_2_hold_id, contact_exclusions
        )
    ]

    pre_move_sender_failures = []
    eligible_rows = []
    for row in candidate_rows:
        contact = reply.get_sequence_contact(sequence_id, row["contactId"])
        if contact.get("emailAccountId") != campaign_config.initial_sender_account_id:
            pre_move_sender_failures.append(
                {
                    **row,
                    "action": "Not moved",
                    "reason": f"Pre-move sender check failed. Expected {campaign_config.initial_sender_label} emailAccountId {campaign_config.initial_sender_account_id}, got {contact.get('emailAccountId')}",
                }
            )
        else:
            eligible_rows.append(
                {
                    **row,
                    "action": "Would move to step 3" if dry_run else "Moved to step 3",
                    "reason": f"Active, not replied, not bounced, not opted out, not OOO, issuer not blocked, currently step 2, sender verified as {campaign_config.initial_sender_label}",
                }
            )

    if pre_move_sender_failures:
        workbook_path = write_run_workbook(
            SCRIPT_NAME,
            workbook_rows(eligible_rows),
            workbook_rows(finished_rows),
            workbook_rows([*rsvp_matches, *pre_move_sender_failures]),
            notes_with_sequence([
                f"No contacts were moved because at least one candidate was not assigned to {campaign_config.initial_sender_label} before the move.",
                "Fix the blocked contacts' sender assignment in Reply.io, then rerun this script.",
            ], sequence_id),
            sequence_id=sequence_id,
            campaign_name=campaign_config.sequence_name,
            output_dir=config.MULTI_SENDER_OUTPUT_DIR,
        )
        raise RuntimeError(
            f"Blocked: {len(pre_move_sender_failures)} Chase 1 candidates are not assigned to {campaign_config.initial_sender_label}. Workbook: {workbook_path}"
        )

    if not dry_run:
        require_live_confirmation(
            SCRIPT_NAME,
            sequence_id,
            campaign_config.initial_sender_label,
            campaign_config.initial_sender_account_id,
            eligible_rows,
            finished_rows,
            confirm_send,
            rsvp_matches,
        )

    if finished_rows and not dry_run:
        reply.set_sequence_status(sequence_id, [row["contactId"] for row in finished_rows], "finished")

    if eligible_rows and not dry_run:
        reply.move_contacts_to_step(sequence_id, [row["contactId"] for row in eligible_rows], campaign_config.step_3_chase_id)

    verification_failures = []
    for row in eligible_rows:
        contact = reply.get_sequence_contact(sequence_id, row["contactId"])
        if contact.get("emailAccountId") != campaign_config.initial_sender_account_id:
            verification_failures.append(
                {
                    **row,
                    "action": "Sender verification failed",
                    "reason": f"Expected {campaign_config.initial_sender_label} emailAccountId {campaign_config.initial_sender_account_id}, got {contact.get('emailAccountId')}",
                }
            )

    workbook_path = write_run_workbook(
        SCRIPT_NAME,
        workbook_rows(eligible_rows),
        workbook_rows(finished_rows),
        workbook_rows([*rsvp_matches, *verification_failures]),
        notes_with_sequence([
            "Dry run: no Reply.io changes were made." if dry_run else "Eligible contacts were moved from step 2 to step 3.",
            "Sender verification checks Reply.io sequence-contact emailAccountId after the move.",
        ], sequence_id),
        sequence_id=sequence_id,
        campaign_name=campaign_config.sequence_name,
        output_dir=config.MULTI_SENDER_OUTPUT_DIR,
    )

    if verification_failures:
        raise RuntimeError(f"Sender verification failed for {len(verification_failures)} contacts. Workbook: {workbook_path}")

    print(f"Done. Workbook: {workbook_path}")


if __name__ == "__main__":
    main()
