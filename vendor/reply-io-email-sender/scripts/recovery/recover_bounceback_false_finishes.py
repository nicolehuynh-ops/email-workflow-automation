#!/usr/bin/env python3
"""Restore false Finished contacts caused by a bounced peer at the same issuer."""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pcs.env import load_env

load_env()

from pcs import config
from pcs.campaign import (
    annotate_auto_replies,
    is_finished_status,
    merge_contact_state_and_details,
    workbook_rows,
)
from pcs.reply_client import ReplyClient
from pcs.workbook import write_run_workbook


SCRIPT_NAME = "Recover bounceback false finishes"


def main():
    args = parse_args()
    reply = ReplyClient(os.environ["REPLY_IO_API_KEY"])
    sequence = reply.assert_sequence_safe(args.sequence_id)

    states = reply.list_sequence_contact_states(args.sequence_id)
    details = reply.get_contacts_by_ids([state["contactId"] for state in states])
    rows = merge_contact_state_and_details(states, details)
    annotate_auto_replies(reply, args.sequence_id, rows)

    rows_by_issuer = group_rows_by_issuer(rows)
    selected_issuer_ids = set(args.issuer_id)
    unknown_issuer_ids = selected_issuer_ids.difference(rows_by_issuer)
    if unknown_issuer_ids:
        raise RuntimeError(f"Issuer IDs are not in sequence {args.sequence_id}: {', '.join(sorted(unknown_issuer_ids))}")

    recovered_rows = []
    bounce_rows = []
    blocked_rows = []
    for issuer_id in sorted(selected_issuer_ids):
        issuer_rows = rows_by_issuer[issuer_id]
        issuer_bounces = [row for row in issuer_rows if row["bounced"]]
        if not issuer_bounces:
            raise RuntimeError(f"Issuer {issuer_id} has no bounced contact in sequence {args.sequence_id}.")

        for row in issuer_bounces:
            bounce_rows.append(
                {
                    **row,
                    "action": "Bounceback issuer signal",
                    "reason": "Used only to identify possible false Finished contacts; the bounced contact is never recovered.",
                }
            )

        issuer_reply_rows = [row for row in issuer_rows if row["replied"] and not row.get("autoReply")]
        for row in issuer_rows:
            reason = recovery_block_reason(row, issuer_reply_rows)
            if reason:
                if is_finished_status(row["status"]) and not row["bounced"]:
                    blocked_rows.append({**row, "action": "Not recovered", "reason": reason})
                continue
            recovered_rows.append(
                {
                    **row,
                    "action": "Marked back active" if args.apply else "Would mark back active",
                    "reason": "Finished peer of a bounced contact, with no response, OOO, opt-out, or bounce blocker.",
                }
            )

    if args.apply and recovered_rows:
        reply.set_sequence_status(args.sequence_id, [row["contactId"] for row in recovered_rows], "active")

    workbook_path = write_run_workbook(
        SCRIPT_NAME,
        workbook_rows(recovered_rows),
        workbook_rows(bounce_rows),
        workbook_rows(blocked_rows),
        notes=[
            "Apply mode: recovered contacts were set back to Active." if args.apply else "Dry run: no Reply.io changes were made.",
            "Recovery is limited to the supplied issuer IDs.",
            "Bounced, replied, out-of-office, and opted-out contacts are never recovered.",
        ],
        sequence_id=args.sequence_id,
        campaign_name=sequence.get("name") or sequence.get("sequenceName") or "unnamed-campaign",
        output_dir=config.MULTI_SENDER_OUTPUT_DIR,
        eligible_sheet_name="Marked Back Active" if args.apply else "Would Mark Back Active",
        finished_sheet_name="Bounceback Signals",
    )
    print(f"Done. Workbook: {workbook_path}")
    print(f"Contacts {'recovered' if args.apply else 'that would be recovered'}: {len(recovered_rows)}")


def parse_args():
    parser = argparse.ArgumentParser(description=SCRIPT_NAME)
    parser.add_argument("--sequence-id", type=int, required=True, help="Reply.io sequence ID to recover.")
    parser.add_argument(
        "--issuer-id",
        action="append",
        required=True,
        help="PCS Issuer ID to recover after reviewing a bounce; repeat for multiple issuers.",
    )
    parser.add_argument("--apply", action="store_true", help="Set approved false-Finished contacts back to Active.")
    return parser.parse_args()


def group_rows_by_issuer(rows):
    grouped = {}
    for row in rows:
        if row["pcsIssuerId"]:
            grouped.setdefault(row["pcsIssuerId"], []).append(row)
    return grouped


def recovery_block_reason(row, issuer_reply_rows):
    if row["bounced"]:
        return "Contact is bounced."
    if not is_finished_status(row["status"]):
        return f"Contact status is {row['status']}, not Finished."
    if row["replied"] and not row.get("autoReply"):
        return "Contact has replied."
    if row.get("autoReply"):
        return "Contact is out of office."
    if row.get("optedOut"):
        return "Contact is opted out."
    if issuer_reply_rows:
        return f"Issuer has {len(issuer_reply_rows)} non-OOO replied contact(s)."
    return ""


if __name__ == "__main__":
    main()
