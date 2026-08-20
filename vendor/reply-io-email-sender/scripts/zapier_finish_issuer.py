#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pcs.env import load_env

load_env()

import json
import os

from pcs.zapier_handler import finish_issuer_from_reply


def main():
    result = finish_issuer_from_reply(
        api_key=os.environ["REPLY_IO_API_KEY"],
        sequence_id=os.getenv("REPLY_SEQUENCE_ID"),
        contact_id=os.getenv("REPLY_CONTACT_ID"),
        contact_email=os.getenv("REPLY_CONTACT_EMAIL"),
        pcs_issuer_id=os.getenv("PCS_ISSUER_ID"),
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
