"""One-claim slice: record a PR merge from gh facts, verify "PR N merged at sha X".

record_merge never takes merge facts from its caller: it runs gh itself (arg list, no
shell, timeout) through an injectable runner and appends only when state is MERGED.
verify_claim checks the store chain and the recorded head, then compares parsed event
fields (never raw lines). Stdlib only. Does not touch Store.append internals; the lock
here covers record-merge against record-merge only (general append locking is a separate
store.py change).
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

from event_store.store import line_sha

MERGE_KIND = "merge"
MERGE_ACTOR = "record-merge"
MERGE_SOURCE = "gh"
GH_TIMEOUT_S = 30
GH_FIELDS = "state,mergeCommit,headRefOid,mergedAt,baseRefName,number,url"
HEAD_RE = re.compile(r"^[0-9a-f]{64}$")
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


def _break_stale(lock, seen_mtime_ns):
    """Break a stale lock atomically. Only the contender whose rename succeeds proceeds.

    The lock is renamed to a name unique to this process; a failed rename (someone else
    broke it first) returns False and the caller goes back to waiting. If the renamed file
    is not the stale one observed (a fresh lock slipped in), it is linked back and False
    is returned.
    """
    try:
        if lock.stat().st_mtime_ns != seen_mtime_ns:
            return False
    except OSError:
        return False
    mine = lock.with_name("%s.stale-%d-%d" % (lock.name, os.getpid(), time.time_ns()))
    try:
        os.rename(str(lock), str(mine))
    except OSError:
        return False
    try:
        if mine.stat().st_mtime_ns != seen_mtime_ns:
            try:
                os.link(str(mine), str(lock))  # restore; fails if a newer lock exists
            except OSError:
                pass
            return False
        return True
    except OSError:
        return False
    finally:
        try:
            mine.unlink()
        except OSError:
            pass


def _acquire(lock):
    deadline = time.monotonic() + LOCK_WAIT_S
    while True:
        try:
            return os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            pass
        except OSError as exc:
            raise RecordMergeError("store busy or lock unusable: %s: %s" % (lock.name, exc))
        try:
            if lock.is_dir():
                raise RecordMergeError("store busy or lock unusable: %s is a directory"
                                       % lock.name)
            st = lock.stat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RecordMergeError("store busy or lock unusable: %s: %s" % (lock.name, exc))
        if time.time() - st.st_mtime > LOCK_STALE_S and _break_stale(lock, st.st_mtime_ns):
            continue
        if time.monotonic() > deadline:
            raise RecordMergeError("store busy: could not take %s" % lock.name)
        time.sleep(0.05)  # sleep-ok


def _merge_events(store, repo, pr):
    """(recognised, unrecognised) merge events for repo+pr, from parsed events.

    Recognised means written by record-merge: actor MERGE_ACTOR and refs source MERGE_SOURCE.
    Anything else of kind merge for the same repo+pr is unrecognised.
    """
    good, bad = [], []
    for ev in store.load():
        if not isinstance(ev, dict) or ev.get("kind") != MERGE_KIND:
            continue
        refs = ev.get("refs")
        if not (isinstance(refs, dict) and refs.get("repo") == repo
                and refs.get("pr") == str(pr)):
            continue
        if ev.get("actor") == MERGE_ACTOR and refs.get("source") == MERGE_SOURCE:
            good.append(ev)
        else:
            bad.append(ev)
    return good, bad


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
    number, url = data.get("number"), data.get("url")
    if isinstance(number, bool) or number != pr:
        raise RecordMergeError("gh returned PR number %r, requested %s" % (number, pr))
    want = "https://github.com/%s/pull/%s" % (repo, pr)
    if not isinstance(url, str) or url.lower() != want.lower():
        raise RecordMergeError("gh returned url %r, expected %s" % (url, want))
    mc = data.get("mergeCommit")
    merge_sha = mc.get("oid") if isinstance(mc, dict) else None
    head_sha = data.get("headRefOid")
    for name, val in (("mergeCommit.oid", merge_sha), ("headRefOid", head_sha)):
        if not isinstance(val, str) or not FULL_SHA_RE.match(val):
            raise RecordMergeError("gh %s is not a 40-hex sha" % name)
    return {"merge_sha": merge_sha, "head_sha": head_sha,
            "merged_at": str(data.get("mergedAt") or ""),
            "base": str(data.get("baseRefName") or "")}


def record_merge(store, repo, pr, runner=default_gh_runner):
    """Append a merge event from gh facts. Returns (status, message); status is
    "recorded" or "already-recorded". Raises RecordMergeError, writing nothing.

    Any unexpected exception (runner, corrupt store) is re-raised as RecordMergeError.
    """
    try:
        return _record_merge(store, repo, pr, runner)
    except RecordMergeError:
        raise
    except Exception as exc:  # fail closed: the caller always gets one documented error
        raise RecordMergeError("unexpected %s: %s" % (type(exc).__name__, exc))


def _record_merge(store, repo, pr, runner):
    if not isinstance(repo, str) or not REPO_RE.match(repo):
        raise RecordMergeError("repo %r is not owner/name" % (repo,))
    n = _norm_pr(pr)
    facts = fetch_merge_facts(repo, n, runner)
    lock = Path(store.path) / ".record-merge.lock"
    fd = _acquire(lock)
    try:
        problems = store.verify()
        if problems:
            raise RecordMergeError("store chain broken: %s; refusing to append" % problems[0])
        existing, unrecognised = _merge_events(store, repo, n)
        if unrecognised:
            raise RecordMergeError(
                "%s#%d has an unrecognised merge event at seq %s (not written by "
                "record-merge); refusing to record" % (repo, n, unrecognised[0].get("seq")))
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
                "base": facts["base"], "source": MERGE_SOURCE}
        text = "%s#%d merged at %s" % (repo, n, facts["merge_sha"])
        ev = store.append(MERGE_KIND, MERGE_ACTOR, text, refs=refs)
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

    verified: a record-merge merge event for repo+pr has merge_sha equal to sha (full) or
    starting with sha (7-40 hex). head_sha is recorded but never matched. refuted: such
    events exist and none match. unknown: no event, malformed claim, an unrecognised merge
    event for the PR, conflicting merge events, a chain that does not verify, a missing or
    wrong head, or ANY error while reading the store (fail closed; never refuted on a crash).
    """
    try:
        return _verify_claim(store, repo, pr, sha)
    except Exception as exc:
        return _result("unknown", reason="store unreadable: %s: %s" % (type(exc).__name__, exc))


def _head_problem(store):
    """None when store.json carries a well-formed head equal to the last line's sha."""
    try:
        meta = json.loads((Path(store.path) / "store.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return "store.json unreadable: %s" % exc
    head = meta.get("head") if isinstance(meta, dict) else None
    if not isinstance(head, str) or not HEAD_RE.match(head):
        return "store.json head is missing or malformed; the tail is not anchored"
    last = None
    for f in store.day_files():
        lines = [x for x in f.read_bytes().split(b"\n") if x]
        if lines:
            last = lines[-1]
    if last is None or line_sha(last) != head:
        return "store.json head does not match the last line"
    return None


def _verify_claim(store, repo, pr, sha):
    sha = str(sha).strip().lower()
    if not CLAIM_SHA_RE.match(sha):
        return _result("unknown", reason="sha must be 7-40 hex chars")
    n = _norm_pr(pr)
    problems = store.verify()
    if problems:
        return _result("unknown", reason="chain broken: %s" % problems[0])
    problem = _head_problem(store)
    if problem:
        return _result("unknown", reason="head check failed: %s" % problem)
    events, unrecognised = _merge_events(store, repo, n)
    if unrecognised:
        return _result("unknown", unrecognised[0].get("seq"),
                       "unrecognised merge event at seq %s for %s#%d (not written by "
                       "record-merge)" % (unrecognised[0].get("seq"), repo, n))
    if not events:
        return _result("unknown", reason="no merge event for %s#%d" % (repo, n))
    shas = {ev["refs"].get("merge_sha") for ev in events}
    if len(shas) > 1:
        return _result("unknown", events[0]["seq"],
                       "ambiguous: %d merge events with different shas for %s#%d"
                       % (len(shas), repo, n))
    for ev in events:
        f = ev["refs"].get("merge_sha")
        if isinstance(f, str) and FULL_SHA_RE.match(f) and (
                f == sha or (len(sha) < 40 and f.startswith(sha))):
            return _result("verified", ev["seq"])
    return _result("refuted", events[0]["seq"],
                   "event seq %d records a different merge sha" % events[0]["seq"])
