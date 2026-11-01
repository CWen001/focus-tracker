# Recording lifecycle

## Sub-features
Start, checkpoint, read saved status, stop with Ctrl+C, restart without losing earlier records.

## How to get to it (user POV)
The product command is `python -m activity_log run --data-dir <absolute-local-path> --allow-observation`. Ctrl+C stops it. A separate status command reads saved facts.

## Driving it with the CLI
After explicit real-observation consent, run `python -B -m tools.verify_lifecycle --allow-observation`. Require exit 0, verdict PASS, two distinct saved sessions, both collector exits 130, old row hashes retained and all_owned_exited true. Open the evidence after cleanup. Keep raw records local.

## Gotchas
The per-login singleton prevents parallel collectors, even with different data directories. An existing collector must cause a refusal, never termination of that other process. The harness enables Ctrl+C inheritance in its own process before creating private consoles because agent shells can inherit an ignore flag. It does not inject a KeyboardInterrupt into application code. This recipe does not verify which app was foreground, switching accuracy, clock changes, or 300-second rules.
