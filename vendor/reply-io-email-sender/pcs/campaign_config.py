import csv
from dataclasses import dataclass
from pathlib import Path

from pcs import config


CONFIG_CSV_PATH = config.CAMPAIGN_CONFIG_DIR / "multi_sender_campaign_configs.csv"
SINGLE_CHASE_CONFIG_CSV_PATH = config.CAMPAIGN_CONFIG_DIR / "single_chase_campaign_configs.csv"

CSV_FIELDS = [
    "sequence_id",
    "sequence_name",
    "step_2_hold_id",
    "step_3_chase_id",
    "step_4_hold_id",
    "step_5_final_id",
    "initial_sender_email",
    "initial_sender_account_id",
    "initial_sender_label",
    "final_sender_email",
    "final_sender_account_id",
    "final_sender_label",
    "final_pcs_sender_value",
]

SINGLE_CHASE_CSV_FIELDS = [
    "sequence_id",
    "sequence_name",
    "step_2_hold_id",
    "step_3_chase_id",
    "sender_email",
    "sender_account_id",
    "sender_label",
]


@dataclass
class CampaignConfig:
    sequence_id: int
    sequence_name: str
    step_2_hold_id: int
    step_3_chase_id: int
    step_4_hold_id: int
    step_5_final_id: int
    initial_sender_email: str
    initial_sender_account_id: int
    initial_sender_label: str
    final_sender_email: str
    final_sender_account_id: int
    final_sender_label: str
    final_pcs_sender_value: str


@dataclass
class SingleChaseConfig:
    sequence_id: int
    sequence_name: str
    step_2_hold_id: int
    step_3_chase_id: int
    sender_email: str
    sender_account_id: int
    sender_label: str


def load_campaign_config(sequence_id):
    rows = read_config_rows()
    for row in rows:
        if int(row["sequence_id"]) == int(sequence_id):
            return row_to_config(row)
    raise RuntimeError(
        f"No campaign config found for sequence {sequence_id}. "
        "Run python3 scripts/multi_sender/configure_campaign.py for this sequence first."
    )


def load_single_chase_config(sequence_id):
    rows = read_config_rows(SINGLE_CHASE_CONFIG_CSV_PATH)
    for row in rows:
        if int(row["sequence_id"]) == int(sequence_id):
            return row_to_single_chase_config(row)
    raise RuntimeError(
        f"No single-chase campaign config found for sequence {sequence_id}. "
        "Run python3 scripts/single_chase/configure_campaign.py for this sequence first."
    )


def read_config_rows(path=CONFIG_CSV_PATH):
    if not Path(path).exists():
        return []
    with open(path, newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def upsert_campaign_config(campaign_config, path=CONFIG_CSV_PATH):
    rows = read_config_rows(path)
    new_row = config_to_row(campaign_config)
    updated = False
    for index, row in enumerate(rows):
        if int(row["sequence_id"]) == int(campaign_config.sequence_id):
            rows[index] = new_row
            updated = True
            break
    if not updated:
        rows.append(new_row)

    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return Path(path).resolve()


def upsert_single_chase_config(single_chase_config, path=SINGLE_CHASE_CONFIG_CSV_PATH):
    rows = read_config_rows(path)
    new_row = single_chase_config_to_row(single_chase_config)
    updated = False
    for index, row in enumerate(rows):
        if int(row["sequence_id"]) == int(single_chase_config.sequence_id):
            rows[index] = new_row
            updated = True
            break
    if not updated:
        rows.append(new_row)

    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=SINGLE_CHASE_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return Path(path).resolve()


def row_to_config(row):
    return CampaignConfig(
        sequence_id=int(row["sequence_id"]),
        sequence_name=row.get("sequence_name") or "",
        step_2_hold_id=int(row["step_2_hold_id"]),
        step_3_chase_id=int(row["step_3_chase_id"]),
        step_4_hold_id=int(row["step_4_hold_id"]),
        step_5_final_id=int(row["step_5_final_id"]),
        initial_sender_email=row.get("initial_sender_email") or "",
        initial_sender_account_id=int(row["initial_sender_account_id"]),
        initial_sender_label=row.get("initial_sender_label") or "",
        final_sender_email=row.get("final_sender_email") or "",
        final_sender_account_id=int(row["final_sender_account_id"]),
        final_sender_label=row.get("final_sender_label") or "",
        final_pcs_sender_value=row.get("final_pcs_sender_value") or "",
    )


def row_to_single_chase_config(row):
    return SingleChaseConfig(
        sequence_id=int(row["sequence_id"]),
        sequence_name=row.get("sequence_name") or "",
        step_2_hold_id=int(row["step_2_hold_id"]),
        step_3_chase_id=int(row["step_3_chase_id"]),
        sender_email=row.get("sender_email") or "",
        sender_account_id=int(row["sender_account_id"]),
        sender_label=row.get("sender_label") or "",
    )


def config_to_row(campaign_config):
    return {
        "sequence_id": campaign_config.sequence_id,
        "sequence_name": campaign_config.sequence_name,
        "step_2_hold_id": campaign_config.step_2_hold_id,
        "step_3_chase_id": campaign_config.step_3_chase_id,
        "step_4_hold_id": campaign_config.step_4_hold_id,
        "step_5_final_id": campaign_config.step_5_final_id,
        "initial_sender_email": campaign_config.initial_sender_email,
        "initial_sender_account_id": campaign_config.initial_sender_account_id,
        "initial_sender_label": campaign_config.initial_sender_label,
        "final_sender_email": campaign_config.final_sender_email,
        "final_sender_account_id": campaign_config.final_sender_account_id,
        "final_sender_label": campaign_config.final_sender_label,
        "final_pcs_sender_value": campaign_config.final_pcs_sender_value,
    }


def single_chase_config_to_row(single_chase_config):
    return {
        "sequence_id": single_chase_config.sequence_id,
        "sequence_name": single_chase_config.sequence_name,
        "step_2_hold_id": single_chase_config.step_2_hold_id,
        "step_3_chase_id": single_chase_config.step_3_chase_id,
        "sender_email": single_chase_config.sender_email,
        "sender_account_id": single_chase_config.sender_account_id,
        "sender_label": single_chase_config.sender_label,
    }


def default_campaign_config(sequence_id):
    return CampaignConfig(
        sequence_id=int(sequence_id),
        sequence_name="",
        step_2_hold_id=config.STEPS["chase1_hold"],
        step_3_chase_id=config.STEPS["chase1_email"],
        step_4_hold_id=config.STEPS["chase2_hold"],
        step_5_final_id=config.STEPS["chase2_email"],
        initial_sender_email="",
        initial_sender_account_id=config.EMAIL_ACCOUNTS["frankie"],
        initial_sender_label="Frankie",
        final_sender_email="",
        final_sender_account_id=config.EMAIL_ACCOUNTS["prab"],
        final_sender_label="Prab",
        final_pcs_sender_value=config.SENDER_VALUES["prab"],
    )


def ordered_sequence_steps(steps):
    by_parent = {}
    for step in steps:
        by_parent.setdefault(step.get("parentId"), []).append(step)

    ordered = []
    current = only_child(by_parent, None, "root")
    while current:
        ordered.append(current)
        children = by_parent.get(current.get("id"), [])
        if len(children) > 1:
            raise RuntimeError(f"Step {current.get('id')} has {len(children)} children. Expected a linear sequence.")
        current = children[0] if children else None

    if len(ordered) != len(steps):
        raise RuntimeError("Could not reconstruct a single linear 5-step sequence.")
    return ordered


def only_child(by_parent, parent_id, label):
    children = by_parent.get(parent_id, [])
    if len(children) != 1:
        raise RuntimeError(f"Expected one {label} step, found {len(children)}.")
    return children[0]


def validate_pcs_step_pattern(ordered_steps):
    expected_types = ["email", "task", "email", "task", "email"]
    if len(ordered_steps) != len(expected_types):
        raise RuntimeError(f"Expected exactly {len(expected_types)} sequence steps, found {len(ordered_steps)}.")
    actual_types = [step.get("type") for step in ordered_steps]
    if actual_types != expected_types:
        raise RuntimeError(f"Expected step pattern {expected_types}, found {actual_types}.")


def validate_single_chase_step_pattern(ordered_steps):
    expected_types = ["email", "task", "email"]
    if len(ordered_steps) != len(expected_types):
        raise RuntimeError(f"Expected exactly {len(expected_types)} sequence steps, found {len(ordered_steps)}.")
    actual_types = [step.get("type") for step in ordered_steps]
    if actual_types != expected_types:
        raise RuntimeError(f"Expected step pattern {expected_types}, found {actual_types}.")
