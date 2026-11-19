# Rename fetch_record to get_record across the pkg package

The working directory contains a small package `pkg/` with `store.py`, `api.py` and an `__init__.py` that re-exports names. The function `fetch_record` is used in all three files.

Rename it to `get_record` everywhere it appears inside `pkg/`: the definition, the re-export, and every call site. Do not leave a `fetch_record` alias behind. Leave `save_record` and the behaviour of the functions unchanged.

## Done when

`pkg.get_record` works, `pkg.fetch_record` is gone, no `.py` file under `pkg/` contains the text `fetch_record`, and `pkg.api.handle_get` still returns the same results.
