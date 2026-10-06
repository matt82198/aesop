#!/usr/bin/env python3
"""CI capability probe: what CI modes can THIS machine run?
INDEX: CI capability probe behind `aesop doctor` (ci-capability section) and the `aesop runner install` preflight: probes OS/version, CPU cores, RAM, Windows code-integrity state (registry `HKLM\\SYSTEM\\CurrentControlSet\\Control\\CI\\Policy` VerifiedAndReputablePolicyState = Smart App Control, and `Win32_DeviceGuard` UsermodeCodeIntegrityPolicyEnforcementStatus), WSL/Docker presence (Linux shards locally), `gh auth status`, `cloudflared` presence, and whether the receipt-gate tools (tools/emit_receipt.py, tools/verify_receipt.py, .github/workflows/verify-receipt.yml -- branch feat/receipt-gate-increment-1, feature-detected) are present; evaluates the table "mode -> runnable here: yes/no + why" for hosted | self-hosted-runner | local-receipt-gate. REPORT-ONLY: always exit 0 (2 = usage); `--json` for machines; `AESOP_CI_PROBE_FIXTURE=<file.json>` injects raw probe results (tests, dry-runs) so no registry/PowerShell/gh is touched; stdlib-only

A machine that enforces Smart App Control (VerifiedAndReputablePolicyState=1) or
user-mode code integrity (UsermodeCodeIntegrityPolicyEnforcementStatus=2) blocks
the unsigned GitHub Actions runner binaries, so `self-hosted-runner` is reported
as not runnable there with that reason -- the same predicate `runner_install.py`
refuses on. Nothing in this module fails the doctor: a missing optional
capability is a row in the table, not an exit code.

CLI: python tools/ci_capability.py [--json] [--repo-root DIR]
Exit: 0 always (report-only); 2 usage error.
"""
import argparse
import ctypes
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))  # sibling imports below resolve when loaded by file path

from common import CI_MODES  # noqa: E402

FIXTURE_ENV = "AESOP_CI_PROBE_FIXTURE"
RECEIPT_LANE = "feat/receipt-gate-increment-1"
SAC_REG_KEY = r"HKLM\SYSTEM\CurrentControlSet\Control\CI\Policy"
SAC_REG_VALUE = "VerifiedAndReputablePolicyState"
UMCI_PS = ("(Get-CimInstance -Namespace root\\Microsoft\\Windows\\DeviceGuard "
           "-ClassName Win32_DeviceGuard).UsermodeCodeIntegrityPolicyEnforcementStatus")

_SAC_STATES = {0: "off", 1: "enforced", 2: "evaluation"}
_UMCI_STATES = {0: "off", 1: "audit", 2: "enforced"}


# --- raw probe helpers -----------------------------------------------------

def _run(cmd, timeout=20):
    """Run a command; (rc, stdout+stderr) or (None, '') when it cannot run."""
    try:
        res = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
        return res.returncode, (res.stdout or "") + (res.stderr or "")
    except (OSError, subprocess.TimeoutExpired):
        return None, ""


def parse_reg_dword(output, value_name):
    """Extract a REG_DWORD from `reg query` output; None when absent."""
    match = re.search(r"%s\s+REG_DWORD\s+0x([0-9a-fA-F]+)" % re.escape(value_name), output or "")
    return int(match.group(1), 16) if match else None


def parse_int_output(output):
    """Parse a bare integer printed by PowerShell; None for anything else."""
    text = (output or "").strip()
    return int(text) if re.fullmatch(r"-?\d+", text) else None


def describe_sac(state):
    """Human word for VerifiedAndReputablePolicyState."""
    return _SAC_STATES.get(state, "unknown")


def describe_umci(state):
    """Human word for UsermodeCodeIntegrityPolicyEnforcementStatus."""
    return _UMCI_STATES.get(state, "unknown")


def _probe_os():
    return {"system": platform.system(), "release": platform.release(), "version": platform.version(),
            "machine": platform.machine(), "hostname": platform.node()}


def _probe_ram_gb():
    try:
        if sys.platform == "win32":
            class _MemStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            status = _MemStatus()
            status.dwLength = ctypes.sizeof(_MemStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return round(status.ullTotalPhys / (1024 ** 3), 1)
            return None
        if sys.platform.startswith("linux"):
            with open("/proc/meminfo", "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("MemTotal:"):
                        return round(int(line.split()[1]) * 1024 / (1024 ** 3), 1)
            return None
        rc, out = _run(["sysctl", "-n", "hw.memsize"])
        value = parse_int_output(out)
        return round(value / (1024 ** 3), 1) if value else None
    except (OSError, ValueError, AttributeError):
        return None


def _probe_windows_code_integrity():
    if platform.system() != "Windows":
        return None
    _, reg_out = _run(["reg", "query", SAC_REG_KEY, "/v", SAC_REG_VALUE])
    _, ps_out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", UMCI_PS], timeout=40)
    return {"smart_app_control_state": parse_reg_dword(reg_out, SAC_REG_VALUE),
            "umci_enforcement_status": parse_int_output(ps_out)}


def _probe_wsl():
    if platform.system() != "Windows":
        return {"present": False, "note": "not Windows: Linux shards run natively"}
    if not shutil.which("wsl"):
        return {"present": False}
    rc, _ = _run(["wsl", "--status"])
    return {"present": rc == 0}


def _probe_which(name):
    return {"present": bool(shutil.which(name))}


def _probe_gh():
    if not shutil.which("gh"):
        return {"present": False, "authenticated": False}
    rc, _ = _run(["gh", "auth", "status"])
    return {"present": True, "authenticated": rc == 0}


def _probe_receipt_tools(repo_root):
    package_root = TOOLS_DIR.parent
    project = Path(repo_root) if repo_root else Path.cwd()
    workflow_rel = Path(".github") / "workflows" / "verify-receipt.yml"
    return {
        "emit_receipt": (package_root / "tools" / "emit_receipt.py").is_file(),
        "verify_receipt": (package_root / "tools" / "verify_receipt.py").is_file(),
        "verify_workflow": (project / workflow_rel).is_file() or (package_root / workflow_rel).is_file(),
    }


def probe_all(repo_root=None):
    """Collect raw probe results. Honors AESOP_CI_PROBE_FIXTURE (a JSON file) verbatim."""
    fixture = os.environ.get(FIXTURE_ENV)
    if fixture:
        with open(fixture, "r", encoding="utf-8") as handle:
            return json.load(handle)
    return {
        "os": _probe_os(),
        "cpu_cores": os.cpu_count(),
        "ram_gb": _probe_ram_gb(),
        "windows_code_integrity": _probe_windows_code_integrity(),
        "wsl": _probe_wsl(),
        "docker": _probe_which("docker"),
        "gh": _probe_gh(),
        "cloudflared": _probe_which("cloudflared"),
        "receipt_tools": _probe_receipt_tools(repo_root),
    }


# --- evaluation --------------------------------------------------------------

def runner_blocked(probes):
    """(blocked, reason): can the unsigned GitHub Actions runner binary execute here?

    Shared verbatim by `aesop runner install`'s preflight so the doctor's row and
    the installer's refusal can never disagree.
    """
    os_info = probes.get("os") or {}
    if os_info.get("system") != "Windows":
        return False, "not Windows: no Smart App Control / UMCI policy applies"
    wci = probes.get("windows_code_integrity") or {}
    sac = wci.get("smart_app_control_state")
    umci = wci.get("umci_enforcement_status")
    reasons = []
    if sac == 1:
        reasons.append("Smart App Control is enforced (%s=1): unsigned runner binaries are blocked" % SAC_REG_VALUE)
    if umci == 2:
        reasons.append("user-mode code integrity is enforced "
                       "(Win32_DeviceGuard UsermodeCodeIntegrityPolicyEnforcementStatus=2): "
                       "unsigned runner binaries are blocked")
    if reasons:
        return True, "; ".join(reasons)
    return False, "Smart App Control %s, UMCI %s: unsigned binaries may run" % (describe_sac(sac), describe_umci(umci))


def windows_summary(probes):
    """The code-integrity facts the doctor prints, interpreted."""
    os_info = probes.get("os") or {}
    blocked, reason = runner_blocked(probes)
    if os_info.get("system") != "Windows":
        return {"smart_app_control": "n/a", "umci": "n/a", "runner_blocked": False, "reason": reason}
    wci = probes.get("windows_code_integrity") or {}
    return {
        "smart_app_control": describe_sac(wci.get("smart_app_control_state")),
        "umci": describe_umci(wci.get("umci_enforcement_status")),
        "runner_blocked": blocked,
        "reason": reason,
    }


def _gh_ok(probes):
    gh = probes.get("gh") or {}
    return bool(gh.get("present")) and bool(gh.get("authenticated"))


def evaluate_modes(probes):
    """The table: one {mode, runnable, why} per CI mode, in CI_MODES order."""
    rows = []
    os_info = probes.get("os") or {}
    is_windows = os_info.get("system") == "Windows"
    gh_ok = _gh_ok(probes)
    cores = probes.get("cpu_cores")
    ram = probes.get("ram_gb")

    why = "runs on GitHub-hosted runners; nothing is required on this machine"
    if not gh_ok:
        why += " (gh auth status is not authenticated: opening/arming PRs from here needs it)"
    rows.append({"mode": "hosted", "runnable": True, "why": why})

    blocked, reason = runner_blocked(probes)
    if blocked:
        rows.append({"mode": "self-hosted-runner", "runnable": False, "why": reason})
    elif not gh_ok:
        rows.append({"mode": "self-hosted-runner", "runnable": False,
                     "why": "gh auth status is not authenticated: the runner registration token comes from `gh api`"})
    else:
        rows.append({"mode": "self-hosted-runner", "runnable": True,
                     "why": "%s; gh authenticated; %s cores / %s GB RAM for runner instances"
                            % (reason, cores if cores is not None else "?", ram if ram is not None else "?")})

    receipt = probes.get("receipt_tools") or {}
    missing = [name for name, key in (("tools/emit_receipt.py", "emit_receipt"),
                                      ("tools/verify_receipt.py", "verify_receipt"),
                                      (".github/workflows/verify-receipt.yml", "verify_workflow"))
               if not receipt.get(key)]
    linux_local = (not is_windows) or bool((probes.get("wsl") or {}).get("present")) \
        or bool((probes.get("docker") or {}).get("present"))
    if missing:
        rows.append({"mode": "local-receipt-gate", "runnable": False,
                     "why": "pending: %s not present in this checkout (receipt lane %s has not landed)"
                            % (", ".join(missing), RECEIPT_LANE)})
    elif not linux_local:
        rows.append({"mode": "local-receipt-gate", "runnable": False,
                     "why": "Linux shards need WSL or Docker on Windows to run locally; neither is present"})
    else:
        rows.append({"mode": "local-receipt-gate", "runnable": True,
                     "why": "receipt tools present; Linux shards can run locally (%s)"
                            % ("native" if not is_windows else "WSL/Docker")})
    assert [r["mode"] for r in rows] == list(CI_MODES)
    return rows


def report(repo_root=None, probes=None):
    """Everything the doctor needs, as one JSON-able dict."""
    probes = probes if probes is not None else probe_all(repo_root)
    return {"probes": probes, "windows": windows_summary(probes), "modes": evaluate_modes(probes)}


def render_text(data):
    """The human table (also what `aesop doctor` prints from the JSON)."""
    probes = data["probes"]
    os_info = probes.get("os") or {}
    win = data["windows"]
    lines = ["CI capability (report-only)"]
    lines.append("  %-20s %s %s (%s)" % ("OS", os_info.get("system", "?"), os_info.get("release", ""), os_info.get("version", "")))
    lines.append("  %-20s %s cores / %s GB" % ("CPU / RAM", probes.get("cpu_cores", "?"), probes.get("ram_gb", "?")))
    lines.append("  %-20s %s" % ("Smart App Control", win["smart_app_control"]))
    lines.append("  %-20s %s" % ("UMCI", win["umci"]))
    lines.append("  %-20s %s / %s" % ("WSL / Docker",
                                      "yes" if (probes.get("wsl") or {}).get("present") else "no",
                                      "yes" if (probes.get("docker") or {}).get("present") else "no"))
    gh = probes.get("gh") or {}
    lines.append("  %-20s %s" % ("gh auth", "authenticated" if gh.get("authenticated") else
                                 ("present, not authenticated" if gh.get("present") else "absent")))
    lines.append("  %-20s %s" % ("cloudflared", "present" if (probes.get("cloudflared") or {}).get("present") else "absent"))
    lines.append("")
    lines.append("  %-22s %-15s %s" % ("mode", "runnable here", "why"))
    for row in data["modes"]:
        lines.append("  %-22s %-15s %s" % (row["mode"], "yes" if row["runnable"] else "no", row["why"]))
    return "\n".join(lines)


def main(argv=None):
    """CLI entry point (report-only; exit 0, or 2 on usage error)."""
    parser = argparse.ArgumentParser(description="Report which aesop CI modes this machine can run.")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of the text table")
    parser.add_argument("--repo-root", default=None, help="project directory to feature-detect the receipt workflow in (default: cwd)")
    args = parser.parse_args(argv)
    data = report(args.repo_root)
    if args.json:
        sys.stdout.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(render_text(data) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
