# DEMS Audit Log & Chain-of-Custody — Content Validation Design

**Date:** 2026-07-02
**Status:** Draft (awaiting review)
**Implementation target:** `dems-qa` repo (https://github.com/YuLin614/dems-qa) — pytest-bdd API layer (`steps/api/`)
**Author:** brainstormed with Julien Brunet

---

## 1. Problem & Goal

The DEMS Audit Log and Chain-of-Custody (CoC) feature is under-validated in automated
tests, and its highest-value output — a downloadable CoC PDF/CSV whose **contents** are
correct — has **zero** running coverage today.

Current state in `dems-qa`:
- `steps/api/audit_steps.py` binds to `features/audit/chain-of-custody.feature`, **which does
  not exist**. In pytest-bdd, `scenarios()` against a missing file fails at collection, so the
  entire `steps/api/` layer is effectively dead (step definitions exist, no `.feature` binds them).
  `steps/api/record_steps.py` likewise points at a missing `features/evidence/upload-evidence.feature`.
- The only audit coverage that runs is `features/integration/rms-audit.feature` (Playwright/UI):
  "audit tab shows ≥1 entry" and "full audit log opens." No label validation, no allow/deny,
  no CoC export.

**Goal:** Build automated coverage that, for every file-lifecycle feature, proves the capability
**works as expected AND is logged correctly**, then proves the CoC export **content** captures
that trail accurately — across multiple users at multiple permission levels, including denied
attempts.

## 2. Guiding principles

1. **Two-axis assertion per action.** Each action verifies both:
   - **Functional** — the capability produced the expected state (after `lock`, the file is
     genuinely private; after `download`, bytes were received).
   - **Audit** — the corresponding audit row exists with the correct verb, actor, role,
     outcome, and category.
   This decouples two real failure modes: *works-but-not-logged* (an audit gap) and
   *logged-but-doesn't-work* (the log lies).
2. **CoC is the integration proof.** Inline steps self-verify each event; the final CoC export
   then proves those verified events were all captured, labeled, and ordered correctly. A row
   that passed inline but is missing from the CoC isolates the bug to the audit/CoC pipeline.
3. **Content, not download.** The PDF download mechanism is already tested and is out of scope.
   This effort validates the **rendered contents**.
4. **Right layer for the job.** Content parsing uses Python (`pdfplumber` for PDF, stdlib `csv`
   for CSV) in the API/pytest-bdd layer. Browser/Playwright is not used here.

## 3. Taxonomy reference (from source)

- Action taxonomy: `AuditActions` StrEnum, dynamically built in
  `common/dems_common/permissions/catalog.py:689` — ~28 permission-gated verbs +
  ~40 audit-only "consequence" verbs.
- No `status` column on `audit_log`. Allowed/denied is derived from `http_response_code`:
  `{401,403}` → `ActionCategory.DENIED`.
- Display categories: `ActionCategory` = `INGEST | ACCESS | CUSTODY | DENIED`
  (`common/dems_common/types/auth_types.py`), mapped by `ACTION_CATEGORY_MAP`.
- CoC report row fields: `timestamp, action(event_code), action_category, user_name,
  actor_role, detail_text, outcome, ip`. CSV columns:
  `Timestamp, Action, Category, User, Role, Description, Outcome` (+`File ID` for case-level).
- Outcome = "Success" if 2xx else "Failure" (`chain_of_custody_generator.py`).
- CoC endpoints (both require `AUDIT_LOGS:VIEW` + `AUDIT_LOGS:DECRYPT`, `?format=pdf|csv`,
  non-blank `X-Download-Reason` header):
  - `GET /api/v1/audit/chain-of-custody/files/<file_id>/export`
  - `GET /api/v1/audit/chain-of-custody/cases/<case_id>/export`
- Audit read: `GET /api/v1/audit/logs?file_id=<id>` (`AUDIT_LOGS:VIEW`), rows carry `action`,
  `http_response_code`, derived `actor_type`.

## 4. Roles & permission profiles (validation actors)

The 6 QA login identities collapse to 5 profiles → 3 archetypes. Source of truth:
`GLOBAL_ROLE_SEED` in `common/dems_common/permissions/catalog.py`; identity mapping in
`common/dems_common/dev_identities.py`.

| Identity | Role | Archetype |
|---|---|---|
| officer1 / officer2 | `standard user` | evidence-allowed (assigned scope), audit-denied |
| sergeant1 | `supervisor` | audit VIEW+DECRYPT, retention, lock (all scope) |
| admin | `agency administrator` | full AGENCY-tier incl. user management |
| iauser | role-less (perms = `[]`) | universal DENY probe → every gated verb 403 |
| sysops1 | `system administration` | SYSTEM plumbing only; every AGENCY verb 403 |

Audit-relevant allow/deny (the crux of permission coverage):

| Verb | standard | supervisor | admin | iauser | sysops1 |
|---|---|---|---|---|---|
| audit VIEW / DECRYPT / CoC export | DENY | ALLOW | ALLOW | DENY | DENY |
| file download / share / lock | ALLOW | ALLOW | ALLOW | DENY | DENY |
| record create | ALLOW | ALLOW | ALLOW | DENY | DENY |
| retention add/remove | DENY | ALLOW | ALLOW | DENY | DENY |
| user management | DENY | DENY | ALLOW | DENY | DENY |

## 5. Architecture — three phases

### Phase A: Event generation (verify-and-log lifecycle)
Orchestrate a scripted lifecycle against one evidence file, each step performed by a specific
role, asserting both axes inline. Each step produces a known-expected audit row.

Evidence file = **`sample.mp4`** (streamable, so `play` works; a PDF would 415 on the stream endpoint).
Endpoint-prefix note: `create`/`view(status)`/`list`/`lock` use `/records/{rid}/files/...`;
`view(POST)`/`play(stream)`/`download`/`get_file` use `/records/files/{fid}/...` (no `rid`).

| # | Actor (role) | Action | Endpoint | Functional check | Audit check (verb · category · outcome) |
|---|---|---|---|---|---|
| 1 | officer1 (standard, owner) | create record | `POST /records` | `record_id` returned | `create` · INGEST · Success |
| 2 | officer1 | upload sample.mp4 | `POST /records/files` → `POST /records/{rid}/files/{fid}` (202) | file in list; `file_status` progresses | `upload_started` (sync) + `upload_completed` (**async — poll**) · INGEST |
| 3 | officer1 | view | `POST /records/files/{fid}/view` (204) | 204 | `view` · ACCESS · Success |
| 4 | officer1 | play/stream | `GET /records/files/{fid}/stream` | 200/206 | `play` · ACCESS · Success (deduped once/session) |
| 5 | officer1 | download | `GET /records/files/{fid}/download` + `X-Download-Reason` | bytes received (len>0) | `download` · ACCESS · Success |
| 6 | officer1 | privatize | `PUT /records/{rid}/files/{fid}/lock-level {lock_level:"private", reason}` | response/metadata `lock.lock_level=="private"` | `file_privatized` · CUSTODY · Success |
| 7 | officer1 | share externally | `POST /records/share/record/{rid}/file {resource_ids, recipients, reason}` (201) | `result.successful_shares` non-empty, `failed_shares` empty | `share` · CUSTODY · Success |
| 8 | officer1 | revoke share | `POST /records/share/record/{rid}/file/{fid}/recipients/{recipient_id}/revoke` (recipient_id from step 7 response) | 200 "Recipient revoked" | `share_revoked` · CUSTODY · Success |
| 9 | officer1 | un-privatize | `PUT .../lock-level {lock_level:null, reason}` | `lock == null` | `file_unprivatized` · CUSTODY · Success |
| 10 | iauser (role-less) | attempt view | `POST /records/files/{fid}/view` | 403; file state unchanged | DENIED · Failure |
| 11 | sysops1 (system) | attempt download | `GET /records/files/{fid}/download` + reason | 403 | DENIED · Failure |
| 12 | officer2 (standard, unassigned) | attempt view | `GET /records/{rid}/files/{fid}/status` | 403/404 (guard: skip if 200 ⇒ officer2==officer1 in env) | DENIED · Failure |
| 13 | officer1 (standard, lacks decrypt) | attempt CoC export | `GET /chain-of-custody/files/{fid}/export?format=pdf` + reason | 403 | DENIED (coc_export) · Failure |

Ordering note: revoke/un-privatize run after their enabling action; the CoC export (Phase B) runs
**before** any destructive delete so the full trail is present. A file `hard_delete` + re-export
(proving the CoC survives from the persisted audit trail) is an optional resilience check, not a
core step.

Dropped from Phase 1 (see §8 findings): **retention** add/remove (emit no audit row — a gap, not a
step), **external-share recipient verbs** `external_share_view/play/download` (token is emailed, not
API-retrievable), **`soft_delete`** (records-only; not a file-scoped verb).

### Phase B: Export
Export the file's CoC as **supervisor** (holds `audit-logs:decrypt`) in both `pdf` and `csv`,
supplying a non-blank `X-Download-Reason`.

### Phase C: Content validation
Parse and assert against the expected-event set built in Phase A:
- Every expected `(action, actor_name, actor_role, outcome, category)` tuple is present.
- Denied attempts render as `Failure` / `DENIED`.
- Allowed actions carry the correct category band.
- Rows are in timestamp order.
- PII (user names, IP) is decrypted (this is the decrypted export).
- **CSV** is the primary structured assertion (deterministic columns); **PDF** (via `pdfplumber`
  text extraction) is the rendered-artifact assertion. Both derive from the same report dict.

## 6. Harness changes (in `dems-qa`)

- **New:** `features/audit/chain-of-custody.feature` — the multi-role verify-and-log scenario(s),
  binding the existing `scenarios(...)` call in `audit_steps.py` so the API layer actually runs.
- **Rewrite:** `steps/api/audit_steps.py` — replace the skip-hatch stubs with paired
  verify-and-log step definitions + Phase B/C export & content assertions. Remove the
  `pytest.skip()` escapes that currently hide the denied-path gap.
- **Reuse:** `steps/api/conftest.py` role-token fixtures (all 6 roles present), `upload_helper.py`.
- **New helper:** a small expected-event model + CoC parser (CSV + `pdfplumber`) — likely
  `steps/api/coc_helper.py` (keep files < 200 lines).
- **Add dependency:** `pdfplumber` to the QA repo's Python deps (`pyproject.toml`).
- **Update:** `STEPS.md` catalogue with the new steps.

## 7. Non-goals

- Re-testing the PDF *download* mechanism (already covered).
- Driving actions through the browser (UI label rendering is a separate dems-ui concern).
- Non-file audit verbs (user management, redaction lifecycle, public-submission lifecycle,
  dead-letters, integrations) — they are audited but do not belong in a *file* CoC. Candidate
  for a later, separate audit-log-matrix effort (Phase 2), not this design.
- Case-level CoC — noted as optional Phase 2 (adds File-ID column + per-file integrity sections).

## 8. Open findings to confirm during implementation

1. **Redacted-export variant may be dead code.** The generator applies redaction for the
   `system administration` role, but per `GLOBAL_ROLE_SEED` that role lacks `audit-logs:decrypt`
   and cannot call the export at all. Determine whether the redaction path is reachable by any
   role; if not, it is dead code / a gap. (A `supervisor`/`admin` export is NOT redacted.)
   **Observed (2026-07-06, local stack):** `sysops1` (`system administration`) lacks
   `audit-logs:decrypt` and gets **403** on the CoC export endpoint — confirming the role can never
   reach the generator, so the `system administration` redaction branch is **unreachable by the
   seeded roles (likely dead code)**. Candidate finding for the DEMS team.
2. **Denied rows may not reach the file CoC (generalized).** A 403 is rejected at auth *before*
   the controller attaches `reference_data.file_ids`, and the CoC file trail is a JSONB containment
   match on that field. So denied attempts (steps 10–13) likely produce audit rows that do **not**
   carry the `file_id` and therefore do **not** appear in the file's CoC. Phase C must empirically
   confirm this: if denied rows are absent from the file-scoped view, that is a documented finding
   (a potential audit-coverage gap), and the denied-row assertions degrade to an all-logs query
   (by actor `user_id` + `action` + recent `timestamp`) rather than a file-scoped one.
   **Observed (2026-07-06, local stack): PARTIAL capture — depends on where the denial fires.**
   `officer2`'s *scope*-denial (a `standard user` denied by visibility scope; 403) **DOES** appear
   in the file CoC as two `view` · `DENIED` · `Failure` rows (records + files). But `iauser`
   (role-less) and `sysops1` (system) *role*-denials, and the decrypt-denied `officer1` CoC-export
   attempt, do **NOT** appear in the file-scoped audit view/CoC — their 403 fires at the permission
   layer before `file_id` reaches `reference_data`. So a chain-of-custody report silently omits
   role-level denied-access attempts on that file. Candidate finding for the DEMS team.
3. **Retention actions emit no audit row.** `agency_retention_controller` PUT (add) / DELETE
   (remove) handlers carry `# TODO add auditing` and never call `add_audit_data`; only the POST
   (set-full) path attaches `reference_data`. So `add_retention`/`remove_retention` are currently
   **unauditable** — recorded here as a gap, and the reason retention is dropped from the lifecycle.
   **Observed (2026-07-06):** not exercised at runtime (retention is out of the file lifecycle);
   remains a source-level gap confirmed by inspection.
4. **Async audit lag.** Audit rows are persisted by a worker off a Redis stream, and
   `upload_completed` fires in the async unquarantine pipeline (not the request). Phase C / the
   inline audit checks must poll/retry (as `record_steps.py::audit_event_exists` already does)
   before asserting a row exists or the trail is complete.
   **Observed (2026-07-06, local stack):** confirmed — the minimal happy-path scenario exported CoC
   immediately and got **404 "File not found in audit logs"**; the rows landed within seconds. Also
   **`upload_completed` never fired locally** (the async unquarantine/finalize pipeline does not
   complete in the local stack), so any assertion expecting it will time out.

## 8a. Live local-stack validation results (2026-07-06)

Validated **without VPN** by running the suite against the **local DEMS stack** (`make start-db-only`
→ `make up-detached` → `make db-migrate`) and minting role tokens from local Keycloak via the
**`dems-dev-ropc`** password-grant client (seeded `dems_*` users in `dev_identities.py`). This proves
VPN was never the dependency — a running stack + Keycloak tokens are. An env-gated ROPC token path was
added to the QA `conftest.py` (`DEMS_ROPC=1`) to enable it.

**Actual audit verbs + categories emitted (file-scoped CoC CSV), vs the plan's assumptions:**

| Action performed | Actual verb(s) | Actual Category | Plan assumed | Discrepancy |
|---|---|---|---|---|
| create record | `create` | — (record-scoped) | `create` INGEST | Not in FILE CoC — record-scoped only |
| create file resource + upload bytes | `edit` + `upload` | INGEST | `upload_started`/`upload_completed` | Real verbs are `upload`+`edit`; started/completed NOT emitted |
| view | `view` | ACCESS | `view` ACCESS | ✓ match |
| play/stream | `play` | ACCESS | `play` ACCESS | ✓ match (Role column blank for play) |
| download | `download` | **CUSTODY** | ACCESS | **Category mismatch — likely product bug** |
| privatize | `file_privatized` | **INGEST** | CUSTODY | **Category mismatch — likely product bug (privatize as INGEST is wrong for CoC)** |
| un-privatize | `file_unprivatized` | **INGEST** | CUSTODY | **Category mismatch — likely product bug** |
| share | `share` | (blocked) | `share` CUSTODY | Endpoint requires an `expiry` field (400 without it) |

**Test-code contract corrections the run surfaced (test model FROZEN per decision — recorded, not applied):**
- `POST /records/files/{fid}/view` requires a non-empty body → send `json={}` (already applied in this branch).
- `POST /records/share/record/{rid}/file` requires `expiry` (hours) → 400 without it.
- The minimal happy-path scenario must poll for ≥1 audit row before exporting (async lag).
- `ALLOWED_LIFECYCLE` should be `edit, upload, view, play, download, file_privatized, share, share_revoked, file_unprivatized` (drop `create`, `upload_started`, `upload_completed`).

**DEMS product-team candidate findings (per "assert current behavior + file findings" decision):**
- **F5:** `download` categorized `CUSTODY` (expected `ACCESS`).
- **F6:** `file_privatized`/`file_unprivatized` categorized `INGEST` (expected `CUSTODY`) — misrepresents custody events in the CoC timeline color-banding.
- **F7:** Role-level denied-access attempts (role-less/system users, decrypt-denied export) are **absent** from the file CoC; only scope-level denials are captured.
- **F8:** `system administration` redaction branch in the CoC generator appears **unreachable** (role lacks `audit-logs:decrypt`).
5. **Environment.** Tests run against the shared dev env (`dems-dev.versaterm.org`) over VPN,
   authenticating via Playwright-saved role sessions (`npm run setup:auth`). All assertions must
   scope to the `file_id`/`record_id` created in the run — never assume a clean audit table.

## 9. Phasing

- **Phase 1 (this design):** file-level verify-and-log lifecycle → CoC PDF+CSV content validation.
- **Phase 2 (future):** case-level CoC aggregation; broader non-file audit-log matrix
  (users/redaction/public-submission/dead-letters) via `GET /audit/logs` assertions.

## 10. Run / verification

Per `dems-qa` conventions: `npm run setup:auth` to mint role sessions, then the pytest-bdd API
suite. Success = the new `features/audit/chain-of-custody.feature` scenario passes with all inline
verify-and-log assertions green and the Phase C CoC content assertions matching the expected-event
set. VPN required per repo notes.
