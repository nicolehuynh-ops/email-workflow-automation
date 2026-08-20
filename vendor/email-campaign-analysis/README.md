# PCS Email Campaign Analysis

This project turns Reply.io exports and the positive-response tracker into CSV detail files and a lightweight campaign-summary workbook. Each campaign owns its own inputs, outputs, and reconciliation settings.

## Folder structure

- `campaigns/<campaign-slug>/input/` — source exports for one campaign.
- `campaigns/<campaign-slug>/config/issuer-overrides.json` — campaign-specific issuer aliases used to reconcile tracker rows to Reply.io records.
- `campaigns/<campaign-slug>/output/` — refreshed workbook and campaign detail files.
- `campaigns/uncovered-issuer-email-campaign/` — the migrated **Uncovered Issuer Email Campaign** and its existing files.
- `booking-links.json` — local campaign metadata and Calendly booking links; this is Git-ignored.
- `booking-links.example.json` — tracked schema and example configuration.
- `scripts/refresh_campaign_analysis.mjs` — the supported refresh command.

## Refreshing the analysis

1. Replace the files in `campaigns/<campaign-slug>/input/` with the latest exports, keeping these requirements:
   - Reply.io contact export named `Reply.io_Contact_Report.csv`
   - Positive-response tracker named with `positive_response` and saved as `.xlsx`
   - Tracker sheet named `Positive Responses`
2. Review `campaigns/<campaign-slug>/config/issuer-overrides.json`. Add an override when a tracker company/domain differs from the Reply.io issuer/domain. Use a lowercase normalized key with punctuation removed (for example, `kinter.ai` becomes `kinterai`).
3. From the project folder, run the refresh script through the Codex bundled Node runtime:

   ```sh
   ln -sfn /Users/nicole/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules node_modules
   /Users/nicole/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node scripts/refresh_campaign_analysis.mjs uncovered-issuer-email-campaign
   rm node_modules
   ```

The script validates required columns, applies aliases, deduplicates issuer records by issuer × sender × version, exports the detailed CSVs, and recreates the summary workbook inside that campaign's `output/` directory. This avoids writing large detail tabs into Excel during each refresh. Omit the campaign slug to use `uncovered-issuer-email-campaign`.

## Matching rules

The tracker is matched to Reply.io using issuer domain, sender, and A/B version. The sender is taken from the sequence name where available, so the attribution reflects the sequence owner. Entries that cannot be matched remain in the issuer breakdown as `Unmatched` for review.

## Calendly booking tracking

The Calendly API can report booked meetings by reading scheduled events and their invitees. API access lives only in `.env`; campaign metadata and booking links live only in the Git-ignored `booking-links.json`.

Each entry in `booking-links.json` represents a campaign, which can contain multiple sequences, each with multiple Calendly links. Every link must include its UTM parameters. The tracked `booking-links.example.json` documents the schema.

Set only `CALENDLY_ACCESS_TOKEN` and `CALENDLY_ORGANIZATION_URI` in `.env`. Generate the personal access token in **Calendly → Integrations → API & Webhooks**. The organization URI is returned by `GET https://api.calendly.com/users/me` as `current_organization`. Use an owner or admin token for organization-wide reporting; an individual token only covers that user’s events. `.env` is ignored by Git.

The reporting integration will filter active scheduled events by the UTM values contained in each booking URL. Use `reportStart` to set the campaign’s first send date.

Run the Calendly report with:

```sh
/Users/nicole/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node scripts/report_calendly_meetings.mjs uncovered-issuer-email-campaign
```

It writes two files in the campaign `output/` directory: `calendly-meeting-summary.csv`, with counts for upcoming booked, completed, and canceled meetings; and `calendly-meeting-details.csv`, with meeting status and timestamps but no invitee data. An active event whose start time is in the past is classified as completed; canceled events are reported separately.

## Metric definitions

- Contact metrics are calculated from `campaigns/<campaign-slug>/output/contact_breakdown.csv`.
- Click rate excludes opted-out contacts from the click count and denominator.
- Positive response includes any tracked interest, meeting request, or booked/scheduled meeting.
- Meeting outcome is a meeting request or booked/scheduled meeting.
- Issuer-level results are deduplicated at issuer × sender × version and written to `campaigns/<campaign-slug>/output/issuer_breakdown.csv`.
