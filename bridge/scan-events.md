# CBB Scan Completion Notification Feed

**This pull request is an automation signal, not a model change. Keep it open. Do not merge it.**

The workflow `Update CBB Tier A dashboard` scans the college-basketball slate.
A separate workflow on `main` (the completion bridge) will publish a new
comment on this pull request each time that scan finishes.

Those comments are a GitHub pull-request activity event that ChatGPT Work can
monitor using a GitHub event-triggered task.

## Event payload

A comment begins with `CBB_SCAN_COMPLETED` and includes:

- workflow result: SUCCESS, FAILURE, CANCELLED, or other conclusion;
- source run URL and trigger type;
- latest committed `site/data/state.json` timestamp and slate date;
- data freshness confirmation when verifiable;
- reminder that a green Actions check is not by itself a betting recommendation.

ChatGPT's event-triggered task should ignore unrelated comments, read the
latest committed model state, independently validate start times, KenPom
location, qualifying edges and sportsbook line freshness, and send a betting
report or PASS/FAIL notice.

This pull request deliberately contains no credentials and does not change
the scanner, thresholds, or model data.
