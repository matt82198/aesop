#!/usr/bin/env python3
"""
AST-based Python import cycle detector for internal project modules.
INDEX: AST-based import cycle detector for Python modules

Scans Python files, extracts import statements via the ast module, builds a
dependency graph of internal modules, and detects cycles using DFS.

CLI: import_cycle_check.py [--check] [--json] [--paths DIR...]
Exit: 0=no cycles, 1=cycles found, 2=error.
Stdlib-only (no pip dependencies).
"""

import ast
import json
import os
import sys
from collections import defaultdict
from pathlib import Path


def discover_python_files(roots):
    """Walk directories and yield .py file paths."""
    for root in roots:
        root = Path(root)
        if root.is_file() and root.suffix == '.py':
            yield root
            continue
        for dirpath, _dirs, filenames in os.walk(root):
            for fn in filenames:
                if fn.endswith('.py'):
                    yield Path(dirpath) / fn


def module_name_from_path(filepath, project_root):
    """Convert a file path to a dotted module name relative to project root."""
    rel = Path(filepath).resolve().relative_to(Path(project_root).resolve())
    parts = list(rel.with_suffix('').parts)
    if parts and parts[-1] == '__init__':
        parts = parts[:-1]
    return '.'.join(parts) if parts else None


def resolve_relative_import(current_module, level, name):
    """Resolve a relative import to an absolute module path."""
    if not current_module:
        return name or ''
    parts = current_module.split('.')
    # Go up `level` packages (level=1 means current package)
    if level > len(parts):
        return None  # invalid relative import
    base_parts = parts[:len(parts) - level + 1] if level <= len(parts) else []
    # For level >= 1, we go up level-1 from the package
    base_parts = parts[:-level] if level > 0 else parts
    if name:
        return '.'.join(base_parts + [name]) if base_parts else name
    return '.'.join(base_parts) if base_parts else None


def extract_imports(filepath, project_root, known_modules):
    """Parse a Python file's AST and extract internal import targets."""
    current_module = module_name_from_path(filepath, project_root)
    if current_module is None:
        return []

    try:
        with open(filepath, encoding='utf-8') as f:
            source = f.read()
        tree = ast.parse(source, filename=str(filepath))
    except (SyntaxError, UnicodeDecodeError, ValueError):
        return []

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name
                # Check if any prefix matches a known module
                if _is_internal(mod, known_modules):
                    imports.append(mod)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # Relative import
                resolved = resolve_relative_import(
                    current_module, node.level, node.module
                )
                if resolved and _is_internal(resolved, known_modules):
                    imports.append(resolved)
            elif node.module:
                if _is_internal(node.module, known_modules):
                    imports.append(node.module)
    return imports


def _is_internal(module_name, known_modules):
    """Check if a module name (or any prefix of it) is in known_modules."""
    parts = module_name.split('.')
    for i in range(len(parts), 0, -1):
        candidate = '.'.join(parts[:i])
        if candidate in known_modules:
            return True
    return False


def build_graph(roots, project_root):
    """Build the import dependency graph for all Python files under roots."""
    # First pass: discover all modules
    all_files = list(discover_python_files(roots))
    known_modules = set()
    file_to_module = {}
    for fp in all_files:
        mod = module_name_from_path(fp, project_root)
        if mod:
            known_modules.add(mod)
            file_to_module[fp] = mod

    # Second pass: extract imports
    graph = defaultdict(set)
    for fp, mod in file_to_module.items():
        # Ensure every module appears in the graph even with no deps
        if mod not in graph:
            graph[mod] = set()
        deps = extract_imports(fp, project_root, known_modules)
        for dep in deps:
            # Resolve to the longest known module prefix
            resolved = _resolve_to_known(dep, known_modules)
            if resolved and resolved != mod:
                graph[mod].add(resolved)

    return dict(graph)


def _resolve_to_known(module_name, known_modules):
    """Find the longest known module prefix for a dotted name."""
    parts = module_name.split('.')
    for i in range(len(parts), 0, -1):
        candidate = '.'.join(parts[:i])
        if candidate in known_modules:
            return candidate
    return None


def find_cycles(graph):
    """Find all elementary cycles in the directed graph using DFS."""
    cycles = []
    visited = set()
    rec_stack = set()
    path = []

    def dfs(node):
        visited.add(node)
        rec_stack.add(node)
        path.append(node)

        for neighbor in sorted(graph.get(node, [])):
            if neighbor not in visited:
                dfs(neighbor)
            elif neighbor in rec_stack:
                # Found a cycle: extract it from path
                idx = path.index(neighbor)
                cycle = path[idx:] + [neighbor]
                cycles.append(cycle)

        path.pop()
        rec_stack.discard(node)

    for node in sorted(graph):
        if node not in visited:
            dfs(node)

    return cycles


def dedupe_cycles(cycles):
    """Remove duplicate cycles (same set of nodes, different starting points)."""
    seen = set()
    unique = []
    for cycle in cycles:
        # Normalize: the chain without the repeated tail
        chain = cycle[:-1]
        if not chain:
            continue
        # Canonical form: rotate so smallest element is first
        min_idx = chain.index(min(chain))
        canonical = tuple(chain[min_idx:] + chain[:min_idx])
        if canonical not in seen:
            seen.add(canonical)
            unique.append(cycle)
    return unique


def format_cycle(cycle):
    """Format a cycle as A -> B -> C -> A."""
    return ' -> '.join(cycle)


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    # Simple arg parsing (stdlib only)
    check_mode = '--check' in argv
    json_mode = '--json' in argv
    paths_flag = '--paths' in argv
    root_flag = '--root' in argv
    help_flag = '--help' in argv or '-h' in argv

    if help_flag:
        print('Usage: import_cycle_check.py [--check] [--json] [--root DIR] [--paths DIR...]')
        print('  --check   Exit 1 if cycles found (CI gate mode)')
        print('  --json    Output results as JSON')
        print('  --root    Project root directory (default: inferred)')
        print('  --paths   Directories to scan (default: repo root)')
        return 0

    # Extract --root value
    explicit_root = None
    if root_flag:
        idx = argv.index('--root')
        if idx + 1 < len(argv):
            explicit_root = argv[idx + 1]

    # Extract --paths values
    args = []
    if paths_flag:
        idx = argv.index('--paths')
        args = [a for a in argv[idx + 1:] if not a.startswith('--')]

    # Determine project root
    if explicit_root:
        project_root = Path(explicit_root).resolve()
    elif args:
        # Use common parent of scan paths as project root
        project_root = Path(args[0]).resolve()
    else:
        script_dir = Path(__file__).resolve().parent
        project_root = script_dir.parent

    if not args:
        scan_roots = [project_root]
    else:
        scan_roots = [Path(a).resolve() for a in args]

    try:
        graph = build_graph(scan_roots, project_root)
        raw_cycles = find_cycles(graph)
        cycles = dedupe_cycles(raw_cycles)
    except Exception as exc:
        if json_mode:
            print(json.dumps({'error': str(exc)}, indent=2))
        else:
            print(f'ERROR: {exc}', file=sys.stderr)
        return 2

    if json_mode:
        result = {
            'cycles_found': len(cycles),
            'cycles': [format_cycle(c) for c in cycles],
            'module_count': len(graph),
        }
        print(json.dumps(result, indent=2))
    else:
        if cycles:
            print(f'Found {len(cycles)} import cycle(s):')
            for i, cycle in enumerate(cycles, 1):
                print(f'  {i}. {format_cycle(cycle)}')
        else:
            print(f'No import cycles detected ({len(graph)} modules scanned).')

    return 1 if cycles else 0


if __name__ == '__main__':
    sys.exit(main())
