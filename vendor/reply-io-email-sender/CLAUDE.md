# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Local Python scripts that drive a Reply.io email campaign with a multi-sender chase flow, plus a Zapier-facing live reply handler. There is no test suite, package manager config, or build step — this is a small operational toolkit run directly with `python3`.

## Commands

Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Syntax/compile check (closest thing to a "build" — there is no test suite):

```bash
python3 -m py_compile pcs/*.py scripts/*.py
```

Run a script (each is also runnable via VS Code's Run/Debug dropdown, see `.vscode/launch.json`):

```bash
python3 scripts/<script_name>.py
python3 scripts/<script_name>.py --sequence-id 1734195   # non-interactive
```

All scripts add the project root to `sys.path` themselves (see the `sys.path.insert` line at the top of each `scripts/*.py` file), so they must be invoked with `python3 scripts/foo.py` from the repo root — they are not an installed package.

Required setup before running anything: create `.env` (copy from `.env.example`) with `REPLY_IO_API_KEY`. `pcs/env.py` loads it and raises immediately if missing.

## Architecture

### Two parallel campaign shapes, same engine

The codebase supports two Reply.io sequence shapes that share the same eligibility/issuer-blocking engine (`pcs/campaign.py`) but have separate config CSVs, dataclasses, and driver scripts:

- **Multi-sender (5-step)**: `email -> task -> email -> task -> email`, where the final email step uses a *different* sender than the first two. Config lives in `campaign_configs.csv` (`CampaignConfig` in `pcs/campaign_config.py`). Driven by `scripts/chase_1_prep_send.py`, `scripts/chase_2_prep.py`, `scripts/chase_2_send.py`.
- **Single-chase (3-step)**: `email -> task -> email`, one sender throughout. Config lives in `single_chase_campaign_configs.csv` (`SingleChaseConfig`). Driven by `scripts/single_chase_prep_send.py`.

Both are configured once per Reply.io sequence via `scripts/configure_campaign.py` / `scripts/configure_single_chase_campaign.py`, which call Reply.io, reconstruct step order from each step's `parentId` (not Reply.io's display order — see `ordered_sequence_steps` in `pcs/campaign_config.py`), validate the expected step-type pattern, resolve sender emails to Reply.io account IDs, and upsert a row into the relevant CSV. Chase scripts refuse to run for a sequence not already present in its CSV — this is intentional, to prevent using step/sender IDs from a different sequence.

### Core modules (`pcs/`)

- `config.py` — static IDs (steps, email accounts, custom field IDs), env-overridable via `int_env`, plus `PROJECT_ROOT`, `OUTPUT_DIR`, paging/chunk sizes. Treat this as the source of truth for Reply.io numeric IDs.
- `env.py` — hand-rolled `.env` loader (no `python-dotenv` dependency); fails loudly if `REPLY_IO_API_KEY` is absent.
- `reply_client.py` — thin wrapper over Reply.io's v3 REST API using only `urllib` (no `requests` dependency). Notable behavior: `move_contacts_to_step` implements "moving" a contact as bulk-delete-then-bulk-add of sequence contact links with `startStepId`, because Reply.io has no direct step-move endpoint. Raises `RuntimeError` on any non-2xx response or on 429 (rate limit).
- `campaign.py` — the shared eligibility/issuer-blocking engine used by every chase script:
  - `merge_contact_state_and_details` joins sequence-contact state with full contact records (for custom fields).
  - `find_issuer_blocks` computes, from one in-memory snapshot, which contacts are "blockers" (replied, or finished for a non-auto-reply reason) and which other same-issuer contacts must be marked finished as a result.
  - `eligible_for_step` filters to contacts that are active, not replied/bounced/OOO, on the required step, and not in a blocked issuer group.
  - Out-of-office contacts are normalized to status `OutOfOffice` and never block an issuer or count as chase-eligible by themselves (see `is_out_of_office_status`).
- `zapier_handler.py` — `finish_issuer_from_reply` is the live (single-contact-triggered) counterpart to the batch issuer-blocking logic: given a replied contact, it infers the sequence (if not passed) via `infer_single_active_sequence_id`, looks up `PCS Issuer ID`, and marks all other active same-issuer contacts finished immediately. Exposed to Zapier via `zapier_entry`, and mirrored as plain JS in `scripts/zapier_code_step.js` for pasting into a Zapier "Code by Zapier" step.
- `workbook.py` — writes every run's audit trail as an `.xlsx` to `runs/` (`Summary`, `Eligible for Push`, `Marked Finished`, and `Blocked` when applicable). Every chase script ends by calling `write_run_workbook`, even on failure paths (e.g. sender-verification failures still produce a workbook before raising).
- `campaign_config.py` — CSV read/write and step-chain validation, described above.
- `cli.py` — `prompt_sequence_id` implements the shared precedence: `--sequence-id` flag > `REPLY_SEQUENCE_ID` env var > interactive prompt.

### Key invariants scripts rely on

- **Snapshot-based decisions**: each chase script pulls contacts/state exactly once per run and makes all eligibility/blocking decisions from that snapshot. Reply.io is known to lag after writes, so scripts deliberately do not re-pull after marking contacts finished mid-run.
- **Sender verification brackets step movement**: for multi-sender chases, a candidate's current `emailAccountId` is checked via the Reply.io API both immediately before and after moving it to the next step. If any pre-move check fails, *no* contacts are moved that run (all-or-nothing per batch) — see `scripts/chase_1_prep_send.py` and `scripts/chase_2_send.py`.
- **Final sender assignment is manual**: Chase 2's sender change is done by hand in the Reply.io UI (API assignment during step movement proved unreliable in testing) and only verified, not performed, by `chase_2_send.py`.
- **Missing `PCS Issuer ID` is a hard stop**: any chase script that finds contacts missing this custom field raises immediately rather than guessing at issuer suppression.

For the full operational runbook (day-by-day sequence, exact eligibility rules per script, troubleshooting steps), see `README.md` — it is kept detailed and should be treated as authoritative for *behavioral* questions; this file is oriented at code architecture.
