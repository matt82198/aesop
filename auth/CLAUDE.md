# auth/ — generic auth and model selection

**Purpose**: a zero-dependency, Claude Code style credential and model-selection surface for aesop. Reached through `bin/cli.js` verbs `auth` and `model`, and through control messages on stdin.

**Files**
- `providers.mjs` — registry `PROVIDERS` ({name, envVar, defaultModel, models}); seeds `anthropic` and `local`; `parseSelector`.
- `store.mjs` — the one JSON store: `readStore`, `writeStore` (temp file + rename, mode 0600 off win32), `resolveCredential` (env beats store).
- `cli.mjs` — verb dispatch, control op registry (`runControlOp`), exit codes. Runnable directly: `node auth/cli.mjs <argv>`.

**Verbs**
- `auth login --provider <name> --api-key-stdin` — key read from stdin only, never an argument.
- `auth status [--provider <name>]` — `<provider>: source=<env|store|none> model=<m> credential=<set|none>`; `keyTail` is always null (a fixed slot kept for the strict-equal contract, no part of a key is shown).
- `auth logout [--provider <name>]` — drops the stored entry; no flag clears all providers. A selection (`store.current`) naming a provider that was logged out is cleared too, whether its credential came from the store or the environment (the env var itself is never touched). If a provider's credential still resolves from its env var after logout, stdout gets one extra line per such provider after the `logged out:` line: `still set: <provider> source=env (...)`, naming the provider only, never the value (R5).
- `model list` — providers, default models, credential requirement.
- `model use <provider>/<model>` — records the selection; needs a credential when the provider has an env var.
- `model current` — prints `<provider>/<model>`.
- Machine output: when stdout is not a TTY, `auth status`, `model list` and `model current` print exactly one JSON line (ops `auth.status`, `model.list`, `model.current`). Bare `aesop` never reads stdin for these.
- Control ops: the only control channel is `aesop pipe --input-format stream-json --output-format stream-json`. Each control record `{"type":"control","op":...,"id"?,...}` is answered by `runControlOp` in cli.mjs, the only op registry (`CONTROL_OPS`), and wrapped by stdio/pipe.mjs in its reply envelope. Ops: `auth.status` (optional `provider`), `auth.login` (`api_key`, optional `provider`, default anthropic), `auth.logout` (optional `provider`), `model.list`, `model.use` (`model` as `<provider>/<model>`), `model.current`. Registered by importing this module (bin/cli.js preloads it).
  - Result shapes: each op's `result` is the same object its verb reports after the op. `auth.login` and `auth.logout` return the `auth.status` shape (`{ok, op:'auth.status', providers}`), not a shape of their own.
  - Refusals: a refused op answers an ok:false envelope and the pipe keeps running (exit 0). Known-op refusals keep `op`. Every envelope carries a machine-readable `code` (R-CONTROL-1); every `ControlOpError` is constructed with one of `CONTROL_ERROR_CODES`.
  - Codes: `unknown_op` (no `op` field, error `unknown op`); `unknown_provider`; `unknown_model_selector` (malformed, unregistered or unlisted selector; never echoed); `invalid_argument` (wrong argument shape, e.g. a bad `api_key`, or a provider that needs no credential); `no_credential` (`model.use` with no credential behind the model; keeps `op` with its credential message); `store_error` (unreadable or corrupt store); `op_failed` (any other refusal of a known op).

**Exit codes**: 0 ok (including every op refused with an ok:false envelope); 2 bad usage, `--control` on any path, malformed control framing, or corrupt store; 3 `model use` for a provider that needs a credential and has none.

**Store**: `$AESOP_CONFIG_DIR/auth.json` (default `<home>/.aesop/auth.json`). Entries `{credential, model, addedAt}` keyed by provider, plus top-level `current`. Env secrets are never persisted. A corrupt file exits 2 and is left untouched.

**Secrets**: never printed, in full or in part. No fragment of a key (not even its last four characters) appears in the login echo, `auth status` (human or JSON), or any control reply; they show the source and the fixed word `set` or `none`. Error messages name the expected shape (provider list, `<provider>/<model>`, flag name, verb list) and never echo an argv value. Precedence: `AESOP_API_KEY`, then the provider env var, then the store.

**Platform gap**: the store file is mode 0600 only on POSIX (`chmod` is skipped on win32); on Windows the file inherits the user profile ACL (row f-dc34e7d6).

**Invariants**: imports are `node:` builtins and relative files only (`tests/auth-zero-dependency.test.mjs`). Auth verbs never scaffold into cwd. Tests: `tests/auth-cli.test.mjs`, `tests/auth-store.test.mjs`.
