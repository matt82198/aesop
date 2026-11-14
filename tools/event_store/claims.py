"""One-claim slice: record a PR merge from gh facts, verify "PR N merged at sha X".

record_merge never takes merge facts from its caller: it runs gh itself (arg list, no
shell, timeout) through an injectable runner and appends only when state is MERGED.
verify_claim checks the store chain, then compares parsed event fields (never raw lines).
Stdlib only. Does not touch Store.append internals; the lock here covers record-merge
against record-merge only (general append locking is a separate store.py change).
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

MERGE_KIND = "merge"
GH_TIMEOUT_S = 30
GH_FIELDS = "state,mergeCommit,headRefOid,mergedAt,baseRefName"
REPO_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*$")
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CLAIM_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
LOCK_WAIT_S = 10.0
LOCK_STALE_S = 60.0


class RecordMergeError(Exception):
    """record-merge could not establish a merged PR from gh; nothing was written."""


def default_gh_runner(argv, timeout):
    """Run gh with an argument list (no shell). Returns (returncode, stdout)."""
    r = subprocess.run(argv, capture_output=True, encoding="utf-8", errors="replace",
                       timeout=timeout, shell=False)
    return r.returncode, r.stdout


def _norm_pr(pr):
    try:
        n = int(str(pr))
    except ValueError:
        raise RecordMergeError("pr %r is not an integer" % (pr,))
    if n <= 0 or str(n) != str(pr).strip():
        raise RecordMergeError("pr %r is not a positive integer" % (pr,))
    return n


def _acquire(lock):
    deadline = time.monotonic() + LOCK_WAIT_S
    while True:
        try:
            return os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > LOCK_STALE_S:
                    lock.unlink()
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise RecordMergeError("store busy: could not take %s" % lock.name)
            time.sleep(0.05)  # sleep-ok


def _merge_events(store, repo, pr):
    out = []
    for ev in store.load():
        refs = ev.get("refs") if isinstance(ev, dict) else None
        if (isinstance(refs, dict) and ev.get("kind") == MERGE_KIND
                and refs.get("repo") == repo and refs.get("pr") == str(pr)):
            out.append(ev)
    return out


def fetch_merge_facts(repo, pr, runner):
    argv = ["gh", "pr", "view", str(pr), "--repo", repo, "--json", GH_FIELDS]
    try:
        rc, out = runner(argv, GH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise RecordMergeError("gh timed out after %ss" % GH_TIMEOUT_S)
    except OSError as exc:
        raise RecordMergeError("gh could not run: %s" % exc)
    if rc != 0:
        raise RecordMergeError("gh exited %s" % rc)
    try:
        data = json.loads(out)
    except (ValueError, TypeError):
        raise RecordMergeError("gh output is not JSON")
    if not isinstance(data, dict):
        raise RecordMergeError("gh output is not a JSON object")
    if data.get("state") != "MERGED":
        raise RecordMergeError("PR %s#%s state is %r, not MERGED" % (repo, pr, data.get("state")))
    mc = data.get("mergeCommit")
    merge_sha = mc.get("oid") if isinstance(mc, dict) else None
    head_sha = data.get("headRefOid")
    for name, val in (("mergeCommit.oid", merge_sha), ("headRefOid", head_sha)):
        if not isinstance(val, str) or not FULL_SHA_RE.match(val):
            raise RecordMergeError("gh %s is not a 40-hex sha" % name)
    return {"merge_sha": merge_sha, "head_sha": head_sha,
            "merged_at": str(data.get("mergedAt") or ""),
            "base": str(data.get("baseRefName") or "")}


def record_merge(store, repo, pr, runner=default_gh_runner, actor="record-merge"):
    """Append a merge event from gh facts. Returns (status, message); status is
    "recorded" or "already-recorded". Raises RecordMergeError, writing nothing."""
    if not isinstance(repo, str) or not REPO_RE.match(repo):
        raise RecordMergeError("repo %r is not owner/name" % (repo,))
    n = _norm_pr(pr)
    facts = fetch_merge_facts(repo, n, runner)
    lock = Path(store.path) / ".record-merge.lock"
    fd = _acquire(lock)
    try:
        existing = _merge_events(store, repo, n)
        for ev in existing:
            if ev["refs"].get("merge_sha") == facts["merge_sha"]:
                return "already-recorded", "%s#%d already recorded at seq %d" % (
                    repo, n, ev["seq"])
        if existing:
            raise RecordMergeError(
                "%s#%d already recorded at seq %d with a different merge sha; "
                "refusing to record a second" % (repo, n, existing[0]["seq"]))
        refs = {"repo": repo, "pr": str(n), "merge_sha": facts["merge_sha"],
                "head_sha": facts["head_sha"], "merged_at": facts["merged_at"],
                "base": facts["base"], "source": "gh"}
        text = "%s#%d merged at %s" % (repo, n, facts["merge_sha"])
        ev = store.append(MERGE_KIND, actor, text, refs=refs)
        return "recorded", "recorded %s#%d seq=%d" % (repo, n, ev["seq"])
    finally:
        os.close(fd)
        try:
            lock.unlink()
        except OSError:
            pass


def _result(status, seq=None, reason=""):
    return {"status": status, "seq": seq, "reason": reason}


def verify_claim(store, repo, pr, sha):
    """Check "PR <pr> in <repo> is merged at <sha>" against the store.

    verified: a merge event for repo+pr has merge_sha or head_sha equal to sha (full) or
    starting with sha (7-40 hex). refuted: merge events exist for the PR, none match.
    unknown: no event, malformed claim, ambiguous prefix, or a store whose chain does not
    verify (the whole chain is verified first; fail closed).
    """
    sha = str(sha).strip().lower()
    if not CLAIM_SHA_RE.match(sha):
        return _result("unknown", reason="sha must be 7-40 hex chars")
    try:
        n = _norm_pr(pr)
        problems = store.verify()
        if problems:
            return _result("unknown", reason="chain broken: %s" % problems[0])
        events = _merge_events(store, repo, n)
    except (RecordMergeError, OSError, ValueError) as exc:
        return _result("unknown", reason="store unreadable: %s" % exc)
    if not events:
        return _result("unknown", reason="no merge event for %s#%d" % (repo, n))
    for ev in events:
        fields = [ev["refs"].get("merge_sha"), ev["refs"].get("head_sha")]
        fields = [f for f in fields if isinstance(f, str) and FULL_SHA_RE.match(f)]
        hits = {f for f in fields if f == sha or (len(sha) < 40 and f.startswith(sha))}
        if len(hits) == 1:
            return _result("verified", ev["seq"])
        if len(hits) > 1:
            return _result("unknown", ev["seq"], "sha prefix is ambiguous within event")
    return _result("refuted", events[0]["seq"],
                   "event seq %d records a different sha" % events[0]["seq"])
