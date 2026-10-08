#!/bin/bash
# Job 173 Fix Deployment Script
# Run this on the production server: bash deploy-job173.sh

set -e
cd /opt/turb-gpt-register

echo "Checking for running jobs..."
RUNNING=$(sqlite3 turb.sqlite3 "SELECT COUNT(*) FROM registration_jobs WHERE status='running'")
if [ "$RUNNING" -gt 0 ]; then
  echo "ERROR: $RUNNING running jobs found, aborting deployment"
  exit 1
fi
echo "No running jobs, proceeding..."

echo "Creating backup..."
BACKUP_DIR="deploy-backups"
mkdir -p "$BACKUP_DIR"
cp core/cloakbrowser_registration.py "$BACKUP_DIR/cloak-job173-pre-37de796.py"
cp core/browser_traffic.py "$BACKUP_DIR/traffic-job173-pre-37de796.py"
echo "Backup created in $BACKUP_DIR"

echo "Pulling changes from origin/deploy/all-local-changes..."
git fetch origin deploy/all-local-changes
git checkout deploy/all-local-changes
git reset --hard origin/deploy/all-local-changes

echo "Verifying Python syntax..."
python3 -m py_compile core/cloakbrowser_registration.py core/browser_traffic.py

echo "Restarting service..."
sudo systemctl restart turb-gpt-register
sleep 3

echo "Checking service status..."
if sudo systemctl is-active turb-gpt-register; then
  echo "✓ Service is active"
else
  echo "✗ Service failed to start"
  exit 1
fi

echo "Verifying deployed commit..."
git log -1 --oneline

echo ""
echo "=========================================="
echo "Deployment complete: 37de796"
echo "Job 173 fix: Non-blocking traffic snapshot"
echo "=========================================="
