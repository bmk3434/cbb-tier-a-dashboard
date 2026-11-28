"""Opt-in local CBB research storage. No API calls or qualification changes."""
import argparse
import csv
import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

MODEL_EDGES = ("Totals_Predicted_Edge", "Totals_V2_Predicted_Edge", "Totals_V3_Predicted_Edge")
MODEL_FLAGS = ("Totals_Qualified", "Totals_V2_Qualified_Shadow", "Totals_V3_Qualified_Shadow")


def finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def agreement(row):
    edges = [finite(row.get(k)) for k in MODEL_EDGES]
    votes = ["Over" if x > 0 else "Under" if x < 0 else "Pass" if x == 0 else "Missing" for x in edges]
    over, under = votes.count("Over"), votes.count("Under")
    qualified = [str(row.get(k, "")).lower() in ("true", "1") for k in MODEL_FLAGS]
    return {
        "votes": votes, "over": over, "under": under,
        "complete": all(x is not None for x in edges),
        "unanimous": over == 3 or under == 3,
        "majority": "Over" if over >= 2 else "Under" if under >= 2 else None,
        "qualified_count": sum(qualified),
        "mean_edge": sum(edges) / 3 if all(x is not None for x in edges) else None,
    }


def timestamp(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def open_db(path):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS snapshots (
          game_date TEXT NOT NULL, game_id TEXT NOT NULL, observed_at TEXT NOT NULL,
          start_at TEXT NOT NULL, payload TEXT NOT NULL, agreement TEXT NOT NULL,
          PRIMARY KEY (game_date, game_id, observed_at)
        );
        CREATE TABLE IF NOT EXISTS results (
          game_date TEXT NOT NULL, game_id TEXT NOT NULL,
          home_score INTEGER NOT NULL, away_score INTEGER NOT NULL,
          regulation_home INTEGER, regulation_away INTEGER,
          PRIMARY KEY (game_date, game_id)
        );
    """)
    return conn


def ingest(path, rows):
    """Persist every scored pre-tip row, including passes and QC holds."""
    inserted, rejected = 0, 0
    with closing(open_db(path)) as db, db:
        for row in rows:
            try:
                date, gid = str(row["Game_Date"]), str(row["Game_ID"])
                observed = timestamp(row["Run_Timestamp_PT"])
                start = timestamp(row["Start_UTC"])
                if not gid or gid == "None" or observed >= start:
                    raise ValueError("invalid game or post-tip snapshot")
            except (KeyError, TypeError, ValueError):
                rejected += 1
                continue
            clean = {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in row.items()}
            result = db.execute(
                "INSERT OR IGNORE INTO snapshots VALUES (?, ?, ?, ?, ?, ?)",
                (date, gid, observed.isoformat(), start.isoformat(),
                 json.dumps(clean, allow_nan=False, sort_keys=True),
                 json.dumps(agreement(clean), allow_nan=False, sort_keys=True)))
            inserted += result.rowcount
    return {"inserted": inserted, "rejected": rejected}


def import_results(path, rows):
    """Import independently verified final scores, never pregame projections."""
    count = 0
    with closing(open_db(path)) as db, db:
        for row in rows:
            if str(row.get("status", "")).lower() != "final":
                continue
            home, away = int(row["home_score"]), int(row["away_score"])
            rh = row.get("regulation_home")
            ra = row.get("regulation_away")
            rh, ra = (int(rh), int(ra)) if rh not in ("", None) and ra not in ("", None) else (None, None)
            if min(home, away) < 0 or (rh is not None and (min(rh, ra) < 0 or rh > home or ra > away)):
                raise ValueError("Invalid final or regulation score")
            db.execute("INSERT INTO results VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(game_date, game_id) DO UPDATE SET home_score=excluded.home_score, away_score=excluded.away_score, regulation_home=COALESCE(excluded.regulation_home, results.regulation_home), regulation_away=COALESCE(excluded.regulation_away, results.regulation_away)",
                       (row["game_date"], str(row["game_id"]), home, away, rh, ra))
            count += 1
    return count


def summarize(path):
    """First pre-tip snapshot per game only; no duplicate-game weighting."""
    groups = {}
    with closing(open_db(path)) as db, db:
        rows = db.execute("""
          SELECT s.payload,s.agreement,r.home_score,r.away_score
          FROM snapshots s
          JOIN results r USING(game_date,game_id)
          WHERE s.observed_at=(SELECT MIN(t.observed_at) FROM snapshots t
             WHERE t.game_date=s.game_date AND t.game_id=s.game_id)
        """)
        for payload, meta, home, away in rows:
            row, a = json.loads(payload), json.loads(meta)
            total = finite(row.get("Market_Total"))
            side = a["votes"][0]
            # Only compare frozen v1-qualified, actionable games; agreement is descriptive.
            if total is None or side not in ("Over", "Under") or str(row.get("Totals_Actionable", "")).lower() not in ("true", "1"):
                continue
            group = "incomplete" if not a["complete"] else "unanimous" if a["unanimous"] else "split"
            g = groups.setdefault(group, {"wins": 0, "losses": 0, "pushes": 0})
            margin = (home + away - total) * (1 if side == "Over" else -1)
            g["wins" if margin > 0 else "losses" if margin < 0 else "pushes"] += 1
        coverage = db.execute("SELECT COUNT(*), COUNT(DISTINCT game_date || ':' || game_id) FROM snapshots").fetchone()
    return {"snapshots": coverage[0], "games": coverage[1], "v1_first_snapshot_agreement": groups}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True)
    p.add_argument("action", choices=("ingest", "results", "summary"))
    p.add_argument("csv_path", nargs="?")
    args = p.parse_args()
    if args.action == "summary":
        print(json.dumps(summarize(args.db), indent=2))
        return
    if not args.csv_path:
        p.error("csv_path required")
    with open(args.csv_path, newline="", encoding="utf-8") as f:
        rows = csv.DictReader(f)
        result = ingest(args.db, rows) if args.action == "ingest" else import_results(args.db, rows)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
