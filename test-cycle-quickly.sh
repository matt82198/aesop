#!/usr/bin/env bash
set -uo pipefail

# Quick test: run backup-fleet with empty fixture
TEST_DIR="/tmp/aesop-quick-test-$$"
mkdir -p "$TEST_DIR/aesop-fixture/state" "$TEST_DIR/aesop-fixture/tools" "$TEST_DIR/conductor-fixture/state"

# Create mock scanner
cat > "$TEST_DIR/aesop-fixture/tools/secret_scan.py" << 'EOF'
#!/usr/bin/env python3
import sys
sys.exit(0)
EOF
chmod +x "$TEST_DIR/aesop-fixture/tools/secret_scan.py"

# Run cycle with timeout
export CONDUCTOR_ROOT="$TEST_DIR/conductor-fixture"
AESOP_ROOT="$TEST_DIR/aesop-fixture" timeout 10 bash daemons/backup-fleet.sh 2>&1 | tail -20
EXIT_CODE=$?

echo ""
echo "Exit code: $EXIT_CODE"

if [ -f "$TEST_DIR/conductor-fixture/state/.watchdog-heartbeat" ]; then
  HB_TIME=$(cat "$TEST_DIR/conductor-fixture/state/.watchdog-heartbeat")
  NOW=$(date +%s)
  AGE=$((NOW - HB_TIME))
  echo "Heartbeat file exists: $HB_TIME (age: ${AGE}s)"
else
  echo "Heartbeat file missing!"
fi

rm -rf "$TEST_DIR"
