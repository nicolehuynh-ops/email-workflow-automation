# Pre-Live Test Plan

## Purpose and safety boundary

Validate the local workflow before it connects to a real Reply.io campaign. All test phases before **Live Review** use fixtures, fake HTTP transports, or non-sensitive test data. Apply is implemented but must not be used with live credentials until the gates below pass.

## Entry criteria

Before any live review run:

- A named test campaign has a confirmed Reply campaign ID, expected sender email for every active sequence step, one confirmed Front inbox ID, and Calendly event-type/reporting filters.
- `.env` exists locally, is ignored by Git, and contains valid Reply, Front, Calendly, and Cloudflare Access credentials.
- The selected Hiive `AI_GATEWAY_MODEL` (`openai/gpt-5.6-luna`) appears in `GET /compat/models` for the authenticated user.
- No credentials, raw reply bodies, or real contact exports are committed to the repository.
- The selected campaign is a non-production/test sequence with a narrowly documented contact scope.

## Test phases

| Phase | Test | Expected result | Gate |
| --- | --- | --- | --- |
| 1. Regression | Run `PYTHONPATH=src python3 -m unittest discover -s tests -v`. | All unit and fake-transport tests pass. | Required before every code change is tested. |
| 2. Configuration | Load the test campaign config with one expected sender per active step, both suppression scopes, Front inboxes, and Calendly filters. Test invalid sender, invalid scope, and invalid confidence threshold. | Valid config loads; invalid config fails before source access. | Required before vendor credentials are used. |
| 3. SQLite/apply | Run a fixture `dry_run`, `review`, approval, and fake-transport `apply`. Inspect `data/outreach.db`. | Migrations work; review rows record reviewer/note; apply is exactly-once, lock-protected, and records request/result rows. | Required before live review. |
| 4. Reply read | Use a small non-production/test Reply campaign. Confirm contact count, current step, reply flag, and resolved sender account against the Reply UI. | Every sampled contact matches Reply UI; no API write route is invoked. | Required before Front reconciliation. |
| 5. Front read | Use the confirmed inbox with known test or non-sensitive conversations. Check inbox-membership verification, inbound-only filtering, contact correlation, Unix/ISO timestamp filtering, an exact-allowlisted forwarded message, and an unrelated sender. | Every inbound, in-window message from a proven inbox conversation becomes evidence. Only mapped contacts or exact author/conversation/message allowlists are sent to the gateway; unrelated messages become `unclear/skipped_scope`. Unix timestamps are normalized to UTC ISO; invalid or out-of-window timestamps fail closed. Unmapped senders cannot create exact-contact decisions; missing-proof conversations are ignored; raw body is absent from SQLite. | Required before classifier use. |
| 6. Calendly read | Use a known test booking and a booking outside the configured event type/time window. | Only qualifying invitees produce `meeting_booked` evidence. | Required before live decision review. |
| 7. Hiive gateway | Authenticate with `cloudflared`; validate `/compat/models`; classify multiple non-sensitive sample replies with `openai/gpt-5.6-luna`; test mapped-or-exactly-allowlisted input scoping, atomic `maxMessagesPerRun`, batch splitting/pacing, exact item-ID correlation, HTTP 429 `Retry-After`, exponential backoff, timeout, malformed/partial/duplicate batch output, and permanent HTTP errors using fake transport. | No unrelated inbox body reaches the gateway. Exceeding the run ceiling sends no partial batch. Otherwise, one completion is sent per bounded batch and every input ID has exactly one valid result; transient/model-output failures retry within limits; permanent failures do not retry; exhausted batches become review-only `unclear`. Each message retains sanitized scope basis, batch/item/response IDs, model, attempts, status, and bounded error metadata. | Required before real reply classification. |
| 8. End-to-end review | Run `--mode review --live` against the small test campaign. Compare every generated evidence record and decision with Reply, Front, Calendly, and expected sender configuration. | Every decision is explainable, sender-attributed, and stored as `pending_review`; no Reply mutation occurs. | Required before live apply. |
| 9. Scoped live apply | Approve one deterministic decision in the test campaign, record before-state evidence, then run `apply` once. | The contact is finished once; campaign digest, step, and sender checks pass; `reply_actions` records the request/result. | Required before exports, webhooks, or schedules are enabled. |

## Required scenario coverage

- Exact-contact suppression: a qualifying reply or meeting finishes only that contact in the proposed decision set.
- Domain/company suppression: a configured event RSVP/domain signal affects every matching campaign contact and no unrelated domain.
- Sender mismatch: an otherwise qualifying signal produces `hold_for_review`.
- Low-confidence/unclear classification: produces `hold_for_review`.
- Out-of-office, bounce, opt-out, unknown contact, duplicate source event, and conflicting Front/Calendly signals: remain auditable and do not create an unsafe automatic action. An `out_of_office` classifier label is always `hold_for_review`, even at high confidence.
- Gateway input scope: an unrelated message in the confirmed inbox is never sufficient authorization to send its body to the gateway. Only a mapped Reply contact or an exact campaign allowlist qualifies; exceeding `maxMessagesPerRun` sends none of the eligible messages.
- Expired/missing vendor credentials, Cloudflare Access denial, unavailable AI model, pagination, vendor timeout, rate limiting, and malformed or partially correlated gateway batches: fail safely without partial decisions or Reply writes.

## Live-review procedure

1. Record the test campaign’s expected contact count and a small set of known Reply/Front/Calendly outcomes.
2. Run the automated suite.
3. Exercise fixture apply with a fake Reply transport. Run it twice and confirm the second result is `skipped_already_applied` with no second vendor write.
4. Run:

   ```sh
   PYTHONPATH=src python3 -m outreach run \
     --campaign <test-campaign-slug> \
     --mode review \
     --live
   ```

5. Use `review list` and SQLite queries to compare the proposed decisions against the four source systems. Verify at least one contact for every configured suppression type and classification outcome present in the test data.
6. Record discrepancies by run ID. Correct configuration or adapter behavior, then rerun from a fresh review run; never edit SQLite decision rows manually.

For the dedicated seven-user classification acceptance, follow [the seven-user matrix](../config/campaigns/Seven-User%20Classification%20Test%20-%202026-08-20/SCENARIO_MATRIX.md). It uses exact-contact outcomes only and reserves the qualifying Calendly booking as the single live-apply candidate.

## Live-apply exit criteria

The workflow is ready for a tightly scoped test-campaign apply only when:

- All automated tests pass.
- The live-review run produces the expected contacts, evidence, sender attribution, and suppression scope.
- No raw Front body or secret appears in SQLite, logs, config, Git status, or generated report.
- An operator signs off on the reviewed run ID and documented discrepancies are resolved.

## Automation boundary

Zapier/webhook handlers and scheduled execution remain disabled. Enable neither until the scoped live apply passes, analytics inputs reconcile with the vendored report, and the owner explicitly approves a separate automation rollout. Scheduled runs must only apply approved, non-stale decisions; a scheduler is never allowed to bypass the review queue.

## Evidence to retain

- Test date, operator, commit identifier, campaign configuration digest, and run ID.
- Test campaign/contact sample list (stored outside Git if it contains personal data).
- Sanitized CLI output and SQLite decision export.
- Before/after Reply screenshots or exports showing no sequence-state changes.
- Gateway model ID and non-sensitive sample classification result.
