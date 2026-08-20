#!/usr/bin/env python3
"""Create a read-only Group A vs Group B campaign-performance workbook."""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pcs.env import load_env

load_env()

from pcs import config
from pcs.calendly_client import CalendlyClient
from pcs.campaign import merge_contact_state_and_details
from pcs.campaign_config import ordered_sequence_steps, read_config_rows
from pcs.performance_reporting import activity_metrics, build_report, campaign_group, write_performance_workbook
from pcs.reply_client import ReplyClient
from pcs.suppression_list import load_suppression_contacts


def main():
    parser = argparse.ArgumentParser(description="Report Group A vs Group B campaign performance.")
    parser.add_argument("--sequence-id", type=int, action="append", default=[], help="Only include this configured sequence ID; repeatable.")
    parser.add_argument("--calendly-link", action="append", default=[], metavar="SENDER=URL", help="Required sender booking link, e.g. 'Frankie=https://calendly.com/acme/15min'. Repeat for every sender.")
    args = parser.parse_args()
    calendly_token = os.getenv("CALENDLY_PERSONAL_ACCESS_TOKEN") or os.getenv("Calendly_personal_access_token")
    if not calendly_token:
        raise RuntimeError("CALENDLY_PERSONAL_ACCESS_TOKEN is missing from .env.")

    tracking_links = parse_tracking_links(args.calendly_link)
    reply = ReplyClient(os.environ["REPLY_IO_API_KEY"])
    snapshots = []
    for row in configured_sequence_rows(args.sequence_id):
        sequence_id = int(row["sequence_id"])
        print(f"Reading Reply.io sequence {sequence_id}...", flush=True)
        reply.assert_sequence_safe(sequence_id)
        states = reply.list_sequence_contact_states(sequence_id)
        details = reply.get_contacts_by_ids([state["contactId"] for state in states])
        activities = reply.get_contact_activities_by_ids([state["contactId"] for state in states])
        contacts = merge_contact_state_and_details(states, details)
        chase_by_step = email_step_labels(ordered_sequence_steps(reply.list_sequence_steps(sequence_id)))
        for contact, contact_activities in zip(contacts, activities):
            contact["metrics"] = activity_metrics(contact_activities, chase_by_step)
        name = row.get("sequence_name") or f"Sequence {sequence_id}"
        snapshots.append({"sequence_id": sequence_id, "sequence_name": name, "group": campaign_group(name), "contacts": contacts})
        print(f"Read {len(contacts)} contacts and activity histories from sequence {sequence_id}.", flush=True)

    print("Reading Calendly bookings for the supplied sender links...", flush=True)
    calendly_invitees = CalendlyClient(calendly_token).list_active_invitees(tracking_links)
    sequence_rows, group_rows, detail_rows, validation_rows, chase_rows = build_report(snapshots, calendly_invitees, load_suppression_contacts())
    workbook_path = write_performance_workbook(
        config.CAMPAIGN_RESPONSES_DIR, sequence_rows, group_rows, detail_rows, validation_rows, chase_rows,
        [
            ("Report", "Campaign performance: Group A vs Group B"),
            ("Scope", f"{len(snapshots)} configured Reply.io sequences; entire current sequence audience and activity history."),
            ("Calendly", f"{len(calendly_invitees)} invitees from active events on the supplied tracking links."),
            ("Validated outcomes", "Only active Calendly bookings and suppression-list entries with manual_response or meeting_booked count as conversions."),
            ("Raw replies", "Reply.io responses are engagement metrics and remain Pending human validation until confirmed in the suppression list."),
            ("Aggregation", "Campaign rollups use distinct email addresses, so a contact present in multiple sequences is counted once per group."),
            ("Classification", "Configured Reply.io sequence names must contain Group A or Group B; other names are reported as Unclassified."),
        ],
    )
    print(f"Workbook: {workbook_path}")


def configured_sequence_rows(sequence_ids):
    rows_by_id = {}
    for path in (config.CAMPAIGN_CONFIG_DIR / "multi_sender_campaign_configs.csv", config.CAMPAIGN_CONFIG_DIR / "single_chase_campaign_configs.csv"):
        for row in read_config_rows(path):
            rows_by_id[int(row["sequence_id"])] = row
    requested = set(sequence_ids)
    if requested:
        missing = requested - set(rows_by_id)
        if missing:
            raise RuntimeError(f"No campaign configuration found for sequence IDs: {', '.join(map(str, sorted(missing)))}")
        rows_by_id = {sequence_id: row for sequence_id, row in rows_by_id.items() if sequence_id in requested}
    if not rows_by_id:
        raise RuntimeError("No configured campaign sequences found. Run a configure_campaign.py script first.")
    return [rows_by_id[sequence_id] for sequence_id in sorted(rows_by_id)]


def parse_tracking_links(values):
    links = {}
    for value in values:
        sender, separator, link = value.partition("=")
        sender = sender.strip()
        if not separator or not sender or not link.strip().startswith("http"):
            raise RuntimeError("Use --calendly-link 'Sender=https://calendly.com/your-link' and repeat for every sender.")
        links[sender] = link.strip()
    if not links:
        raise RuntimeError("Provide at least one --calendly-link for a campaign sender.")
    return links


def email_step_labels(steps):
    labels = {}
    email_index = 0
    for step in steps:
        if str(step.get("type") or "").lower() != "email":
            continue
        email_index += 1
        labels[str(step.get("id"))] = "Email 1 (Initial)" if email_index == 1 else f"Email {email_index} (Chase {email_index - 1})"
    return labels


if __name__ == "__main__":
    main()
