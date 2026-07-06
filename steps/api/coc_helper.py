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
