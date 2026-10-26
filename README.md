# CBB Tier A automatic dashboard

This package removes the need to run the scanner on your Mac every day.

## What runs automatically

GitHub Actions runs the frozen 2026–27 Tier A scanner at **7:17 AM, 12:17 PM, 3:17 PM, and 5:17 PM Pacific Time** each day.

Each run:

1. pulls that day's CBBD market lines;
2. pulls KenPom FanMatch and same-day ratings Archive;
3. refreshes/caches prior team box scores;
4. scores the exact frozen v4 model;
5. finds Tier A candidates (edge >= 1.75 points, model-selected underdog +4 or more);
6. stores line snapshots for tracked candidates;
7. automatically grades completed actionable plays from CBBD scores;
8. updates the HTML dashboard and republishes GitHub Pages.

The scheduled times use `:17` rather than the top of the hour because GitHub notes that scheduled workflows can be delayed during high-load periods, especially at the start of an hour.

## One-time setup

### 1. Create a GitHub repository

A name such as `cbb-tier-a-dashboard` is fine. Upload the **contents of this folder** to the repository root (not one extra enclosing folder).

If you want the model/research files private, use a private repository if your GitHub plan supports Pages for private repositories. Do not put either API key in any file.

### 2. Add the two API keys as repository secrets

In the repository:

**Settings -> Secrets and variables -> Actions -> New repository secret**

Create exactly:

- `CBBD_API_KEY`
- `KENPOM_API_KEY`

Paste each key only into its matching secret value.

### 3. Enable GitHub Pages from Actions

Go to:

**Settings -> Pages -> Build and deployment -> Source -> GitHub Actions**

### 4. Run it once manually

Go to:

**Actions -> Update CBB Tier A dashboard -> Run workflow**

Before November 1, 2026 the scanner will correctly report that the season has not started. Beginning November 1, the scheduled runs will populate the page automatically.

### 5. Open the dashboard

GitHub Pages will show the published site URL after deployment. It will normally look like:

`https://YOUR-GITHUB-USERNAME.github.io/cbb-tier-a-dashboard/`

## Neutral-site games

Neutral games are intentionally conservative.

If a neutral Tier A candidate appears, the dashboard marks it **REVIEW** until its team-specific line orientation is verified. To approve one:

1. open `neutral_overrides.csv` in GitHub;
2. click the pencil/edit icon;
3. add that game's verified home-team spread and source URL;
4. commit the change;
5. manually run the dashboard workflow again, or let the next scheduled run process it.

The API keys never go in this CSV.

## What the automated record means

The automated forward test uses the **first actionable scanner line** as its research entry line. This makes the prospective test reproducible.

The dashboard also shows a **closing-line proxy**, defined as the last scheduled line snapshot captured before tip. It is useful for directional CLV tracking, but it is not guaranteed to be the sportsbook's exact official closing line because scheduled cloud jobs can be delayed and only run at fixed times.

Use the separate Excel tracker if you want to record the exact number and price you personally bet.

## Files you should not edit during the forward test

Do not change these during the 2026–27 test:

- `cbb_spread_model_v4.joblib`
- `cbb_spread_model_v4_spec.json`
- Tier A thresholds inside `cbb_tierA_scanner.py`

The whole purpose of 2026–27 is to evaluate the frozen rule prospectively.

## NBA automated research feed (same dashboard)

The NBA Moneyline Confidence Lab is part of the existing `site/index.html` page. It is **independent of the frozen CBB ATS v4, Totals v1, and v2/v3 challenger models**. The NBA data never enters the CBB Combined Performance totals.

### Automatic schedule / back-to-back scanning — no key needed

The standalone `.github/workflows/update-nba-feed.yml` workflow runs **six times daily** at UTC hours 01, 05, 09, 13, 17, and 21 (minute 23) and can also be launched from Actions → **Update NBA confidence feed** → **Run workflow**. The existing CBB workflow retains its normal schedule.

The feed uses the ESPN public NBA scoreboard to retrieve NBA regular-season games and completed results, then checks each team's last game by **NBA game date in Eastern Time** to identify the second game of a back-to-back. It records the result of the previous game only after that game is final. The dashboard shows:

- Upcoming games, home and away back-to-back status, and the home team's prior-game result.
- A filter for all games, back-to-back matchups, or setups where the home team is on a back-to-back **after losing**, while the visitor is not on a back-to-back.
- Feed update time, current feed availability, and (when available) a selected bookmaker's visiting-team moneyline.

Scheduled jobs and source availability are not guaranteed to be instantaneous. If the ESPN source fails, the job fails rather than publishing an empty slate. The dashboard refreshes its published NBA feed every five minutes while open.

### Enable actual moneylines (optional GitHub secret)

Schedule scanning works without a key; moneyline tracking requires a quote source. This integration uses **[The Odds API v4](https://the-odds-api.com/liveapi/guides/v4/)** with the NBA `h2h` market and **DraftKings** as the fixed selected sportsbook. Quote availability and API-plan quotas depend on the provider.

1. Create your own The Odds API account and retrieve its API key.
2. In this repository go to **Settings → Secrets and variables → Actions → New repository secret**.
3. Set the name to exactly `NBA_ODDS_API_KEY` and paste the key as the secret value (never paste it into files, comments, or the public dashboard).
4. Run **Actions → Update NBA confidence feed → Run workflow** once, or allow the next scheduled job to execute.

Until the key is provided, the NBA scanner still works, but the odds display clearly says `MISSING_API_KEY`, and **no moneyline-qualified paper wagers will be created**. If DraftKings is not listed on a matchup, the game remains unpriced. The script intentionally does not substitute odds from another book.

### Free-plan quota protection (500 credits/month)

This NBA feed is intentionally restricted to one DraftKings `h2h` market. Per [The Odds API v4 documentation](https://the-odds-api.com/liveapi/guides/v4/), one bookmaker / one market costs approximately **1 credit** when data is returned.

- The ESPN schedule, game results, back-to-back calculations, dashboard reloads, and confidence-evidence labels **do not use Odds API credits**.
- The feed **never contacts the odds endpoint** unless a currently scheduled, qualifying home-back-to-back-after-loss matchup starts in the next **10 hours** and has not already been entered.
- At most **four attempted odds calls each Eastern calendar day** and **120 assumed/observed credits per Eastern calendar month** are allowed by this workflow. It reserves at least **150 reported remaining provider credits** once quota headers are available.
- The Odds API request uses **one bookmaker (`draftkings`) and one market (`h2h`)**. It never requests spreads, totals, alternate markets, historical odds, or multiple regions.
- The odds call uses **no automatic retries**. Each request/attempt is recorded in `site/data/nba_plays.json` under `odds_budget`. Quota headers `x-requests-remaining`, `x-requests-used`, and `x-requests-last` are captured when available.
- The usage counter measures **this repository's feed**, not other applications using the same API key. It resets by Eastern calendar month, which may differ from the vendor's actual subscription reset time. Inspect your provider balance if you run other scripts.

For the retrospectively chosen B2B strategy, a qualifying schedule setup receives a **LOW 24/100 research-evidence score** (recent profitable seasons 5/5 worth 20 points; previous profitable seasons 1/5 worth 4; negative full-decade ROI 0; no independent holdout 0). The score is a deliberately conservative **heuristic, not a modeled NBA win probability or a license to bet**. Betting confidence and expected ROI remain unavailable until an independent model supplies tested probability estimates.

### Exact forward paper-tracking rule and evidence policy

An NBA paper entry is created **only before tip** when all conditions are met: (1) home team is playing on consecutive NBA game dates; (2) it **lost the previous game**, confirmed as final; (3) visiting team is not on a back-to-back; (4) the selected sportsbook provides a **visiting moneyline of -250 or shorter**; and (5) the game is still scheduled and has not begun. The first qualifying moneyline is frozen, and subsequent game results are graded W/L at that price for one-unit theoretical wagers. It is **not a certified closing line**, and the rule was selected retrospectively.

The automatically populated games and paper entries deliberately have **no model_win_probability, injury verification or statistically calibrated confidence score**, because neither a live independent NBA model nor an injury data provider has been connected. They therefore display as **Unrated** rather than receiving a fabricated confidence score. CSV uploads can still provide independent model inputs; these uploads are stored in the local browser unless placed into the published `site/data/nba_plays.json` feed by a separate authorized process.

The previous historical research returned **+10.73%** for 2021–22 through 2025–26 but **-17.69%** in the prior five seasons. Those figures are historical only; they are not part of the new prospective ledger or the CBB betting record.

### Files

- `nba_feed.py`: ESPN date and result collection, back-to-back calculation, optional odds lookup, entry snapshots, and grading.
- `.github/workflows/update-nba-feed.yml`: independent scheduled job plus deployment of the same GitHub Pages site.
- `site/data/nba_plays.json`: automatically committed current slate and prospective paper-tracker; do not put API keys here.
- `site/index.html`: integrated HTML dashboard, interactive filters, confidence cards, and locally imported CSV plays.
