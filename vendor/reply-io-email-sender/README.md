# PCS Multi Sender Reply.io Scripts

Local Python scripts for running a Reply.io campaign where:

- Step 1 is the initial email from the initial sender.
- Step 2 is a manual task hold.
- Step 3 is Chase 1 from the same initial sender.
- Step 4 is a manual task hold.
- Step 5 is the final chase from a different final sender.

The scripts also enforce issuer-level suppression: if any contact for an issuer replies or is marked finished for a non-out-of-office reason, every other active contact in that same issuer group is marked finished before more chase emails are sent. Pure out-of-office contacts are not treated as issuer blockers and are not pushed to chase steps.

## Files

- `scripts/multi_sender/configure_campaign.py`  
  Creates or updates the multi-sender campaign row in `configs/multi_sender_campaign_configs.csv`.

- `scripts/single_chase/configure_campaign.py`  
  Creates or updates the single-chase campaign row in `configs/single_chase_campaign_configs.csv`.

- `scripts/multi_sender/chase_1_prep_send.py`  
  Runs the Chase 1 prep and moves eligible contacts from step 2 to step 3.

- `scripts/multi_sender/chase_2_prep.py`  
  Runs the Chase 2 prep and sets `PCS Sender` for eligible contacts. It does not move contacts to step 5.

- `scripts/multi_sender/chase_2_send.py`  
  Runs after the manual sender update in Reply.io. It verifies final sender assignment, then moves eligible contacts from step 4 to step 5.

- `scripts/single_chase/prep_send.py`  
  Runs cleaning and moves eligible contacts from step 2 to step 3 for a three-step single-chase sequence where the sender does not change.

- `scripts/zapier_finish_issuer.py`  
  Local wrapper for the Zapier/live reply handler.

- `scripts/zapier_code_step.js`  
  Paste-ready JavaScript for Code by Zapier.

- `configs/`  
  Separate campaign config tables for the multi-sender and single-chase workflows.

- `campaigns/<campaign>/`  
  Holds that campaign's suppression lists, response reporting, and `.xlsx` audit workbooks.

## Setup

Create `.env` in this folder:

```env
REPLY_IO_API_KEY=your_reply_api_key_here
CALENDLY_PERSONAL_ACCESS_TOKEN=your_calendly_personal_access_token_here
```

Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

The scripts can be run from VS Code's Run button, Run and Debug dropdown, or the integrated terminal.

### Campaign performance reporting

Run the read-only A/B report on demand to compare all configured sequences whose configured Reply.io campaign name contains `Group A` or `Group B`:

```bash
python3 scripts/campaign_tracking/report_campaign_performance.py \
  --calendly-link "Frankie=https://calendly.com/your-workspace/15-minute-meeting" \
  --calendly-link "Darragh=https://calendly.com/your-workspace/15min"
```

The supplied Calendly links limit booking attribution to the campaign senders' event types. The script then attributes a matching invitee email to Group A or Group B using its Reply.io audience membership. The script pulls Reply.io activity history for delivery, open, click, and reply metrics, and creates a fresh timestamped workbook under `campaigns/<campaign>/responses/` on every run. It never changes Reply.io or Calendly.

Only an active Calendly booking or a row in `campaigns/<campaign>/suppression/suppression_contacts.csv` marked `manual_response` or `meeting_booked` counts as a conversion. These outcomes are aggregated by `PCS Issuer ID`, so several contacts from one issuer still create only one conversion. Raw Reply.io replies are listed for review as `Pending human validation` until they are manually confirmed in that list.

To focus the report, repeat `--sequence-id`:

```bash
python3 scripts/campaign_tracking/report_campaign_performance.py \
  --sequence-id 1734195 --sequence-id 1734196 \
  --calendly-link "Frankie=https://calendly.com/your-workspace/15-minute-meeting" \
  --calendly-link "Darragh=https://calendly.com/your-workspace/15min"
```

Each run workbook is saved under its workflow folder in a campaign-specific directory named `<sequence-id>-<campaign-name>`, for example `campaigns/uncovered-issuer-outreach/runs/multi_sender/1736386-pcs-uncovered-issuers-version-a-group-1-frankie/`.

### Campaign folders

`Uncovered Issuer Outreach Campaign` now uses `campaigns/uncovered-issuer-outreach/`. It is the default when `PCS_CAMPAIGN` is unset, so existing commands continue to write to that campaign.

Create a separate folder before working a new campaign:

```bash
python3 scripts/create_campaign_folder.py new-campaign-name
PCS_CAMPAIGN=new-campaign-name python3 scripts/multi_sender/chase_1_prep_send.py --sequence-id 1234567 --dry-run
```

This creates independent sequence configuration, contact/domain/exact-contact exclusion lists, responses, and run folders. Use the same `PCS_CAMPAIGN` value with every configuration, chase, or performance-report command for that campaign.

### RSVP exclusions

Place RSVP exports as CSV files containing `email` and/or name fields in `campaigns/<campaign>/suppression/` with `rsvp` in the filename. Chase workflows treat each RSVP match as a contact-level exclusion: an exact email match or a matching first and last name is skipped without suppressing the rest of that issuer. RSVP matches appear in the run workbook's `Blocked` sheet. Before a live run, the preflight lists every match and requires an additional `RSVP` confirmation before it accepts `SEND`.

### Dry runs

All chase scripts support `--dry-run`, for example:

```bash
python3 scripts/multi_sender/chase_1_prep_send.py --sequence-id 1734195 --dry-run
```

A dry run reads Reply.io, applies the full eligibility and sender-validation logic, and writes the audit workbook, but does not mark contacts finished, update custom fields, or move contacts between steps. Workbook actions are labelled `Would ...`.

Live chase runs require a second, deliberate confirmation after a dry-run review:

```bash
python3 scripts/multi_sender/chase_1_prep_send.py --sequence-id 1734195 --confirm-send
```

The script displays the sequence, verified sender/account, proposed advances, proposed finishes, and the checked contact fields. It proceeds only when the operator types `SEND` exactly.

### Manual booking and response inputs

Reply.io-detected replies block the rest of an issuer automatically. To account for a Calendly booking or a reply that has not yet reached Reply.io, pass the contact email when running a chase script:

```bash
python3 scripts/multi_sender/chase_1_prep_send.py --sequence-id 1734195 --dry-run \
  --booked-email booked@example.com \
  --responded-email replied@example.com
```

Each email must be present in the sequence and have `PCS Issuer ID`; its issuer is then blocked and recorded in the workbook. A genuine reply or a manual booking/response always blocks the issuer, even when Reply.io also marks that contact opted out. Bounces and opt-outs by themselves never block an issuer; OOO auto-replies never block an issuer and are never chase eligible.

For multiple manual responses, you can pass a comma-separated list or a Reply export:

```bash
python3 scripts/multi_sender/chase_1_prep_send.py --sequence-id 1734195 --dry-run \
  --responded-emails "person1@example.com, person2@example.com"

python3 scripts/multi_sender/chase_1_prep_send.py --sequence-id 1734195 --dry-run \
  --responded-emails-file /path/to/reply-response-export.csv
```

The CSV must contain an `Email` or `email` column. A plain-text file with one email per line is also supported.

### The three suppression types

Choose the list based on the scope of the signal:

| List | Scope | Use it when | Chase-run effect |
| --- | --- | --- | --- |
| `campaigns/<campaign>/suppression/suppression_contacts.csv` | One contact's issuer | The contact booked a meeting, responded manually, or said they are not interested. | Blocks the issuer and marks other eligible active contacts at that issuer as Finished. |
| `campaigns/<campaign>/suppression/suppression_domains.csv` | A domain and its subdomains | The entire company/issuer must be excluded, including when the response came from a different address. | Every matching in-sequence contact becomes an issuer blocker; their issuer groups are suppressed. |
| `campaigns/<campaign>/suppression/contact_exclusions.csv` | One exact email address | The person is out of office, no longer with the company, or otherwise should not receive a chase while colleagues remain eligible. | Skips that contact only; it does not block the issuer or change other contacts' status. |

### Automatic Reply.io suppression signals

In addition to the three maintained lists, each chase run reads the current Reply.io sequence-contact snapshot. These signals do not need to be added manually:

| Reply.io signal | Effect |
| --- | --- |
| Reply disposition (`isReplied`) | Blocks the issuer, unless the contact is classified as an automatic out-of-office reply or is listed as a manual `out_of_office` exclusion. |
| Sequence status `Finished` | Blocks the issuer for backwards compatibility, unless the contact is bounced, opted out, OOO, or listed in the exact-contact exclusion list. |
| Bounce disposition (`isBounced`) | Does not block the issuer by itself; the bounced contact is not chase eligible. |
| Contact opt-out (`isOptedOut`) | Does not block the issuer by itself; the opted-out contact is not chase eligible. |
| Sequence status `outOfOffice` or automatic-reply flag | Does not block the issuer and is never chase eligible. |

Every chase workflow also reads its selected campaign's `suppression_contacts.csv`. Maintain it with `email`, `reason`, and optional `notes` columns; valid reasons are `meeting_booked` and `manual_response`. Matching contacts are always treated as issuer blockers during a chase run. Entries for contacts that are not in the current sequence are ignored.

For company-wide exclusions where a responder may use a different address, maintain the selected campaign's `suppression_domains.csv`. Any in-sequence email at a listed domain, including its subdomains, blocks that contact's issuer. `netomi.com` is included as the initial general exclusion. Rules in `global_suppression_domains.csv` apply to every campaign; `hiive.com` is included there so Hiive internal users never receive cold outreach.

For a person who must not receive a chase but whose colleagues should remain eligible, maintain the selected campaign's `contact_exclusions.csv`. These exclusions are exact-email only: they do not block the issuer or change anyone's status. An excluded contact's generic `Finished` status is not treated as an issuer blocker. For a manually verified `out_of_office` entry, Reply.io's unreliable `replied` flag is also ignored; an actual reply from another excluded contact still blocks the issuer.

### Bounceback recovery

Use the recovery workflow only for an issuer whose contacts were previously marked Finished because another contact bounced. It is dry-run by default and requires explicit issuer scope:

```bash
python3 scripts/recovery/recover_bounceback_false_finishes.py \
  --sequence-id 1734195 \
  --issuer-id issuer-abc
```

After reviewing the workbook, add `--apply` to restore only eligible false-Finished contacts to Active. Bounced, replied, opted-out, and OOO contacts are never restored.

## Campaign Requirements

The multi-sender flow requires this exact five-step shape:

```text
Step 1: email
Step 2: task hold
Step 3: email
Step 4: task hold
Step 5: email
```

The config script verifies this pattern by reading the sequence step chain from Reply.io. It uses each step's `parentId`, not Reply.io's returned display order.

Each contact should have:

- `PCS Issuer ID` custom field populated.
- Optional `PCS Sender` custom field, which Script 2 will set for final-chase-eligible contacts.

Current custom field IDs are configured in `pcs/config.py`:

- `PCS Sender`: `147786`
- `PCS Issuer ID`: `147787`

The single-chase flow requires this exact three-step shape:

```text
Step 1: email
Step 2: task hold
Step 3: email
```

## Configure A Sequence

Run this once per new Reply.io sequence:

```bash
python3 scripts/multi_sender/configure_campaign.py
```

The script prompts for:

- Reply.io sequence ID
- initial/chase-1 sender email
- final sender email
- sender labels
- final `PCS Sender` value

You can also run it non-interactively:

```bash
python3 scripts/multi_sender/configure_campaign.py --sequence-id 1734195 --initial-sender-email initial-sender@example.com --initial-sender-label "Initial Sender" --final-sender-email final-sender@example.com --final-sender-label "Final Sender" --final-pcs-sender-value "Final"
```

What the config script does:

1. Calls Reply.io for the sequence.
2. Pulls the sequence steps.
3. Reconstructs the true step order using `parentId`.
4. Validates the pattern `email -> task -> email -> task -> email`.
5. Looks up sender account IDs from Reply.io using the provided sender emails.
6. Writes the row to `configs/multi_sender_campaign_configs.csv`.

If a chase script is run for a sequence that is not in `configs/multi_sender_campaign_configs.csv`, it stops with an error. This prevents accidentally using step IDs from another sequence.

## Configure A Single-Chase Sequence

Run this once per new three-step Reply.io sequence:

```bash
python3 scripts/single_chase/configure_campaign.py
```

The script prompts for:

- Reply.io sequence ID
- sender email
- sender label

You can also run it non-interactively:

```bash
python3 scripts/single_chase/configure_campaign.py --sequence-id 1234567 --sender-email sender@example.com --sender-label "Campaign Sender"
```

What the single-chase config script does:

1. Calls Reply.io for the sequence.
2. Pulls the sequence steps.
3. Reconstructs the true step order using `parentId`.
4. Validates the pattern `email -> task -> email`.
5. Looks up the sender account ID from Reply.io using the provided sender email.
6. Writes the row to `configs/single_chase_campaign_configs.csv`.

If `scripts/single_chase/prep_send.py` is run for a sequence that is not in `configs/single_chase_campaign_configs.csv`, it stops with an error.

## Runbook

### Day 1: Initial Send

This is done by Reply.io. No script is needed.

Make sure contacts have `PCS Issuer ID` populated before the sequence starts.

### Day 3: Chase 1

Run:

```bash
python3 scripts/multi_sender/chase_1_prep_send.py
```

Or:

```bash
python3 scripts/multi_sender/chase_1_prep_send.py --sequence-id 1734195
```

The script:

1. Loads campaign config from `configs/multi_sender_campaign_configs.csv`.
2. Pulls current contacts from Reply.io's clean sequence contacts endpoint.
3. Pulls contact details so it can read `PCS Issuer ID`.
4. Finds any contacts that replied or are finished for a non-out-of-office reason.
5. Finds all same-issuer active contacts and marks them finished.
6. Finds eligible contacts:
   - has `PCS Issuer ID`
   - issuer is not blocked
   - status is `Active`
   - not replied
   - not bounced
   - not opted out
   - not out of office
   - currently on step 2
   - assigned to the configured initial/chase-1 sender
7. Moves eligible contacts to step 3.
8. Verifies moved contacts are still assigned to the configured initial/chase-1 sender.
9. Writes a workbook to `campaigns/<campaign>/runs/multi_sender/`.

If any candidate is assigned to the wrong sender, no contacts are moved.

## Single-Chase Runbook

Use this for campaigns with only one chase and no sender change:

```text
Step 1: initial email
Step 2: task hold
Step 3: chase email
```

Configure the sequence first:

```bash
python3 scripts/single_chase/configure_campaign.py
```

Then run the prep/send script on chase day:

```bash
python3 scripts/single_chase/prep_send.py
```

Or:

```bash
python3 scripts/single_chase/prep_send.py --sequence-id 1234567
```

The script:

1. Loads single-chase campaign config from `configs/single_chase_campaign_configs.csv`.
2. Pulls current contacts from Reply.io's clean sequence contacts endpoint.
3. Pulls contact details so it can read `PCS Issuer ID`.
4. Finds any contacts that replied or are finished for a non-out-of-office reason.
5. Finds all same-issuer active contacts and marks them finished.
6. Finds eligible contacts:
   - has `PCS Issuer ID`
   - issuer is not blocked
   - status is `Active`
   - not replied
   - not bounced
   - not opted out
   - not out of office
   - currently on step 2
   - assigned to the configured sender
7. Moves eligible contacts to step 3.
8. Verifies moved contacts are still assigned to the configured sender.
9. Writes a workbook to `campaigns/<campaign>/runs/single_chase/`.

If any candidate is assigned to the wrong sender, no contacts are moved.

### Day 5 Prep: Chase 2 Prep

Run:

```bash
python3 scripts/multi_sender/chase_2_prep.py
```

Or:

```bash
python3 scripts/multi_sender/chase_2_prep.py --sequence-id 1734195
```

The script:

1. Loads campaign config.
2. Repeats the issuer-blocking logic.
3. Marks same-issuer active contacts finished when any contact in that issuer has replied or finished for a non-out-of-office reason.
4. Finds eligible contacts:
   - has `PCS Issuer ID`
   - issuer is not blocked
   - status is `Active`
   - not replied
   - not bounced
   - not out of office
   - currently on step 4
5. Updates eligible contacts' `PCS Sender` custom field to the configured final value.
6. Writes a workbook to `campaigns/<campaign>/runs/multi_sender/`.

This script does not move contacts to step 5.

### Manual UI Step Before Chase 2 Send

In Reply.io:

1. Filter contacts by the configured `PCS Sender` value, for example `PCS Sender = Prab`.
2. Bulk update those contacts' sending email account to the configured final sender.
3. Confirm the UI shows the correct final sender for those contacts.

This manual step exists because API attempts to force the sender during step movement were not reliable in testing.

### Day 5 Send: Chase 2 Send

Run after the manual UI sender update:

```bash
python3 scripts/multi_sender/chase_2_send.py
```

Or:

```bash
python3 scripts/multi_sender/chase_2_send.py --sequence-id 1734195
```

The script only moves contacts to step 5 if:

- `PCS Sender` matches the configured final sender value.
- contact is currently on step 4.
- contact is active.
- contact has not replied.
- contact has not bounced.
- contact has not opted out.
- contact is not out of office.
- Reply.io says the contact's sender account is the configured final sender.

If any candidate is still assigned to the wrong sender, no contacts are moved.

## Zapier Live Reply Handler

The Zapier handler is meant to stop same-issuer contacts quickly when a reply comes in after the batch script has already queued contacts for a chase.

Local examples:

```bash
REPLY_CONTACT_EMAIL=person@example.com python3 scripts/zapier_finish_issuer.py
REPLY_CONTACT_ID=123456789 python3 scripts/zapier_finish_issuer.py
PCS_ISSUER_ID=issuer-abc python3 scripts/zapier_finish_issuer.py
```

For Zapier Code by Zapier, paste `scripts/zapier_code_step.js` into a JavaScript code step and pass:

- `REPLY_IO_API_KEY`
- `contactEmail`, or `contactId`
- optional `sequenceId`
- optional `pcsIssuerId`

If `sequenceId` is not passed, the handler looks up the contact's Reply.io sequences and uses the single active sequence for that contact. It stops with an error if the contact has zero active sequences or more than one active sequence.

Passing `pcsIssuerId` skips the trigger contact custom-field lookup, but the handler still needs `contactEmail` or `contactId` so it can infer the active sequence when `sequenceId` is omitted.

## Workbook Outputs

Multi-sender runs write to `campaigns/<campaign>/runs/multi_sender/`; single-chase runs write to `campaigns/<campaign>/runs/single_chase/`.

Typical sheets:

- `Summary`
- `Eligible for Push`
- `Marked Finished`
- `Blocked`, when applicable

Use these workbooks as the audit trail for each campaign run.

## How The Issuer Logic Works

### Suppression Rule Hierarchy

Issuer suppression uses this precedence order:

1. A manually supplied booking (`--booked-email`) or response (`--responded-email`) blocks the issuer.
2. A genuine Reply.io reply blocks the issuer, even when Reply.io also marks that responder opted out.
3. An out-of-office auto-reply does not block the issuer, even when Reply.io exposes it as a reply.
4. A bounce alone does not block the issuer.
5. An opt-out alone does not block the issuer.
6. A Finished contact that is not bounced, opted out, or OOO remains a blocker for backwards compatibility.

When an issuer is blocked, other active contacts at that issuer are marked Finished. Bounced, opted-out, OOO, and already Finished contacts are not changed by that issuer-suppression write.

Manual booking and response inputs are validated against the current sequence snapshot: the supplied email must be in the sequence and must have `PCS Issuer ID`.

### Chase Eligibility

A contact can advance to a chase email only when all of the following are true:

- It has `PCS Issuer ID`.
- Its issuer is not blocked.
- Its sequence status is `Active`.
- It has not replied, bounced, opted out, or been marked OOO.
- It is currently at the required hold step.
- Its actual Reply.io sender account matches the configured sender.
- For Chase 2, `PCS Sender` also matches the configured final-sender value.

Before any live workflow write, the script shows the proposed advances and finishes. It requires `--confirm-send` plus typing `SEND` before it changes Reply.io.

The scripts use one in-memory snapshot per run:

1. Pull all sequence contacts.
2. Pull details for custom fields.
3. Identify blocker contacts:
   - `replied = true` unless the contact is OOO/automatic or a manual `out_of_office` exclusion, or
   - status is `Finished` and the contact is not bounced, opted out, OOO, or an exact-contact exclusion
4. Build blocked issuer IDs from those contacts' `PCS Issuer ID`.
5. Mark active contacts from blocked issuers as finished.
6. Determine eligible chase recipients from the original sorted snapshot.

The script intentionally does not re-pull active contacts after marking related contacts finished. Reply.io can lag after writes, so the run's eligibility is calculated from the same in-memory decision set.

Out-of-office handling:

- Reply.io exposes manually or automatically detected out-of-office contacts as sequence status `outOfOffice`.
- The scripts normalize that into workbook status `OutOfOffice` and set the workbook column `Auto Reply / OOO = TRUE`.
- An out-of-office contact does not block the issuer, even if Reply.io also exposes a reply flag.
- That out-of-office contact is also not eligible for a chase push because eligible recipients must still be `Active`.
- Other active contacts with the same `PCS Issuer ID` can still be pushed if no non-OOO blocker exists for that issuer.

## Reply.io Behaviors Observed During Testing

- `/sequences/{id}/contacts/state` can return stale contacts after bulk delete/re-add testing.
- `/sequences/{id}/contacts` was cleaner and is now used by the scripts.
- Step movement is done by removing and re-adding sequence contact links with `startStepId`.
- Reply.io processing can lag by a few minutes after sends, replies, or step transitions.
- Reply.io can expose out-of-office status directly on sequence contacts as `outOfOffice`; the scripts use that status instead of doing slow activity-history scans.
- Direct API sender assignment during move was unreliable, so final sender changes are handled manually in the UI and verified by API before step 5 movement.
- Reply.io opt-out detection can be contact-level and may be triggered by Reply.io's internal processing, not these scripts.

## Troubleshooting

### `No campaign config found`

Run:

```bash
python3 scripts/multi_sender/configure_campaign.py
```

Then rerun the chase script.

### `Found contacts missing PCS Issuer ID`

Fix the upload/custom field data in Reply.io before running production chase scripts. Missing issuer IDs are blocked because issuer-level suppression cannot be guaranteed without them.

### Sender verification failed

For Chase 1, confirm the contact's sender is the configured initial sender.

For Chase 2 Send, filter by `PCS Sender` in Reply.io and bulk update sender to the configured final sender, then rerun `chase_2_send.py`.

### Contact replied but still appears eligible

Wait a few minutes and rerun the dry/read check or script. Reply.io reply processing can lag.

### Contact is unexpectedly opted out

Check the contact's activity history in Reply.io. If the activity type is `OptingOut` with `sourceType: NewProcessingFlow`, that was Reply.io internal processing, not these scripts.

## Developer Notes

Core modules:

- `pcs/reply_client.py`: Reply.io API wrapper.
- `pcs/campaign.py`: eligibility and issuer-blocking helpers.
- `pcs/campaign_config.py`: CSV campaign config loading and step-chain detection.
- `pcs/workbook.py`: Excel workbook output.
- `pcs/zapier_handler.py`: live issuer finish logic.

Validation:

```bash
python3 -m py_compile pcs/*.py scripts/*.py scripts/multi_sender/*.py scripts/single_chase/*.py
```
