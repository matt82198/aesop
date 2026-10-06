#!/bin/bash
# Tests for linux_shape_check.py WSL detection
set -e

test_wsl_available_utf16le_decoding() {
  echo "Test: WSL detection with UTF-16LE encoded output"
  
  python3 << 'PYEOF'
# Simulate wsl.exe -l -q returning UTF-16LE with BOM and newlines
bom = b'\xff\xfe'  # UTF-16LE BOM
ubuntu = 'Ubuntu'.encode('utf-16-le')
newline_utf16le = '\n'.encode('utf-16-le')
debian = 'Debian'.encode('utf-16-le')
output_bytes = bom + ubuntu + newline_utf16le + debian + newline_utf16le

# FIXED - decode as UTF-16LE  
fixed_output = output_bytes.decode('utf-16-le')
distros_fixed = [d.strip() for d in fixed_output.split("\n") if d.strip()]

# Verify: fixed should correctly detect 2 distros
if len(distros_fixed) == 2:
    print("[PASS] UTF-16LE decoding correctly detects distros")
    exit(0)
else:
    print("[FAIL] UTF-16LE decoding failed, got {} distros".format(len(distros_fixed)))
    exit(1)
PYEOF

  echo "PASS: UTF-16LE decoding test"
}

test_wsl_available_utf16le_decoding
