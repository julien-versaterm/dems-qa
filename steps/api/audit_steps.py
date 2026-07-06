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
