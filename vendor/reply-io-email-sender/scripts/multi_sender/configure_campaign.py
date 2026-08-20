#!/usr/bin/env python3
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pcs.env import load_env

load_env()

from pcs.campaign_config import (  # noqa: E402
    CampaignConfig,
    CONFIG_CSV_PATH,
    ordered_sequence_steps,
    upsert_campaign_config,
    validate_pcs_step_pattern,
)
from pcs.reply_client import ReplyClient  # noqa: E402


SCRIPT_NAME = "Configure campaign"


def main():
    args = parse_args()
    reply = ReplyClient(os.environ["REPLY_IO_API_KEY"])

    sequence_id = args.sequence_id or prompt_int("Enter Reply.io sequence ID")
    sequence = reply.assert_sequence_safe(sequence_id)
    steps = ordered_sequence_steps(reply.list_sequence_steps(sequence_id))
    validate_pcs_step_pattern(steps)

    print("")
    print(f"Sequence: {sequence.get('name')} ({sequence_id})")
    for index, step in enumerate(steps, start=1):
        print(f"Step {index}: id={step.get('id')} type={step.get('type')}")

    initial_sender_email = args.initial_sender_email or prompt_text("Initial/chase-1 sender email")
    initial_sender = reply.get_email_account_by_email(initial_sender_email)
    final_sender_email = args.final_sender_email or prompt_text("Final sender email")
    final_sender = reply.get_email_account_by_email(final_sender_email)

    initial_sender_label = args.initial_sender_label or prompt_text(
        "Initial/chase-1 sender label",
        default=initial_sender.get("senderName") or initial_sender.get("email") or "",
    )
    final_sender_label = args.final_sender_label or prompt_text(
        "Final sender label",
        default=final_sender.get("senderName") or final_sender.get("email") or "",
    )
    final_pcs_sender_value = args.final_pcs_sender_value or prompt_text(
        "Final PCS Sender value",
        default=first_name(final_sender_label),
    )

    campaign_config = CampaignConfig(
        sequence_id=sequence_id,
        sequence_name=sequence.get("name") or "",
        step_2_hold_id=steps[1]["id"],
        step_3_chase_id=steps[2]["id"],
        step_4_hold_id=steps[3]["id"],
        step_5_final_id=steps[4]["id"],
        initial_sender_email=initial_sender.get("email") or initial_sender_email,
        initial_sender_account_id=initial_sender["id"],
        initial_sender_label=initial_sender_label,
        final_sender_email=final_sender.get("email") or final_sender_email,
        final_sender_account_id=final_sender["id"],
        final_sender_label=final_sender_label,
        final_pcs_sender_value=final_pcs_sender_value,
    )
    path = upsert_campaign_config(campaign_config)

    print("")
    print(f"Saved campaign config to {path}")
    print(f"Initial/chase-1 sender: {campaign_config.initial_sender_label} <{campaign_config.initial_sender_email}> account {campaign_config.initial_sender_account_id}")
    print(f"Final sender: {campaign_config.final_sender_label} <{campaign_config.final_sender_email}> account {campaign_config.final_sender_account_id}")
    print(f"Final PCS Sender value: {campaign_config.final_pcs_sender_value}")
    print("")
    print("Step mapping saved:")
    print(f"- Step 2 hold ID: {campaign_config.step_2_hold_id}")
    print(f"- Step 3 chase ID: {campaign_config.step_3_chase_id}")
    print(f"- Step 4 hold ID: {campaign_config.step_4_hold_id}")
    print(f"- Step 5 final ID: {campaign_config.step_5_final_id}")


def parse_args():
    parser = argparse.ArgumentParser(description=SCRIPT_NAME)
    parser.add_argument("--sequence-id", type=int, default=None)
    parser.add_argument("--initial-sender-email", default=None)
    parser.add_argument("--initial-sender-label", default=None)
    parser.add_argument("--final-sender-email", default=None)
    parser.add_argument("--final-sender-label", default=None)
    parser.add_argument("--final-pcs-sender-value", default=None)
    return parser.parse_args()


def prompt_text(label, default=None):
    while True:
        suffix = f" [{default}]" if default else ""
        value = input(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default:
            return default
        print(f"{label} is required. Please enter a value.")


def prompt_int(label):
    raw = prompt_text(label)
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{label} must be a number. Received: {raw}") from exc


def first_name(label):
    return (label.strip().split() or [""])[0]


if __name__ == "__main__":
    main()
