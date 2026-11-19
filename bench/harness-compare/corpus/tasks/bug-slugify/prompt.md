# Fix slugify

The working directory contains `slug.py` with `slugify(text)`. It turns a title into a URL slug: lowercase, runs of other characters become a single `-`, and leading or trailing `-` are removed.

Digits are being dropped. `"Version 2 Release"` should become `version-2-release`, but it currently becomes `version-release`. Fix the function. Do not change its signature.

## Done when

`slugify("Version 2 Release!")` returns `version-2-release`, `slugify("  Hello, World  ")` returns `hello-world`, and `slugify("C3PO & R2-D2")` returns `c3po-r2-d2`.
