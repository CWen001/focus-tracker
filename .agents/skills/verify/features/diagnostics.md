# Read-only diagnostics

## Sub-features
Environment reporting and absent-history reporting.

## How to get to it (user POV)
Run `python -m activity_log doctor --data-dir <new-absolute-local-path>` or replace doctor with status.

## Driving it with the CLI
Use a nonexistent data subdirectory under a newly allocated local temporary directory. Save stdout, stderr and exit code. Confirm it remains nonexistent. Without a registered host, doctor must name HOST_NOT_CONFIGURED; status must report MISSING_DATABASE with exit 3.

## Gotchas
Doctor exit 1 reflects incomplete capability, not a crashed command. Do not query the user's daily database as a test. Doctor cannot certify a running collector.
