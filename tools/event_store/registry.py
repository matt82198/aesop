"""Registry of per-project stores (~/.claude/event-stores.json, env EVENT_STORES_REGISTRY)."""
import json
import os
import re
from pathlib import Path

ENV_VAR = "EVENT_STORES_REGISTRY"
GOAL_MAJOR = re.compile(r"^(G\d+)(?:\..*)?$")


class RegistryError(Exception):
    """Raised when the registry is missing or malformed."""


def registry_path():
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env)
    return Path.home() / ".claude" / "event-stores.json"


def load_registry(path=None):
    p = Path(path) if path else registry_path()
    try:
        reg = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RegistryError("cannot read registry %s: %s" % (p, exc))
    if not isinstance(reg.get("stores"), dict):
        raise RegistryError("registry %s has no 'stores' object" % p)
    return reg


def project_for_goal(reg, goal):
    """Map G2 or G2.1 to its project via the major id; None when unmapped."""
    m = GOAL_MAJOR.match(goal or "")
    if not m:
        return None
    for name, entry in reg["stores"].items():
        if m.group(1) in entry.get("goals", []):
            return name
    return None


def store_path(reg, project):
    entry = reg["stores"].get(project)
    if not entry or not entry.get("path"):
        raise RegistryError("project %r is not in the registry" % project)
    return Path(entry["path"])
