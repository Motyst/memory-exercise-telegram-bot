#!/usr/bin/env bash
# Deploy the bot on the VPS: refuse on a dirty tree, fast-forward pull,
# restart, verify. Idempotent — safe to rerun.
#
# From your machine (PowerShell or bash — no nested quotes, on purpose):
#   ssh root@<VPS_IP> bash /root/mental_training_bot/scripts/deploy.sh
#
# Why the dirty-tree check: files edited by hand on the server once blocked
# a pull for weeks while the bot quietly ran old code. Stash or discard
# server edits deliberately; never let a deploy paper over them.
set -euo pipefail

APP_DIR="${APP_DIR:-/root/mental_training_bot}"
SERVICE="${SERVICE:-mental_training_bot}"

cd "$APP_DIR"

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "REFUSING TO DEPLOY: tracked files were edited on the server:" >&2
  git status --short --untracked-files=no >&2
  echo "Stash them (git stash push -m server-edits) or discard them, then rerun." >&2
  exit 1
fi

BEFORE=$(git rev-parse --short HEAD)
git pull --ff-only
AFTER=$(git rev-parse --short HEAD)
if [ "$BEFORE" = "$AFTER" ]; then
  echo "Already at $AFTER — restarting anyway."
fi

systemctl restart "$SERVICE"
sleep 6

STATUS=$(systemctl is-active "$SERVICE" || true)
echo "service: $STATUS"
# The startup log names the running build; errors here mean a bad deploy.
journalctl -u "$SERVICE" --since "-30s" --no-pager -o cat \
  | grep -E "build|Bot started|ERROR|Traceback" || true

if [ "$STATUS" != "active" ]; then
  echo "DEPLOY FAILED — service is not active. Full log:" >&2
  journalctl -u "$SERVICE" -n 30 --no-pager -o cat >&2
  exit 1
fi
echo "deployed $BEFORE -> $AFTER"
