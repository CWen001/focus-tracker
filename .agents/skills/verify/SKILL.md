---
name: verify
description: "Verify focus-tracker Windows CLI diagnostics and real collection lifecycle; inspect saved evidence and identify blocked Chrome or idle-rule acceptance."
---

# Verify focus-tracker

Run commands from the repository root. Read [the feature map](features/README.md) before choosing coverage. A passing lifecycle check does not prove idle accounting or browser integration.

## Launch

Use Windows and Python 3.13+. No dependencies or installation are required. First obtain explicit permission for real foreground application and input-idle observation. The flag below is not permission by itself. Each invocation records into a new directory beneath LOCALAPPDATA, never the daily activity database.

```
python -B -m tools.verify_lifecycle --allow-observation
```

The helper launches the real `python -m activity_log run` command twice, each in a private hidden console. Readiness requires a live owned process and a fresh persisted checkpoint, not merely a RUNNING line. No registered Chrome native host may be present for this limited recipe. If one exists, stop rather than uninstall it.

## Doctor

Before collection, `python -B -m activity_log doctor --data-dir <new-absolute-local-path>` checks environment only. HOST_NOT_CONFIGURED is expected for this recipe, not proof of a healthy collector.

During the recipe, instance health means the owned process is alive, its new database has a current unstopped checkpoint, and foreground segments exist. Evidence records the base Git SHA and source hashes because an uncommitted working tree may differ from HEAD. Compare those hashes with the final commit before reusing the proof.

## Drive

The helper checks empty-directory status without creating data, waits for a checkpoint, sends real Ctrl+C to the owned private console, requires exit 130 and a stopped checkpoint, then reads status. It restarts using the same isolated database and requires a different session and byte-identical hashes of prior segment payloads. Status on the stopped database must not change its bytes.

The helper has a 60-second watchdog and an 18-second readiness limit per cycle. Ordinary unit tests never collect real activity. Run them separately with `python -B -m unittest discover -s tests -v`.

## Evidence

The final printed path points to a new `LOCALAPPDATA/FocusTracker-verify-*/result.json`. Keep that file, the two collector transcripts, and the isolated database locally. Inspect exit code, verdict, both cycles, stopped checkpoints, preserved row hashes and all_owned_exited. A missing report, timeout, forced stop, or failed command is not PASS.

The report omits application names and page metadata, but it contains local paths and timestamps. Neither it nor the raw database is approved for public upload. Publish only a reviewed aggregate summary. No screenshot or raw foreground content is required.

## Cleanup

The helper stops only processes it started. A normal run uses Ctrl+C. A failure kills remaining owned collectors and records FAIL. It retains evidence and the test database. Afterward verify all_owned_exited and that the reported files still exist. Never kill by executable name, remove another installation, or touch the daily database.

## Maintenance

Use /maintain-verification-skill after CLI or storage changes. Keep unrun feature-map journeys blocked until their actual user paths are exercised.
