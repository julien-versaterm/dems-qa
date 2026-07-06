# steps/api/audit_steps.py
import uuid as _uuid

from pytest_bdd import given
from pytest_bdd import scenarios
from pytest_bdd import then
from pytest_bdd import parsers
from pytest_bdd import when

from coc_helper import find_row
from coc_helper import parse_coc_csv
from coc_helper import timestamps_sorted
from coc_helper import wait_for_action
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
    client = api_client(officer_token)
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

    # inline audit-row verification (poll for async rows).
    # officer1 is a "standard user" with no audit-logs:view permission, so
    # rows must be read back with a view+decrypt-capable role (sergeant1 is
    # in the same agency/integration as officer1 and can see its audit trail).
    audit_client = api_client(sergeant_token)
    precheck = audit_client.get('/api/v1/audit/logs', params={'file_id': fid, 'page_size': 1})
    assert precheck.status_code == 200, \
        f"audit_client cannot read audit logs: {precheck.status_code} {precheck.text}"

    for expected in ALLOWED_LIFECYCLE:
        row = wait_for_action(audit_client, fid, expected['action'], timeout=45)
        assert row is not None, f"audit row missing for action {expected['action']!r}"
        code = row.get('http_response_code')
        assert code is None or 200 <= int(code) < 300, \
            f"{expected['action']} expected 2xx, got {code}"

    context['expected_allowed'] = ALLOWED_LIFECYCLE


def _coc_rows(context):
    """Parse the CoC CSV response once per scenario and cache the rows."""
    if 'coc_rows' not in context:
        resp = context['coc_response']
        assert resp.status_code == 200, f"CoC csv: {resp.status_code} {resp.text}"
        context['coc_rows'] = parse_coc_csv(resp.text)
    return context['coc_rows']


@then('the CoC CSV contains every allowed lifecycle event with correct category and outcome')
def csv_contains_allowed(context):
    rows = _coc_rows(context)
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
    rows = _coc_rows(context)
    assert timestamps_sorted(rows), "CoC rows are not in non-decreasing timestamp order"
