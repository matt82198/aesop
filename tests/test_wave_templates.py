#!/usr/bin/env python3
"""Test suite for wave templates: preset manifest validation and generation.

Validates that wave-manifest presets (SaaS, data, library) conform to the
required schema and can be loaded and instantiated correctly.

Tests:
  - Template file existence and valid JSON/YAML format
  - Manifest schema compliance (required fields, structure)
  - Placeholder substitution (ownsFiles, testCmd)
  - No overlap in file ownership across items
  - Linux parity (paths, newlines, encoding)
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Add tools/ to path for imports.
REPO = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

try:
    from wave_templates import (
        load_preset,
        instantiate_template,
        validate_manifest,
        PRESETS_DIR,
    )
except ImportError as e:
    print(f"Failed to import wave_templates: {e}")
    sys.exit(1)


class TestWaveTemplatesSchemaCompliance(unittest.TestCase):
    """Validate manifest schema compliance for all presets."""

    def test_preset_files_exist(self):
        """Each preset file should exist."""
        presets_to_test = ["saas", "data", "library"]
        for preset_name in presets_to_test:
            preset_file = PRESETS_DIR / f"{preset_name}.json"
            self.assertTrue(
                preset_file.exists(),
                f"Preset file missing: {preset_file}"
            )

    def test_preset_json_valid(self):
        """Each preset file should contain valid JSON."""
        presets_to_test = ["saas", "data", "library"]
        for preset_name in presets_to_test:
            try:
                preset = load_preset(preset_name)
                self.assertIsNotNone(preset)
                self.assertIsInstance(preset, dict)
            except Exception as e:
                self.fail(f"Failed to load preset {preset_name}: {e}")

    def test_manifest_schema_compliance_saas(self):
        """SaaS preset should generate valid manifests."""
        preset = load_preset("saas")
        manifest = instantiate_template(preset, project_name="my-saas", base_dir="/tmp/test")
        self._validate_manifest_structure(manifest)

    def test_manifest_schema_compliance_data(self):
        """Data preset should generate valid manifests."""
        preset = load_preset("data")
        manifest = instantiate_template(preset, project_name="my-data", base_dir="/tmp/test")
        self._validate_manifest_structure(manifest)

    def test_manifest_schema_compliance_library(self):
        """Library preset should generate valid manifests."""
        preset = load_preset("library")
        manifest = instantiate_template(preset, project_name="my-lib", base_dir="/tmp/test")
        self._validate_manifest_structure(manifest)

    def _validate_manifest_structure(self, manifest):
        """Helper to validate core manifest structure."""
        # Must have items array
        self.assertIn("items", manifest, "Manifest missing 'items' array")
        items = manifest["items"]
        self.assertIsInstance(items, list, "'items' must be a list")
        self.assertGreater(len(items), 0, "'items' must not be empty")

        # Each item must have required fields
        required_item_fields = {"slug", "prompt", "ownsFiles"}
        for item in items:
            for field in required_item_fields:
                self.assertIn(
                    field, item,
                    f"Item {item.get('slug', 'unknown')} missing required field '{field}'"
                )

            # slug must be non-empty string
            self.assertIsInstance(item["slug"], str)
            self.assertGreater(len(item["slug"]), 0)

            # ownsFiles must be a non-empty list
            self.assertIsInstance(item["ownsFiles"], list)
            self.assertGreater(len(item["ownsFiles"]), 0)

            # prompt must be non-empty string
            self.assertIsInstance(item["prompt"], str)
            self.assertGreater(len(item["prompt"]), 0)

    def test_no_file_ownership_overlap(self):
        """No two items should own the same file."""
        presets_to_test = ["saas", "data", "library"]
        for preset_name in presets_to_test:
            preset = load_preset(preset_name)
            manifest = instantiate_template(preset, project_name="test", base_dir="/tmp")

            owner_map = {}
            conflicts = []
            for item in manifest.get("items", []):
                for f in item.get("ownsFiles", []):
                    if f in owner_map:
                        conflicts.append((f, owner_map[f], item["slug"]))
                    else:
                        owner_map[f] = item["slug"]

            self.assertEqual(
                len(conflicts), 0,
                f"File ownership overlap in {preset_name}: {conflicts}"
            )

    def test_placeholder_substitution(self):
        """Placeholders like {project_name} should be substituted."""
        preset = load_preset("saas")
        project_name = "my-awesome-project"
        manifest = instantiate_template(
            preset,
            project_name=project_name,
            base_dir="/workspace"
        )

        manifest_str = json.dumps(manifest)
        # Should NOT contain placeholder strings after substitution
        self.assertNotIn("{project_name}", manifest_str)
        self.assertNotIn("{base_dir}", manifest_str)

    def test_manifest_validation_passes(self):
        """validate_manifest() should pass for all generated manifests."""
        presets_to_test = ["saas", "data", "library"]
        for preset_name in presets_to_test:
            preset = load_preset(preset_name)
            manifest = instantiate_template(preset, project_name="test", base_dir="/tmp")
            # Should not raise an exception
            try:
                validate_manifest(manifest)
            except Exception as e:
                self.fail(f"Validation failed for {preset_name}: {e}")

    def test_linux_parity_paths(self):
        """Paths should use forward slashes (POSIX) for portability."""
        presets_to_test = ["saas", "data", "library"]
        for preset_name in presets_to_test:
            preset = load_preset(preset_name)
            manifest = instantiate_template(preset, project_name="test", base_dir="/workspace")

            for item in manifest.get("items", []):
                for file_path in item.get("ownsFiles", []):
                    # On Windows, os.sep is '\', but we want '/' for portability
                    self.assertNotIn("\\", file_path,
                        f"Path should use forward slashes, got: {file_path}")

    def test_linux_parity_encoding(self):
        """Files should be UTF-8 with LF line endings."""
        presets_to_test = ["saas", "data", "library"]
        for preset_name in presets_to_test:
            preset_file = PRESETS_DIR / f"{preset_name}.json"
            content = preset_file.read_text(encoding="utf-8")
            # Should not contain CRLF
            self.assertNotIn("\r\n", content,
                f"{preset_name}.json should use LF, not CRLF")


class TestWaveTemplatesIntegration(unittest.TestCase):
    """Integration tests for template instantiation."""

    def test_instantiate_saas_template(self):
        """Can instantiate a SaaS manifest with parameters."""
        preset = load_preset("saas")
        manifest = instantiate_template(
            preset,
            project_name="payment-api",
            base_dir="/home/dev/projects"
        )
        self.assertGreater(len(manifest["items"]), 0)

    def test_instantiate_data_template(self):
        """Can instantiate a data-project manifest with parameters."""
        preset = load_preset("data")
        manifest = instantiate_template(
            preset,
            project_name="analytics-pipeline",
            base_dir="/opt/data"
        )
        self.assertGreater(len(manifest["items"]), 0)

    def test_instantiate_library_template(self):
        """Can instantiate a library manifest with parameters."""
        preset = load_preset("library")
        manifest = instantiate_template(
            preset,
            project_name="crypto-utils",
            base_dir="/src"
        )
        self.assertGreater(len(manifest["items"]), 0)

    def test_different_parameters_yield_different_manifests(self):
        """Different project names should yield different manifests."""
        preset = load_preset("saas")
        m1 = instantiate_template(preset, project_name="app1", base_dir="/tmp")
        m2 = instantiate_template(preset, project_name="app2", base_dir="/tmp")

        # The manifests should differ (e.g., in slugs, file paths)
        self.assertNotEqual(
            json.dumps(m1, sort_keys=True),
            json.dumps(m2, sort_keys=True)
        )


class TestWaveTemplatesValidateSubcommand(unittest.TestCase):
    """Test the validate subcommand functionality."""

    def test_validate_valid_preset(self):
        """Validate should pass for a valid preset."""
        preset = load_preset("saas")
        # Should not raise for valid preset
        validate_manifest(preset)

    def test_validate_all_presets_pass(self):
        """All shipped presets should validate clean."""
        presets_to_test = ["saas", "data", "library"]
        for preset_name in presets_to_test:
            preset = load_preset(preset_name)
            # Should not raise for any preset
            try:
                validate_manifest(preset)
            except ValueError as e:
                self.fail(f"Preset {preset_name} validation failed: {e}")

    def test_validate_preset_with_missing_slug(self):
        """Validation should fail when slug is missing."""
        invalid_preset = {
            "name": "Invalid",
            "items": [
                {
                    # slug missing
                    "prompt": "test",
                    "ownsFiles": ["file.py"],
                    "testCmd": "test"
                }
            ]
        }
        with self.assertRaises(ValueError) as ctx:
            validate_manifest(invalid_preset)
        self.assertIn("missing", str(ctx.exception).lower())

    def test_validate_preset_with_missing_ownsfiles(self):
        """Validation should fail when ownsFiles is missing."""
        invalid_preset = {
            "name": "Invalid",
            "items": [
                {
                    "slug": "test",
                    "prompt": "test",
                    "testCmd": "test",
                    # ownsFiles missing
                }
            ]
        }
        with self.assertRaises(ValueError) as ctx:
            validate_manifest(invalid_preset)
        self.assertIn("missing", str(ctx.exception).lower())

    def test_validate_preset_with_missing_prompt(self):
        """Validation should fail when prompt is missing."""
        invalid_preset = {
            "name": "Invalid",
            "items": [
                {
                    "slug": "test",
                    # prompt missing
                    "ownsFiles": ["file.py"],
                    "testCmd": "test"
                }
            ]
        }
        with self.assertRaises(ValueError) as ctx:
            validate_manifest(invalid_preset)
        self.assertIn("missing", str(ctx.exception).lower())

    def test_validate_preset_with_overlapping_ownfiles(self):
        """Validation should fail when two items own the same file."""
        invalid_preset = {
            "name": "Invalid",
            "items": [
                {
                    "slug": "item1",
                    "prompt": "test",
                    "ownsFiles": ["shared.py"],
                    "testCmd": "test"
                },
                {
                    "slug": "item2",
                    "prompt": "test",
                    "ownsFiles": ["shared.py"],  # Overlap!
                    "testCmd": "test"
                }
            ]
        }
        with self.assertRaises(ValueError) as ctx:
            validate_manifest(invalid_preset)
        self.assertIn("overlap", str(ctx.exception).lower())

    def test_validate_preset_missing_items_array(self):
        """Validation should fail when items array is missing."""
        invalid_preset = {
            "name": "Invalid"
            # items missing
        }
        with self.assertRaises(ValueError) as ctx:
            validate_manifest(invalid_preset)
        self.assertIn("items", str(ctx.exception).lower())

    def test_validate_preset_empty_items(self):
        """Validation should fail when items array is empty."""
        invalid_preset = {
            "name": "Invalid",
            "items": []
        }
        with self.assertRaises(ValueError) as ctx:
            validate_manifest(invalid_preset)
        self.assertIn("empty", str(ctx.exception).lower())

    def test_validate_preset_with_empty_ownfiles(self):
        """Validation should fail when ownsFiles is empty."""
        invalid_preset = {
            "name": "Invalid",
            "items": [
                {
                    "slug": "test",
                    "prompt": "test",
                    "ownsFiles": []  # Empty!
                }
            ]
        }
        with self.assertRaises(ValueError) as ctx:
            validate_manifest(invalid_preset)
        self.assertIn("empty", str(ctx.exception).lower())

    def test_validate_preset_with_testcmd_field(self):
        """Presets should support testCmd field (required by wave engine)."""
        # Instantiated presets require testCmd for the wave engine
        preset = load_preset("saas")
        manifest = instantiate_template(preset, project_name="test", base_dir="/tmp")
        # All items should have testCmd
        for item in manifest.get("items", []):
            self.assertIn("testCmd", item,
                f"Item {item.get('slug', 'unknown')} missing testCmd")
            self.assertIsInstance(item["testCmd"], str)
            self.assertGreater(len(item["testCmd"]), 0)

    def test_validate_preset_with_workdir_field(self):
        """Presets should support workDir field (used by wave engine)."""
        # Instantiated presets should have workDir
        preset = load_preset("saas")
        manifest = instantiate_template(preset, project_name="test", base_dir="/tmp")
        # All items should have workDir after instantiation
        for item in manifest.get("items", []):
            self.assertIn("workDir", item,
                f"Item {item.get('slug', 'unknown')} missing workDir")



class TestValidateOutputIntegrity(unittest.TestCase):
    """Gate-1 pilot regression: a codex full-file replacement once coerced the
    validate glyphs to 0x7F DEL control bytes (invisible in most terminals).
    Output must round-trip clean: expected glyphs present, no control bytes."""

    def test_validate_output_glyphs_and_no_control_bytes(self):
        import subprocess, sys as _sys
        r = subprocess.run(
            [_sys.executable, str(Path(__file__).parent.parent / "tools" / "wave_templates.py"),
             "validate", "--template", "saas"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env={**__import__("os").environ, "PYTHONUTF8": "1"},
        )
        combined = (r.stdout or "") + (r.stderr or "")
        self.assertIn("✓", combined, "check glyph must survive in output")
        allowed = {chr(13), chr(10), chr(9)}
        bad = [c for c in combined if (ord(c) < 32 and c not in allowed) or ord(c) == 127]
        self.assertFalse(bad, f"control bytes in validate output: {[hex(ord(c)) for c in bad]}")

class TestPipefailFailClosed(unittest.TestCase):
    """A pipeline into head/tail must not mask a failing stage (exit status of head is not the test's)."""

    def _dashboard_cmd(self):
        preset = load_preset("data")
        manifest = instantiate_template(preset, project_name="my-data", base_dir="/tmp/test")
        item = next(i for i in manifest["items"] if i["slug"] == "analytics-dashboard")
        return item["testCmd"]

    def test_dashboard_testcmd_fails_when_streamlit_missing(self):
        """With streamlit missing (exit 127), the shipped dashboard testCmd must exit nonzero."""
        cmd = self._dashboard_cmd()
        cmd = cmd.replace("python -m pytest tests/dashboard/ -v", "true")
        cmd = cmd.replace("streamlit run", "aesop_no_such_streamlit_binary run")
        r = subprocess.run(
            [shutil.which("bash") or "bash", "-c", cmd],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertNotEqual(r.returncode, 0,
            f"failing stage masked by head: rc={r.returncode} stderr={r.stderr!r}")

    def test_validator_rejects_head_pipe_without_pipefail(self):
        manifest = {"items": [{"slug": "dash", "prompt": "p", "ownsFiles": ["a.py"],
            "testCmd": "python -m pytest tests/ && streamlit run app.py 2>&1 | head -50"}]}
        with self.assertRaises(ValueError) as ctx:
            validate_manifest(manifest)
        self.assertIn("pipefail", str(ctx.exception).lower())

    def test_validator_rejects_tail_pipe_without_pipefail(self):
        manifest = {"items": [{"slug": "dash", "prompt": "p", "ownsFiles": ["a.py"],
            "testCmd": "python -m pytest tests/ | tail -5"}]}
        with self.assertRaises(ValueError) as ctx:
            validate_manifest(manifest)
        self.assertIn("pipefail", str(ctx.exception).lower())

    def test_validator_accepts_head_pipe_with_pipefail(self):
        manifest = {"items": [{"slug": "dash", "prompt": "p", "ownsFiles": ["a.py"],
            "testCmd": "set -o pipefail && python -m pytest tests/ && streamlit run app.py 2>&1 | head -50"}]}
        validate_manifest(manifest)


class TestHyphenatedModuleName(unittest.TestCase):
    """A hyphenated project name must yield importable identifiers in import/--cov positions."""

    def test_library_hyphenated_core_import_runs(self):
        """The core-module import stage must import the package for project 'payment-api'."""
        preset = load_preset("library")
        manifest = instantiate_template(preset, project_name="payment-api", base_dir="/tmp/test")
        core = next(i for i in manifest["items"] if i["slug"] == "core-module")
        import_stage = core["testCmd"].split(" && ")[0]
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "src" / "payment_api"
            pkg.mkdir(parents=True)
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            env = {**os.environ, "PYTHONPATH": str(Path(tmp) / "src")}
            r = subprocess.run(
                [shutil.which("bash") or "bash", "-c", import_stage],
                cwd=tmp, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
        self.assertEqual(r.returncode, 0, f"core import failed: {r.stderr!r}")

    def test_library_hyphenated_owns_importable_package_dir(self):
        """Package directories must match the importable module name, not the hyphenated display name."""
        preset = load_preset("library")
        manifest = instantiate_template(preset, project_name="payment-api", base_dir="/tmp/test")
        for item in manifest["items"]:
            for f in item["ownsFiles"]:
                if f.startswith("src/"):
                    self.assertEqual(f.split("/")[1], "payment_api", f)

    def test_library_hyphenated_manifest_passes_instantiated_validation(self):
        preset = load_preset("library")
        manifest = instantiate_template(preset, project_name="payment-api", base_dir="/tmp/test")
        validate_manifest(manifest, allow_placeholders=False, require_testcmd=True)

    def test_validator_rejects_hyphenated_import(self):
        manifest = {"items": [{"slug": "core", "prompt": "p", "ownsFiles": ["src/payment_api/core.py"],
            "testCmd": 'python -c "import payment-api"'}]}
        with self.assertRaises(ValueError) as ctx:
            validate_manifest(manifest, allow_placeholders=False)
        self.assertIn("identifier", str(ctx.exception).lower())

    def test_validator_rejects_hyphenated_cov_module(self):
        manifest = {"items": [{"slug": "tests", "prompt": "p", "ownsFiles": ["tests/x.py"],
            "testCmd": "python -m pytest tests/ --cov=payment-api"}]}
        with self.assertRaises(ValueError) as ctx:
            validate_manifest(manifest, allow_placeholders=False)
        self.assertIn("identifier", str(ctx.exception).lower())

    def test_validator_accepts_cov_path(self):
        """--cov=src/pipeline is a path, not a module name, and must stay valid."""
        manifest = {"items": [{"slug": "pipe", "prompt": "p", "ownsFiles": ["src/pipeline/x.py"],
            "testCmd": "python -m pytest tests/pipeline/ -v --cov=src/pipeline"}]}
        validate_manifest(manifest, allow_placeholders=False)


if __name__ == "__main__":
    unittest.main()
