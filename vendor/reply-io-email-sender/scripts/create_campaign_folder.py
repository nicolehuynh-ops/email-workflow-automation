#!/usr/bin/env python3
"""Create an isolated folder for a Reply.io campaign's operational data."""

import argparse
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CAMPAIGNS_DIR = PROJECT_ROOT / "campaigns"
FILES = {
    "config/multi_sender_campaign_configs.csv": "sequence_id,sequence_name,step_2_hold_id,step_3_chase_id,step_4_hold_id,step_5_final_id,initial_sender_email,initial_sender_account_id,initial_sender_label,final_sender_email,final_sender_account_id,final_sender_label,final_pcs_sender_value\n",
    "config/single_chase_campaign_configs.csv": "sequence_id,sequence_name,step_2_hold_id,step_3_chase_id,sender_email,sender_account_id,sender_label\n",
    "suppression/suppression_contacts.csv": "email,reason,notes\n",
    "suppression/suppression_domains.csv": "domain,reason,notes\n",
    "suppression/contact_exclusions.csv": "email,reason,notes\n",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", help="Folder name, for example: san-francisco-event-invite")
    args = parser.parse_args()

    if not args.campaign.strip() or Path(args.campaign).name != args.campaign or args.campaign in {".", ".."}:
        raise SystemExit("Campaign must be a single folder name.")

    campaign_dir = CAMPAIGNS_DIR / args.campaign
    if campaign_dir.exists():
        raise SystemExit(f"Campaign folder already exists: {campaign_dir}")

    for relative_path, content in FILES.items():
        path = campaign_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    for relative_path in ("responses", "runs/multi_sender", "runs/single_chase"):
        (campaign_dir / relative_path).mkdir(parents=True, exist_ok=True)

    print(f"Created {campaign_dir}")
    print(f"Run campaign scripts with: PCS_CAMPAIGN={args.campaign!r} python3 scripts/...")


if __name__ == "__main__":
    main()
