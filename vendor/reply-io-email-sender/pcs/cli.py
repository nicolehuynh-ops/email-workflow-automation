import argparse
import csv
import os
from pathlib import Path

from pcs import config


def prompt_run_options(script_name):
    parser = argparse.ArgumentParser(description=script_name)
    parser.add_argument("--sequence-id", type=int, default=None, help="Reply.io sequence ID to operate on.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Calculate and report actions without changing Reply.io.",
    )
    parser.add_argument(
        "--booked-email",
        action="append",
        default=[],
        metavar="EMAIL",
        help="Email address of a contact manually confirmed as booked; repeat for multiple contacts.",
    )
    parser.add_argument(
        "--responded-email",
        action="append",
        default=[],
        metavar="EMAIL",
        help="Email address of a contact manually confirmed as having responded; repeat for multiple contacts.",
    )
    parser.add_argument(
        "--responded-emails",
        action="append",
        default=[],
        metavar="EMAILS",
        help="Comma- or newline-separated manual response emails; repeatable.",
    )
    parser.add_argument(
        "--responded-emails-file",
        type=Path,
        default=None,
        metavar="PATH",
        help="Reply-export CSV with an Email/email column, or a text file with one email per line.",
    )
    parser.add_argument(
        "--confirm-send",
        action="store_true",
        help="Acknowledge the live-send preflight; you will still be prompted to type SEND.",
    )
    args = parser.parse_args()

    responded_emails = [*args.responded_email]
    for value in args.responded_emails:
        responded_emails.extend(split_email_values(value))
    if args.responded_emails_file:
        responded_emails.extend(read_response_emails_file(args.responded_emails_file))

    if args.sequence_id:
        return args.sequence_id, args.dry_run, args.booked_email, responded_emails, args.confirm_send

    env_value = os.getenv("REPLY_SEQUENCE_ID")
    if env_value:
        return int(env_value), args.dry_run, args.booked_email, responded_emails, args.confirm_send

    raw = input(f"Enter Reply.io sequence ID for {script_name}: ").strip()
    if not raw:
        raise RuntimeError("Sequence ID is required.")
    try:
        return int(raw), args.dry_run, args.booked_email, responded_emails, args.confirm_send
    except ValueError as exc:
        raise RuntimeError(f"Sequence ID must be a number. Received: {raw}") from exc


def prompt_sequence_id(script_name):
    """Backward-compatible sequence-only CLI helper."""
    sequence_id, _, _, _, _ = prompt_run_options(script_name)
    return sequence_id


def notes_with_sequence(notes, sequence_id):
    return [f"Sequence ID used for this run: {sequence_id}", *notes]


def split_email_values(value):
    return [email.strip() for email in str(value or "").replace("\n", ",").replace(";", ",").split(",") if email.strip()]


def read_response_emails_file(path):
    path = Path(path)
    if not path.is_file():
        raise RuntimeError(f"Manual response file does not exist: {path}")

    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            email_column = next(
                (name for name in (reader.fieldnames or []) if name.strip().lower() in {"email", "email address"}),
                None,
            )
            if not email_column:
                raise RuntimeError(f"CSV manual response file must include an Email or email column: {path}")
            emails = [row.get(email_column, "").strip() for row in reader if row.get(email_column, "").strip()]
    else:
        emails = split_email_values(path.read_text(encoding="utf-8"))

    if not emails:
        raise RuntimeError(f"Manual response file contains no email addresses: {path}")
    return emails


def require_live_confirmation(script_name, sequence_id, sender_label, sender_account_id, eligible_rows, finished_rows, confirm_send, rsvp_matches=None):
    """Require a human review after all data and sender checks pass, before any write."""
    if not confirm_send:
        raise RuntimeError(
            "Live campaign changes require --confirm-send after reviewing a dry-run workbook. "
            "No Reply.io changes were made."
        )

    print("\nLIVE CAMPAIGN PREFLIGHT")
    print(f"Script: {script_name}")
    print(f"Sequence ID: {sequence_id}")
    print(f"Required sender: {sender_label} (emailAccountId {sender_account_id})")
    print(f"Contacts eligible to advance: {len(eligible_rows)}")
    print(f"Contacts to mark Finished: {len(finished_rows)}")
    print("Checked fields: PCS Issuer ID, PCS Sender where applicable, status, reply, bounce, opt-out, OOO, current step, and sender account where a contact advances.")
    for row in eligible_rows:
        print(
            f"  Advance: {row['email']} | issuer={row['pcsIssuerId']} | "
            f"PCS Sender={row.get('pcsSender') or '(blank)'} | step={row['currentStepId']}"
        )
    for row in finished_rows:
        print(f"  Finish: {row['email']} | issuer={row['pcsIssuerId']} | reason={row['reason']}")

    rsvp_matches = rsvp_matches or []
    if rsvp_matches:
        print("RSVP matches excluded from this chase:")
        for row in rsvp_matches:
            print(f"  RSVP match: {row['email']} | Reply.io name={row['firstName']} {row['lastName']} | RSVP={row['rsvpName']} <{row['rsvpEmail'] or 'no email'}>")
        if input("Confirm RSVP matches have been reviewed. Type RSVP to continue: ").strip() != "RSVP":
            raise RuntimeError("RSVP confirmation did not match. No Reply.io changes were made.")

    if input("Review the fields above. Type SEND to apply these Reply.io changes: ").strip() != "SEND":
        raise RuntimeError("Confirmation did not match SEND. No Reply.io changes were made.")
