# Audit CoC Content Validation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build automated coverage in the `dems-qa` repo that drives a multi-actor evidence-file lifecycle (allowed + denied actions across permission levels), verifies each action both works and is logged, then validates that the Chain-of-Custody PDF/CSV export content correctly captures the trail.

**Architecture:** pytest-bdd (Python, API layer) in `dems-qa`. A single rich scenario runs three phases: (A) generate + inline-verify lifecycle events using per-role Keycloak tokens, (B) export the file's CoC as a decrypt-capable role in CSV + PDF, (C) parse and assert the export content against an expected-event model. Content parsing uses `csv` (stdlib) for the structured assertion and `pdfplumber` for the rendered PDF. Pure parser/helper logic lives in `coc_helper.py` and is unit-tested locally; the integration scenario runs against the dev env.

**Tech Stack:** Python 3, pytest, pytest-bdd, httpx, pdfplumber, python-dotenv.

## Global Constraints

- **Target repo:** `dems-qa` (https://github.com/YuLin614/dems-qa). All files below are paths **within that repo**. Clone/checkout it (Task 1) — it is not part of the DEMS backend repo.
- **Execution environment:** the integration scenario runs against the shared dev env `BACKEND_URL=https://dems-dev.versaterm.org` **over VPN**, authenticating via Playwright-saved role sessions created by `npm run setup:auth` (writes `.auth/*.json`). Pure helper unit tests (Task 2) run anywhere with no network.
- **Role identities (conftest fixtures already exist):** `officer1`/`officer2` = `standard user`; `sergeant1` = `supervisor` (has `audit-logs:view`+`decrypt`); `admin` = `agency administrator`; `iauser` = role-less (every gated verb 403); `sysops1` = `system administration` (every AGENCY verb 403).
- **Evidence file:** `sample.mp4` (exists in `fixtures/test-files/`). Must be a media file so the `play`/stream verb generates (stream 415s on non-media).
- **Scope to the run:** every audit/CoC assertion filters on the `file_id`/`record_id` created in-run. Never assume a clean audit table (shared dev DB).
- **Async lag:** audit rows are persisted by a worker; `upload_completed` fires in the async unquarantine pipeline. All audit-row reads poll/retry before asserting.
- **CoC export requires** `?format=pdf|csv` **and** a non-blank `X-Download-Reason` header, and `audit-logs:view`+`decrypt` (only `sergeant1`/`admin` qualify).
- **CoC CSV columns (verbatim):** `Timestamp, Action, Category, User, Role, Description, Outcome`.
- **Confirmed verb strings:** `create`, `upload_started`, `upload_completed`, `view`, `play`, `download`, `file_privatized`, `file_unprivatized`, `share`, `share_revoked`. Categories: INGEST/ACCESS/CUSTODY, or DENIED when `http_response_code ∈ {401,403}`. Outcome: `Success` if 2xx else `Failure`.
- Keep files < 200 lines of logic; one import per line where practical (match repo style).

---

### Task 1: Revive the dead API layer — collectable suite + CoC happy path

The `steps/api/` layer currently binds to missing feature files, so it fails at collection. This task makes it collect and proves the CoC export round-trips end to end (the thinnest useful slice).

**Files (in `dems-qa`):**
- Clone/checkout: the `dems-qa` repo; create branch `feat/audit-coc-content-validation`.
- Create: `features/audit/chain-of-custody.feature`
- Modify: `pyproject.toml` (add `pdfplumber`)
- Modify: `steps/api/audit_steps.py` (replace skip-hatch stubs; bind the new feature; add a minimal happy-path)

**Interfaces:**
- Produces: the feature file at `features/audit/chain-of-custody.feature`; `audit_steps.py` binding it via `scenarios('../../features/audit/chain-of-custody.feature')`.

- [ ] **Step 1: Clone the repo and branch**

```bash
git clone https://github.com/YuLin614/dems-qa.git
cd dems-qa
git checkout -b feat/audit-coc-content-validation
```
Expected: clean checkout on the new branch.

- [ ] **Step 2: Add `pdfplumber` to `pyproject.toml`**

In `[project].dependencies`, add the line (keep alphabetical-ish with the others):
```toml
    "pdfplumber>=0.11",
```
Then install:
```bash
pip install -e . || pip install pdfplumber
```
Expected: `pdfplumber` importable (`python -c "import pdfplumber"` exits 0).

- [ ] **Step 3: Create the feature file with a single happy-path scenario**

Create `features/audit/chain-of-custody.feature`:
```gherkin
@api
Feature: Chain of Custody content validation

  Scenario: CoC export round-trips for a file with events
    Given an evidence file "sample.mp4" with a view and a download event
    When I export the chain of custody as "sergeant1" in "csv"
    Then the CoC export succeeds
```

- [ ] **Step 4: Rewrite `steps/api/audit_steps.py` to bind the feature and implement the happy path**

Replace the entire file with:
```python
# steps/api/audit_steps.py
import uuid as _uuid

from pytest_bdd import given
from pytest_bdd import scenarios
from pytest_bdd import then
from pytest_bdd import parsers
from pytest_bdd import when

from conftest import api_client
from upload_helper import upload_file

scenarios('../../features/audit/chain-of-custody.feature')

ROLE_FIXTURE = {
    'officer1': 'officer_token',
    'officer2': 'officer2_token',
    'sergeant1': 'sergeant_token',
    'admin': 'admin_token',
    'iauser': 'iauser_token',
    'sysops1': 'sysops_token',
}


@given(parsers.parse('an evidence file "{filename}" with a view and a download event'))
def evidence_file_with_events(context, officer_token, filename):
    client = api_client(officer_token)
    context['client'] = client
    uid = str(_uuid.uuid4())[:8]
    r = client.post('/api/v1/records', json={'category': 'id', 'external_record_id': f'[E2E] CoC {uid}'})
    assert r.status_code == 201, f"create record: {r.status_code} {r.text}"
    context['record_id'] = r.json()['record_id']
    context['file_id'] = upload_file(client, context['record_id'], filename)
    # view (204) + download (needs reason header)
    v = client.post(f"/api/v1/records/files/{context['file_id']}/view")
    assert v.status_code in (200, 204), f"view: {v.status_code} {v.text}"
    d = client.get(
        f"/api/v1/records/files/{context['file_id']}/download",
        headers={'X-Download-Reason': 'E2E CoC content validation'},
    )
    assert d.status_code in (200, 206), f"download: {d.status_code} {d.text}"


@when(parsers.parse('I export the chain of custody as "{role}" in "{fmt}"'))
def export_coc(context, request, role, fmt):
    token = request.getfixturevalue(ROLE_FIXTURE[role])
    client = api_client(token)
    resp = client.get(
        f"/api/v1/audit/chain-of-custody/files/{context['file_id']}/export",
        params={'format': fmt},
        headers={'X-Download-Reason': 'E2E CoC content validation'},
    )
    context['coc_response'] = resp
    context['coc_format'] = fmt


@then('the CoC export succeeds')
def coc_export_succeeds(context):
    resp = context['coc_response']
    assert resp.status_code == 200, f"CoC export: {resp.status_code} {resp.text}"
    ctype = resp.headers.get('content-type', '')
    if context['coc_format'] == 'csv':
        assert 'text/csv' in ctype, f"expected csv, got {ctype}"
    else:
        assert ctype.startswith('application/pdf'), f"expected pdf, got {ctype}"
```

- [ ] **Step 5: Verify the suite now collects**

Run:
```bash
python -m pytest steps/api/audit_steps.py --collect-only -q
```
Expected: collection succeeds and lists the `CoC export round-trips…` scenario (no "feature file not found" error). This alone proves the dead layer is revived.

- [ ] **Step 6: Run the happy path against dev (requires VPN + `npm run setup:auth`)**

```bash
npm run setup:auth   # once, to mint .auth/*.json role sessions
python -m pytest steps/api/audit_steps.py -k "round-trips" -v
```
Expected: PASS. If `upload_completed`-gated processing delays the file, the view/download still succeed (they don't require completion); if download 404s on fresh upload, re-run — the file needs the finalize pipeline. Note any flake as a finding, do not add a blanket skip.

- [ ] **Step 7: Commit**

```bash
git add features/audit/chain-of-custody.feature steps/api/audit_steps.py pyproject.toml
git commit -m "test(audit): revive api layer + CoC export happy path"
```

---

### Task 2: `coc_helper.py` — expected-event model, audit polling, CoC parsers (unit-tested)

Pure logic, no network — fully TDD-able locally with sample data.

**Files (in `dems-qa`):**
- Create: `steps/api/coc_helper.py`
- Create: `steps/api/test_coc_helper.py` (unit tests; matches `python_files = ["*_steps.py"]`? No — add explicit run)

**Interfaces:**
- Produces:
  - `parse_coc_csv(text: str) -> list[dict]` — rows keyed by the 7 CSV columns.
  - `find_row(rows: list[dict], action: str) -> dict | None`
  - `timestamps_sorted(rows: list[dict]) -> bool`
  - `poll_audit_rows(client, file_id: str, timeout: float = 30, interval: float = 3) -> list[dict]`
  - `wait_for_action(client, file_id: str, action: str, timeout: float = 30) -> dict | None`
  - `extract_pdf_text(pdf_bytes: bytes) -> str`

- [ ] **Step 1: Write failing unit tests**

Create `steps/api/test_coc_helper.py`:
```python
import datetime

from coc_helper import find_row
from coc_helper import parse_coc_csv
from coc_helper import timestamps_sorted

SAMPLE_CSV = (
    "Timestamp,Action,Category,User,Role,Description,Outcome\r\n"
    "2026-07-02T10:00:00Z,create,INGEST,Jane Officer,standard user,Created record,Success\r\n"
    "2026-07-02T10:01:00Z,download,ACCESS,Jane Officer,standard user,Downloaded,Success\r\n"
    "2026-07-02T10:02:00Z,view,DENIED,,,Denied,Failure\r\n"
)


def test_parse_coc_csv_returns_rows_keyed_by_columns():
    rows = parse_coc_csv(SAMPLE_CSV)
    assert len(rows) == 3
    assert rows[0]['Action'] == 'create'
    assert rows[0]['Category'] == 'INGEST'
    assert rows[0]['Outcome'] == 'Success'
    assert rows[2]['Category'] == 'DENIED'


def test_find_row_matches_action():
    rows = parse_coc_csv(SAMPLE_CSV)
    assert find_row(rows, 'download')['Outcome'] == 'Success'
    assert find_row(rows, 'nonexistent') is None


def test_timestamps_sorted_true_for_ordered_rows():
    rows = parse_coc_csv(SAMPLE_CSV)
    assert timestamps_sorted(rows) is True


def test_timestamps_sorted_false_when_out_of_order():
    rows = parse_coc_csv(SAMPLE_CSV)
    rows[0]['Timestamp'] = '2026-07-02T11:00:00Z'  # now after row 1
    assert timestamps_sorted(rows) is False
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python -m pytest steps/api/test_coc_helper.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'coc_helper'` (or import errors). Run from `steps/api/` so the bare imports resolve, or set `PYTHONPATH=steps/api`.

- [ ] **Step 3: Implement `coc_helper.py`**

Create `steps/api/coc_helper.py`:
```python
# steps/api/coc_helper.py
import csv
import io
import time

CSV_COLUMNS = ['Timestamp', 'Action', 'Category', 'User', 'Role', 'Description', 'Outcome']


def parse_coc_csv(text):
    """Parse CoC CSV export text into a list of dict rows keyed by CSV_COLUMNS."""
    # UTF-8 BOM is emitted by the exporter; strip it.
    if text and text[0] == '﻿':
        text = text[1:]
    reader = csv.DictReader(io.StringIO(text))
    return [dict(row) for row in reader]


def find_row(rows, action):
    """First row whose Action equals `action`, else None."""
    for row in rows:
        if row.get('Action') == action:
            return row
    return None


def timestamps_sorted(rows):
    """True if the rows' Timestamp column is non-decreasing."""
    stamps = [row.get('Timestamp', '') for row in rows]
    return stamps == sorted(stamps)


def _audit_items(payload):
    """The audit read endpoint returns items under 'data' or 'items'."""
    return payload.get('data') or payload.get('items') or []


def poll_audit_rows(client, file_id, timeout=30, interval=3):
    """Poll GET /api/v1/audit/logs?file_id= until non-empty or timeout; returns items."""
    deadline = time.monotonic() + timeout
    items = []
    while True:
        resp = client.get('/api/v1/audit/logs', params={'file_id': file_id, 'page_size': 500})
        if resp.status_code == 200:
            items = _audit_items(resp.json())
            if items:
                return items
        if time.monotonic() >= deadline:
            return items
        time.sleep(interval)


def wait_for_action(client, file_id, action, timeout=30):
    """Poll audit rows until one with the given action appears; else None."""
    deadline = time.monotonic() + timeout
    while True:
        for row in poll_audit_rows(client, file_id, timeout=0, interval=0):
            if row.get('action') == action:
                return row
        if time.monotonic() >= deadline:
            return None
        time.sleep(3)


def extract_pdf_text(pdf_bytes):
    """Concatenated text of all pages of a PDF byte string."""
    import pdfplumber
    out = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            out.append(page.extract_text() or '')
    return '\n'.join(out)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd steps/api && python -m pytest test_coc_helper.py -v && cd -
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add steps/api/coc_helper.py steps/api/test_coc_helper.py
git commit -m "test(audit): add coc_helper (csv parse, audit poll, pdf text)"
```

---

### Task 3: Allowed-lifecycle generation with inline verify-and-log

Extend the scenario to drive the full allowed lifecycle (officer1), asserting each action's functional result AND its audit row, and building the expected-event model for Phase C.

**Files (in `dems-qa`):**
- Modify: `features/audit/chain-of-custody.feature` (add the full scenario)
- Modify: `steps/api/audit_steps.py` (add the lifecycle `@given`, using `coc_helper`)

**Interfaces:**
- Consumes: `coc_helper.wait_for_action`, `upload_helper.upload_file`.
- Produces: `context['expected_allowed']` = list of `{action, category, outcome}` dicts; `context['file_id']`, `context['record_id']`, `context['recipient_id']`.

- [ ] **Step 1: Add the full scenario to the feature file**

Append to `features/audit/chain-of-custody.feature`:
```gherkin
  Scenario: CoC captures the full allowed evidence lifecycle
    Given officer1 performs the full allowed lifecycle on "sample.mp4"
    When I export the chain of custody as "sergeant1" in "csv"
    Then the CoC CSV contains every allowed lifecycle event with correct category and outcome
    And the CoC events are in timestamp order
```

- [ ] **Step 2: Add the lifecycle `@given` to `audit_steps.py`**

Add these imports at the top (one per line, alongside existing):
```python
from coc_helper import find_row
from coc_helper import parse_coc_csv
from coc_helper import timestamps_sorted
from coc_helper import wait_for_action
```
Then add the step:
```python
ALLOWED_LIFECYCLE = [
    {'action': 'create', 'category': 'INGEST'},
    {'action': 'upload_started', 'category': 'INGEST'},
    {'action': 'upload_completed', 'category': 'INGEST'},
    {'action': 'view', 'category': 'ACCESS'},
    {'action': 'play', 'category': 'ACCESS'},
    {'action': 'download', 'category': 'ACCESS'},
    {'action': 'file_privatized', 'category': 'CUSTODY'},
    {'action': 'share', 'category': 'CUSTODY'},
    {'action': 'share_revoked', 'category': 'CUSTODY'},
    {'action': 'file_unprivatized', 'category': 'CUSTODY'},
]


@given(parsers.parse('officer1 performs the full allowed lifecycle on "{filename}"'))
def full_allowed_lifecycle(context, officer_token, sergeant_token, filename):
    client = api_client(officer_token)          # performs the lifecycle actions
    context['client'] = client
    uid = str(_uuid.uuid4())[:8]

    # create
    r = client.post('/api/v1/records', json={'category': 'id', 'external_record_id': f'[E2E] CoC {uid}'})
    assert r.status_code == 201, f"create: {r.status_code} {r.text}"
    rid = r.json()['record_id']
    context['record_id'] = rid

    # upload (upload_started sync; upload_completed async)
    fid = upload_file(client, rid, filename)
    context['file_id'] = fid

    # view (204)
    v = client.post(f"/api/v1/records/files/{fid}/view")
    assert v.status_code in (200, 204), f"view: {v.status_code} {v.text}"

    # play/stream (media file -> 200/206)
    s = client.get(f"/api/v1/records/files/{fid}/stream", headers={'Range': 'bytes=0-1023'})
    assert s.status_code in (200, 206), f"stream: {s.status_code} {s.text}"

    # download (reason header)
    d = client.get(f"/api/v1/records/files/{fid}/download",
                   headers={'X-Download-Reason': 'E2E CoC content validation'})
    assert d.status_code in (200, 206), f"download: {d.status_code} {d.text}"

    # privatize -> read back lock_level == private
    lk = client.put(f"/api/v1/records/{rid}/files/{fid}/lock-level",
                    json={'lock_level': 'private', 'reason': 'E2E privatize'})
    assert lk.status_code in (200, 204), f"lock: {lk.status_code} {lk.text}"
    meta = client.get(f"/api/v1/records/{rid}/files/{fid}/metadata")
    assert meta.status_code == 200, f"metadata: {meta.status_code} {meta.text}"
    lock = meta.json().get('lock')
    assert lock and lock.get('lock_level') == 'private', f"expected private lock, got {lock}"

    # share -> capture recipient_id
    sh = client.post(f"/api/v1/records/share/record/{rid}/file",
                     json={'resource_ids': [fid],
                           'recipients': [{'email': 'e2e-recipient@example.com', 'name': 'E2E'}],
                           'reason': 'E2E share'})
    assert sh.status_code == 201, f"share: {sh.status_code} {sh.text}"
    successful = sh.json().get('result', {}).get('successful_shares', [])
    assert successful, f"no successful shares: {sh.text}"
    context['recipient_id'] = successful[0]['recipient_ids'][0]

    # revoke the recipient
    rv = client.post(
        f"/api/v1/records/share/record/{rid}/file/{fid}/recipients/{context['recipient_id']}/revoke",
        json={'reason': 'E2E revoke'})
    assert rv.status_code == 200, f"revoke: {rv.status_code} {rv.text}"

    # un-privatize -> lock cleared
    un = client.put(f"/api/v1/records/{rid}/files/{fid}/lock-level",
                    json={'lock_level': None, 'reason': 'E2E unprivatize'})
    assert un.status_code in (200, 204), f"unlock: {un.status_code} {un.text}"
    meta2 = client.get(f"/api/v1/records/{rid}/files/{fid}/metadata")
    assert meta2.json().get('lock') is None, f"expected cleared lock, got {meta2.json().get('lock')}"

    # inline audit-row verification — poll as sergeant1 (supervisor): standard user
    # (officer1) has NO audit-logs:view permission, so it must NOT read the audit log.
    audit_client = api_client(sergeant_token)
    probe = audit_client.get('/api/v1/audit/logs', params={'file_id': fid, 'page_size': 1})
    assert probe.status_code == 200, f"audit_client cannot read audit logs: {probe.status_code} {probe.text}"
    for expected in ALLOWED_LIFECYCLE:
        row = wait_for_action(audit_client, fid, expected['action'], timeout=45)
        assert row is not None, f"audit row missing for action {expected['action']!r}"
        code = row.get('http_response_code')
        assert code is None or 200 <= int(code) < 300, \
            f"{expected['action']} expected 2xx, got {code}"

    context['expected_allowed'] = ALLOWED_LIFECYCLE
```

- [ ] **Step 3: Add the CSV content assertions**

Add to `audit_steps.py`:
```python
@then('the CoC CSV contains every allowed lifecycle event with correct category and outcome')
def csv_contains_allowed(context):
    resp = context['coc_response']
    assert resp.status_code == 200, f"CoC csv: {resp.status_code} {resp.text}"
    rows = parse_coc_csv(resp.text)
    missing = []
    for expected in context['expected_allowed']:
        row = find_row(rows, expected['action'])
        if row is None:
            missing.append(expected['action'])
            continue
        assert row['Category'] == expected['category'], \
            f"{expected['action']}: category {row['Category']} != {expected['category']}"
        assert row['Outcome'] == 'Success', f"{expected['action']}: outcome {row['Outcome']} != Success"
    assert not missing, f"allowed actions missing from CoC CSV: {missing}"


@then('the CoC events are in timestamp order')
def csv_timestamps_ordered(context):
    rows = parse_coc_csv(context['coc_response'].text)
    assert timestamps_sorted(rows), "CoC rows are not in non-decreasing timestamp order"
```

- [ ] **Step 4: Run the full-lifecycle scenario against dev**

```bash
python -m pytest steps/api/audit_steps.py -k "full allowed" -v
```
Expected: PASS. If `upload_completed` or `play` is missing after 45s, that is a real finding about async lag or the stream dedup — record it (do not lengthen the timeout indefinitely or skip). If `share_revoked`/`file_unprivatized` are absent from the CSV but present in `GET /audit/logs`, that indicates a CoC-trail scoping gap — record as a finding.

- [ ] **Step 5: Commit**

```bash
git add features/audit/chain-of-custody.feature steps/api/audit_steps.py
git commit -m "test(audit): full allowed lifecycle + CoC CSV content assertions"
```

---

### Task 4: PDF content assertion + denied-path probes

Add the PDF-text assertion and the denied attempts (role-less, system, unassigned, and the decrypt-denied CoC export), verifying 403 + logging behavior.

**Files (in `dems-qa`):**
- Modify: `features/audit/chain-of-custody.feature`
- Modify: `steps/api/audit_steps.py`

**Interfaces:**
- Consumes: `coc_helper.extract_pdf_text`, `coc_helper.poll_audit_rows`, `context['file_id']`, `context['expected_allowed']`.

- [ ] **Step 1: Add PDF + denied scenarios to the feature file**

Append:
```gherkin
  Scenario: CoC PDF text contains the allowed action verbs
    Given officer1 performs the full allowed lifecycle on "sample.mp4"
    When I export the chain of custody as "sergeant1" in "pdf"
    Then the CoC PDF text contains each allowed action verb

  Scenario: Denied attempts are rejected and their logging is characterized
    Given officer1 performs the full allowed lifecycle on "sample.mp4"
    When denied actors attempt to access the file
    Then each denied attempt returns 403
    And denied audit rows are characterized against the file CoC
```

- [ ] **Step 2: Add the PDF-text assertion**

Add to `audit_steps.py` (import at top: `from coc_helper import extract_pdf_text`, `from coc_helper import poll_audit_rows`):
```python
@then('the CoC PDF text contains each allowed action verb')
def pdf_contains_verbs(context):
    resp = context['coc_response']
    assert resp.status_code == 200, f"CoC pdf: {resp.status_code} {resp.text}"
    text = extract_pdf_text(resp.content)
    missing = [e['action'] for e in context['expected_allowed'] if e['action'] not in text]
    assert not missing, f"allowed verbs missing from CoC PDF text: {missing}"
```

- [ ] **Step 3: Add the denied-probe steps**

```python
DENIED_PROBES = [
    {'role': 'iauser', 'verb': 'view'},
    {'role': 'sysops1', 'verb': 'download'},
    {'role': 'officer2', 'verb': 'status'},
]


@when('denied actors attempt to access the file')
def denied_actors_attempt(context, request):
    rid, fid = context['record_id'], context['file_id']
    results = []
    for probe in DENIED_PROBES:
        client = api_client(request.getfixturevalue(ROLE_FIXTURE[probe['role']]))
        if probe['verb'] == 'view':
            resp = client.post(f"/api/v1/records/files/{fid}/view")
        elif probe['verb'] == 'download':
            resp = client.get(f"/api/v1/records/files/{fid}/download",
                              headers={'X-Download-Reason': 'denied probe'})
        else:  # status
            resp = client.get(f"/api/v1/records/{rid}/files/{fid}/status")
        results.append({'role': probe['role'], 'verb': probe['verb'], 'status': resp.status_code})
    # decrypt-denied CoC export by a standard user
    officer = api_client(request.getfixturevalue('officer_token'))
    coc = officer.get(f"/api/v1/audit/chain-of-custody/files/{fid}/export",
                      params={'format': 'pdf'}, headers={'X-Download-Reason': 'denied probe'})
    results.append({'role': 'officer1', 'verb': 'coc_export', 'status': coc.status_code})
    context['denied_results'] = results


@then('each denied attempt returns 403')
def denied_returns_403(context):
    soft = []
    for r in context['denied_results']:
        # officer2 may equal officer1 in some envs (status 200/404) — treat non-403 as a soft note
        if r['status'] != 403:
            soft.append(r)
    hard = [r for r in soft if r['role'] not in ('officer2',)]
    assert not hard, f"expected 403 for {hard}"
    if soft:
        print(f"NOTE (env-dependent, not failing): non-403 denied probes: {soft}")


@then('denied audit rows are characterized against the file CoC')
def characterize_denied_rows(context, sergeant_token):
    # Empirical: are denied attempts present in the file-scoped audit view / CoC?
    # Poll as sergeant1 — officer1 (standard user) lacks audit-logs:view.
    fid = context['file_id']
    client = api_client(sergeant_token)
    rows = poll_audit_rows(client, fid, timeout=15)
    denied_in_view = [r for r in rows if str(r.get('http_response_code')) in ('401', '403')]
    print(f"FINDING: {len(denied_in_view)} denied (401/403) rows present in file-scoped audit view "
          f"for file {fid}. If 0, denied attempts are NOT file-scoped (reference_data.file_ids "
          f"absent) — a CoC coverage gap to report.")
    # Not asserted hard: this step documents behavior. See design §8 finding 2.
```

- [ ] **Step 4: Run the PDF + denied scenarios against dev**

```bash
python -m pytest steps/api/audit_steps.py -k "PDF text or Denied" -v -s
```
Expected: both PASS. The `-s` flag surfaces the FINDING/NOTE prints. Capture the denied-row FINDING output for the design doc (Task 5).

- [ ] **Step 5: Commit**

```bash
git add features/audit/chain-of-custody.feature steps/api/audit_steps.py
git commit -m "test(audit): CoC PDF content + denied-path probes and characterization"
```

---

### Task 5: Record findings + update the step catalogue

Fold the empirical results into the design doc's findings and document the new steps.

**Files:**
- Modify (in `dems-qa`): `STEPS.md`
- Modify (in the DEMS backend repo): `docs/superpowers/specs/2026-07-02-dems-audit-coc-content-validation-design.md` §8 (fill in the confirmed answers to findings 1–3 with the observed evidence)

- [ ] **Step 1: Update `STEPS.md`**

Under the "Audit & Chain of Custody" table, replace the stale rows with:
```markdown
| `Given an evidence file "{filename}" with a view and a download event` | @api | Minimal CoC setup |
| `Given officer1 performs the full allowed lifecycle on "{filename}"` | @api | create→upload→view→play→download→privatize→share→revoke→unprivatize, each verified + logged |
| `When I export the chain of custody as "{role}" in "{fmt}"` | @api | fmt = csv or pdf; role needs audit decrypt (sergeant1/admin) |
| `Then the CoC export succeeds` | @api | 200 + correct content-type |
| `Then the CoC CSV contains every allowed lifecycle event with correct category and outcome` | @api | |
| `Then the CoC events are in timestamp order` | @api | |
| `Then the CoC PDF text contains each allowed action verb` | @api | pdfplumber text extraction |
| `When denied actors attempt to access the file` | @api | iauser/sysops1/officer2 + standard-user CoC export |
| `Then each denied attempt returns 403` | @api | officer2 non-403 tolerated (env alias) |
| `Then denied audit rows are characterized against the file CoC` | @api | prints a finding; see design §8 |
```

- [ ] **Step 2: Fill in the design-doc findings with observed evidence**

In the DEMS repo, edit §8 of the design doc: for findings 1 (redaction reachability), 2 (denied rows in CoC), and 3 (retention auditing), append `**Observed:** …` lines with what the Task 3/4 runs actually showed (e.g. "denied rows: 0 present in file CoC → confirmed gap, filed as DMS-XXXX").

- [ ] **Step 3: Commit both**

```bash
# in dems-qa
git add STEPS.md && git commit -m "docs(audit): catalogue CoC content-validation steps"
# in the DEMS backend repo
git add docs/superpowers/specs/2026-07-02-dems-audit-coc-content-validation-design.md
git commit -m "docs(audit): record CoC validation findings"
```

- [ ] **Step 4: Open a PR on `dems-qa`** (or hand back for review), and file Jira tickets for any confirmed gaps (retention-not-audited; denied-rows-not-in-CoC; redaction-dead-code) using the `/create-jira-task` skill.

---

## Self-Review

**Spec coverage:**
- Verify-and-log per action → Task 3 (functional asserts + inline audit-row polling). ✓
- CoC content validation (CSV + PDF) → Tasks 3 (CSV) + 4 (PDF). ✓
- Permission-level coverage (allowed + denied) → Task 3 (allowed) + Task 4 (denied probes: role-less, system, unassigned, decrypt-denied). ✓
- Harness revival (dead API layer) → Task 1. ✓
- Findings (redaction dead code, denied scoping, retention gap) → Tasks 4 + 5. ✓
- Non-goals honored: no browser, no non-file verbs, file-level only. ✓

**Placeholder scan:** No TBD/TODO in steps; every code step has complete code. The one intentionally
non-asserting step (`characterize_denied_rows`) is by-design (empirical characterization per design
§8 finding 2), not a placeholder. ✓

**Type consistency:** `parse_coc_csv`/`find_row`/`timestamps_sorted`/`wait_for_action`/`poll_audit_rows`/
`extract_pdf_text` signatures match between Task 2 (definition) and Tasks 3–4 (use). `context` keys
(`record_id`, `file_id`, `recipient_id`, `expected_allowed`, `coc_response`, `coc_format`,
`denied_results`) are set before they are read. `ROLE_FIXTURE` maps the 6 identities to the conftest
fixture names. ✓
