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


SCRIPT_NAME = "Chase 2 Prep"


def main():
    sequence_id, dry_run, booked_emails, responded_emails, confirm_send = prompt_run_options(SCRIPT_NAME)
    campaign_config = load_campaign_config(sequence_id)
    reply = ReplyClient(os.environ["REPLY_IO_API_KEY"])
    reply.assert_sequence_safe(sequence_id)

    states = reply.list_sequence_contact_states(sequence_id)
    details = reply.get_contacts_by_ids([row["contactId"] for row in states])
    rows = merge_contact_state_and_details(states, details)
    annotate_auto_replies(reply, sequence_id, rows, campaign_config.step_4_hold_id)
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
    eligible_rows = [
        {
            **row,
            "pcsSender": campaign_config.final_pcs_sender_value,
            "action": "Would update PCS Sender" if dry_run else "Updated PCS Sender",
            "reason": f"Active, not replied, not bounced, not opted out, not OOO, issuer not blocked, currently step 4; eligible after manual {campaign_config.final_sender_label} sender update",
        }
        for row in eligible_for_step(
            rows, blocks["blocked_issuer_ids"], campaign_config.step_4_hold_id, contact_exclusions
        )
    ]
    if not dry_run:
        require_live_confirmation(
            SCRIPT_NAME,
            sequence_id,
            campaign_config.final_sender_label,
            campaign_config.final_sender_account_id,
            eligible_rows,
            finished_rows,
            confirm_send,
            rsvp_matches,
        )

    if finished_rows and not dry_run:
        reply.set_sequence_status(sequence_id, [row["contactId"] for row in finished_rows], "finished")

    if eligible_rows and not dry_run:
        reply.update_contact_custom_field(
            [row["contactId"] for row in eligible_rows],
            config.CUSTOM_FIELDS["pcs_sender"],
            campaign_config.final_pcs_sender_value,
        )

    workbook_path = write_run_workbook(
        SCRIPT_NAME,
        workbook_rows(eligible_rows),
        workbook_rows(finished_rows),
        blocked_rows=workbook_rows(rsvp_matches),
        notes=notes_with_sequence([
            f"Dry run: PCS Sender would be set to {campaign_config.final_pcs_sender_value}; no Reply.io changes were made."
            if dry_run
            else f"Eligible contacts had PCS Sender set to {campaign_config.final_pcs_sender_value}.",
            f"Next manual step: filter Reply.io by PCS Sender = {campaign_config.final_pcs_sender_value} and bulk update sender email to {campaign_config.final_sender_label} in the UI.",
            "After the manual UI sender update, run python3 scripts/multi_sender/chase_2_send.py.",
        ], sequence_id),
        sequence_id=sequence_id,
        campaign_name=campaign_config.sequence_name,
        output_dir=config.MULTI_SENDER_OUTPUT_DIR,
    )
    print(f"Done. Workbook: {workbook_path}")


if __name__ == "__main__":
    main()
