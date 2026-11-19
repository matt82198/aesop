# Remove the duplicated digit test from validators

The working directory contains `validators.py` with three validators: `is_zip`, `is_pin` and `is_year`. Each one repeats the same "ASCII digits only" loop.

Refactor so the digit test is written exactly once, in a single shared helper that the three validators call. Keep the public function names and their results unchanged. Do not change `is_zip`, `is_pin` or `is_year` behaviour.

## Done when

`validators.py` contains the digit test (`isdigit()`) exactly once, and `is_zip`, `is_pin` and `is_year` return the same results as before.
