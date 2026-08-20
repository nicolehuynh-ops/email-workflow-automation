import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def campaign_slug_env():
    """Return the selected campaign folder name, rejecting path-like values."""
    value = os.getenv("PCS_CAMPAIGN", "uncovered-issuer-outreach").strip()
    if not value or Path(value).name != value or value in {".", ".."}:
        raise ValueError("PCS_CAMPAIGN must be a single campaign folder name.")
    return value


def int_env(name, default):
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer. Received: {raw}") from exc


SEQUENCE_ID = int_env("REPLY_SEQUENCE_ID", 1733724)

STEPS = {
    "initial_email": 7705444,
    "chase1_hold": 7705448,
    "chase1_email": 7705447,
    "chase2_hold": 7705445,
    "chase2_email": 7705446,
}

EMAIL_ACCOUNTS = {
    "frankie": int_env("REPLY_FRANKIE_EMAIL_ACCOUNT_ID", 609667),
    "prab": int_env("REPLY_PRAB_EMAIL_ACCOUNT_ID", 709797),
}

CUSTOM_FIELDS = {
    "pcs_sender": int_env("PCS_SENDER_FIELD_ID", 147786),
    "pcs_issuer_id": int_env("PCS_ISSUER_ID_FIELD_ID", 147787),
}

CUSTOM_FIELD_NAMES = {
    "pcs_sender": "PCS Sender",
    "pcs_issuer_id": "PCS Issuer ID",
}

SENDER_VALUES = {
    "prab": "Prab",
}

CAMPAIGN_SLUG = campaign_slug_env()
CAMPAIGN_DIR = PROJECT_ROOT / "campaigns" / CAMPAIGN_SLUG
CAMPAIGN_SUPPRESSION_DIR = CAMPAIGN_DIR / "suppression"
CAMPAIGN_RESPONSES_DIR = CAMPAIGN_DIR / "responses"
CAMPAIGN_CONFIG_DIR = CAMPAIGN_DIR / "config"
MULTI_SENDER_OUTPUT_DIR = CAMPAIGN_DIR / "runs" / "multi_sender"
SINGLE_CHASE_OUTPUT_DIR = CAMPAIGN_DIR / "runs" / "single_chase"
PAGE_SIZE = 1000
WRITE_CHUNK_SIZE = 100
SETTLE_SECONDS = int_env("REPLY_SETTLE_SECONDS", 5)
