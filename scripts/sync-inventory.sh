#!/usr/bin/env bash
# Trigger a manual NetBox inventory sync against the dashboard.
#
# Usage:
#   ./scripts/sync-inventory.sh [DASHBOARD_URL] [API_TOKEN]
#
# Defaults:
#   DASHBOARD_URL  http://localhost:8080
#   API_TOKEN      env STACKHIVE_API_TOKEN (session bearer is not used; the
#                  endpoint requires an editor session — use a service token
#                  exposed by your fronting proxy or a dedicated API user)
set -euo pipefail

DASHBOARD_URL="${1:-${DASHBOARD_URL:-http://localhost:8080}}"
API_TOKEN="${2:-${STACKHIVE_API_TOKEN:-}}"

echo "Syncing NetBox inventory via ${DASHBOARD_URL}/api/inventory/sync"
response=$(curl -fsS -X POST "${DASHBOARD_URL}/api/inventory/sync" \
  ${API_TOKEN:+-H "Authorization: Bearer ${API_TOKEN}"} \
  -H "Content-Type: application/json")
echo "${response}"
