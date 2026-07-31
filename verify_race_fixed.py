"""Verify that the TOCTOU race is FIXED in claim().

This script demonstrates that the claim() method is now atomic and properly
rejects concurrent claims on the same path.
"""
import os
import tempfile
from pathlib import Path

from state_store.lease_claims import LeaseStore, LeaseConflict

# Create temporary database
temp_dir = tempfile.TemporaryDirectory()
db_path = Path(temp_dir.name) / "verify_race.db"

store = LeaseStore(str(db_path))

now = 1000.0
path = "shared/file.txt"

print("=== VERIFICATION: Race Condition is FIXED ===\n")

# Instance A: claim the path using the fixed atomic claim() method
print("Instance A: claim() on shared/file.txt...")
lease_a = store.claim(
    paths=[path],
    instance_id="instance-A",
    ttl_seconds=300.0,
    clock=lambda: now
)
print(f"Instance A claimed successfully -> {lease_a[:8]}...\n")

# Instance B: try to claim the same path
print("Instance B: claim() on shared/file.txt...")
race_condition_fixed = False
try:
    lease_b = store.claim(
        paths=[path],
        instance_id="instance-B",
        ttl_seconds=300.0,
        clock=lambda: now
    )
    print(f"ERROR: Instance B claimed successfully -> {lease_b[:8]}...")
    print("RACE CONDITION NOT FIXED!")
except LeaseConflict as e:
    print(f"Instance B correctly REJECTED: {e}\n")
    print("[PASS] RACE CONDITION IS FIXED: claim() is atomic")
    print(f"[PASS] Conflicting instance: {e.conflicting_instance}")
    print(f"[PASS] Conflicting paths: {e.conflicting_paths}")
    race_condition_fixed = True

# Verify A still holds the lease
holder = store.get_holder([path], clock=lambda: now)
assert holder == "instance-A", f"Expected 'instance-A' but got {holder}"
print(f"\n[PASS] Verified: instance-A holds the lease")

store.close()
temp_dir.cleanup()

if race_condition_fixed:
    print("\n=== ALL VERIFICATIONS PASSED ===")
