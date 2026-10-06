#!/usr/bin/env python3
"""Tests for tools/multibox_preflight.py (multibox Inc 0).

Covers the network-filesystem guard (detect_fs_kind / assert_local_sqlite), the
visibility-delay and clock-skew probes, the aggregate preflight report, and the
CLI contract (exit 0 clean / 1 findings / 2 error).

Hermetic: no network, no real NFS/SMB mount required. POSIX detection is driven
by injected /proc/mounts fixtures; Windows detection by an injected
GetDriveTypeW shim. All filesystem work happens in temp dirs.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL_PATH = os.path.join(REPO_ROOT, "tools", "multibox_preflight.py")
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import tools.multibox_preflight as mp  # noqa: E402

# /proc/mounts fixtures: "device mountpoint fstype options dump pass"
MOUNTS_LOCAL = (
    "/dev/sda1 / ext4 rw,relatime 0 0\n"
    "tmpfs /tmp tmpfs rw,nosuid 0 0\n"
    "/dev/sda2 /home xfs rw,relatime 0 0\n"
)
MOUNTS_NFS4 = MOUNTS_LOCAL + "srv:/export /mnt/share nfs4 rw,vers=4.1 0 0\n"
MOUNTS_CIFS = MOUNTS_LOCAL + "//srv/share /mnt/share cifs rw,vers=3.0 0 0\n"
MOUNTS_SSHFS = MOUNTS_LOCAL + "user@srv:/d /mnt/share fuse.sshfs rw 0 0\n"
MOUNTS_NESTED = MOUNTS_NFS4 + "/dev/sdb1 /mnt/share/local ext4 rw 0 0\n"
# Mount points containing an octal-escaped space, as /proc/mounts encodes them.
MOUNTS_ESCAPED = MOUNTS_LOCAL + "srv:/e /mnt/my\\040share nfs rw 0 0\n"


def _posix(path, mounts_text):
    """detect_fs_kind on a simulated POSIX box with the given mount table."""
    return mp.detect_fs_kind(path, os_name="posix", mounts_text=mounts_text)


def _windows(path, drive_type):
    """detect_fs_kind on a simulated Windows box with a GetDriveTypeW shim."""
    return mp.detect_fs_kind(
        path, os_name="nt", drive_type_fn=lambda root: drive_type
    )


class TestDetectFsKindPosix(unittest.TestCase):
    def test_nfs4_mount_is_network(self):
        self.assertEqual(_posix("/mnt/share/claims", MOUNTS_NFS4), "network")

    def test_nfs_mount_is_network(self):
        mounts = MOUNTS_LOCAL + "srv:/e /mnt/share nfs rw 0 0\n"
        self.assertEqual(_posix("/mnt/share", mounts), "network")

    def test_cifs_mount_is_network(self):
        self.assertEqual(_posix("/mnt/share/x/y", MOUNTS_CIFS), "network")

    def test_sshfs_mount_is_network(self):
        self.assertEqual(_posix("/mnt/share/a", MOUNTS_SSHFS), "network")

    def test_all_documented_network_types_detected(self):
        for fstype in ("nfs", "nfs4", "cifs", "smbfs", "smb3",
                       "fuse.sshfs", "glusterfs"):
            mounts = MOUNTS_LOCAL + "dev /mnt/share %s rw 0 0\n" % fstype
            self.assertEqual(
                _posix("/mnt/share/db", mounts), "network", fstype)

    def test_ext4_mount_is_local(self):
        self.assertEqual(_posix("/home/matt/state.db", MOUNTS_LOCAL), "local")

    def test_tmpfs_mount_is_local(self):
        self.assertEqual(_posix("/tmp/probe", MOUNTS_LOCAL), "local")

    def test_longest_prefix_wins_local_under_network(self):
        # /mnt/share is nfs4 but /mnt/share/local is a local ext4 mount.
        self.assertEqual(_posix("/mnt/share/local/db", MOUNTS_NESTED), "local")
        self.assertEqual(_posix("/mnt/share/other", MOUNTS_NESTED), "network")

    def test_mountpoint_prefix_must_be_a_path_component(self):
        # /mnt/shared-other must NOT match the /mnt/share nfs4 mount.
        self.assertEqual(_posix("/mnt/shareother/db", MOUNTS_NFS4), "local")

    def test_octal_escaped_mountpoint_decoded(self):
        self.assertEqual(_posix("/mnt/my share/db", MOUNTS_ESCAPED), "network")

    def test_no_matching_mount_is_unknown(self):
        # A mount table with no root entry cannot classify anything.
        self.assertEqual(_posix("/data/db", "tmpfs /tmp tmpfs rw 0 0\n"),
                         "unknown")

    def test_unreadable_proc_mounts_is_unknown(self):
        self.assertEqual(_posix("/data/db", None), "unknown")

    def test_missing_proc_mounts_file_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "no-such-mounts")
            self.assertEqual(
                mp.detect_fs_kind("/data/db", os_name="posix",
                                  proc_mounts_path=missing),
                "unknown",
            )

    def test_proc_mounts_read_from_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            mounts = os.path.join(tmp, "mounts")
            with open(mounts, "w", encoding="utf-8") as fh:
                fh.write(MOUNTS_NFS4)
            self.assertEqual(
                mp.detect_fs_kind("/mnt/share/db", os_name="posix",
                                  proc_mounts_path=mounts),
                "network",
            )

    def test_explicit_none_mounts_beats_a_readable_proc_mounts(self):
        # Linux-parity guard: on Linux /proc/mounts exists, on Windows it does
        # not, so "mounts_text=None falls back to the real mount table" would
        # classify the same call local on Linux and unknown on Windows.
        with tempfile.TemporaryDirectory() as tmp:
            mounts = os.path.join(tmp, "mounts")
            with open(mounts, "w", encoding="utf-8") as fh:
                fh.write(MOUNTS_LOCAL)
            original = mp.PROC_MOUNTS_PATH
            mp.PROC_MOUNTS_PATH = mounts
            try:
                self.assertEqual(
                    mp.detect_fs_kind("/x/db", os_name="posix",
                                      mounts_text=None),
                    "unknown",
                )
                # Omitting mounts_text DOES consult the mount table.
                self.assertEqual(
                    mp.detect_fs_kind("/x/db", os_name="posix"), "local")
            finally:
                mp.PROC_MOUNTS_PATH = original

    def test_malformed_mount_lines_are_skipped(self):
        mounts = "garbage\n\n/dev/sda1 / ext4 rw 0 0\n"
        self.assertEqual(_posix("/x", mounts), "local")


class TestDetectFsKindWindows(unittest.TestCase):
    def test_drive_remote_is_network(self):
        self.assertEqual(_windows("Z:\\claims", mp.DRIVE_REMOTE), "network")

    def test_drive_fixed_is_local(self):
        self.assertEqual(_windows("C:\\Users\\x\\state.db", mp.DRIVE_FIXED),
                         "local")

    def test_drive_ramdisk_is_local(self):
        self.assertEqual(_windows("R:\\db", mp.DRIVE_RAMDISK), "local")

    def test_drive_unknown_code_is_unknown(self):
        self.assertEqual(_windows("Q:\\db", mp.DRIVE_UNKNOWN), "unknown")

    def test_drive_no_root_dir_is_unknown(self):
        self.assertEqual(_windows("Q:\\db", mp.DRIVE_NO_ROOT_DIR), "unknown")

    def test_unavailable_ctypes_is_unknown(self):
        def boom(root):
            raise OSError("no ctypes")

        self.assertEqual(
            mp.detect_fs_kind("C:\\db", os_name="nt", drive_type_fn=boom),
            "unknown",
        )

    def test_relative_path_without_drive_is_unknown(self):
        self.assertEqual(_windows("relative\\db", mp.DRIVE_FIXED), "unknown")


class TestUncDetection(unittest.TestCase):
    """UNC prefixes are network on every platform (fail-closed by design)."""

    def test_unc_backslash_prefix_is_network_on_windows(self):
        self.assertEqual(_windows("\\\\srv\\share\\state.db", mp.DRIVE_FIXED),
                         "network")

    def test_unc_backslash_prefix_is_network_on_posix(self):
        self.assertEqual(_posix("\\\\srv\\share\\state.db", MOUNTS_LOCAL),
                         "network")

    def test_extended_unc_prefix_is_network(self):
        self.assertEqual(
            _windows("\\\\?\\UNC\\srv\\share\\db", mp.DRIVE_FIXED), "network")

    def test_forward_slash_unc_is_network_on_windows(self):
        self.assertEqual(_windows("//srv/share/db", mp.DRIVE_FIXED), "network")

    def test_is_unc_path_helper(self):
        self.assertTrue(mp.is_unc_path("\\\\srv\\share"))
        self.assertFalse(mp.is_unc_path("C:\\Users"))
        self.assertFalse(mp.is_unc_path("/mnt/share"))


class TestFailClosed(unittest.TestCase):
    def test_unknown_is_treated_as_network(self):
        self.assertTrue(mp.is_network_kind("unknown"))
        self.assertTrue(mp.is_network_kind("network"))
        self.assertFalse(mp.is_network_kind("local"))

    def test_unrecognised_kind_is_treated_as_network(self):
        self.assertTrue(mp.is_network_kind("banana"))


class TestAssertLocalSqlite(unittest.TestCase):
    def test_raises_on_unc_path(self):
        with self.assertRaises(mp.NetworkFilesystemError):
            mp.assert_local_sqlite("\\\\srv\\share\\state.db")

    def test_raises_on_nfs_mount(self):
        with self.assertRaises(mp.NetworkFilesystemError):
            mp.assert_local_sqlite("/mnt/share/state.db", os_name="posix",
                                   mounts_text=MOUNTS_NFS4)

    def test_raises_on_unknown_kind(self):
        with self.assertRaises(mp.NetworkFilesystemError):
            mp.assert_local_sqlite("/data/state.db", os_name="posix",
                                   mounts_text=None)

    def test_passes_on_local_mount(self):
        mp.assert_local_sqlite("/home/matt/state.db", os_name="posix",
                               mounts_text=MOUNTS_LOCAL)

    def test_passes_on_real_tempdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "state.db")
            if mp.detect_fs_kind(db) == "unknown":
                self.skipTest("filesystem kind undetectable on this platform")
            mp.assert_local_sqlite(db)

    def test_error_message_is_ascii_and_names_the_path(self):
        try:
            mp.assert_local_sqlite("/mnt/share/state.db", os_name="posix",
                                   mounts_text=MOUNTS_CIFS)
        except mp.NetworkFilesystemError as exc:
            msg = str(exc)
        else:
            self.fail("expected NetworkFilesystemError")
        msg.encode("ascii")
        self.assertIn("/mnt/share/state.db", msg)
        self.assertIn("network", msg.lower())

    def test_error_message_carries_mount_option_guidance(self):
        try:
            mp.assert_local_sqlite("/mnt/share/state.db", os_name="posix",
                                   mounts_text=MOUNTS_NFS4)
        except mp.NetworkFilesystemError as exc:
            msg = str(exc)
        else:
            self.fail("expected NetworkFilesystemError")
        self.assertIn("WAL", msg)

    def test_exception_exposes_path_and_kind(self):
        try:
            mp.assert_local_sqlite("/mnt/share/state.db", os_name="posix",
                                   mounts_text=MOUNTS_NFS4)
        except mp.NetworkFilesystemError as exc:
            self.assertEqual(exc.kind, "network")
            self.assertIn("state.db", exc.path)


class TestPercentiles(unittest.TestCase):
    def test_single_sample(self):
        self.assertEqual(mp.percentile([0.5], 99), 0.5)

    def test_known_distribution(self):
        values = [float(n) for n in range(1, 101)]
        self.assertAlmostEqual(mp.percentile(values, 50), 50.5, places=6)
        self.assertAlmostEqual(mp.percentile(values, 100), 100.0, places=6)
        self.assertAlmostEqual(mp.percentile(values, 0), 1.0, places=6)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            mp.percentile([], 50)


class TestVisibilityDelay(unittest.TestCase):
    def test_local_tmpdir_delay_is_near_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = mp.measure_visibility_delay(tmp, samples=5)
        self.assertEqual(result["samples"], 5)
        self.assertEqual(len(result["delays_seconds"]), 5)
        for key in ("p50_seconds", "p95_seconds", "p99_seconds",
                    "max_seconds"):
            self.assertIn(key, result)
            # Generous bound: a same-process write must be visible instantly.
            self.assertLess(result[key], 5.0)
            self.assertGreaterEqual(result[key], 0.0)

    def test_percentiles_are_monotonic(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = mp.measure_visibility_delay(tmp, samples=4)
        self.assertLessEqual(result["p50_seconds"], result["p95_seconds"])
        self.assertLessEqual(result["p95_seconds"], result["p99_seconds"])
        self.assertLessEqual(result["p99_seconds"], result["max_seconds"])

    def test_probe_files_are_cleaned_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            mp.measure_visibility_delay(tmp, samples=3)
            leftovers = [n for n in os.listdir(tmp)
                         if n != mp.PROBE_DIRNAME]
            self.assertEqual(leftovers, [])
            probe_dir = os.path.join(tmp, mp.PROBE_DIRNAME)
            if os.path.isdir(probe_dir):
                self.assertEqual(
                    [n for n in os.listdir(probe_dir)
                     if n.startswith("visprobe-")], [])

    def test_does_not_pollute_cwd(self):
        before = sorted(os.listdir(os.getcwd()))
        with tempfile.TemporaryDirectory() as tmp:
            mp.measure_visibility_delay(tmp, samples=2)
        self.assertEqual(before, sorted(os.listdir(os.getcwd())))

    def test_zero_samples_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                mp.measure_visibility_delay(tmp, samples=0)

    def test_missing_directory_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(mp.PreflightError):
                mp.measure_visibility_delay(os.path.join(tmp, "nope"),
                                            samples=1)

    def test_timeout_reports_unbounded_delay(self):
        # A listing that never reveals the probe must fail closed, not hang.
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(mp.VisibilityTimeout):
                mp.measure_visibility_delay(
                    tmp, samples=1, timeout_seconds=0.2,
                    interval_seconds=0.01,
                    listdir_fn=lambda d: [],
                )

    def test_revalidated_listdir_sees_a_new_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "hello.txt")
            with open(target, "w", encoding="utf-8") as fh:
                fh.write("x")
            self.assertIn("hello.txt", mp.revalidated_listdir(tmp))


class TestClockSkew(unittest.TestCase):
    def test_local_tmpdir_skew_is_small(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = mp.measure_clock_skew(tmp, samples=3)
        self.assertEqual(len(result["samples_seconds"]), 3)
        self.assertLess(abs(result["median_seconds"]), 5.0)
        self.assertLess(result["max_abs_seconds"], 5.0)
        self.assertEqual(result["method"], "server-mtime")

    def test_cleans_up_and_does_not_pollute_cwd(self):
        before = sorted(os.listdir(os.getcwd()))
        with tempfile.TemporaryDirectory() as tmp:
            mp.measure_clock_skew(tmp, samples=2)
            probe_dir = os.path.join(tmp, mp.PROBE_DIRNAME)
            if os.path.isdir(probe_dir):
                self.assertEqual(
                    [n for n in os.listdir(probe_dir)
                     if n.startswith("skewprobe-")], [])
        self.assertEqual(before, sorted(os.listdir(os.getcwd())))

    def test_missing_directory_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(mp.PreflightError):
                mp.measure_clock_skew(os.path.join(tmp, "nope"), samples=1)


class TestPeerProbe(unittest.TestCase):
    """Two-sided mode, exercised single-process with two peer ids."""

    def test_peer_records_are_visible_to_the_other_peer(self):
        with tempfile.TemporaryDirectory() as tmp:
            for seq in range(3):
                mp.write_peer_probe(tmp, "boxA", seq)
            observed = mp.observe_peer_delays(tmp, "boxB")
        self.assertEqual(len(observed), 3)
        for rec in observed:
            self.assertEqual(rec["peer_id"], "boxA")
            self.assertLess(abs(rec["delay_seconds"]), 5.0)

    def test_own_records_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            mp.write_peer_probe(tmp, "boxA", 0)
            self.assertEqual(mp.observe_peer_delays(tmp, "boxA"), [])

    def test_corrupt_peer_record_is_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            mp.write_peer_probe(tmp, "boxA", 0)
            peers = os.path.join(tmp, mp.PROBE_DIRNAME, mp.PEER_DIRNAME)
            with open(os.path.join(peers, "peerprobe-truncated.json"),
                      "w", encoding="utf-8") as fh:
                fh.write("{not json")
            observed = mp.observe_peer_delays(tmp, "boxB")
        self.assertEqual(len(observed), 1)

    def test_no_peers_yields_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(mp.observe_peer_delays(tmp, "boxA"), [])

    def test_peer_record_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = mp.write_peer_probe(tmp, "boxA", 7)
            with open(path, "r", encoding="utf-8") as fh:
                rec = json.load(fh)
        self.assertEqual(rec["peer_id"], "boxA")
        self.assertEqual(rec["seq"], 7)
        self.assertIsInstance(rec["epoch_ms"], int)
        self.assertEqual(rec["v"], 1)


class TestRunPreflight(unittest.TestCase):
    def _report(self, tmp, **kwargs):
        db = os.path.join(tmp, "state.db")
        return mp.run_preflight(shared_dir=tmp, db_path=db, samples=3,
                                **kwargs)

    def test_clean_run_on_local_tempdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self._report(tmp, fs_kind_fn=lambda p: "local")
        self.assertTrue(report["ok"])
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["db"]["fs_kind"], "local")
        self.assertIn("visibility", report)
        self.assertIn("clock_skew", report)

    def test_network_db_is_a_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self._report(
                tmp,
                fs_kind_fn=lambda p: "network" if p.endswith(".db") else "local",
            )
        self.assertFalse(report["ok"])
        ids = [f["id"] for f in report["findings"]]
        self.assertIn("DB-ON-NETWORK-FS", ids)

    def test_unknown_db_fs_is_a_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self._report(tmp, fs_kind_fn=lambda p: "unknown")
        self.assertFalse(report["ok"])
        self.assertIn("DB-ON-NETWORK-FS",
                      [f["id"] for f in report["findings"]])

    def test_visibility_over_settle_is_a_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = mp.run_preflight(
                shared_dir=tmp, db_path=os.path.join(tmp, "state.db"),
                samples=2, settle_seconds=0.0,
                fs_kind_fn=lambda p: "local",
                visibility_fn=lambda *a, **k: {
                    "samples": 2, "delays_seconds": [9.0, 9.0],
                    "p50_seconds": 9.0, "p95_seconds": 9.0,
                    "p99_seconds": 9.0, "max_seconds": 9.0,
                },
            )
        self.assertFalse(report["ok"])
        self.assertIn("VISIBILITY-DELAY-EXCEEDS-SETTLE",
                      [f["id"] for f in report["findings"]])

    def test_clock_skew_over_bound_is_a_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = mp.run_preflight(
                shared_dir=tmp, db_path=os.path.join(tmp, "state.db"),
                samples=2, max_skew_seconds=1.0,
                fs_kind_fn=lambda p: "local",
                clock_skew_fn=lambda *a, **k: {
                    "samples_seconds": [40.0, 41.0], "median_seconds": 40.5,
                    "max_abs_seconds": 41.0, "method": "server-mtime",
                },
            )
        self.assertFalse(report["ok"])
        self.assertIn("CLOCK-SKEW-EXCEEDS-BOUND",
                      [f["id"] for f in report["findings"]])

    def test_visibility_timeout_becomes_a_finding_not_a_crash(self):
        def boom(*a, **k):
            raise mp.VisibilityTimeout("probe never became visible")

        with tempfile.TemporaryDirectory() as tmp:
            report = mp.run_preflight(
                shared_dir=tmp, db_path=os.path.join(tmp, "state.db"),
                samples=1, fs_kind_fn=lambda p: "local",
                visibility_fn=boom)
        self.assertFalse(report["ok"])
        self.assertIn("VISIBILITY-UNBOUNDED",
                      [f["id"] for f in report["findings"]])

    def test_missing_shared_dir_raises_preflight_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(mp.PreflightError):
                mp.run_preflight(shared_dir=os.path.join(tmp, "nope"),
                                 db_path=os.path.join(tmp, "state.db"),
                                 samples=1, fs_kind_fn=lambda p: "local")

    def test_report_is_json_serialisable_and_ascii(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self._report(tmp, fs_kind_fn=lambda p: "local")
        json.dumps(report).encode("ascii")

    def test_findings_carry_id_severity_and_detail(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self._report(tmp, fs_kind_fn=lambda p: "network")
        for finding in report["findings"]:
            self.assertIn("id", finding)
            self.assertIn("severity", finding)
            self.assertIn("detail", finding)


def _run_cli(args, cwd):
    return subprocess.run(
        [sys.executable, TOOL_PATH] + args,
        cwd=cwd, capture_output=True, text=True, encoding="utf-8",
        timeout=120,
    )


class TestCli(unittest.TestCase):
    def test_help_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = _run_cli(["--help"], tmp)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--shared-dir", proc.stdout)

    def test_unknown_flag_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = _run_cli(["--not-a-flag"], tmp)
        self.assertEqual(proc.returncode, 2)

    def test_check_on_local_tempdir_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "state.db")
            if mp.detect_fs_kind(db) == "unknown":
                self.skipTest("filesystem kind undetectable on this platform")
            proc = _run_cli(
                ["--check", "--shared-dir", tmp, "--db", db, "--samples", "3"],
                tmp)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK", proc.stdout)

    def test_json_output_parses(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "state.db")
            proc = _run_cli(
                ["--check", "--shared-dir", tmp, "--db", db,
                 "--samples", "2", "--json"], tmp)
        self.assertIn(proc.returncode, (0, 1), proc.stdout + proc.stderr)
        report = json.loads(proc.stdout)
        self.assertIn("findings", report)
        self.assertIn("visibility", report)

    def test_unc_db_exits_one_with_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = _run_cli(
                ["--check", "--shared-dir", tmp,
                 "--db", "\\\\srv\\share\\state.db",
                 "--samples", "2", "--json"], tmp)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        report = json.loads(proc.stdout)
        self.assertIn("DB-ON-NETWORK-FS", [f["id"] for f in report["findings"]])

    def test_missing_shared_dir_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = _run_cli(
                ["--check", "--shared-dir", os.path.join(tmp, "nope"),
                 "--db", os.path.join(tmp, "state.db")], tmp)
        self.assertEqual(proc.returncode, 2)

    def test_missing_required_args_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = _run_cli(["--check"], tmp)
        self.assertEqual(proc.returncode, 2)

    def test_output_is_ascii(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "state.db")
            proc = _run_cli(
                ["--check", "--shared-dir", tmp, "--db", db, "--samples", "2"],
                tmp)
        proc.stdout.encode("ascii")
        proc.stderr.encode("ascii")

    def test_peer_probe_mode_runs_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "state.db")
            proc = _run_cli(
                ["--check", "--shared-dir", tmp, "--db", db, "--samples", "2",
                 "--peer-probe", "--peer-id", "boxA",
                 "--peer-wait-seconds", "0.2", "--json"], tmp)
        self.assertIn(proc.returncode, (0, 1), proc.stdout + proc.stderr)
        report = json.loads(proc.stdout)
        self.assertIn("peer_probe", report)
        self.assertEqual(report["peer_probe"]["peer_id"], "boxA")

    def test_does_not_write_into_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = os.path.join(tmp, "work")
            shared = os.path.join(tmp, "shared")
            os.makedirs(work)
            os.makedirs(shared)
            _run_cli(["--check", "--shared-dir", shared,
                      "--db", os.path.join(tmp, "state.db"),
                      "--samples", "2"], work)
            self.assertEqual(os.listdir(work), [])


if __name__ == "__main__":
    unittest.main()
