#!/usr/bin/env python3
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pcs.env import load_env

load_env()

from pcs.campaign_config import (  # noqa: E402
    SingleChaseConfig,
    ordered_sequence_steps,
    upsert_single_chase_config,
    validate_single_chase_step_pattern,
)
from pcs.reply_client import ReplyClient  # noqa: E402


SCRIPT_NAME = "Configure single-chase campaign"


def main():
    args = parse_args()
    reply = ReplyClient(os.environ["REPLY_IO_API_KEY"])

    sequence_id = args.sequence_id or prompt_int("Enter Reply.io sequence ID")
    sequence = reply.assert_sequence_safe(sequence_id)
    steps = ordered_sequence_steps(reply.list_sequence_steps(sequence_id))
    validate_single_chase_step_pattern(steps)

    print("")
    print(f"Sequence: {sequence.get('name')} ({sequence_id})")
    for index, step in enumerate(steps, start=1):
        print(f"Step {index}: id={step.get('id')} type={step.get('type')}")

    sender_email = args.sender_email or prompt_text("Sender email")
    sender = reply.get_email_account_by_email(sender_email)
    sender_label = args.sender_label or prompt_text(
        "Sender label",
        default=sender.get("senderName") or sender.get("email") or "",
    )

    single_chase_config = SingleChaseConfig(
        sequence_id=sequence_id,
        sequence_name=sequence.get("name") or "",
        step_2_hold_id=steps[1]["id"],
        step_3_chase_id=steps[2]["id"],
        sender_email=sender.get("email") or sender_email,
        sender_account_id=sender["id"],
        sender_label=sender_label,
    )
    path = upsert_single_chase_config(single_chase_config)

    print("")
    print(f"Saved single-chase campaign config to {path}")
    print(f"Sender: {single_chase_config.sender_label} <{single_chase_config.sender_email}> account {single_chase_config.sender_account_id}")
    print("")
    print("Step mapping saved:")
    print(f"- Step 2 hold ID: {single_chase_config.step_2_hold_id}")
    print(f"- Step 3 chase ID: {single_chase_config.step_3_chase_id}")


def parse_args():
    parser = argparse.ArgumentParser(description=SCRIPT_NAME)
    parser.add_argument("--sequence-id", type=int, default=None)
    parser.add_argument("--sender-email", default=None)
    parser.add_argument("--sender-label", default=None)
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


if __name__ == "__main__":
    main()
