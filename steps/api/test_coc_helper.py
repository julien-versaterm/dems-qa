import time

from coc_helper import find_row
from coc_helper import parse_coc_csv
from coc_helper import poll_audit_rows
from coc_helper import timestamps_sorted
from coc_helper import wait_for_action

SAMPLE_CSV = (
    "Timestamp,Action,Category,User,Role,Description,Outcome\r\n"
    "2026-07-02T10:00:00Z,create,INGEST,Jane Officer,standard user,Created record,Success\r\n"
    "2026-07-02T10:01:00Z,download,ACCESS,Jane Officer,standard user,Downloaded,Success\r\n"
    "2026-07-02T10:02:00Z,view,DENIED,,,Denied,Failure\r\n"
)

BOM_CSV = "﻿" + SAMPLE_CSV


class FakeResponse:
    """Minimal stand-in for an httpx.Response: .status_code + .json()."""

    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeClient:
    """Minimal stand-in for an httpx.Client: a `.get(path, params=...)` that
    returns queued FakeResponse objects, one per call (last one repeats)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


def test_parse_coc_csv_returns_rows_keyed_by_columns():
    rows = parse_coc_csv(SAMPLE_CSV)
    assert len(rows) == 3
    assert rows[0]['Action'] == 'create'
    assert rows[0]['Category'] == 'INGEST'
    assert rows[0]['Outcome'] == 'Success'
    assert rows[2]['Category'] == 'DENIED'


def test_parse_coc_csv_strips_leading_bom():
    rows = parse_coc_csv(BOM_CSV)
    assert 'Timestamp' in rows[0]
    assert '﻿Timestamp' not in rows[0]
    assert rows[0]['Timestamp'] == '2026-07-02T10:00:00Z'


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


def test_poll_audit_rows_returns_items_from_data_envelope():
    client = FakeClient([FakeResponse(200, {'data': [{'action': 'create'}]})])
    items = poll_audit_rows(client, 'file-1', timeout=1, interval=0.05)
    assert items == [{'action': 'create'}]


def test_poll_audit_rows_returns_items_from_items_envelope():
    client = FakeClient([FakeResponse(200, {'items': [{'action': 'download'}]})])
    items = poll_audit_rows(client, 'file-1', timeout=1, interval=0.05)
    assert items == [{'action': 'download'}]


def test_poll_audit_rows_returns_empty_list_on_timeout():
    client = FakeClient([FakeResponse(200, {'data': []})])
    items = poll_audit_rows(client, 'file-1', timeout=0.1, interval=0.05)
    assert items == []


def test_wait_for_action_returns_matching_row_when_present():
    client = FakeClient([
        FakeResponse(200, {'data': [{'action': 'create'}]}),
        FakeResponse(200, {'data': [{'action': 'create'}, {'action': 'download'}]}),
    ])
    row = wait_for_action(client, 'file-1', 'download', timeout=1)
    assert row == {'action': 'download'}


def test_wait_for_action_returns_none_without_overshooting_timeout():
    """Regression test: wait_for_action must never sleep past its deadline.

    Fails against the old implementation, which slept a hardcoded 3s after
    each failed poll regardless of the requested timeout.
    """
    client = FakeClient([FakeResponse(200, {'data': []})])
    timeout = 0.2
    start = time.monotonic()
    row = wait_for_action(client, 'file-1', 'never-happens', timeout=timeout)
    elapsed = time.monotonic() - start
    assert row is None
    assert elapsed < timeout + 0.3
