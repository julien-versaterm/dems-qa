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
