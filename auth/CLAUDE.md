# auth/ — generic auth and model selection

**Purpose**: a zero-dependency, Claude Code style credential and model-selection surface for aesop. Reached through `bin/cli.js` verbs `auth` and `model`, and through control messages on stdin.

**Files**
- `providers.mjs` — registry `PROVIDERS` ({name, envVar, defaultModel, models}); seeds `anthropic` and `local`; `parseSelector`.
- `store.mjs` — the one JSON store: `readStore`, `writeStore` (temp file + rename, mode 0600 off win32), `resolveCredential` (env beats store).
- `cli.mjs` — verb dispatch, control channel, exit codes. Runnable directly: `node auth/cli.mjs <argv>`.

**Verbs**
- `auth login --provider <name> --api-key-stdin` — key read from stdin only, never an argument.
- `auth status [--provider <name>]` — `<provider>: source=<env|store|none> model=<m> key=...<last4>`.
- `auth logout [--provider <name>]` — drops the stored entry; no flag clears all providers.
- `model list` — providers, default models, credential requirement.
- `model use <provider>/<model>` — records the selection; needs a credential when the provider has an env var.
- `model current` — prints `<provider>/<model>`.
- Machine output: when stdout is not a TTY, `auth status`, `model list` and `model current` print exactly one JSON line (ops `auth.status`, `model.list`, `model.current`). Bare `aesop` never reads stdin for these; a stdio control channel (`--input-format stream-json` or `frames`) is the only other route, and is not wired here.

**Exit codes**: 0 ok; 2 bad usage, malformed input, unknown op, or corrupt store; 3 `model use` for a provider that needs a credential and has none.

**Store**: `$AESOP_CONFIG_DIR/auth.json` (default `<home>/.aesop/auth.json`). Entries `{credential, model, addedAt}` keyed by provider, plus top-level `current`. Env secrets are never persisted. A corrupt file exits 2 and is left untouched.

**Secrets**: never printed. Status shows only the last four characters. Precedence: `AESOP_API_KEY`, then the provider env var, then the store.

**Invariants**: imports are `node:` builtins and relative files only (`tests/auth-zero-dependency.test.mjs`). Auth verbs never scaffold into cwd. Tests: `tests/auth-cli.test.mjs`, `tests/auth-store.test.mjs`.
