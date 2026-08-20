#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pcs.env import load_env

load_env()

import os

from pcs import config
from pcs.campaign import annotate_auto_replies, find_issuer_blocks, merge_contact_state_and_details, normalize_email, workbook_rows
from pcs.campaign_config import load_campaign_config
from pcs.cli import notes_with_sequence, prompt_run_options, require_live_confirmation
from pcs.contact_exclusions import load_contact_exclusions, load_out_of_office_exclusions
from pcs.reply_client import ReplyClient
from pcs.rsvp_list import find_rsvp_matches, load_rsvp_entries
from pcs.suppression_list import load_suppression_contacts, load_suppression_domains
from pcs.workbook import write_run_workbook


SCRIPT_NAME = "Chase 2 Send"


def main():
    sequence_id, dry_run, booked_emails, responded_emails, confirm_send = prompt_run_options(SCRIPT_NAME)
    campaign_config = load_campaign_config(sequence_id)
    reply = ReplyClient(os.environ["REPLY_IO_API_KEY"])
    reply.assert_sequence_safe(sequence_id)

    states = reply.list_sequence_contact_states(sequence_id)
    details = reply.get_contacts_by_ids([row["contactId"] for row in states])
    rows = merge_contact_state_and_details(states, details)
    annotate_auto_replies(reply, sequence_id, rows, campaign_config.step_4_hold_id)
    excluded_emails = load_contact_exclusions()
    rsvp_matches = find_rsvp_matches(rows, load_rsvp_entries())
    excluded_emails |= {row["email"].strip().lower() for row in rsvp_matches}
    manual_ooo_exclusions = load_out_of_office_exclusions()
    blocks = find_issuer_blocks(
        rows,
        booked_emails,
        responded_emails,
        load_suppression_contacts(),
        load_suppression_domains(),
        excluded_emails,
        manual_ooo_exclusions,
    )

    if blocks["rows_missing_issuer"]:
        raise RuntimeError(f"Found {len(blocks['rows_missing_issuer'])} contacts missing PCS Issuer ID.")

    finished_rows = [
        {
            **row,
            "action": "Would mark finished" if dry_run else "Marked Finished",
            "reason": "; ".join(blocks["block_reasons"][row["pcsIssuerId"]]),
        }
        for row in blocks["related_rows_to_finish"]
    ]
    candidates = [
        row
        for row in rows
        if normalize_email(row["email"]) not in excluded_emails
        and row["pcsSender"] == campaign_config.final_pcs_sender_value
        and row["pcsIssuerId"] not in blocks["blocked_issuer_ids"]
        and row["status"] == "Active"
        and not row["replied"]
        and not row.get("autoReply")
        and not row["bounced"]
        and not row.get("optedOut")
        and int(row["currentStepId"] or 0) == int(campaign_config.step_4_hold_id)
    ]

    blocked_rows = []
    verified_rows = []
    for row in candidates:
        sequence_contact = reply.get_sequence_contact(sequence_id, row["contactId"])
        if sequence_contact.get("emailAccountId") != campaign_config.final_sender_account_id:
            blocked_rows.append(
                {
                    **row,
                    "action": "Not moved",
                    "reason": f"Sender is not {campaign_config.final_sender_label}. Expected emailAccountId {campaign_config.final_sender_account_id}, got {sequence_contact.get('emailAccountId')}",
                }
            )
        else:
            verified_rows.append(
                {
                    **row,
                    "action": "Would move to step 5" if dry_run else "Moved to step 5",
                    "reason": f"PCS Sender is {campaign_config.final_pcs_sender_value}, current step is 4, active, and Reply.io sender account is {campaign_config.final_sender_label}",
                }
            )

    if blocked_rows:
        workbook_path = write_run_workbook(
            SCRIPT_NAME,
            workbook_rows(verified_rows),
            workbook_rows(finished_rows),
            workbook_rows([*rsvp_matches, *blocked_rows]),
            notes_with_sequence([
                f"No contacts were moved because at least one candidate was not assigned to {campaign_config.final_sender_label} in Reply.io.",
                "Manually update sender email in Reply.io for the blocked contacts, then rerun this script.",
            ], sequence_id),
            sequence_id=sequence_id,
            campaign_name=campaign_config.sequence_name,
            output_dir=config.MULTI_SENDER_OUTPUT_DIR,
        )
        raise RuntimeError(f"Blocked: {len(blocked_rows)} candidates are not assigned to {campaign_config.final_sender_label}. Workbook: {workbook_path}")

    if not dry_run:
        require_live_confirmation(
            SCRIPT_NAME,
            sequence_id,
            campaign_config.final_sender_label,
            campaign_config.final_sender_account_id,
            verified_rows,
            finished_rows,
            confirm_send,
            rsvp_matches,
        )

    if finished_rows and not dry_run:
        reply.set_sequence_status(sequence_id, [row["contactId"] for row in finished_rows], "finished")

    if verified_rows and not dry_run:
        reply.move_contacts_to_step(sequence_id, [row["contactId"] for row in verified_rows], campaign_config.step_5_final_id)

    workbook_path = write_run_workbook(
        SCRIPT_NAME,
        workbook_rows(verified_rows),
        workbook_rows(finished_rows),
        blocked_rows=workbook_rows(rsvp_matches),
        notes=notes_with_sequence(
            [
                f"Dry run: contacts would move from step 4 to step 5 after {campaign_config.final_sender_label} sender verification; no Reply.io changes were made."
                if dry_run
                else f"Contacts were moved from step 4 to step 5 only after {campaign_config.final_sender_label} sender verification passed."
            ],
            sequence_id,
        ),
        sequence_id=sequence_id,
        campaign_name=campaign_config.sequence_name,
        output_dir=config.MULTI_SENDER_OUTPUT_DIR,
    )
    print(f"Done. Workbook: {workbook_path}")


if __name__ == "__main__":
    main()
