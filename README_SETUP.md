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
