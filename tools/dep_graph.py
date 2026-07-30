#!/usr/bin/env python3
"""Python module dependency graph generator (Mermaid/DOT/JSON output).

Scans Python files, extracts import relationships via ast, builds a directed
graph of internal module dependencies, and outputs in the requested format.
Highlights cycles in red when detected.

CLI: dep_graph.py [--paths DIR...] [--root DIR] [--output FILE] [--format mermaid|dot|json]
Exit: 0=success, 2=error.
"""

import argparse
import ast
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


def find_python_files(root: Path, paths: Optional[List[str]] = None) -> List[Path]:
    """Find all .py files under root, optionally filtered to given subdirs."""
    targets: List[Path] = []
    if paths:
        for p in paths:
            search = root / p
            if search.is_file() and search.suffix == ".py":
                targets.append(search)
            elif search.is_dir():
                for dirpath, _dirs, files in os.walk(search):
                    for f in files:
                        if f.endswith(".py"):
                            targets.append(Path(dirpath) / f)
    else:
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if f.endswith(".py"):
                    targets.append(Path(dirpath) / f)
    return sorted(targets)


def file_to_module(filepath: Path, root: Path) -> str:
    """Convert a file path to a dotted module name relative to root."""
    rel = filepath.relative_to(root)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1].removesuffix(".py")
    return ".".join(parts)


def extract_imports(filepath: Path) -> List[str]:
    """Extract import targets from a Python file using ast."""
    try:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(filepath))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []
    imports: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return imports


def build_graph(
    root: Path, paths: Optional[List[str]] = None
) -> Tuple[Dict[str, Set[str]], Set[str]]:
    """Build a dependency graph of internal modules.

    Returns (adjacency dict, set of all module names).
    """
    py_files = find_python_files(root, paths)
    known_modules: Set[str] = set()
    file_module_map: Dict[Path, str] = {}
    for fp in py_files:
        mod = file_to_module(fp, root)
        known_modules.add(mod)
        file_module_map[fp] = mod

    graph: Dict[str, Set[str]] = {m: set() for m in known_modules}

    for fp, mod in file_module_map.items():
        raw_imports = extract_imports(fp)
        for imp in raw_imports:
            # Match full module or parent package
            if imp in known_modules:
                if imp != mod:
                    graph[mod].add(imp)
            else:
                # Check if import is a sub-path of a known module
                parts = imp.split(".")
                for i in range(len(parts), 0, -1):
                    prefix = ".".join(parts[:i])
                    if prefix in known_modules and prefix != mod:
                        graph[mod].add(prefix)
                        break
    return graph, known_modules


def find_cycles(graph: Dict[str, Set[str]]) -> List[List[str]]:
    """Find all elementary cycles using DFS (Johnson-lite)."""
    cycles: List[List[str]] = []
    color: Dict[str, int] = {}  # 0=white, 1=gray, 2=black
    path: List[str] = []

    def dfs(node: str) -> None:
        color[node] = 1
        path.append(node)
        for neighbor in sorted(graph.get(node, set())):
            if color.get(neighbor, 0) == 1:
                # Found a cycle
                idx = path.index(neighbor)
                cycle = path[idx:] + [neighbor]
                cycles.append(cycle)
            elif color.get(neighbor, 0) == 0:
                dfs(neighbor)
        path.pop()
        color[node] = 2

    for node in sorted(graph):
        if color.get(node, 0) == 0:
            dfs(node)

    return cycles


def cycle_edges(cycles: List[List[str]]) -> Set[Tuple[str, str]]:
    """Extract the set of directed edges involved in cycles."""
    edges: Set[Tuple[str, str]] = set()
    for cycle in cycles:
        for i in range(len(cycle) - 1):
            edges.add((cycle[i], cycle[i + 1]))
    return edges


def sanitize_id(name: str) -> str:
    """Make a module name safe for use as a Mermaid/DOT node ID."""
    return name.replace(".", "_").replace("-", "_")


def render_mermaid(
    graph: Dict[str, Set[str]], cycles: List[List[str]]
) -> str:
    """Render the graph as a Mermaid flowchart."""
    lines = ["flowchart LR"]
    red_edges = cycle_edges(cycles)
    red_nodes: Set[str] = set()
    for a, b in red_edges:
        red_nodes.add(a)
        red_nodes.add(b)

    # Declare nodes
    for mod in sorted(graph):
        sid = sanitize_id(mod)
        lines.append(f"    {sid}[\"{mod}\"]")

    # Edges
    for mod in sorted(graph):
        for dep in sorted(graph[mod]):
            sid_from = sanitize_id(mod)
            sid_to = sanitize_id(dep)
            if (mod, dep) in red_edges:
                lines.append(f"    {sid_from} --> {sid_to}")
            else:
                lines.append(f"    {sid_from} --> {sid_to}")

    # Style cycle nodes red
    if red_nodes:
        for node in sorted(red_nodes):
            sid = sanitize_id(node)
            lines.append(f"    style {sid} fill:#f99,stroke:#f00")

    return "\n".join(lines) + "\n"


def render_dot(
    graph: Dict[str, Set[str]], cycles: List[List[str]]
) -> str:
    """Render the graph as a Graphviz DOT digraph."""
    lines = ["digraph dependencies {", "    rankdir=LR;"]
    red_edges = cycle_edges(cycles)
    red_nodes: Set[str] = set()
    for a, b in red_edges:
        red_nodes.add(a)
        red_nodes.add(b)

    for mod in sorted(graph):
        sid = sanitize_id(mod)
        attrs = 'style=filled fillcolor="#ff9999"' if mod in red_nodes else ""
        label = f'label="{mod}"'
        parts = [label]
        if attrs:
            parts.append(attrs)
        lines.append(f"    {sid} [{' '.join(parts)}];")

    for mod in sorted(graph):
        for dep in sorted(graph[mod]):
            sid_from = sanitize_id(mod)
            sid_to = sanitize_id(dep)
            color = ' [color=red]' if (mod, dep) in red_edges else ""
            lines.append(f"    {sid_from} -> {sid_to}{color};")

    lines.append("}")
    return "\n".join(lines) + "\n"


def render_json(
    graph: Dict[str, Set[str]], cycles: List[List[str]]
) -> str:
    """Render the graph as JSON."""
    data = {
        "modules": sorted(graph.keys()),
        "edges": [
            {"from": mod, "to": dep}
            for mod in sorted(graph)
            for dep in sorted(graph[mod])
        ],
        "cycles": cycles,
    }
    return json.dumps(data, indent=2) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Python module dependency graph generator."
    )
    parser.add_argument(
        "--paths",
        nargs="+",
        default=None,
        help="Directories or files to scan (relative to --root).",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Project root directory (default: cwd).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output file (default: stdout).",
    )
    parser.add_argument(
        "--format",
        choices=["mermaid", "dot", "json"],
        default="mermaid",
        help="Output format (default: mermaid).",
    )

    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code != 0 else 0

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"Error: root directory not found: {root}", file=sys.stderr)
        return 2

    try:
        graph, _modules = build_graph(root, args.paths)
        cycles = find_cycles(graph)

        renderers = {
            "mermaid": render_mermaid,
            "dot": render_dot,
            "json": render_json,
        }
        output = renderers[args.format](graph, cycles)

        if args.output:
            Path(args.output).write_text(output, encoding="utf-8")
        else:
            sys.stdout.write(output)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
