# Port reply.io-email-sender multi-sender logic into src/outreach/reply/

## Context

`BUILD_PLAN.md:15` requires porting the existing `reply.io-email-sender` repo's Reply
client, sequence-shape validation, sender validation, step movement, and audit-workbook
behavior into `src/outreach/reply/`. Today `cli.py`'s `apply()` is a stub that always
raises ("Reply.io apply adapter is not connected yet") — approved decisions sit in SQLite
forever with no way to actually act on them in Reply.io. This port fills that gap while
adapting the legacy repo's *issuer-level* suppression model (keyed on an opaque `PCS
Issuer ID`) onto this project's two explicit suppression scopes (`exact_contact` /
`domain_company`), per `BUILD_PLAN.md:167`'s explicit instruction not to use `PCS Issuer
ID` as an implicit suppression scope. It also ports the production Zapier live-reply
webhook handler (confirmed still active in production) and a suppression-list export for
human/audit use — confirmed the separate `email-campaign-analysis` repo does **not**
consume any suppression-list file (it only reads a raw `Reply.io_Contact_Report.csv`
export and a `positive_response*.xlsx` tracker — verified directly, no "suppress"/CSV
references anywhere in its scripts), so the suppression list is purely this project's own
audit artifact, not a cross-repo contract.

Scope decisions already confirmed with the user:
- Full write path now (not just decision-logic), gated by the existing review/approval flow.
- No `.xlsx` multi-sheet audit workbook in this pass — just a suppression list (CSV, legacy shape).
- Typed step roles + per-step sender aliases added to campaign config.
- Zapier live handler **is** in scope (active in production) — ported faithfully but now persists an audit record instead of being silent.
- Staleness check at apply time hard-fails on ANY sequence-step drift since review, not just a sender change.

## Legacy issuer-blocking precedence to preserve exactly (most safety-critical piece)

Ported from `pcs/campaign.py::find_issuer_blocks`, generalized from "issuer id" to
"whatever `match_key` `decisions.py` resolved for a `domain_company` scope" (a domain, or
an explicitly configured issuer identifier per `BUILD_PLAN.md:102`'s "or an explicitly
configured company/issuer identifier" clause):

1. **Exemptions checked per-contact first, before any blocking check:** (a) email in a
   campaign-configured manual-OOO list → never an automatic blocker; (b) Reply.io's own
   `autoReply` flag → never an automatic blocker, even if Reply.io also reports it as a
   reply.
2. **Automatic detection (if/elif — one reason per contact, only for rows that passed
   step 1):** `replied=True` → blocks, reason "Reply.io response" (highest) — `elif`
   `bounced or opted_out` alone → **never blocks**, continue — `elif` `status=="finished"`
   (and email not in a separate contact-exclusion set) → blocks, reason "Finished for a
   non-bounce, non-opt-out, non-OOO reason" (lowest).
3. **Strip pass:** for any match_key group containing a manual-OOO contact, remove any
   blocker in that group whose reason is specifically the "Finished..." string — but do
   **not** strip a "Reply.io response" blocker in the same group from a different contact.
4. **Manual overrides appended unconditionally, always block, never subject to the strip:**
   booked/responded signals, campaign-configured suppression-domain matches.
5. Every other active (non-exempt) contact sharing a blocked match_key becomes a
   "related row to finish."

Any reimplementation must preserve this exact four-phase order (exempt → detect →
strip-if-manual-ooo → append-unconditional-manual) or suppression could silently misfire.

## New files under `src/outreach/reply/`

- **`__init__.py`** — empty marker.
- **`client.py`** — `ReplyWriteClient(api_key, opener=None)`, following the existing
  `JsonClient`/`opener=` injection convention (see `adapters.py`, `tests/test_gateway.py`)
  so it's testable with the existing `FakeOpener`/`FakeResponse` pattern. Ported from
  `pcs/reply_client.py`:
  - `request(method, path, body=None)` — same retry policy: `URLError` retried up to 3x
    with `1+attempt` backoff; HTTP 429 raises `ReplyRateLimitError` immediately (no
    retry), carrying `Retry-After`.
  - `assert_sequence_safe(sequence_id)`, `get_sequence_contact(sequence_id, contact_id)`,
    `list_sequence_steps(sequence_id)`.
  - `set_sequence_status(sequence_id, contact_ids, status)` — chunked by 100
    (`WRITE_CHUNK_SIZE`), raises `ReplyWriteError` on any per-item failure.
  - `move_contacts_to_step(sequence_id, contact_ids, step_id)` — delete-then-add exactly
    as legacy (`bulk-delete` then `bulk` with `startStepId`/`removeFromExisting=False`),
    raises on any `notProcessed` entries.
  - `update_contact_custom_field(contact_id, field_id, value)` — merges into the
    contact's `customFields` array by field id, matching `merge_custom_field_value`.
  - This module **writes** to Reply.io — it must NOT live in `adapters.py`, whose
    docstring is an explicit "read-only, no vendor writes" invariant. Keep that invariant
    intact; `adapters.py` is unchanged.

- **`issuer_blocking.py`** — ported precedence logic (see section above), operating on a
  new `ReplyContactState` dataclass (`reply_contact_id, email, pcs_issuer_id,
  sequence_status, current_step_id, replied, bounced, opted_out, auto_reply,
  sender_email`):
  - `find_domain_company_blocks(rows, manual_ooo_emails, contact_exclusion_emails) -> Dict`
    — returns blocked match_keys, per-match_key reasons, and related-rows-to-finish.
    Does **not** take `booked_emails`/`responded_emails`/suppression CSVs as params like
    legacy did — those are now Signals feeding `decisions.build_decisions` (see below),
    not inputs to this pure function.
  - `eligible_for_step(rows, blocked_match_keys, match_key_of, required_step_id,
    excluded_emails) -> List[ReplyContactState]` — same predicate as legacy, generalized
    from `pcsIssuerId` to any `match_key_of()` extraction function.

- **`sender_verification.py`** — `verify_senders(reply, sequence_id, contact_ids,
  expected_account_id) -> List[Dict]` (mismatches only). Used before AND after
  `move_contacts_to_step`, matching legacy chase-1's pre+post check and chase-2-send's
  pre-only check.

- **`campaign_steps.py`** — PCS-multi-sender-specific sequence-shape validation, kept
  separate from the generic `config.py` (a future non-5-step campaign shouldn't need
  this): `ordered_sequence_steps(raw_steps)` (ported `parentId`-walk reconstruction,
  raises on non-linear chains), `validate_pcs_step_pattern(ordered)` (asserts the 5-step
  `email/task/email/task/email` shape), `role_step_ids(campaign) -> Dict[str, str]`.

- **`action_dispatch.py`** — `dispatch(reply, campaign, decision) -> Dict` maps a
  `suppression_decisions` row to a concrete Reply.io write:
  - `exact_contact` + `finish` → `set_sequence_status(sequence_id, [contact_id],
    "finished")` — only that contact.
  - `domain_company` + `finish` → `set_sequence_status(sequence_id, all_impacted_ids,
    "finished")` — using the impacted-contact-id list **already stored in the decision's
    `decision_json` at review time**, not re-derived at apply time, so apply acts on
    exactly what was reviewed.
  - `hold_for_review`/`no_change` never reach dispatch (only `approved` decisions are
    processed, and `build_decisions` today only ever proposes `finish` or
    `hold_for_review` — approving a `hold_for_review` decision still executes `finish`,
    since `database.decide()` only flips `status`, never `proposed_action`; this matches
    existing review-flow design and needs no code change).

- **`apply.py`** — `apply_approved_decisions(db, campaign, reply, campaign_slug) ->
  List[ApplyResult]` (`ApplyResult(decision_id, status, reply_action, error)`):
  1. `db.acquire_apply_lock(campaign_slug)` — raises `LockHeldError` if another apply is
     in progress; released in `finally`.
  2. `db.approved_for_campaign(campaign_slug)`.
  3. Per decision:
     a. If `db.reply_action_status(decision_id) == "succeeded"` → skip, status
        `"skipped_already_applied"` (idempotency — never re-calls Reply.io for an
        already-applied decision; belt-and-suspenders with `reply_actions.decision_id
        UNIQUE`).
     b. **Staleness check** (hard-fail per confirmed decision): re-fetch the contact's
        live `sequence_step_id` via `reply.get_sequence_contact` and compare to what's
        stored in `decision_json`; ANY drift → mark `"failed"`, skip the Reply.io call.
        Also re-verify sender via `sender_verification.verify_senders`.
     c. `db.record_reply_action_requested(decision_id, request_json)` — INSERT with
        status `"requested"` BEFORE calling Reply.io, so a crash mid-call is visible.
     d. `action_dispatch.dispatch(...)`.
     e. `db.record_reply_action_result(...)` + `db.set_decision_status(decision_id,
        "applied" | "failed")`.
  4. Return `List[ApplyResult]` for CLI summary printing.

- **`live_reply_handler.py`** — port of `pcs/zapier_handler.py`'s
  `finish_issuer_from_reply` (single-reply real-time reaction), since the Zapier
  automation is confirmed active in production:
  - Same resolution logic: contact lookup by id/email, sequence inference if not given
    (single-active-sequence requirement, else raise), issuer/match-key resolution.
  - Same ignore-guard: bounced-and-not-replied, or OOO status → `{"ignored": True, ...}`,
    no action.
  - Same immediate action: find all other Active contacts sharing the match_key, call
    `reply.set_sequence_status(..., "finished")` — **still bypasses the SQLite
    review/approval queue**, exactly as it does in production today (this is a deliberate
    behavior-preserving port, not a new capability).
  - **New**: unlike legacy (console/no persistence), every invocation writes a
    `source_evidence` row (`source_type="live_reply_webhook"`) and a `reply_actions` row
    (keyed on a synthesized decision-less id, e.g. `f"live:{sequence_id}:{trigger_contact_id}:{timestamp}"`,
    since there's no pre-existing `suppression_decisions` row for an out-of-band webhook
    finish) so this previously-silent mutation now has an audit trail like everything
    else in this system.
  - Entry point mirrors legacy: a thin CLI/script wrapper reading the same env-var
    inputs (`REPLY_CONTACT_EMAIL`/`REPLY_CONTACT_ID`/`PCS_ISSUER_ID`) so the existing
    Zapier Code-by-Zapier step (`scripts/zapier_code_step.js`) keeps working unchanged
    against the new backend.

- **`suppression_export.py`** — `write_suppression_lists(db, campaign_slug, output_dir)`,
  invoked by a new CLI subcommand `python -m outreach export suppression-list --campaign
  <slug>` (on-demand, not auto-run inside `apply`). Generates, from
  `suppression_decisions` rows with `status IN ('applied','approved')`:
  - `output/<slug>/suppression_contacts.csv` — columns `email,reason,notes` (one row per
    `exact_contact` decision).
  - `output/<slug>/suppression_domains.csv` — columns `domain,reason,notes` (one row per
    distinct `domain_company` match_key; if keyed by an issuer identifier rather than a
    literal domain, the `domain` column carries that identifier with a note explaining
    it's an issuer id, not a DNS domain).
  This is a pure audit/reference artifact for human review during the campaign — not
  consumed by the separate analytics codebase (confirmed above).

## Changes to existing files

- **`models.py`**:
  - New `SequenceStep(id, role, expected_sender, sender_aliases: Tuple[str, ...] = ())`
    — `role` is one of `initial | hold | chase | final_hold | final`.
  - `Campaign` gains: `sender_aliases: Dict[str, Tuple[str, ...]]` (step_id → aliases,
    back-compat with existing `expected_senders`), `sequence_steps: Tuple[SequenceStep,
    ...]`, `domain_suppression_domains: Tuple[str, ...] = ()`, `manual_ooo_emails: Tuple[str,
    ...] = ()`, `manual_exclusion_emails: Tuple[str, ...] = ()` — all default to empty so
    existing fixtures/tests that don't set them keep passing unchanged.
  - `Contact` gains, all defaulted: `pcs_issuer_id: Optional[str] = None` (analytics/
    correlation only, per `BUILD_PLAN.md:168` — never used as an implicit suppression
    scope), `sequence_status`, `replied`, `bounced`, `opted_out`, `auto_reply`.

- **`config.py`**:
  - New `VALID_ROLES = {"initial", "hold", "chase", "final_hold", "final"}`.
  - Extend `sequenceSteps` parsing to accept `role` (required) and `senderAliases`
    (optional list, default `[]`), building `sequence_steps`, `sender_aliases`, and the
    existing `expected_senders` (unchanged shape, back-compat) together.
  - Validate: unknown `role` values rejected; exactly one `initial` and one `final` step
    per campaign; a `domain_company` outcome scope requires the campaign to declare
    `suppressionPolicy.domainSuppression.domains` (a list, subdomain-matched) — an
    entirely unconfigured domain rule hard-fails config load rather than silently
    producing runtime holds, per `BUILD_PLAN.md:82`.
  - New optional `suppressionPolicy.manualOverrides: {"outOfOffice": [...], "excluded":
    [...]}` parsed into `Campaign.manual_ooo_emails`/`manual_exclusion_emails`.

- **`decisions.py`** — `build_decisions` gains a new step 0, ahead of the existing
  per-signal loop: compute `domain_company` blocks via
  `reply.issuer_blocking.find_domain_company_blocks`, keyed by `company_key` or a
  configured issuer identifier, using contact-state fields (`bounced`/`opted_out`/
  `replied`/`auto_reply`/`sequence_status`). Produces synthetic decisions for "related
  rows to finish" using the exact same decision-dict/fingerprint construction as today
  (so idempotency-key hashing is unaffected), ahead of AI classification — satisfying
  `BUILD_PLAN.md:108-115`'s precedence tiers 1-4 running before tier 5. Sender-match
  check becomes: `item.sender_email in ({expected_sender} | set(campaign.sender_aliases.get(step_id, ())))`.

- **`database.py`** — new methods: `acquire_apply_lock(campaign_slug, ttl_seconds=900)` /
  `release_apply_lock(campaign_slug, token)` (simple SQLite mutex row — a
  `apply_locks(campaign_slug PK, token, acquired_at, expires_at)` table; appropriate for
  this local-first single-process CLI, not over-engineered into a distributed lock),
  `reply_action_status(decision_id)`, `record_reply_action_requested(decision_id,
  request_json)`, `record_reply_action_result(decision_id, status, response_json)`,
  `set_decision_status(decision_id, status)`, `decision_run_digest(decision_id)` (for
  staleness comparison against the current campaign digest).

- **`migrations/0002_reply_actions_locking.sql`** — new `apply_locks` table only;
  `reply_actions` already exists in `0001_initial.sql` and needs no schema change (just
  new code writing to it). Reply-action status vocabulary (`requested|succeeded|failed`)
  enforced in Python, since SQLite can't add a CHECK constraint to an existing table
  without a rebuild.

- **`cli.py`** — replace the `apply()` stub with a call to
  `reply.apply.apply_approved_decisions(...)`, wrapped to catch `LockHeldError` and print
  a clear message instead of crashing; print a JSON summary of applied/failed/skipped
  counts plus per-decision results. The `run()` function's `mode == "apply"` short-circuit
  behavior (skip loading contacts/signals, go straight to `apply()`) is unchanged — correct
  per `BUILD_PLAN.md:162`, `apply` only processes previously-approved decisions.
  Also add the new `export suppression-list --campaign <slug>` subcommand.

## Mapping legacy CSV manual-override inputs (no CSVs at runtime in this project)

| Legacy CSV | New mechanism |
|---|---|
| `suppression_contacts.csv` (meeting_booked/manual_response) | New Signal outcomes fed from a small git-diffable `config/campaigns/<slug>.manual-signals.json` file, loaded alongside snapshot/live sources in `cli.run()` — flows through the existing `build_decisions` machinery like any other signal, keeping SQLite as the sole approval source of truth. |
| `suppression_domains.csv` (subdomain matching) | `Campaign.domain_suppression_domains` (config field, not an event) — decision-time domain matching, ported subdomain-match logic. |
| `contact_exclusions.csv` (incl. manual-OOO override) | `Campaign.manual_ooo_emails` / `manual_exclusion_emails` (config fields) — feed directly into `find_domain_company_blocks`. |
| RSVP CSVs | Already covered by the existing `event_rsvp` Signal path (no new mechanism). |
| `--booked-email`/`--responded-email` CLI args | Superseded entirely by existing Calendly/Front/Reply signal collection + the review/approval flow. |

## Test strategy (new files)

- **`tests/test_reply_client.py`** — using existing `FakeOpener`/`FakeResponse`: chunking
  by 100, delete-then-add ordering for step moves, raise on `notProcessed`, 429 raises
  immediately with no retry, transient `URLError` retries up to 3x, custom-field merge
  behavior.
- **`tests/test_issuer_blocking.py`** — table-driven precedence tests, including the two
  cases the user specifically wants verified: a Finished-but-manually-OOO contact does
  NOT block its group (manual OOO strips the Finished blocker) while a genuine reply
  DOES block even when that same contact is separately opted out (replied wins in the
  elif chain, checked before opted_out); bounced/opted-out-alone never blocks;
  strip-pass doesn't remove a same-group reply blocker.
- **`tests/test_apply_workflow.py`** — using the existing temp-sqlite-db pattern: apply
  calls Reply.io exactly once for an approved decision; calling apply twice makes ZERO
  additional requests the second time (`"skipped_already_applied"`); concurrent apply
  attempt raises `LockHeldError`; a decision becomes `"failed"` (no Reply.io call) when
  the live sequence step no longer matches what was reviewed; `domain_company` decisions
  finish exactly the impacted-contact-id list stored in `decision_json` (not re-derived);
  `exact_contact` decisions never fan out.
- **`tests/test_config_reply_steps.py`** — role/alias parsing and validation, ported
  `ordered_sequence_steps`/`validate_pcs_step_pattern` behavior.
- **`tests/test_live_reply_handler.py`** — ignore-guard (bounced/OOO → no action),
  same-issuer finish on a genuine reply, single-active-sequence inference/ambiguity
  error, and that every invocation persists a `source_evidence`/`reply_actions` audit
  record.
- Extend **`tests/test_local_workflow.py`** with a domain-suppression-list-matches-
  subdomains case and a manual-override-signals-file case.

## Verification

1. `PYTHONPATH=src python3 -m unittest discover -s tests -v` — full suite green,
   including all new test files above.
2. Manual dry run against a fixture campaign exercising the new step-0 issuer-blocking
   path in `decisions.py`, confirming decisions match the precedence table above.
3. `python -m outreach run --campaign <test> --mode review --snapshot <fixture>` →
   `review approve <id> --reviewer <name>` → `python -m outreach run --campaign <test>
   --mode apply`, using a `FakeOpener`-backed `ReplyWriteClient` in a scripted check (not
   live Reply.io) to confirm the full pipeline end-to-end, then re-run `apply` a second
   time to confirm idempotency (no duplicate Reply.io calls, `"skipped_already_applied"`).
4. Confirm `python -m outreach export suppression-list --campaign <test>` produces the
   two CSVs with the expected columns and content matching applied decisions.
5. Only after the above passes on fixtures: repeat against a live test Reply.io sequence
   per `TEST_PLAN.md`'s existing phased approach (dry_run → review → tightly-scoped
   apply), consistent with the rest of this project's live-validation discipline.

## Explicitly out of scope for this pass

- The `.xlsx` multi-sheet audit workbook (`pcs/workbook.py`) — superseded by the
  suppression-list CSVs plus existing SQLite audit tables for this pass.
- Single-chase (3-step) campaign shape — only the 5-step multi-sender shape is ported,
  matching the specific repo/scope requested.
