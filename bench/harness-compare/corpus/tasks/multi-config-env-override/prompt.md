# Layer environment variables into load_config

The working directory contains a `config` package. `config/defaults.py` defines `DEFAULTS`. `config/loader.py` has `load_config(overrides=None, environ=None)`, which currently returns only the defaults plus `overrides`. It ignores the environment.

Implement the following precedence, lowest to highest:
1. `DEFAULTS`
2. Environment variables whose names start with `APP_`. `APP_PORT` sets the key `port`. Use the `environ` mapping when one is passed, otherwise `os.environ`.
3. The `overrides` argument, which wins over everything.

Coerce environment strings to the type of the default for that key. Booleans accept `1/true/yes/on` and `0/false/no/off`, case-insensitive. Integers use `int()`. Strings stay as they are. Environment variables for keys that are not in `DEFAULTS` are ignored. Do not mutate `DEFAULTS`.

## Done when

`load_config` applies the three layers in that order with the coercion above, and `DEFAULTS` is unchanged after calling it.
