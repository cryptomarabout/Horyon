#!/usr/bin/env bash
# Run a SonarQube analysis against the local SonarQube server.
#
# Usage:
#   scripts/sonar-scan.sh
#   SONAR_TOKEN=sqp_xxx scripts/sonar-scan.sh   # override token
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SONAR_HOST_URL="http://localhost:9000"
SONAR_INTERNAL_URL="http://sonarqube:9000"

# Default token (admin account, localhost-only server — no secrets here).
SONAR_TOKEN="${SONAR_TOKEN:-sqa_abde998cceeb0f3a47606ed2076fa7b26e0e8fc9}"

# Python env: prefer a project .venv, fall back to the shared scan venv.
PYTHON_BIN="python3"
for candidate in \
  "${PROJECT_ROOT}/.venv/bin/python" \
  "/tmp/sonar-venv/bin/python"; do
  if [ -x "$candidate" ]; then
    PYTHON_BIN="$candidate"
    break
  fi
done

# ── Preflight ──────────────────────────────────────────────────────────────────

# SonarQube's embedded Elasticsearch requires vm.max_map_count >= 524288.
current_map=$(sysctl -n vm.max_map_count 2>/dev/null || echo 0)
if [ "$current_map" -lt 524288 ]; then
  echo "Setting vm.max_map_count=524288 (requires sudo)..."
  sudo sysctl -w vm.max_map_count=524288
fi

if ! curl -sf "${SONAR_HOST_URL}/api/system/status" | grep -q '"status":"UP"'; then
  echo "ERROR: SonarQube is not running at ${SONAR_HOST_URL}"
  echo "       docker compose -f docker-compose.sonar.yml up -d"
  exit 1
fi

# ── Python coverage ────────────────────────────────────────────────────────────

echo ""
echo "==> Running Python tests with coverage..."
cd "$PROJECT_ROOT"

set +e
"$PYTHON_BIN" -m pytest \
  --cov=app \
  --cov-report=xml:"${PROJECT_ROOT}/coverage.xml" \
  -q
PYTEST_EXIT=$?
set -e

[ $PYTEST_EXIT -ne 0 ] && echo "WARN: Some tests failed (exit $PYTEST_EXIT) — scan will still proceed."

# ── SonarQube scan ─────────────────────────────────────────────────────────────

echo ""
echo "==> Running SonarQube scanner..."

docker run --rm \
  --network sonar \
  -v "${PROJECT_ROOT}:/usr/src" \
  -e SONAR_HOST_URL="${SONAR_INTERNAL_URL}" \
  -e SONAR_TOKEN="${SONAR_TOKEN}" \
  sonarsource/sonar-scanner-cli:latest \
  -Dsonar.projectBaseDir=/usr/src

echo ""
echo "==> Done → ${SONAR_HOST_URL}/dashboard?id=horyon"
