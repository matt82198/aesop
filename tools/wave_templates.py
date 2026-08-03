#!/usr/bin/env python3
"""Wave template manager: load and instantiate preset manifests for common project types.
INDEX: Wave-manifest preset generator: instantiate/validate templates/wave-presets/*.json into ready manifests; CLI: `validate [--template saas|data|library|all]` (exits 0=clean / 1=defects per item), `instantiate <preset> --project-name --base-dir [--output FILE]`

Provides reusable wave-manifest scaffolds for bootstrapping first waves:
  - SaaS: API + frontend + ops (typical 3-tier)
  - Data: pipeline + analytics + infra (ETL/analytics pattern)
  - Library: core + tests + docs (reusable module pattern)

Each preset is a JSON file with template variables ({project_name}, {base_dir})
that are substituted during instantiation.

Usage (CLI):
  python tools/wave_templates.py <preset> --project-name my-app --base-dir /workspace

Usage (Python API):
  from wave_templates import load_preset, instantiate_template
  preset = load_preset("saas")
  manifest = instantiate_template(preset, project_name="my-app", base_dir="/workspace")

Invariants:
  - Presets live in templates/wave-presets/ (git-tracked, editable)
  - No file ownership overlap within a manifest (preflight guard)
  - All paths use forward slashes (POSIX) for cross-platform compatibility
  - UTF-8 encoding, LF line endings (Linux parity)
  - Manifests are fully resolved JSON (no further substitution by the wave engine)
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

# Presets directory (relative to this file's location).
TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parent
PRESETS_DIR = REPO_ROOT / "templates" / "wave-presets"


def load_preset(preset_name: str) -> Dict[str, Any]:
    """Load a preset manifest template by name.

    Args:
        preset_name: preset identifier (e.g., "saas", "data", "library")

    Returns:
        dict with preset template (contains {project_name}, {base_dir} placeholders)

    Raises:
        FileNotFoundError: if preset file does not exist
        json.JSONDecodeError: if preset JSON is invalid
    """
    preset_file = PRESETS_DIR / f"{preset_name}.json"
    if not preset_file.exists():
        raise FileNotFoundError(f"Preset not found: {preset_file}")

    with open(preset_file, "r", encoding="utf-8") as f:
        return json.load(f)


def instantiate_template(
    preset: Dict[str, Any],
    project_name: str,
    base_dir: str,
) -> Dict[str, Any]:
    """Instantiate a preset template with project-specific values.

    Replaces placeholder strings {project_name} and {base_dir} throughout
    the preset. Also derives a wave_id and wave_description if not provided.

    Args:
        preset: template dict from load_preset()
        project_name: user's project/app name (e.g., "payment-api")
        base_dir: root working directory (e.g., "/workspace/my-app")

    Returns:
        dict with fully resolved manifest (ready to pass to wave engine)
    """
    # Deep copy to avoid mutating the original preset.
    manifest = json.loads(json.dumps(preset))

    # Utility to substitute placeholders recursively.
    def substitute(obj):
        if isinstance(obj, str):
            return obj.replace("{project_name}", project_name).replace("{base_dir}", base_dir)
        elif isinstance(obj, dict):
            return {k: substitute(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [substitute(item) for item in obj]
        else:
            return obj

    manifest = substitute(manifest)

    # Add wave metadata if not already present.
    if "wave_id" not in manifest:
        manifest["wave_id"] = f"wave-{project_name}".lower().replace(" ", "-")
    if "wave_description" not in manifest:
        manifest["wave_description"] = f"Bootstrap wave for {project_name}"

    return manifest


def validate_manifest(
    manifest: Dict[str, Any],
    allow_placeholders: bool = True,
    require_testcmd: bool = True
) -> None:
    """Validate manifest schema and invariants.

    Checks:
      - items array exists and is non-empty
      - each item has required fields (slug, prompt, ownsFiles, and optionally testCmd)
      - no file ownership overlap (per-manifest)
      - optionally: no placeholder strings remain (for instantiated manifests)

    Args:
        manifest: the manifest dict to validate
        allow_placeholders: if False, reject unsubstituted placeholders (default: True,
                           for backward compatibility with presets). Set to False for
                           instantiated manifests.
        require_testcmd: if True, require testCmd field (default: True, since the wave
                        engine needs it). Set to False to validate presets only.

    Raises:
        ValueError: if validation fails with detailed error per item
    """
    if "items" not in manifest:
        raise ValueError("Manifest missing 'items' array")

    items = manifest["items"]
    if not isinstance(items, list) or len(items) == 0:
        raise ValueError("'items' must be a non-empty list")

    # Core required fields (always required).
    required_core_fields = {"slug", "prompt", "ownsFiles"}
    # Optional fields for instantiated manifests.
    optional_fields = {"testCmd", "workDir"} if require_testcmd else set()
    required_fields = required_core_fields | ({"testCmd"} if require_testcmd else set())

    owner_map = {}
    conflicts = []
    errors = []

    for item_idx, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"Item {item_idx}: must be a dict, got {type(item)}")
            continue

        # Check required core fields.
        for field in required_core_fields:
            if field not in item:
                errors.append(f"Item {item_idx} ({item.get('slug', 'unknown')}): missing required field '{field}'")

        # Check required testCmd if flag is set.
        if require_testcmd and "testCmd" not in item:
            errors.append(f"Item {item_idx} ({item.get('slug', 'unknown')}): missing required field 'testCmd'")

        slug = item.get("slug")
        if slug is not None:
            if not isinstance(slug, str) or not slug:
                errors.append(f"Item {item_idx}: slug must be non-empty string, got {slug}")

        # Check ownsFiles.
        owned_files = item.get("ownsFiles", [])
        if not isinstance(owned_files, list):
            errors.append(f"Item {item_idx} ({slug}): ownsFiles must be a list")
            continue
        if len(owned_files) == 0:
            errors.append(f"Item {item_idx} ({slug}): ownsFiles must be non-empty")
            continue

        # Track file ownership (within this manifest only).
        for f in owned_files:
            if f in owner_map:
                conflicts.append((f, owner_map[f], slug))
            else:
                owner_map[f] = slug

    # Report all collected errors first.
    if errors:
        error_msg = "Manifest validation failed:\n  " + "\n  ".join(errors)
        raise ValueError(error_msg)

    if conflicts:
        conflict_msg = "File ownership overlap (within manifest):\n  " + "\n  ".join(
            [f"{f!r}: owned by both {o1!r} and {o2!r}" for f, o1, o2 in conflicts]
        )
        raise ValueError(conflict_msg)

    # Check no placeholders remain (if instantiated manifest).
    if not allow_placeholders:
        manifest_str = json.dumps(manifest)
        if "{project_name}" in manifest_str or "{base_dir}" in manifest_str:
            raise ValueError("Manifest contains unsubstituted placeholders (not fully instantiated)")


def validate_presets(preset_names: List[str], output_json: bool = False) -> Tuple[bool, List[str]]:
    """Validate one or more presets.

    Args:
        preset_names: list of preset names to validate (e.g., ["saas", "data", "library"])

    Returns:
        tuple (success: bool, errors: List[str])
          - success is True if all presets validate clean
          - errors is a list of formatted error messages per preset/item
    """
    errors = []
    all_valid = True
    results = {}

    for preset_name in preset_names:
        try:
            preset = load_preset(preset_name)
            # Validate the preset: allow placeholders (presets have them), require testCmd (wave engine needs it)
            validate_manifest(preset, allow_placeholders=True, require_testcmd=True)
            results[preset_name] = {"valid": True, "errors": []}
            print(f"✓ {preset_name}: valid", file=sys.stderr)
        except FileNotFoundError as e:
            errors.append(f"✗ {preset_name}: {e}")
            all_valid = False
            results[preset_name] = {"valid": False, "errors": [str(e)]}
        except ValueError as e:
            errors.append(f"✗ {preset_name}: {e}")
            all_valid = False
            results[preset_name] = {"valid": False, "errors": [str(e)]}
        except Exception as e:
            errors.append(f"✗ {preset_name}: unexpected error: {e}")
            all_valid = False
            results[preset_name] = {"valid": False, "errors": [str(e)]}

    if output_json:
        print(json.dumps({"ok": all_valid, "templates": results}))
    else:
        # Print errors if any
        for error in errors:
            print(error, file=sys.stderr)

    return all_valid, errors


def main():
    """CLI entry point: load preset, instantiate, validate, or validate presets."""
    parser = argparse.ArgumentParser(
        description="Wave manifest preset manager: load, instantiate, and validate presets"
    )

    # Create subparsers for different commands
    subparsers = parser.add_subparsers(dest="command", help="command to run")

    # Subcommand: validate
    validate_parser = subparsers.add_parser(
        "validate",
        help="validate preset template(s) for completeness"
    )
    validate_parser.add_argument(
        "--template",
        choices=["saas", "data", "library", "all"],
        default="all",
        help="which preset(s) to validate (default: all)"
    )
    validate_parser.add_argument(
        "--json",
        action="store_true",
        help="output results in JSON format"
    )

    # Subcommand: instantiate
    inst_parser = subparsers.add_parser(
        "instantiate",
        help="load and instantiate a preset manifest"
    )
    inst_parser.add_argument(
        "preset",
        help="preset name: saas, data, or library"
    )
    inst_parser.add_argument(
        "--project-name",
        required=True,
        help="project/app name (e.g., 'payment-api')"
    )
    inst_parser.add_argument(
        "--base-dir",
        required=True,
        help="base working directory (e.g., '/workspace/my-app')"
    )
    inst_parser.add_argument(
        "--output",
        default=None,
        help="output file (default: stdout)"
    )

    args = parser.parse_args()

    try:
        if args.command == "validate":
            # Validate preset(s)
            if args.template == "all":
                presets = ["saas", "data", "library"]
            else:
                presets = [args.template]

            success, errors = validate_presets(presets, output_json=args.json)

            if not success:
                sys.exit(1)
            else:
                print(f"\nAll {len(presets)} preset(s) validated successfully.", file=sys.stderr)
                sys.exit(0)

        elif args.command == "instantiate":
            # Instantiate preset
            preset = load_preset(args.preset)
            manifest = instantiate_template(preset, args.project_name, args.base_dir)

            # Validate the instantiated manifest (disallow placeholders, require testCmd)
            validate_manifest(manifest, allow_placeholders=False, require_testcmd=True)

            # Output.
            output_json = json.dumps(manifest, indent=2)
            if args.output:
                output_path = Path(args.output)
                output_path.write_text(output_json, encoding="utf-8")
                print(f"Manifest written to {output_path}", file=sys.stderr)
            else:
                print(output_json)

        else:
            # No command specified - print help
            parser.print_help()
            sys.exit(0)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
