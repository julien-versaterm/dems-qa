# steps/api/audit_flows.py
"""Procedural flow helpers for the audit/CoC step definitions.

Plain functions (no pytest-bdd bindings) so that step definitions can stay
in a single file (steps/api/audit_steps.py) — cross-file pytest-bdd step
lookup is unreliable in this repo — while keeping both files under the
200-line "no god files" cap.
"""
import uuid as _uuid

from coc_helper import wait_for_action
from upload_helper import upload_file

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

DENIED_PROBES = [
    {'role': 'iauser', 'verb': 'view'},
    {'role': 'sysops1', 'verb': 'download'},
    {'role': 'officer2', 'verb': 'status'},
]


def run_allowed_lifecycle(officer_client, audit_client, filename):
    """Run the full allowed evidence lifecycle as officer1, then verify every
    expected action shows up (2xx) in the sergeant1-visible audit trail.

    Returns dict with record_id, file_id, recipient_id, expected_allowed —
    the context keys the calling step stashes.
    """
    uid = str(_uuid.uuid4())[:8]

    # create
    r = officer_client.post('/api/v1/records', json={'category': 'id', 'external_record_id': f'[E2E] CoC {uid}'})
    assert r.status_code == 201, f"create: {r.status_code} {r.text}"
    rid = r.json()['record_id']

    # upload (upload_started sync; upload_completed async)
    fid = upload_file(officer_client, rid, filename)

    # view (204)
    v = officer_client.post(f"/api/v1/records/files/{fid}/view")
    assert v.status_code in (200, 204), f"view: {v.status_code} {v.text}"

    # play/stream (media file -> 200/206)
    s = officer_client.get(f"/api/v1/records/files/{fid}/stream", headers={'Range': 'bytes=0-1023'})
    assert s.status_code in (200, 206), f"stream: {s.status_code} {s.text}"

    # download (reason header)
    d = officer_client.get(f"/api/v1/records/files/{fid}/download",
                            headers={'X-Download-Reason': 'E2E CoC content validation'})
    assert d.status_code in (200, 206), f"download: {d.status_code} {d.text}"

    # privatize -> read back lock_level == private
    lk = officer_client.put(f"/api/v1/records/{rid}/files/{fid}/lock-level",
                            json={'lock_level': 'private', 'reason': 'E2E privatize'})
    assert lk.status_code in (200, 204), f"lock: {lk.status_code} {lk.text}"
    meta = officer_client.get(f"/api/v1/records/{rid}/files/{fid}/metadata")
    assert meta.status_code == 200, f"metadata: {meta.status_code} {meta.text}"
    lock = meta.json().get('lock')
    assert lock and lock.get('lock_level') == 'private', f"expected private lock, got {lock}"

    # share -> capture recipient_id
    sh = officer_client.post(f"/api/v1/records/share/record/{rid}/file",
                             json={'resource_ids': [fid],
                                   'recipients': [{'email': 'e2e-recipient@example.com', 'name': 'E2E'}],
                                   'reason': 'E2E share'})
    assert sh.status_code == 201, f"share: {sh.status_code} {sh.text}"
    successful = sh.json().get('result', {}).get('successful_shares', [])
    assert successful, f"no successful shares: {sh.text}"
    recipient_id = successful[0]['recipient_ids'][0]

    # revoke the recipient
    rv = officer_client.post(
        f"/api/v1/records/share/record/{rid}/file/{fid}/recipients/{recipient_id}/revoke",
        json={'reason': 'E2E revoke'})
    assert rv.status_code == 200, f"revoke: {rv.status_code} {rv.text}"

    # un-privatize -> lock cleared
    un = officer_client.put(f"/api/v1/records/{rid}/files/{fid}/lock-level",
                            json={'lock_level': None, 'reason': 'E2E unprivatize'})
    assert un.status_code in (200, 204), f"unlock: {un.status_code} {un.text}"
    meta2 = officer_client.get(f"/api/v1/records/{rid}/files/{fid}/metadata")
    assert meta2.json().get('lock') is None, f"expected cleared lock, got {meta2.json().get('lock')}"

    # inline audit-row verification (poll for async rows).
    # officer1 is a "standard user" with no audit-logs:view permission, so
    # rows must be read back with a view+decrypt-capable role (sergeant1 is
    # in the same agency/integration as officer1 and can see its audit trail).
    precheck = audit_client.get('/api/v1/audit/logs', params={'file_id': fid, 'page_size': 1})
    assert precheck.status_code == 200, \
        f"audit_client cannot read audit logs: {precheck.status_code} {precheck.text}"

    for expected in ALLOWED_LIFECYCLE:
        row = wait_for_action(audit_client, fid, expected['action'], timeout=45)
        assert row is not None, f"audit row missing for action {expected['action']!r}"
        code = row.get('http_response_code')
        assert code is None or 200 <= int(code) < 300, \
            f"{expected['action']} expected 2xx, got {code}"

    return {
        'record_id': rid,
        'file_id': fid,
        'recipient_id': recipient_id,
        'expected_allowed': ALLOWED_LIFECYCLE,
    }


def run_denied_probes(clients, rid, fid):
    """Fire the denied-access probes (DENIED_PROBES) plus a standard-user
    (officer1) CoC-export attempt.

    `clients` maps role name -> api client already wrapping that role's
    token; must include an entry per DENIED_PROBES role plus 'officer1'.
    Returns a list of {'role', 'verb', 'status'} dicts.
    """
    results = []
    for probe in DENIED_PROBES:
        client = clients[probe['role']]
        if probe['verb'] == 'view':
            resp = client.post(f"/api/v1/records/files/{fid}/view")
        elif probe['verb'] == 'download':
            resp = client.get(f"/api/v1/records/files/{fid}/download",
                              headers={'X-Download-Reason': 'denied probe'})
        else:  # status
            resp = client.get(f"/api/v1/records/{rid}/files/{fid}/status")
        results.append({'role': probe['role'], 'verb': probe['verb'], 'status': resp.status_code})

    # decrypt-denied CoC export by a standard user
    officer = clients['officer1']
    coc = officer.get(f"/api/v1/audit/chain-of-custody/files/{fid}/export",
                      params={'format': 'pdf'}, headers={'X-Download-Reason': 'denied probe'})
    results.append({'role': 'officer1', 'verb': 'coc_export', 'status': coc.status_code})
    return results
