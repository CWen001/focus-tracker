# Idle and media accounting

## Sub-features
300-second retrospective idle exclusion, foreground playing-video exception, unknown browser state, lock and sleep exclusion.

## How to get to it (user POV)
Use the collector with the Chrome extension and native host installed through the documented consent flow in docs/install.md.

## Driving it with a timed desktop journey
BLOCKED in the lifecycle-only recipe. With separate installation/observation permission, compare recorded intervals against a timestamped script: no input and known not-playing media for over 300 seconds; playing foreground video without input; pause then wait; browser disconnect; lock and resume. Read back saved segments and compare actual boundaries with the approved rules. Keep interruption and missing data distinct from idle.

## Gotchas
The short lifecycle proof cannot establish these outcomes. No Chrome host means playback is unknown, not paused. Synthetic media tests are useful checks but do not prove installed extension behavior. Lock and sleep require their own authorized disruptive actions. Do not shorten the threshold or inject fake observations to claim real acceptance.
