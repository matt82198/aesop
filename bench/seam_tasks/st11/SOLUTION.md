# Solution: Config-Code Interaction Bug (st11)

## Defect Class
**Config and code interaction where two individually-correct defaults combine into wrong behavior**: The logger's production configuration (suppress WARNING level) and the validator's logging choice (log at WARNING level) are each reasonable in isolation, but together they cause important error messages to be silently discarded in production.

## Interaction Chain (Why localization requires 4+ modules)

1. **config.py**: Sets log level to ERROR in production (individually sensible: reduce noise)
2. **logger.py**: Reads config and applies the log level to the logging system
3. **validator.py**: Logs validation errors at WARNING level (individually sensible: not critical enough for ERROR)
4. **processor.py**: Uses both validator and logger, assuming validation failures will be logged
5. **The Defect Interaction**:
   - config.py says: "In production, only show ERROR level messages" (reasonable for reducing log noise)
   - validator.py says: "Log validation failures at WARNING level" (reasonable for validation issues)
   - logger.py correctly applies config.py's level
   - When validation fails in production, the WARNING is suppressed
   - processor.py silently accepts invalid data because no error message appears to alert the operator

**Why this requires 4+ modules to localize**:
- Can't find the bug by reading config.py alone (setting seems reasonable)
- Can't find the bug by reading logger.py alone (correctly applies configuration)
- Can't find the bug by reading validator.py alone (WARNING level is appropriate for its domain)
- Can't find the bug by reading processor.py alone (uses both modules correctly)
- The bug emerges from the INTERACTION of:
  - Config choice: suppress WARNING in production
  - Validator choice: log failures at WARNING
  - Logger implementation: respects config
  - Processor usage: assumes logged warnings will appear
- Localization requires reading all 4 modules to understand that individually sensible choices combine poorly

## Fix

**Solution**: Ensure validation failures are logged at ERROR level (critical to operations) rather than WARNING level.

**File: validator.py** - Change all `log.warning()` calls to `log.error()`:

```python
def validate_record(record):
    if not isinstance(record, dict):
        log.error(f"Invalid record type: {type(record)}")  # Changed from log.warning
        return False

    if "id" not in record:
        log.error("Record missing required field: id")  # Changed from log.warning
        return False

    # ... etc for all validation messages
```

**Rationale**: Validation failures that cause data to be rejected are operational errors that must be visible to operators, regardless of log level settings. They should be at ERROR level, not WARNING.

**Alternative Fix**: In config.py, ensure ERROR-level messages are always shown but use WARNING level as the default for INFO/DEBUG suppression:

```python
def get_log_level():
    if is_production():
        return "WARNING"  # Show warnings and errors in production
    else:
        return "DEBUG"  # Show everything in development
```

But this is weaker because it fails if someone later decides to filter WARNING in production.

## Verification Transcript

### Before Fix (Defective Code)

```
test_process_invalid_records_with_production_env - AssertionError: Validation warnings should appear in logs.
Got: Valid: 0
Invalid: 2
Output: ''
Has WARNING: False
```

Defect Demonstrated:
- Processor correctly rejects 2 invalid records
- But no WARNING message appears in production logs
- The operator has no visibility into why data was rejected

### After Fix (Applied to repo copy)

Changed validator.py: All `log.warning()` → `log.error()`

Oracle Output:
```
PASSED test_process_invalid_records_with_production_env
PASSED test_validation_warns_on_invalid_data
...all tests pass
```

## Summary

This bug demonstrates how two independently correct design choices (suppress warning noise in production; log validation issues at WARNING level) can combine into a serious problem. Localization requires understanding the interaction across config, logging, validation, and processing modules. The fix ensures that validation failures are logged at a level that won't be suppressed by any reasonable production configuration.
