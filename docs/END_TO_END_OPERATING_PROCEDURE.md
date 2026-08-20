# End-to-End Operating Procedure

This procedure is the required path from a new non-production campaign to one approved Reply.io action and reconciled reporting. It does not enable webhooks, scheduling, production campaigns, or automatic application of decisions.

## 1. Create the non-production campaign

1. In Reply.io, create a clearly named non-production campaign and keep it paused by default.
2. Add only internal/test contacts. Do not add historical bouncebacks or production contacts.
3. Configure the sequence in this order:
   - `initial`: email sent by Nicole.
   - `hold`: non-sending eligibility gate.
   - `chase`: email sent by Nicole.
   - `final_hold`: non-sending eligibility gate.
   - `final`: email sent by Leo.
4. Record the Reply campaign ID, each step ID, and the sender account for each sending step in the private test ledger.

## 2. Create the test inbox and Calendly scope

1. Create or designate one Front inbox for this campaign. The current test inbox is `Emails-Test-Ops` (`inb_eugm4`).
2. Create or designate one Calendly test event type. Only bookings for that event type and after the reporting start time are in scope.
3. Do not enable Front sends, webhooks, scheduled jobs, or any production campaign.

## 3. Discover and confirm the Front inbox

This is a configuration gate for every campaign. It is read-only.

1. Confirm the Front token has the `inboxes:read` scope.
2. List the inboxes that the token can access:

   ```sh
   GET https://api2.frontapp.com/inboxes
   ```

3. Choose exactly one returned `id`. Confirm its display name in Front before recording it.
4. Put that same ID in both `front.inboxIds` and `front.confirmedInboxId` in the ignored local campaign configuration.
5. Add the required acknowledgement in `front.confirmation`.

The application refuses a configuration with zero or multiple inboxes, a mismatched confirmation, or a missing acknowledgement. During a live read it lists conversations only through the confirmed inbox, then independently calls the conversation-inboxes endpoint and fetches messages only when that endpoint proves membership in the confirmed inbox. Any future Front mutation must make the same check and fails closed when membership cannot be proven.

## 4. Configure the local campaign

1. Copy the appropriate tracked example to an ignored `.local.json` configuration. For the seven-user test use:

   ```sh
   cp 'config/campaigns/Seven-User Classification Test - 2026-08-20/campaign.local.json.example' \
      'config/campaigns/Seven-User Classification Test - 2026-08-20/campaign.local.json'
   ```

2. Replace all Reply, sender, Front, and Calendly placeholders. Never commit these values if they include customer data or vendor identifiers.
3. Keep `domainSuppression.domains` empty for the seven-user test. Its reply and meeting outcomes are exact-contact only.
4. Confirm `classification.gatewayInputScope`. Leave all three unmapped allowlists empty by default and set a conservative `maxMessagesPerRun`. A mapped Reply contact needs no allowlist. For a forwarded or same-firm message, add only its exact Front conversation ID, exact message ID, or exact author email; wildcards are forbidden.
5. Store Reply.io, Front, Calendly, and Cloudflare Access credentials only in `.env`.

## 5. Populate and record the seven-user scenarios

1. Add the seven contacts to Reply.io with the companies defined in [the seven-user test matrix](../config/campaigns/Seven-User%20Classification%20Test%20-%202026-08-20/SCENARIO_MATRIX.md).
2. Keep every contact `active` in the `initial` step before its scenario begins.
3. Record contact email, Reply contact ID, company, sender, and expected response in the Git-ignored ledger.
4. Historical bouncebacks, forwarded messages, and messages from non-campaign contacts may be classified when they are in the confirmed inbox. They remain unmapped evidence and must never lead to an exact-contact Reply decision or action.

## 6. Verify credentials and readiness

1. Run the automated suite:

   ```sh
   PYTHONPATH=src python3 -m unittest discover -s tests -v
   ```

2. Run the campaign preflight:

   ```sh
   PYTHONPATH=src python3 -m outreach preflight --campaign seven-user-classification-test
   ```

3. Run the read-only gateway model check:

   ```sh
   PYTHONPATH=src python3 -m outreach gateway check
   ```

4. Stop if any command fails. Correct credentials, gateway model/route, or configuration first; do not bypass a failed gate.

## 7. Validate each vendor read independently

1. Reply.io: compare campaign contact count, active step, Reply status, reply flags, and sending account with the Reply UI.
2. Front: verify that every inbound, in-window message from the confirmed inbox becomes evidence. Confirm `matched_reply_contact` accurately records correlation. Only mapped contacts and exact campaign allowlists may be sent to the gateway; other messages must be `unclear/skipped_scope`. Unmapped evidence creates no exact-contact decision, and raw body text is absent from SQLite and exported artifacts.
3. Calendly: create one qualifying test booking and one out-of-scope booking. Confirm only the configured event type and reporting window generate `meeting_booked` evidence.
4. Gateway batching: confirm eligible Front messages are split according to `AI_GATEWAY_BATCH_SIZE`, with no more than one completion request per batch. Confirm the configured pause occurs between batches.
5. Gateway resilience: simulate or fixture-test a rate limit, timeout, malformed batch, and permanent authentication/configuration error. Transient failures must honor bounded retry/backoff; permanent errors must not retry; exhausted batches must become review-only `unclear` evidence.
6. Gateway audit: for every classified Front message, confirm SQLite retains its batch ID, opaque item ID, batch size, response ID, model, attempts, and status. For failures, confirm only bounded HTTP status, error code, and Cloudflare Ray ID are retained. Confirm raw bodies and credentials are absent.
7. Gateway input scope: include one mapped contact, one exact-allowlisted forwarded conversation, and one unrelated inbox message. Only the first two may appear in a gateway request. Then fixture more eligible messages than `maxMessagesPerRun`; require zero gateway requests and `skipped_run_limit` for the full eligible set.

## 8. Run live review

1. Run:

   ```sh
   PYTHONPATH=src python3 -m outreach run \
     --campaign seven-user-classification-test \
     --mode review \
     --live
   ```

2. Record the run ID and configuration digest in the private ledger.
3. Compare every decision with the matrix: evidence source ID, sender, label, confidence, scope, and proposed action.
4. Compare every Front evidence row with its gateway input-scope and batch audit fields. A missing scope basis, missing result, duplicated item ID, or result ID that was not requested must invalidate the affected processing and leave it review-only.
5. Required outcomes include: exact-contact isolation for the two Harbor contacts; no User 7 decision before a booking; `hold_for_review` rather than `finish` for User 3's conflicting OOO-plus-unsubscribe evidence; and `hold_for_review` for User 4's unclear reply.
6. A cross-contact fan-out, an OOO `finish`, a bounceback mapped to a campaign contact, or a mismatched batch result is a blocking defect. Fix code or configuration and begin a new review run; never edit SQLite rows directly.

## 9. Approve and apply one decision

1. After all prior cases pass, create the qualifying User 7 Calendly booking.
2. Approve only User 7's reviewed `meeting_booked` exact-contact decision. Record an approval note and Reply state before applying.
3. Run:

   ```sh
   PYTHONPATH=src python3 -m outreach apply --campaign seven-user-classification-test
   ```

4. Confirm only the reviewed contact was finished and the Reply action record succeeded.
5. Run the same command again. It must return `skipped_already_applied` and cause no additional Reply mutation.

## 10. Export and reconcile analytics

1. Generate suppression and analytics-input exports for the accepted run.
2. Run the vendored analytics refresh.
3. Reconcile the contact, sender, step, source, outcome, and evidence identity against the ledger, including positive-response and meeting metrics.
4. Retain the run ID, configuration digest, sanitized outputs, approval note, Reply before/after proof, and report artifacts outside Git.

## 11. Exit criteria

Do not progress to production, webhooks, scheduling, or automatic actions until all of the following are true:

- Automated tests pass.
- All configuration, credential, and gateway checks pass.
- The Front inbox boundary was confirmed and every processed conversation was proven to belong to it.
- The seven-user review matched the expected outcomes without blocking defects.
- User 7 applied exactly once and the repeat apply was skipped.
- Reporting outputs reconcile with the private ledger.
