"""Storage for scanned receipts that are waiting to be reviewed (the inbox).

A scan is created the moment photos are uploaded and updated as the background reader makes progress:

    processing  -> ready          everything read, looks clean (one tap to approve)
                -> needs_review   read, but something needs a human look (duplicate, totals off, missing date...)
                -> error          the reader failed (can be retried)
    ready / needs_review / error -> saved | discarded
"""
import json
import sqlite3
import uuid
from datetime import datetime, timedelta

DB_PATH = 'receipts.db'
OPEN_STATUSES = ('processing', 'ready', 'needs_review', 'error')      # still in the inbox
JSON_FIELDS = ('dup_info', 'result', 'notes', 'photo_paths')

SCHEMA = '''
    CREATE TABLE IF NOT EXISTS scans (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'processing',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        filename TEXT,
        file_hash TEXT,
        photo_count INTEGER DEFAULT 1,
        photo_paths TEXT,
        quick_merchant TEXT,
        quick_amount REAL,
        quick_date TEXT,
        quick_status TEXT DEFAULT 'pending',
        dup_status TEXT DEFAULT 'unknown',
        dup_info TEXT,
        result TEXT,
        notes TEXT,
        error TEXT,
        expense_id INTEGER,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
'''


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)       # background readers and web requests share the file
    conn.row_factory = sqlite3.Row
    return conn


def init_table(conn=None):
    own = conn is None
    conn = conn or sqlite3.connect(DB_PATH, timeout=30)
    conn.execute(SCHEMA)
    conn.execute('CREATE INDEX IF NOT EXISTS idx_scans_user_status ON scans(user_id, status)')
    conn.commit()
    if own:
        conn.close()


def _now():
    return datetime.utcnow().isoformat(timespec='seconds')


def _decode(row):
    if row is None:
        return None
    scan = dict(row)
    for field in JSON_FIELDS:
        raw = scan.get(field)
        scan[field] = json.loads(raw) if raw else None
    return scan


def create_scan(user_id, filename, file_hash, photo_count=1, photo_paths=None, notes=None):
    scan_id = uuid.uuid4().hex
    now = _now()
    conn = connect()
    try:
        conn.execute(
            'INSERT INTO scans (id, user_id, status, created_at, updated_at, filename, file_hash, photo_count, photo_paths, notes) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (scan_id, user_id, 'processing', now, now, filename, file_hash, photo_count,
             json.dumps(photo_paths or []), json.dumps(notes or [])))
        conn.commit()
    finally:
        conn.close()
    return scan_id


def update_scan(scan_id, **fields):
    if not fields:
        return
    fields['updated_at'] = _now()
    for key in JSON_FIELDS:
        if key in fields and not isinstance(fields[key], str):
            fields[key] = json.dumps(fields[key], default=str)
    conn = connect()
    try:
        conn.execute(f"UPDATE scans SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                     list(fields.values()) + [scan_id])
        conn.commit()
    finally:
        conn.close()


def get_scan(scan_id, user_id=None):
    conn = connect()
    try:
        if user_id is None:
            row = conn.execute('SELECT * FROM scans WHERE id = ?', (scan_id,)).fetchone()
        else:
            row = conn.execute('SELECT * FROM scans WHERE id = ? AND user_id = ?', (scan_id, user_id)).fetchone()
        return _decode(row)
    finally:
        conn.close()


def list_scans(user_id, statuses=OPEN_STATUSES):
    marks = ','.join('?' * len(statuses))
    conn = connect()
    try:
        rows = conn.execute(f'SELECT * FROM scans WHERE user_id = ? AND status IN ({marks}) ORDER BY created_at DESC',
                            (user_id, *statuses)).fetchall()
        return [_decode(r) for r in rows]
    finally:
        conn.close()


def count_open(user_id):
    conn = connect()
    try:
        marks = ','.join('?' * len(OPEN_STATUSES))
        return conn.execute(f'SELECT COUNT(*) FROM scans WHERE user_id = ? AND status IN ({marks})',
                            (user_id, *OPEN_STATUSES)).fetchone()[0]
    finally:
        conn.close()


def other_open_scans(user_id, exclude_id):
    """Other scans still waiting in the inbox (used to catch a receipt scanned twice before either is saved)."""
    return [s for s in list_scans(user_id, ('processing', 'ready', 'needs_review')) if s['id'] != exclude_id]


def fail_stale(max_minutes=10):
    """Scans stuck in 'processing' (e.g. the server restarted mid-read) become retryable errors."""
    cutoff = (datetime.utcnow() - timedelta(minutes=max_minutes)).isoformat(timespec='seconds')
    conn = connect()
    try:
        conn.execute("UPDATE scans SET status = 'error', error = 'Interrupted - tap Retry', updated_at = ? "
                     "WHERE status = 'processing' AND updated_at < ?", (_now(), cutoff))
        conn.commit()
    finally:
        conn.close()


def discard_by_filename(user_id, filename):
    """The user discarded a receipt from the review screen: close any inbox entry that points at it."""
    conn = connect()
    try:
        conn.execute("UPDATE scans SET status = 'discarded', updated_at = ? "
                     "WHERE user_id = ? AND filename = ? AND status IN ('ready', 'needs_review', 'error', 'processing')",
                     (_now(), user_id, filename))
        conn.commit()
    finally:
        conn.close()
