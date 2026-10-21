#!/usr/bin/env python3
"""README Mermaid gate.

INDEX: CI/publish gate: fails when README Mermaid fences ship bare (outside <details>), lack a docs/img SVG image line, or have a stale SVG (sha256 comment mismatch).

npm does not render Mermaid, so every ```mermaid fence in README.md must be
paired with a rendered image that npm can show. This gate fails (exit 1) when:

  1. a ```mermaid fence is not inside a <details> block (a bare fence would
     ship to npm as raw text);
  2. the nearest image line above the fence does not point at a docs/img/*.svg
     file, or that SVG does not exist;
  3. the SVG is stale: its embedded
     `<!-- mermaid-source-sha256: <hex> -->` comment does not match the sha256
     of the fence body (or the comment is missing).

Usage: python tools/readme_lint.py [--root DIR]
Exit 0 = clean, exit 1 = violations (or README unreadable: fail closed).
"""

import argparse
import hashlib
import os
import re
import sys

IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
SVG_HASH_RE = re.compile(r"<!--\s*mermaid-source-sha256:\s*([0-9a-f]{64})\s*-->")
FENCE_OPEN = "```mermaid"
FENCE_CLOSE = "```"


def mermaid_body_hash(body):
    """sha256 hex of a fence body (lines joined with LF, no trailing newline)."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _svg_name_from_url(url):
    marker = "docs/img/"
    idx = url.find(marker)
    if idx < 0:
        return None
    name = url[idx + len(marker):]
    if not name.endswith(".svg") or "/" in name or not name:
        return None
    return name


def lint_readme(text, root):
    """Return a list of violation strings for README text under repo root."""
    violations = []
    lines = text.splitlines()
    depth = 0
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("<details"):
            depth += 1
        elif stripped.startswith("</details>"):
            depth = max(0, depth - 1)
        if stripped != FENCE_OPEN:
            i += 1
            continue

        fence_line = i + 1
        j = i + 1
        while j < len(lines) and lines[j].strip() != FENCE_CLOSE:
            j += 1
        if j >= len(lines):
            violations.append("line %d: unterminated mermaid fence" % fence_line)
            break
        body = "\n".join(lines[i + 1:j])

        if depth == 0:
            violations.append(
                "line %d: mermaid fence is not inside a <details> block" % fence_line
            )

        image_url = None
        for k in range(i - 1, -1, -1):
            m = IMAGE_RE.search(lines[k])
            if m:
                image_url = m.group(1)
                break
        if image_url is None:
            violations.append(
                "line %d: no image line above mermaid fence" % fence_line
            )
        else:
            svg_name = _svg_name_from_url(image_url)
            svg_path = os.path.join(root, "docs", "img", svg_name) if svg_name else None
            if svg_path is None:
                violations.append(
                    "line %d: image above fence does not point at docs/img/*.svg (%s)"
                    % (fence_line, image_url)
                )
            elif not os.path.isfile(svg_path):
                violations.append(
                    "line %d: image SVG missing: docs/img/%s" % (fence_line, svg_name)
                )
            else:
                with open(svg_path, "r", encoding="utf-8", errors="replace") as fh:
                    svg = fh.read()
                m = SVG_HASH_RE.search(svg)
                expected = mermaid_body_hash(body)
                if m is None:
                    violations.append(
                        "docs/img/%s: no mermaid-source-sha256 comment (stale or unrendered)"
                        % svg_name
                    )
                elif m.group(1) != expected:
                    violations.append(
                        "docs/img/%s: stale SVG, hash %s != fence hash %s"
                        % (svg_name, m.group(1), expected)
                    )
        i = j + 1
    return violations


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    here = os.path.dirname(os.path.abspath(__file__))
    parser.add_argument("--root", default=os.path.dirname(here))
    args = parser.parse_args(argv)
    readme = os.path.join(args.root, "README.md")
    try:
        with open(readme, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        print("readme_lint: cannot read %s: %s" % (readme, exc))
        return 1
    violations = lint_readme(text, args.root)
    for v in violations:
        print("readme_lint: " + v)
    if violations:
        print("readme_lint: FAIL (%d violation(s))" % len(violations))
        return 1
    print("readme_lint: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
