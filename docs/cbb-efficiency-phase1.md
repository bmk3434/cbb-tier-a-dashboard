# CBB efficiency phase 1 (research only)

This is an opt-in prototype; production workflow and frozen qualification rules
remain unchanged. No new API requests are made by the research ledger.

## Private data storage
The repository is public. Keep the SQLite database and archived provider
responses on private storage outside the repository (or under ignored
research_private/ for local testing). Do not upload them as public GitHub
artifacts. Confirm provider terms and arrange durable private storage before
enabling unattended collection on ephemeral Actions runners.

## Local execution
After configuring the existing API environment variables:

    python cbb_tierA_scanner.py --date YYYY-MM-DD --research-db ~/private-cbb/research.sqlite --pregame-archive-cache ~/private-cbb/archive
    python cbb_research.py --db ~/private-cbb/research.sqlite summary

Use --refresh-history to bypass the optional pregame archive cache. Existing
daily odds and FanMatch retrieval are always live. The cache only reuses
the same requested prior-date archive, validates its checksum and refreshes
on corruption. A dated archive can still be revised by the provider; compare
cached vs refreshed data before enabling production reuse. A cache hit is
one avoided archive request, not a demonstrated latency or ROI improvement.

All scored pre-tip rows (including passes and QC holds) are retained with
their contemporaneous model outputs, venue and market inputs. It does not
include games that cannot be scored, raw feature vectors, or historical
predictions that were never recorded. The existing output CSV can be
imported without calling any APIs:

    python cbb_research.py --db ~/private-cbb/research.sqlite ingest output/all_scored_YYYY-MM-DD.csv

Final-score import expects verified CSV columns:
game_date,game_id,status,home_score,away_score,regulation_home,regulation_away.
The final status must be "final"; regulation columns are optional.

    python cbb_research.py --db ~/private-cbb/research.sqlite results verified_finals.csv

Agreement is descriptive only. It distinguishes directional votes, missing
predictions and number of models meeting their own thresholds. The summary
uses the first pre-tip observation per game and restricts the agreement
outcome comparison to v1-actionable rows. It is not the existing first
actionable-entry paper ledger, and should not be combined with that record.
No stake-size, confidence or qualification promotion is authorized.

## Validation and rollout gates
1. Run python -m unittest -v test_cbb_research (offline).
2. Run existing frozen-model QC tests.
3. Replay identical inputs through legacy and opt-in scanner paths; require
   equality of predictions, QC flags and qualifying decisions. Compare
   cached and fresh archives; measure actual request count and timing.
4. Choose authorized durable private storage. Add a separate final-score
   settlement process covering every recorded game, and verify completeness.
5. Track agreement prospectively with unique games and uncertainty intervals.
   Do not change live picks based on the exploratory summary.

Phase 1 intentionally does not enable automatic persistence, automatic
all-game settlement, feature-vector caching, targeted-only rescoring, or
production scheduling until these gates are met.
