#!/usr/bin/env bash
# Online backup of the bot's SQLite database.
#
# Uses SQLite's backup API, not `cp`: the bot runs in WAL mode, so recent
# writes live in a separate -wal file and a plain copy taken while the bot is
# running can be inconsistent or missing rows. This is safe on a live DB —
# no need to stop the service.
#
# Usage:  bash scripts/backup_db.sh
# Cron:   see docs/ADMIN_GUIDE.md ("Backups")
#
# Override any of these with env vars if paths differ:
set -euo pipefail

APP_DIR="${APP_DIR:-/root/mental_training_bot}"
BACKUP_DIR="${BACKUP_DIR:-/root/backups}"
PYTHON="${PYTHON:-$APP_DIR/venv/bin/python}"
DB_FILE="${DB_FILE:-$APP_DIR/mental_training.db}"
KEEP_DAYS="${KEEP_DAYS:-14}"

mkdir -p "$BACKUP_DIR"
DEST="$BACKUP_DIR/mtb_$(date +%F_%H%M).db"

"$PYTHON" - "$DB_FILE" "$DEST" <<'PY'
import sqlite3
import sys

src, dest = sys.argv[1], sys.argv[2]
# Read-only source connection: a backup must never be able to write to the
# database the bot is using.
source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
target = sqlite3.connect(dest)
with target:
    source.backup(target)
target.close()
source.close()
PY

# Integrity-check the copy — a backup you never verified is a guess.
"$PYTHON" - "$DEST" <<'PY'
import sqlite3
import sys

conn = sqlite3.connect(sys.argv[1])
result = conn.execute("PRAGMA integrity_check").fetchone()[0]
conn.close()
if result != "ok":
    sys.exit(f"integrity check FAILED: {result}")
PY

find "$BACKUP_DIR" -name 'mtb_*.db' -mtime +"$KEEP_DAYS" -delete

echo "backup ok: $DEST ($(du -h "$DEST" | cut -f1))"
