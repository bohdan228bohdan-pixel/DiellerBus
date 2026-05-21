#!/usr/bin/env python3
"""Simple DB snapshot for SQLite used by the project.

Usage: from the project root run `python scripts/backup_db.py`.
This creates a safe backup of the SQLite database using the sqlite3 backup API.
"""
import os
import sqlite3
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE, 'db.sqlite3')
BACKUP_DIR = os.path.join(BASE, 'backups')

os.makedirs(BACKUP_DIR, exist_ok=True)

if not os.path.exists(DB_PATH):
    print('Database not found at', DB_PATH)
    raise SystemExit(1)

timestamp = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
backup_path = os.path.join(BACKUP_DIR, f'db_backup_{timestamp}.sqlite3')

try:
    src = sqlite3.connect(DB_PATH)
    dest = sqlite3.connect(backup_path)
    with dest:
        src.backup(dest, pages=0, progress=None)
    src.close()
    dest.close()
    print('Backup created at', backup_path)
except Exception as e:
    print('Backup failed:', e)
    raise
