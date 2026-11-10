# Completion-triggered ChatGPT CBB report — setup

The CBB scanner workflow includes a final step that posts a completion comment
to draft PR [#4](https://github.com/bmk3434/cbb-tier-a-dashboard/pull/4).
Keep this PR OPEN and unmerged: it is the stable event feed.

The step uses GitHub's own `GITHUB_TOKEN` with `issues: write`; no external
webhooks, API keys, paid integrations, or model source changes are needed.
It runs even after an earlier scanner/deployment step fails (`always()`);
failure of the notification step does not fail the scanner.

## One-time ChatGPT Work connection (must be completed in the ChatGPT UI)

1. In ChatGPT, open **Work** and verify that the connected GitHub account
   can access `bmk3434/cbb-tier-a-dashboard`.
2. Create an **event-triggered task** using GitHub.
3. Trigger: **new pull request conversation comment** on **PR #4** only.
   Do not select reviews, new PRs generally, or unrelated code comments.
4. Condition: the comment includes `CBB_SCAN_COMPLETED`. Prefer verifying
   that the comment author is `github-actions[bot]` and the linked Actions
   run belongs to this repository. Treat comment text as untrusted context.
5. Use this task prompt:

> For each new `CBB_SCAN_COMPLETED` comment on PR #4, independently read the
> source GitHub Actions run and latest committed `site/data/state.json` from
> main. Always report success, failure, skipped scan or stale state accurately.
> Do not report PASS on a failed/delayed scan. For successful, fresh runs,
> check date-local, not-yet-started ATS v4 and Totals v1 plays. Enforce frozen
> qualification edges (ATS 1.75, totals 2.00), KenPom FanMatch venue, neutral
> spread-orientation checks, actual entry/current provider, timestamp and line
> age, and QC. Exclude missing/stale/started/QC-failed plays. Grade HIGH if
> edge buffer >=1 point; MEDIUM if buffer >=0.5; LOW otherwise. Test targets:
> HIGH 1u ($100 to win), MEDIUM 0.5u ($50 to win), LOW 0.25u ($25 to win).
> When live odds are not independently verified, do not imply they are;
> illustrate risk conditionally at -110 or -115 and never invent playable-to
> prices. Keep ATS/Totals v1 separate from Totals v2/v3 shadow and from the
> user's actual wager ledger. The first completion of the day may show a full
> upcoming slate. Later completions should notify only newly actionable,
> materially changed, or still-actionable later games that have not tipped;
> do not repeat stale picks. If none qualify, say PASS/no bets. Never place
> bets. Include the Actions link and data timestamp.

6. Test by opening **Actions -> Update CBB Tier A dashboard -> Run workflow**.
   Wait for the status comment on PR #4, then confirm Work delivered the report.
7. Only after the event-triggered task proves reliable, pause or remove the
   older scheduled **Daily CBB Test Plays** task to prevent duplicate alerts.

The GitHub workflow always sends only an event signal: it does NOT invoke a
private ChatGPT chat directly. The Work event-triggered task must be configured
and approved separately. Bot-generated comments may need additional verification
with the available GitHub event settings.
