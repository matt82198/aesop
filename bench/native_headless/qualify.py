"""Run one native ticket shard against the factory benchmark's frozen schedule."""
import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import ssl
import subprocess
import sys
import tempfile
import time


@dataclass(frozen=True)
class Arm:
    driver: str
    model: str
    provider: str

    @property
    def label(self):
        return " / ".join((self.driver, self.model, self.provider))


def unique_value(values, field):
    values = set(values)
    if len(values) != 1 or not all(isinstance(v, str) and v.strip() for v in values):
        raise ValueError(f"missing or conflicting {field} in rollout: {values}")
    return values.pop()


def observed_arm(events):
    sessions = [e["payload"] for e in events if e["type"] == "session_meta"]
    if not sessions or any(s.get("source") != "exec" or s.get("originator") != "codex_exec"
                           for s in sessions):
        raise ValueError("expected a native codex exec rollout")
    provider = unique_value((s.get("model_provider") for s in sessions), "provider")
    model = unique_value((e["payload"].get("model") for e in events
                          if e["type"] == "turn_context"), "model")
    return Arm("codex-exec", model, provider)


def read_rollouts(paths):
    events, hashes = [], []
    for path in paths:
        data = path.read_bytes()
        hashes.append(hashlib.sha256(data).hexdigest())
        events.extend(json.loads(line) for line in data.splitlines())
    return events, hashes


def call(*args, cwd=None):
    return subprocess.check_output(args, cwd=cwd, stderr=subprocess.PIPE,
                                   encoding="utf-8", errors="replace").strip()


def serve(port_file, tls, model):
    os.environ["EVAL_MODEL"] = model  # The factory gateway reads this on import.
    import gateway
    gateway.UPSTREAM = "127.0.0.1"
    digest = gateway.model_digest()
    with gateway.ThreadingHTTPServer(("127.0.0.1", 0), gateway.Gateway) as server:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(tls / "server.crt", tls / "server.key")
        server.socket = context.wrap_socket(server.socket, server_side=True)
        port_file.write_text(json.dumps([server.server_port, digest]), encoding="utf-8")
        server.serve_forever()


def certificates(tls):
    args = ["openssl", "req", "-newkey", "rsa:2048", "-nodes"]
    call(*args, "-x509", "-days", "1", "-subj", "/CN=Test root", "-keyout",
         "root.key", "-out", "root.crt", cwd=tls)
    call(*args, "-subj", "/CN=localhost", "-keyout", "server.key", "-out", "server.csr", cwd=tls)
    (tls / "extensions.cnf").write_text("subjectAltName=IP:127.0.0.1\nextendedKeyUsage=serverAuth\n",
                                        encoding="ascii")
    call("openssl", "x509", "-req", "-in", "server.csr", "-CA", "root.crt", "-CAkey",
         "root.key", "-CAcreateserial", "-days", "1", "-extfile", "extensions.cnf",
         "-out", "server.crt", cwd=tls)


@contextmanager
def trial_seat(arm, folder, gateway_log):
    if arm.provider != "local_eval":
        yield {}, None
        return
    seat = folder / "seat"
    seat.mkdir()
    with tempfile.TemporaryDirectory() as temp, gateway_log.open("wb") as log:
        tls = Path(temp)
        certificates(tls)
        port_file = tls / "port.json"
        cmd = [sys.executable, "-B", "-u", *sys.argv, "--port-file", str(port_file), "--tls", temp]
        server = subprocess.Popen(cmd, stdout=log, stderr=log)
        try:
            for _ in range(100):
                if port_file.exists() and port_file.stat().st_size:
                    break
                if server.poll() is not None:
                    raise RuntimeError("local gateway startup failed; see gateway log")
                time.sleep(.1)
            port, digest = json.loads(port_file.read_bytes())
            (seat / "config.toml").write_text(
                f'[model_providers.local_eval]\nname="local"\n'
                f'base_url="https://127.0.0.1:{port}/v1"\nwire_api="responses"\n'
                '[windows]\nsandbox="mxc"\n', encoding="utf-8")
            ca = str(tls / "root.crt")
            yield {"CODEX_HOME": str(seat), "CODEX_CA_CERTIFICATE": ca, "SSL_CERT_FILE": ca}, digest
        finally:
            server.terminate()
            server.wait(timeout=10)


def dispatch(job, arm):
    from agent_driver import WorkerRequest
    from headless_cli_driver import HeadlessCliDriver
    data = json.loads(job.read_bytes())
    os.environ.update(data["env"])
    driver = HeadlessCliDriver(arm.driver, model=arm.model, provider=arm.provider,
                               timeout_s=data["timeout"],
                               env_passthrough=["CODEX_CA_CERTIFICATE", "SSL_CERT_FILE"])
    result = driver.dispatch_worker(WorkerRequest(prompt=data["prompt"], workdir=data["repo"],
                                                  owned_files=(data["target"],)))
    job.with_suffix(".result").write_text(json.dumps({"ok": result.ok, "error": result.error}),
                                          encoding="utf-8")


def run_job(job, remaining):
    cmd = [sys.executable, "-B", *sys.argv, "--dispatch", str(job)]
    with job.with_suffix(".log").open("wb") as log:
        child = subprocess.Popen(cmd, stdout=log, stderr=log, start_new_session=os.name != "nt")
        try:
            if child.wait(timeout=max(.01, remaining)):
                raise RuntimeError(f"dispatch failed; see {job.with_suffix('.log')}")
            return json.loads(job.with_suffix(".result").read_bytes())
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill.exe", "/PID", str(child.pid), "/T", "/F"],
                               capture_output=True, check=False)
            else:
                os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=10)
            return {"ok": False, "error": "external wall deadline"}


def traces_for(seat, repo, started):
    dates = {datetime.fromtimestamp(t, tz).date() for t in (started, time.time())
             for tz in (None, timezone.utc)}
    paths = []
    for day in dates:
        for p in (seat / "sessions" / day.strftime("%Y/%m/%d")).glob("rollout*.jsonl"):
            if p.stat().st_mtime < started - 2:
                continue
            with p.open(encoding="utf-8") as stream:
                line = stream.readline()
            if line:
                meta = json.loads(line)["payload"]
                if meta.get("source") == "exec" and Path(meta["cwd"]).resolve() == repo.resolve():
                    paths.append(p)
    return sorted(paths)


def collect(ticket, repeat, repo, out, image, result, elapsed, paths, requested):
    from run import LIMITS, GRADE, diff_stats, digest, grade
    from tickets import prompt, repository
    events, hashes = read_rollouts(paths)
    actual = observed_arm(events)
    if actual != requested:
        raise ValueError(f"requested {requested.label}; observed {actual.label}")
    contexts = [e["payload"] for e in events if e["type"] == "turn_context"]
    policies = {(v["approval_policy"], v["approvals_reviewer"], v["sandbox_policy"]["type"])
                for v in contexts}
    if policies != {("on-request", "auto_review", "workspace-write")}:
        raise ValueError(f"unexpected native policy: {policies}")
    before = {name: data.encode() for name, data in repository().items()}
    after = {}
    for path in repo.rglob("*"):
        rel = path.relative_to(repo)
        if ".git" in rel.parts or "__pycache__" in rel.parts:
            continue
        if path.is_symlink():
            after[rel.as_posix()] = b"\x00nonregular"
        elif path.is_file():
            after[rel.as_posix()] = path.read_bytes()
    stats, changed = diff_stats(before, after)
    target = ticket.key + ".py"
    candidate = after.get(target, b"")
    (out / f"{ticket.key}-{repeat}.candidate").write_bytes(candidate)
    passed = grade(ticket, candidate.decode("utf8", "replace"),
                   ["docker", "run", "--rm", "-i", "--network", "none", *LIMITS,
                    image, "python3", "-I", "-B", "-c", GRADE])
    usage = [e["payload"]["info"]["total_token_usage"] for e in events if
             e["type"] == "event_msg" and e["payload"].get("type") == "token_count"
             and e["payload"].get("info")]
    return dict(**asdict(actual), ticket=ticket.key, repeat=repeat, wall_s=elapsed,
                tests_pass=passed, scoped=changed == [target], worker_ok=result["ok"],
                worker_error=result["error"], diff=stats, changed_paths=changed,
                candidate_sha256=digest(candidate), prompt_sha256=digest(prompt(ticket).encode()),
                tokens=usage[-1] if usage else None, reported_usd=None, policy=sorted(policies),
                trace_sha256=hashes, tool_calls=sum(e["type"] == "response_item" and
                e["payload"].get("type") in ("function_call", "custom_tool_call") for e in events))


def trial(ticket, repeat, folder, out, prior, arm, resume):
    from tickets import prompt, repository
    log = out / f"{ticket.key}-{repeat}.gateway"
    job, repo = folder / "job.json", folder / "repo"
    limit = prior["timeout_s"]
    if resume and folder.exists():
        data = json.loads(job.read_bytes())
        if data["arm"] != asdict(arm):
            raise ValueError("cannot resume a different arm")
        result = {"ok": False, "error": "external wall deadline"}
        elapsed = limit
    else:
        repo.mkdir(parents=True)
        with trial_seat(arm, folder, log) as (env, model_digest):
            start = time.monotonic()
            data = dict(env=env, arm=asdict(arm), started=time.time(), model_digest=model_digest,
                        timeout=limit, prompt=prompt(ticket), repo=str(repo), target=ticket.key+".py")
            for name, value in repository().items():
                (repo / name).write_bytes(value.encode())
            call("git", "init", "-q", cwd=repo)
            call("git", "add", ".", cwd=repo)
            call("git", "-c", "user.name=Fixture", "-c", "user.email=fixture@localhost",
                 "commit", "-qm", "seed", cwd=repo)
            job.write_text(json.dumps(data), encoding="utf-8")
            result = run_job(job, limit - (time.monotonic() - start))
            elapsed = round(time.monotonic() - start, 3)
    seat = Path(data["env"].get("CODEX_HOME") or os.environ.get("CODEX_HOME") or Path.home()/".codex")
    paths = traces_for(seat, repo, data["started"])
    row = collect(ticket, repeat, repo, out, prior["image"], result, elapsed, paths, arm)
    inference = bool((row["tokens"] or {}).get("total_tokens"))
    if arm.provider == "local_eval":
        statuses = []
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if "status" in event:
                statuses.append(event["status"])
        row.update(gateway_responses=statuses, model_digest=data["model_digest"])
        inference = 200 in statuses
    row["accepted"] = bool(row["worker_ok"] and row["tests_pass"] and row["scoped"]
                           and elapsed <= limit and inference)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("kernel", "factory", "out"):
        parser.add_argument("--"+arg, type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", required=True, choices=("local_eval", "openai"))
    parser.add_argument("--ticket", required=True)
    parser.add_argument("--resume", type=Path, help="existing per-trial workspace root")
    parser.add_argument("--dispatch", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--port-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--tls", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.model.strip() or args.model.startswith("-") or any(c in args.model for c in "|\r\n"):
        parser.error("--model requires a model id")
    arm = Arm("codex-exec", args.model, args.provider)
    sys.path[:0] = [str(args.kernel / "driver"), str(args.factory / "evals/harness")]
    if args.dispatch:
        return dispatch(args.dispatch, arm)
    if args.port_file:
        return serve(args.port_file, args.tls, arm.model)
    from run import digest, encode, write
    from tickets import repository, TICKETS, prompt
    prior_path = args.factory / "evals/harness/rerun-03/run.json"
    prior = json.loads(prior_path.read_bytes())
    ticket = next((t for t in TICKETS if t.key == args.ticket), None)
    if not ticket:
        parser.error("unknown ticket")
    cli_version = call(shutil.which("codex") or "codex", "--version")
    slots = [r for r in prior["rows"] if r["profile"] == arm.driver and r["ticket"] == ticket.key]
    if (prior["planned"] != len(prior["rows"]) or prior["planned"] != 36 or
        digest(encode(repository())) != prior["baseline_sha256"] or
        call("docker", "image", "inspect", "-f", "{{.Id}}", prior["image"]) != prior["image"] or
        cli_version != prior["cli_versions"]["codex"] or
        sorted(r["repeat"] for r in slots) != [1, 2, 3] or
        any(digest(prompt(ticket).encode()) != r["prompt_sha256"] for r in slots)):
        raise ValueError("schedule, fixture, prompt, CLI version or grader differs from benchmark")
    info = dict(requested_arm=asdict(arm), comparison_sha256=digest(prior_path.read_bytes()),
                kernel=call("git", "rev-parse", "HEAD", cwd=args.kernel),
                factory=call("git", "rev-parse", "HEAD", cwd=args.factory),
                cli_version=cli_version, platform=sys.platform, baseline_sha256=prior["baseline_sha256"],
                image=prior["image"], timeout_s=prior["timeout_s"], shard=args.ticket, planned=3)
    args.out.mkdir(parents=True, exist_ok=bool(args.resume))
    home = (args.resume or Path(tempfile.mkdtemp(prefix="native-ticket-grid-"))).resolve()
    run = args.out / "run.json"
    rows = []
    if args.resume and run.exists():
        saved = json.loads(run.read_bytes())
        rows = saved.pop("rows")
        if saved != info or any(any(row.get(k) != v for k, v in asdict(arm).items()) for row in rows):
            raise ValueError("resume receipt differs from requested evaluation")
    for slot in slots:
        repeat = slot["repeat"]
        if any(r["repeat"] == repeat for r in rows):
            continue
        row = trial(ticket, repeat, home / f"{ticket.key}-{repeat}", args.out, prior, arm, args.resume)
        rows.append(row)
        write(run, dict(**info, rows=rows))
        print(f"{row['driver']} / {row['model']} / {row['provider']} | {ticket.key} {repeat} | "
              f"tests {row['tests_pass']} | scoped {row['scoped']} | accepted {row['accepted']}", flush=True)


if __name__ == "__main__":
    main()
