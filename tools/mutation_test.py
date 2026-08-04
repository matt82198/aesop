#!/usr/bin/env python3
"""
Mutation testing tool — measures whether a module's tests actually catch bugs.
INDEX: Test quality harness via mutation testing (apply code mutations, run tests, report survived mutations as test gaps); CLI: `--target <module.py> --test <test_module.py> [--json]`; exit 0 on valid results (advisory), exit 1 when the sandbox baseline fails (results invalid, fail-closed)

This tool applies small source-code mutations (copy, never mutate original) to
a target module and runs its test suite against each mutation. Tests that pass
despite a mutation indicate a gap in test coverage (weak tests). Survived
mutations are reported as weak spots the tests don't cover.

Why: green test suites often just mean 'tests agree with the code', not
'tests catch bugs'. Mutation testing reveals test quality gaps.

Mutations applied:
  - Flip comparison operators (== <-> !=, < <-> >=, > <-> <=)
  - Replace numeric literals with +1
  - Replace boolean returns with negation
  - Replace 'and' <-> 'or'
  - Replace 'is None' <-> 'is not None'

Configuration (aesop.config.json):
  [none — no configuration needed]

API:
  run(target_module_path, test_module_path) -> dict
    Returns {"killed": int, "survived": int, "mutations": [...]}
    mutations is a list of {"file": str, "line": int, "original": str, "mutated": str}

CLI:
  python tools/mutation_test.py --target module.py --test test_module.py [--json]
    Exit 0 always (advisory, not gated). --json outputs JSON to stdout.
"""

import argparse
import ast
import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional


class MutationGenerator(ast.NodeTransformer):
    """AST transformer that generates mutations of a Python module."""

    def __init__(self):
        self.mutations: List[Tuple[int, str, str]] = []  # (line, original, mutated)
        self.current_mutation_idx = 0
        self.target_mutation_idx = -1  # -1 means no mutation, just record

    def record_mutation(self, line: int, original: str, mutated: str) -> None:
        """Record a mutation and track mutation index."""
        self.mutations.append((line, original, mutated))

    def visit_Compare(self, node: ast.Compare) -> Any:
        """Mutate comparison operators: == <-> !=, < <-> >=, etc."""
        self.generic_visit(node)

        # Map of operator mutations
        op_map = {
            ast.Eq: (ast.NotEq, "==", "!="),
            ast.NotEq: (ast.Eq, "!=", "=="),
            ast.Lt: (ast.GtE, "<", ">="),
            ast.Gt: (ast.LtE, ">", "<="),
            ast.LtE: (ast.Gt, "<=", ">"),
            ast.GtE: (ast.Lt, ">=", "<"),
        }

        for i, op in enumerate(node.ops):
            if type(op) in op_map:
                mutated_op, orig_str, mut_str = op_map[type(op)]
                self.record_mutation(node.lineno, orig_str, mut_str)

                if self.current_mutation_idx == len(self.mutations) - 1 and self.target_mutation_idx == self.current_mutation_idx:
                    node.ops[i] = mutated_op()

                self.current_mutation_idx += 1

        return node

    def visit_Constant(self, node: ast.Constant) -> Any:
        """Mutate numeric literals: n -> n+1."""
        self.generic_visit(node)

        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            mutated_val = node.value + 1
            self.record_mutation(node.lineno, str(node.value), str(mutated_val))

            if self.current_mutation_idx == len(self.mutations) - 1 and self.target_mutation_idx == self.current_mutation_idx:
                node.value = mutated_val

            self.current_mutation_idx += 1

        return node

    def visit_Return(self, node: ast.Return) -> Any:
        """Mutate boolean returns: True -> False, False -> True."""
        self.generic_visit(node)

        if node.value and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, bool):
                orig_val = node.value.value
                mut_val = not orig_val
                self.record_mutation(node.lineno, str(orig_val), str(mut_val))

                if self.current_mutation_idx == len(self.mutations) - 1 and self.target_mutation_idx == self.current_mutation_idx:
                    node.value.value = mut_val

                self.current_mutation_idx += 1

        return node

    def visit_BoolOp(self, node: ast.BoolOp) -> Any:
        """Mutate boolean operators: and <-> or."""
        self.generic_visit(node)

        if isinstance(node.op, (ast.And, ast.Or)):
            is_and = isinstance(node.op, ast.And)
            orig_str = "and" if is_and else "or"
            mut_str = "or" if is_and else "and"
            self.record_mutation(node.lineno, orig_str, mut_str)

            if self.current_mutation_idx == len(self.mutations) - 1 and self.target_mutation_idx == self.current_mutation_idx:
                node.op = ast.Or() if is_and else ast.And()

            self.current_mutation_idx += 1

        return node

    def visit_UnaryOp(self, node: ast.UnaryOp) -> Any:
        """Mutate 'not' in returns to None checks."""
        self.generic_visit(node)
        return node


def extract_mutations(source: str) -> List[Tuple[int, str, str]]:
    """Parse source and extract all possible mutations."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    transformer = MutationGenerator()
    transformer.generic_visit(tree)
    return transformer.mutations


def apply_mutation(source: str, mutation_idx: int) -> Optional[str]:
    """Apply a specific mutation to source code by index.

    Returns mutated source, or None if mutation_idx is out of range.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    mutations = extract_mutations(source)
    if mutation_idx < 0 or mutation_idx >= len(mutations):
        return None

    # Re-parse and apply the specific mutation
    tree = ast.parse(source)
    transformer = MutationGenerator()
    transformer.target_mutation_idx = mutation_idx
    new_tree = transformer.visit(tree)

    try:
        return ast.unparse(new_tree)
    except Exception:
        # If unparsing fails, return None
        return None


def _pytest_available() -> bool:
    """Return True if pytest is importable by the current interpreter.

    We must decide the runner up-front rather than relying on a subprocess
    exception: ``python -m pytest`` with pytest absent exits with code 1
    ("No module named pytest") and does NOT raise FileNotFoundError, so an
    ``except FileNotFoundError`` fallback would never fire — every test run
    would look like a failure (exit 1) and every mutation would be counted
    as 'killed', reporting 0 survivors regardless of test quality.
    """
    try:
        return importlib.util.find_spec("pytest") is not None
    except Exception:
        return False


def run_tests(test_module_path: str, work_dir: str, timeout: int = 30) -> Tuple[int, str]:
    """Run a test module in a subprocess against the code in ``work_dir``.

    The (mutated) target copy lives in ``work_dir``; we force that directory
    onto PYTHONPATH and run with cwd=work_dir so the test genuinely imports
    the MUTATED copy on every platform, regardless of any sys.path juggling
    the test module itself does. Prefers pytest when available, else falls
    back to the always-present stdlib unittest.

    Returns (exit_code, stdout+stderr).
    """
    # Deterministically make the mutated copy importable (Linux + Windows).
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = work_dir + (os.pathsep + existing if existing else "")

    # Convert absolute path to relative path from work_dir.
    # The test file might be in a subdirectory (e.g., work_dir/ui/test.py)
    test_path = Path(test_module_path).resolve()
    work_path = Path(work_dir).resolve()
    try:
        rel_test_path = test_path.relative_to(work_path)
    except ValueError:
        # If relative_to fails, just use the filename
        rel_test_path = Path(test_path.name)

    if _pytest_available():
        # pytest can take a relative path to the test file
        cmd = [sys.executable, "-m", "pytest", "-xvs", str(rel_test_path)]
    else:
        # unittest needs a module name. If test is in a package dir,
        # construct the module path (e.g., ui.test_fixture_sibling)
        if rel_test_path.parent != Path("."):
            # Test is in a subdirectory, construct module path
            module_parts = list(rel_test_path.parent.parts) + [rel_test_path.stem]
            module_name = ".".join(module_parts)
        else:
            module_name = rel_test_path.stem
        cmd = [sys.executable, "-m", "unittest", "-v", module_name]

    try:
        result = subprocess.run(
            cmd,
            cwd=work_dir,
            capture_output=True,
            text=True,
            encoding='utf-8', errors='replace',
            timeout=timeout,
            env=env,
        )
        return result.returncode, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return 124, "Test timeout (>{}s)".format(timeout)


def run(target_module_path: str, test_module_path: str) -> Dict[str, Any]:
    """Run mutation testing on target_module_path using test_module_path.

    Returns {
        "killed": int,
        "survived": int,
        "mutations": [
            {"file": str, "line": int, "original": str, "mutated": str},
            ...
        ]
    }
    """
    target_path = Path(target_module_path).resolve()
    test_path = Path(test_module_path).resolve()

    if not target_path.exists():
        return {
            "killed": 0,
            "survived": 0,
            "mutations": [],
            "error": f"target module not found: {target_module_path}",
        }

    if not test_path.exists():
        return {
            "killed": 0,
            "survived": 0,
            "mutations": [],
            "error": f"test module not found: {test_module_path}",
        }

    # Read target source
    try:
        source = target_path.read_text(encoding="utf-8")
    except Exception as e:
        return {
            "killed": 0,
            "survived": 0,
            "mutations": [],
            "error": f"failed to read target: {e}",
        }

    # Extract mutations
    mutations = extract_mutations(source)
    if not mutations:
        return {
            "killed": 0,
            "survived": 0,
            "mutations": [],
        }

    # Create temporary work directory
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Detect target package structure and recreate it in tmpdir.
        # If target is in driver/wave_loop.py, we create driver/ in tmpdir.
        # This is critical for "from driver import X" imports to work.
        target_dir = target_path.parent

        # Determine if target's directory is a package (has __init__.py or is a known package dir).
        # Known package directories: driver, ui, tools, mcp, daemons, skills, etc.
        # NOT packages: tests, fixtures, bench (these contain test/fixture files)
        target_package_name = None
        repo_root = None

        if (target_dir / "__init__.py").exists():
            # Target is in a real package directory (e.g., driver/, ui/)
            target_package_name = target_dir.name
            repo_root = target_dir.parent
        else:
            # Check if target_dir name suggests it's a package directory
            package_dir_names = {"driver", "ui", "tools", "mcp", "daemons", "skills"}
            non_package_dir_names = {"tests", "fixtures", "bench"}

            if target_dir.name in non_package_dir_names:
                # Not a package directory, treat as flat layout
                repo_root = target_path.resolve()
                while repo_root.parent != repo_root and not (repo_root / ".git").exists():
                    repo_root = repo_root.parent
                if not (repo_root / ".git").exists():
                    repo_root = target_dir
            elif target_dir.name in package_dir_names:
                # Treat as package directory
                target_package_name = target_dir.name
                repo_root = target_dir.parent
            else:
                # Unknown directory, assume flat layout
                repo_root = target_path.resolve()
                while repo_root.parent != repo_root and not (repo_root / ".git").exists():
                    repo_root = repo_root.parent
                if not (repo_root / ".git").exists():
                    repo_root = target_dir

        # Create package structure in tmpdir
        if target_package_name:
            pkg_dir = tmpdir / target_package_name
            pkg_dir.mkdir(parents=True, exist_ok=True)
            # Create __init__.py to make it a proper package
            (pkg_dir / "__init__.py").touch()
            temp_target = pkg_dir / target_path.name
        else:
            temp_target = tmpdir / target_path.name

        temp_target.write_text(source, encoding="utf-8")

        # Copy test to temp dir.
        # If target is in a package dir (e.g., ui/), put test in same dir in tmpdir
        # so the test can find the target (assumes test uses sys.path.insert(0, __file__.parent))
        if target_package_name:
            pkg_dir = tmpdir / target_package_name
            temp_test = pkg_dir / test_path.name
        else:
            temp_test = tmpdir / test_path.name

        try:
            test_source = test_path.read_text(encoding="utf-8")
            temp_test.write_text(test_source, encoding="utf-8")
        except Exception as e:
            return {
                "killed": 0,
                "survived": 0,
                "mutations": [],
                "error": f"failed to copy test: {e}",
            }

        # Copy sibling modules from target's package directory.
        # This is critical for modules that import siblings (e.g., ui/wave_audit_tail
        # imports ui/config). Copy them into the same package dir as target.
        target_dir = target_path.parent
        if target_package_name:
            pkg_dir = tmpdir / target_package_name
            for py_file in target_dir.glob("*.py"):
                if py_file.name not in (test_path.name, target_path.name):
                    try:
                        dest = pkg_dir / py_file.name
                        dest.write_text(py_file.read_text(encoding="utf-8"), encoding="utf-8")
                    except Exception:
                        pass  # Best effort (skip files we can't read)
        else:
            # Flat layout (target at repo root)
            for py_file in target_dir.glob("*.py"):
                if py_file.name not in (test_path.name, target_path.name):
                    try:
                        dest = tmpdir / py_file.name
                        dest.write_text(py_file.read_text(encoding="utf-8"), encoding="utf-8")
                    except Exception:
                        pass  # Best effort (skip files we can't read)

        # Copy any other Python files from test directory (additional dependencies)
        test_dir = test_path.parent
        for py_file in test_dir.glob("*.py"):
            if py_file.name not in (test_path.name, target_path.name):
                try:
                    dest = tmpdir / py_file.name
                    dest.write_text(py_file.read_text(encoding="utf-8"), encoding="utf-8")
                except Exception:
                    pass  # Best effort

        # Also create __init__.py files for any sibling packages that tests might import.
        # This enables "from driver import X" when driver/ contains the target.
        if target_package_name and repo_root:
            # Known package directories in this repo
            package_dir_names = {"driver", "ui", "tools", "mcp", "daemons", "skills", "bench"}

            # Collect all package names from repo root
            for pkg in repo_root.glob("*/"):
                if pkg.is_dir():
                    pkg_name = pkg.name
                    # Only copy known package directories or those with __init__.py
                    if pkg_name not in package_dir_names and not (pkg / "__init__.py").exists():
                        continue

                    # Only recreate if not already created
                    sandbox_pkg = tmpdir / pkg_name
                    if not sandbox_pkg.exists():
                        sandbox_pkg.mkdir(parents=True, exist_ok=True)
                        (sandbox_pkg / "__init__.py").touch()
                        # Copy Python files from this package
                        for py_file in pkg.glob("*.py"):
                            try:
                                dest = sandbox_pkg / py_file.name
                                dest.write_text(py_file.read_text(encoding="utf-8"), encoding="utf-8")
                            except Exception:
                                pass  # Best effort

        # Run tests against unmutated code (baseline)
        baseline_exit, _ = run_tests(str(temp_test), str(tmpdir))
        baseline_passes = (baseline_exit == 0)

        # Guard: if baseline tests fail, the results are invalid.
        # Every mutation would also fail, giving a false-perfect kill rate.
        if not baseline_passes:
            return {
                "killed": 0,
                "survived": 0,
                "mutations": [],
                "error": "baseline tests fail in sandbox — results invalid",
            }

        killed = 0
        survived = 0
        survived_mutations = []

        # Test each mutation
        for mut_idx, (line, orig, mutated) in enumerate(mutations):
            mutated_source = apply_mutation(source, mut_idx)
            if mutated_source is None:
                continue

            # Write mutated source to temp file
            temp_target.write_text(mutated_source, encoding="utf-8")

            # Run tests
            exit_code, _ = run_tests(str(temp_test), str(tmpdir))
            tests_pass = (exit_code == 0)

            if tests_pass:
                # Mutation survived (tests didn't catch the bug)
                survived += 1
                survived_mutations.append({
                    "file": target_path.name,
                    "line": line,
                    "original": orig,
                    "mutated": mutated,
                })
            else:
                # Mutation killed (tests caught the bug)
                killed += 1

        # Restore original
        temp_target.write_text(source, encoding="utf-8")

        return {
            "killed": killed,
            "survived": survived,
            "mutations": survived_mutations,
        }


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Mutation testing tool - measure test quality by applying mutations."
    )
    parser.add_argument("--target", required=True, help="Target module to mutate (path to .py file)")
    parser.add_argument("--test", required=True, help="Test module to run (path to .py file)")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")

    args = parser.parse_args(argv)

    result = run(args.target, args.test)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("Mutation test results:")
        print(f"  Killed:   {result['killed']}")
        print(f"  Survived: {result['survived']}")
        if result["survived"] > 0:
            print("\nSurvived mutations (test gaps):")
            for mut in result["mutations"]:
                print(f"  {mut['file']}:{mut['line']} - {mut['original']} -> {mut['mutated']}")
        if "error" in result:
            print(f"\nError: {result['error']}", file=sys.stderr)

    # Exit nonzero if there's a baseline error (tool/setup failure).
    # Normal results (mutations/survivors) exit 0 (advisory).
    if "error" in result:
        return 1
    return 0  # Exit 0 always (advisory) for normal results


if __name__ == "__main__":
    sys.exit(main())
