# Aesop auth and account layer: plan

Status: DRAFT plan, 2026-10-31. Owner: Matt. No code in this PR.
Companion docs: [stdio pipelines plan](./native-stdio-pipelines.md), [interactive chat plan](./aesop-interactive-chat.md).

## 1. Intent

Matt (owner), 2026-10-31, verbatim: "we also need a really good auth pattern for aesop... like connecting accounts and selecting models in a cli is fine first and standard auth, but this all needs to be planned" and "so that it can be used with a ui in the future".

Reading: an auth and account layer that is CLI-first, uses standard flows, lets a person connect several accounts and pick models, and exposes one stable machine-readable contract so a UI (and the interactive chat) is a client of it, never a second implementation.

## 2. Inventory: how aesop authenticates today (cited; code reads, nothing run)

- ANTHROPIC_API_KEY: read from env in driver/anthropic_transport.py:76-80 with a fallback chain (configured api_key_env, then BENCH_API_KEY, then ANTHROPIC_API_KEY); sent as x-api-key (anthropic_transport.py:87-92). Not stored by aesop. The silent fallback across names conflicts with the no-credential-hunting rule that a missing key is SKIP and REPORT (inference; not reproduced).
- BENCH_API_KEY: tools/bench_api_runner.py:77-82 (fails if missing, setx hint), bench/probe_refusals.py:114-116 (exit 2), preferred key in anthropic_transport.py:78.
- OPENAI_API_KEY: driver/openai_transport.py:116-122 (raises if unset); default api_key_env in driver/openai_compatible_driver.py:37 and driver/backend_config.py:875; bench/accuracy_harness.py:695-698. OPENROUTER_API_KEY and TOGETHER_API_KEY appear only in driver/aesop.config.example.json:26, :54.
- Config holds the NAME of the env var, never the secret: api_key_env allowlist check at driver/backend_config.py:319; base_url SSRF guard at backend_config.py:84-140.
- GitHub: no aesop code reads GITHUB_TOKEN or GH_TOKEN. The gh CLI auth is probed by bin/cli.js:595 and docs/CI-MODES.md:101. Actions template uses secrets.ANTHROPIC_API_KEY (docs/ACTIONS-TEMPLATE.md:117-122).
- Claude Code login: aesop shells out to Claude Code (ClaudeCodeDriver, driver/claude_code_driver.py:58-62) which uses the user's own Claude Code login; aesop never touches that credential.
- Receipt signing key (Ed25519): path from AESOP_RECEIPT_KEY, then AESOP_HOME or HOME under .aesop/receipt_key.pem (tools/receipt_common.py:264-285); public keys in tools/receipt_pubkey.pub and tools/receipt_pubkeys/*.pub (receipt_common.py:143-161); emitter scrubs AESOP_RECEIPT_KEY and AESOP_RECEIPT_HMAC_SECRET from child env (tools/emit_receipt.py:237); exits 2 with no key material (emit_receipt.py:879-888); HMAC fallback via AESOP_RECEIPT_HMAC_SECRET; keypair generator at receipt_common.py:58-72 (chmod 600). The receipt-keys memory records the signing keyring as deferred; treat rotation policy as an open item.
- Secrets gate: tools/secret_scan.py (header lines 1-40) with fatal classes including PEM private keys, AWS/GitHub/Slack/OpenAI and Anthropic tokens, connection strings; modes --staged, --range, --history, paths, --from-stdin (:1103).
- Local web UI auth: loopback bind 127.0.0.1 (ui/handler.py:1407); Host allowlist (handler.py:116-183); per-session token generated at ui/csrf.py (token_urlsafe(32)), stored 0600 at state/.ui-session-token, required as X-Aesop-Token on mutations (csrf.py:169-204); GET routes are unauthenticated (ui/CLAUDE.md:105); dev origins via AESOP_DASH_DEV_ORIGINS (csrf.py:55-58). No user login exists.
- Model selection today: driver role maps in code (claude_code_driver.py:58-62 worker haiku, setup sonnet, verify haiku; codex_driver.py:70-74; anthropic_driver.py:111-115, possibly stale model ids, unverified); backend config file aesop.config.json with seats.worker and seats.orchestrator (driver/backend_config.py:9-34, :466, :612, :761; example driver/aesop.config.example.json:68-83). The config path is cwd-relative (backend_config.py:499-524) while ui/config.py:94 resolves against AESOP_ROOT, so two readers can disagree for the same file (observation, not reproduced).
- Model lever (orchestrator-side, outside the repo): ~/.claude/state/context-levers.json block "model" {default_model, max_model, reason, set_at, ttl_s, set_by}, written only by ~/scripts/context_levers.py (docstring lines 1-80), enforced by ~/.claude/hooks/model-lever.mjs decide() lines 107-128 (denies missing model, fork unless max is opus, non-ladder model, above max; fails closed on bad stdin, lines 149-161). Expired or malformed means haiku.
- No `aesop auth` or `aesop models` command exists; bin/cli.js has three dispatch tiers (pythonNamespaces table at :46, runtimeCommands :247, commandMap :380; dispatch :165-235).

Gaps: no account concept; keys exist only as env vars; no listing, selection persistence or logout; no keychain; two config roots; model choice for lanes is governed by a separate lever the CLI cannot see.

## 3. Account model

One account record shape for every provider:

    {
      "id": "acct_<8 hex>",            // stable, local
      "provider": "anthropic_api | claude_code | github | openai_compat | bedrock | vertex | oidc:<issuer-alias>",
      "label": "work-codal",           // human name, unique per provider
      "kind": "api_key | oauth | cli_delegated | cloud_iam",
      "scopes": ["..."],               // as granted, informational
      "secret_ref": "keychain:aesop/<id> | env:NAME | file:<path> | gh:cli | none",
      "created_at": "ISO-8601 UTC",
      "last_used_at": "ISO-8601 UTC | null",
      "status": "ok | expired | revoked | unverified"
    }

Rules:
- The record never contains a secret value; only a secret_ref pointer. The record file is safe to read by a UI and by a lane.
- Many accounts per provider. Exactly one default per provider, plus one global default. `aesop auth use` switches the default; there is no implicit selection by scanning the environment (matches the no-credential-hunting rule; env:NAME is allowed only when explicitly registered).
- Providers (v1 candidates): Anthropic API (API key); Claude Code login (cli_delegated: aesop records that it exists and which model aliases it supports, and never reads its tokens); GitHub (delegate to gh auth; token read via `gh auth token` at use time and never stored by aesop); OpenAI-compatible endpoints (API key plus base_url); cloud IAM providers (Bedrock, Vertex) as later providers using ambient cloud credentials; Codal's identity provider is an OPEN QUESTION (open question 5.1), modelled as provider "oidc:<alias>" so it can be added without schema change.
- Claude subscription OAuth for third-party tools: treat as unsupported by default. Whether a third party may use subscription OAuth tokens is a provider-policy question we have not verified; the plan routes subscription use through the Claude Code login (cli_delegated) instead (UNVERIFIED policy, decision A3).
- Storage of account records: a single JSON file `accounts.json` in the aesop user config directory (section 6), schema-versioned, written atomically (write temp, rename), permission 0600 on POSIX.

## 4. Standard auth flows

Each provider uses the flow it actually supports; flows are specs (RFC numbers are public; provider-specific support is stated as UNVERIFIED where not checked here).

- API key paste: Anthropic API (x-api-key header), OpenAI-compatible (bearer). The only flow these providers offer for third parties. `aesop auth login anthropic_api` reads the key from a no-echo TTY prompt or from stdin (`--key-stdin`), never from argv (argv leaks into process lists and shell history). Validated by a cheap authenticated call (list models) before storing; failure stores nothing.
- OAuth 2.0 device authorization grant (RFC 8628): for GitHub (documented device flow) and any enterprise IdP that supports it. Good for headless and SSH; no browser on the host required. Tokens refreshed via refresh tokens where issued.
- Authorization code with PKCE and loopback redirect (RFC 7636, RFC 8252): for IdPs that do not support device flow, and for a UI-driven login. Loopback listener on 127.0.0.1 with an ephemeral port, state parameter checked, code_verifier never logged.
- OIDC discovery (.well-known/openid-configuration) for an enterprise IdP such as Okta or Entra (Codal's, if confirmed): provider type "oidc:<alias>" with issuer, client_id (no client secret in v1; public client with PKCE or device flow).
- Token refresh: refresh before expiry with a skew window; rotation-aware (store the new refresh token before discarding the old one; if the write fails, keep the old one and mark status unverified). Refresh is single-flight per account (file lock) so two processes cannot race.
- Logout and revocation: `aesop auth logout` deletes the local secret and, where the provider supports RFC 7009 token revocation, revokes remotely first; if remote revocation fails, local deletion still happens and the result is reported as partial (never silent). API keys cannot be revoked by aesop; logout removes the local copy and prints the provider console URL.
- Claude Code login: `aesop auth login claude_code` runs no flow; it checks that `claude` is on PATH and logged in via its own CLI and records a cli_delegated account.
- Supported matrix (v1): anthropic_api: key paste. openai_compat: key paste. github: delegate to gh (device flow done by gh). claude_code: delegated. oidc: device or PKCE, pending Codal answer. bedrock/vertex: ambient cloud credentials, later.

## 5. Model selection

Single source of truth: `models.json` next to accounts.json (section 6), with schema:

    {
      "version": 1,
      "defaults": {"worker": {"account": "acct_x", "model": "..."}, "setup": {...}, "verify": {...}, "orchestrator": {...}},
      "catalog_cache": {"acct_x": {"fetched_at": "...", "models": ["..."]}}
    }

- `aesop models list [--account ID]`: queries the provider's model-list endpoint with the account (API key providers) or reads the static catalog (claude_code aliases haiku, sonnet, opus); caches the result; marks unreachable providers as such (never fabricates a list).
- `aesop models use <role> <account>:<model>`: validates the model exists in the catalog for that account and writes the default for that role. Roles match the driver (worker, setup, verify at driver/agent_driver.py:92-96) plus orchestrator (backend_config.py:761).
- Bridge to the existing driver config: the driver keeps reading aesop.config.json (seats block). Phase 2 changes `build_driver` to consult models.json first and falls back to aesop.config.json, and `aesop models use` can also emit the equivalent seats block for people who keep config in a repo. This fixes the two-roots observation by having one resolver (AESOP_CONFIG_DIR, then cwd) used by driver, CLI and UI.
- Interaction with the orchestrator model lever (outside the repo: ~/scripts/context_levers.py, ~/.claude/hooks/model-lever.mjs). The lever is a ceiling imposed by the orchestrating session on lanes, not a user preference. The rule: effective model = min(selected model, lever max) on the ladder haiku < sonnet < opus, and the lever always wins; the lever file is READ-ONLY to the auth layer (lanes cannot write it, hook header lines 24-27). `aesop models list` shows the lever ceiling as a column when the lever file exists. The force-haiku dispatch hook stays authoritative for Claude Code lanes; the CLI selection only governs aesop's own driver backends. Both read-only views are exposed to the UI via the same JSON contract (section 7).
- Model ids in code defaults may be stale (anthropic_driver.py:111-115); phase 2 replaces hardcoded ids with catalog lookups and a stated fallback.

## 6. Credential storage

- Preferred: OS keychain.
  - Windows: Credential Manager (DPAPI-protected), accessed through the Python standard library with ctypes calling advapi32 CredWriteW/CredReadW/CredDeleteW (no third-party package). Generic credential blob limit is about 2.5 KB (public limit; UNVERIFIED for current Windows builds), enough for API keys and refresh tokens, so large tokens use the file fallback with a DPAPI-wrapped key (open decision A5).
  - macOS: the `security` command-line tool (ships with the OS) via subprocess with arguments passed in a way that keeps the secret off argv (stdin where supported; otherwise fall back to the file store; do not put secrets in argv).
  - Linux: libsecret via the `secret-tool` binary when present (optional system tool, not a package dependency); otherwise the file fallback.
- Fallback file store: `credentials.json` at 0600 in a 0700 directory (POSIX) or under the user profile with an ACL restricted to the current user (Windows), clearly labelled less safe in `aesop auth list` output (`storage: file`).
- Zero-dependency: all of the above use only stdlib plus OS-provided binaries, consistent with the stdio plan's rule (stdio plan 2.3). No `keyring` package. Optional adapters could be added outside the core package later.
- Config directory: AESOP_CONFIG_DIR, else %APPDATA%/aesop on Windows, XDG_CONFIG_HOME/aesop or ~/.config/aesop on POSIX. Never inside a repository; secret_scan keeps blocking commits of any file matching credential patterns, and a new test asserts that the config directory is rejected as a path inside a git work tree.
- Never in logs: all auth code writes through a redacting writer; secrets are wrapped in an object whose repr is "<redacted>"; stdout and stderr schemas (section 7) contain no field that can carry a secret.
- Receipts and signing keys are a separate credential class (the receipt-signing keyring, tools/receipt_common.py) and stay under their current paths; `aesop auth list` shows them as a read-only "signing" entry with key_id and no path to the private key content.

## 7. Service boundary: one contract for CLI, chat and a future UI

The auth layer is a library plus a thin CLI. A future UI and the interactive chat call the same commands over the stdio contract from the pipes plan. No UI-only code paths.

Commands (all accept `--json`; with it, stdout is exactly one JSON object or a JSONL stream, diagnostics on stderr, exit codes 0 ok, 1 negative verdict such as "not logged in", 2 usage or cannot evaluate; no secret ever appears on stdout):

- `aesop auth login <provider> [--label L] [--key-stdin | --device | --browser]`. Output: {"ok":true,"account":<record>} on success. In device flow with --json, stdout streams JSONL events: {"event":"device_code","verification_uri":"...","user_code":"...","expires_in":N,"interval":N} then {"event":"authorized","account":<record>}. The UI renders the code; the CLI prints it.
- `aesop auth logout <account> [--revoke]`. Output: {"ok":true,"local_deleted":true,"remote_revoked":true|false|null}.
- `aesop auth list`. Output: {"accounts":[<record without secret_ref internals beyond storage type>],"defaults":{"global":"acct_x","anthropic_api":"acct_x"}}.
- `aesop auth use <account>`. Output: {"ok":true,"default":"acct_x","provider":"..."}.
- `aesop auth status [<account>]`. Verifies the credential with a cheap call. Output: {"account":"acct_x","status":"ok|expired|revoked|unverified","checked_at":"..."}.
- `aesop models list [--account A]`. Output: {"account":"acct_x","models":[{"id":"...","tier":"haiku|sonnet|opus|other","lever_max":true|false}],"fetched_at":"..."}.
- `aesop models use <role> <account>:<model>`. Output: {"ok":true,"role":"worker","account":"acct_x","model":"..."}.
- `aesop models show`. Output: the defaults block plus the effective model per role after the lever ceiling, with the reason.

Transport for UI and chat: the pipe contract's control channel carries these as request/response control messages of type auth.* and models.* (stdio plan 5.4). Credentials are never carried in-band; a request names an account id and the service resolves the secret itself. A browser-based UI later talks to the loopback web server (ui/), which spawns the same commands and relays their JSON; mutations keep the existing X-Aesop-Token CSRF gate (ui/csrf.py:169-204).

Slash commands in the chat map one to one: /auth login|logout|list|use|status and /model list|use|show.

Runtime choice: the auth service is Python (tools/aesop_auth/, stdlib only: ctypes for Windows Credential Manager, urllib for HTTPS, hashlib/secrets for PKCE) wired as a pythonNamespaces verb in bin/cli.js:46, because Python's stdlib covers credential APIs and HTTPS clients and the repo's tools are Python. The Node chat is a client over the pipe contract.

## 8. Security

Threats and fail-closed defaults:
1. Token leakage into receipts, event store, logs, transcripts or PR bodies. Mitigation: redacting writer; no secret field in any schema; receipts and events carry account id only; secret_scan runs in CI over fixtures; a canary test plants a fake token and asserts it never appears in any output stream or file.
2. Credential-hunting agents (the no-credential-hunting rule). Mitigation: lanes get no access to the config directory by default; `aesop auth` refuses to run with an agent-session marker unless explicitly allowed; commands that reveal secrets do not exist (`aesop auth` has no "show key"); a missing credential is SKIP and REPORT, never a search of env or disk. The silent BENCH/ANTHROPIC fallback chain (anthropic_transport.py:76-80) is narrowed to the configured name in phase 3.
3. Multi-account confusion (acting as the wrong account, e.g. personal versus client). Mitigation: every command and every pipe session announces the active account label; destructive or billable actions print and record the account id; `aesop auth use` is explicit, never inferred; a per-repo pin file `.aesop/account` (label only, no secrets) can force an account for a repository.
4. Replay and token theft. Mitigation: OAuth state and PKCE verifier single-use with expiry; device codes expire; refresh rotation; loopback listener binds 127.0.0.1 only and closes after one request; session tokens for the UI already rotate per session.
5. Local malware reading the file fallback. Mitigation: keychain preferred; file mode 0600/ACL; `aesop auth list` flags file storage.
6. SSRF and endpoint spoofing via base_url. Mitigation: reuse the existing base_url guard (backend_config.py:84-140); HTTPS required except loopback.
7. Lever bypass (using a stronger model than the orchestrator allows). Mitigation: effective model = min(selected, lever max); the lever is read-only here.
8. Fail-closed defaults: unreadable or corrupt accounts.json is an error (exit 2), never "no accounts, proceed"; unknown schema version refuses; a failed keychain write does not fall back to the file store without an explicit flag; empty stdin for --key-stdin is an error.
9. Mixed trust in hooks: two existing hooks fail open on malformed stdin (see stdio plan 4.3); the auth layer must not rely on them.

## 9. Phased delivery

All lanes are Haiku, file-disjoint, TDD-first (failing test first, mutation proof per LANE-CONTRACT s2 and s5), and read LANE-CONTRACT.md and one domain CLAUDE.md. Shared-file edits (bin/cli.js verb table, docs/README.md) are done by the lead.

Phase 0, spec and failing tests (no implementation): docs/guides/aesop-auth-spec.md (A0-SPEC); tests/fixtures/auth/ (A0-FIX); tests/test_auth_accounts.py (A0-ACCT): test_account_record_has_no_secret_field, test_many_accounts_one_default_per_provider, test_use_unknown_account_exits_2, test_corrupt_accounts_json_exits_2, test_unknown_schema_version_refuses, test_atomic_write_survives_kill_midway; tests/test_auth_storage.py (A0-STORE): test_file_store_mode_0600_posix, test_windows_acl_current_user_only, test_keychain_write_failure_does_not_fall_back_silently, test_config_dir_inside_git_worktree_rejected, test_secret_not_in_argv_of_any_subprocess; tests/test_auth_flows.py (A0-FLOW): test_device_flow_polls_at_interval_and_honors_slow_down, test_device_flow_expiry_exits_1, test_pkce_verifier_challenge_s256_matches_rfc7636_vector, test_state_mismatch_rejected, test_refresh_rotation_keeps_old_token_if_write_fails, test_refresh_is_single_flight_across_two_processes, test_logout_revoke_failure_reports_partial; tests/test_auth_cli_contract.py (A0-CLI): test_list_json_has_no_secret_ref_value, test_login_json_streams_device_code_event, test_exit_codes_0_1_2, test_stdout_only_json_and_stderr_only_diagnostics, test_canary_token_never_appears_in_any_output; tests/test_models.py (A0-MODEL): test_models_use_validates_against_catalog, test_effective_model_is_min_of_selection_and_lever, test_lever_file_never_written, test_unreachable_provider_is_marked_not_fabricated; tests/test_auth_zero_dependency.py (A0-ZERODEP): test_no_non_stdlib_import_in_auth_package (shares the stdio gate, tools/check_pipe_stdlib.py extended by the lead), test_gate_fails_on_planted_third_party_import.
Phase 1, accounts and API-key login with file storage: tools/aesop_auth/{accounts.py,store_file.py,redact.py,cli.py} (one lane per file) plus the verb entry; goes green A0-ACCT, A0-CLI partially.
Phase 2, keychains and models: store_windows.py (ctypes Credential Manager), store_macos.py, store_linux.py, models.py, resolver change in driver/backend_config.py (single lane, owns that file; consults models.json before aesop.config.json).
Phase 3, OAuth flows: flows_device.py, flows_pkce.py, refresh.py, oidc.py (OIDC only after open question 5.1); narrow the credential fallback chain.
Phase 4, service boundary: auth.* and models.* control messages over the pipe contract; UI endpoints in ui/ (owner lane for ui/handler.py routes); chat slash commands (owned by the chat plan lanes).
Dependency: phase 0 parallel; phase 1 after phase 0 red baseline; phase 2 and 3 parallel after phase 1; phase 4 after the pipes plan phase 1 and this plan's phase 2.

## 10. Open decisions for Matt
- A1. Confirm the architecture: Python auth service with a stable JSON CLI contract, Node chat as a client, one accounts.json and models.json source of truth.
- A2. Is Codal's identity provider in scope for v1 (open question 5.1), or API keys plus GitHub only?
- A3. Claude subscription: route only via Claude Code login (recommended), or investigate direct subscription OAuth for third-party tools (policy unverified)?
- A4. Config directory location and whether a per-repo account pin file is acceptable.
- A5. Windows large-token storage: DPAPI-wrapped file versus refusing tokens over 2.5 KB.
- A6. Should the effective model always be min(selected, lever)? (recommended yes; the lever stays orchestrator-owned.)
- A7. Narrow the BENCH_API_KEY/ANTHROPIC_API_KEY fallback chain now (phase 3) or leave for benchmarks?
- A8. Is a hosted (non-loopback) UI ever in scope? It would need real user authentication and changes the threat model.
