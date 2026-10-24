#!/usr/bin/env python3
"""
2026-27 College Basketball Tier A Scanner

Frozen forward-test rule:
  - V4 predicted ATS edge >= 1.75 points
  - V4 preferred side is an underdog of +4.0 or greater
  - Neutral-site games require a verified line-orientation override before they
    are treated as actionable.
  - AWAY_4_TO_6_5 is a descriptive profile tag only, not a higher-confidence tier.

Data:
  - CollegeBasketballData (CBBD): lines + prior team box scores
  - KenPom: FanMatch + same-day ratings archive

Environment variables:
  export CBBD_API_KEY='...'
  export KENPOM_API_KEY='...'

The model is the frozen cbb_spread_model_v4.joblib stored beside this script.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import joblib
import numpy as np
from scipy.stats import norm

SEASON = 2027
SEASON_START = date(2026, 11, 1)
LOCAL_TZ = ZoneInfo("America/Los_Angeles")
CBBD_BASE = "https://api.collegebasketballdata.com"
KENPOM_BASE = "https://kenpom.com/api.php"

ROOT = Path(__file__).resolve().parent
MODEL_PATH = ROOT / "cbb_spread_model_v4.joblib"
ALIASES_PATH = ROOT / "team_name_aliases.json"
OVERRIDES_PATH = ROOT / "neutral_overrides.csv"
CACHE_DIR = ROOT / "cache"
OUTPUT_DIR = ROOT / "output"

CACHE_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# Preserve the provider hierarchy used in the 2025-26 reconciliation.
PROVIDER_PRIORITY = ["espnbet", "draftkings", "bovada"]


def clean_provider(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def norm_name(name: str) -> str:
    s = (name or "").lower().replace("&", "and")
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def safe_float(v: Any) -> float:
    if v is None or v == "":
        return math.nan
    try:
        return float(v)
    except Exception:
        return math.nan


def get_nested(d: Dict[str, Any], *keys: str, default: float = 0.0) -> float:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    try:
        return float(cur) if cur is not None else default
    except Exception:
        return default


def pct(num: float, den: float) -> float:
    return num / den * 100.0 if den else math.nan


def implied_prob_american(odds: Any) -> float:
    x = safe_float(odds)
    if math.isnan(x):
        return math.nan
    if x < 0:
        return (-x) / ((-x) + 100.0)
    return 100.0 / (x + 100.0)


def http_json(url: str, params: Dict[str, Any], api_key: str, timeout: int = 60) -> Any:
    full = url + ("?" + urlencode(params) if params else "")
    req = Request(full, headers={"Authorization": f"Bearer {api_key}", "User-Agent": "CBB-TierA-Scanner/1.0"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        if e.code == 404:
            try:
                obj = json.loads(body)
                if isinstance(obj, dict) and obj.get("error") == "No games found for the specified date":
                    return None
            except Exception:
                pass
        if e.code == 429:
            raise RuntimeError("API rate limit reached (HTTP 429). Stop and run the scanner later.") from e
        raise RuntimeError(f"HTTP {e.code} from {full}: {body[:400]}") from e
    except URLError as e:
        raise RuntimeError(f"Network error calling {full}: {e}") from e


def cbbd_get(endpoint: str, params: Dict[str, Any], key: str) -> Any:
    return http_json(CBBD_BASE + endpoint, params, key)


def kenpom_get(endpoint: str, params: Dict[str, Any], key: str) -> Any:
    q = {"endpoint": endpoint}
    q.update(params)
    return http_json(KENPOM_BASE, q, key)


def load_aliases() -> Dict[str, str]:
    if not ALIASES_PATH.exists():
        return {}
    with open(ALIASES_PATH, encoding="utf-8") as f:
        return json.load(f)


def kp_name_for_cbbd(name: str, aliases: Dict[str, str]) -> str:
    return aliases.get(name, name)


def load_overrides() -> Dict[str, Dict[str, str]]:
    if not OVERRIDES_PATH.exists():
        return {}
    out: Dict[str, Dict[str, str]] = {}
    with open(OVERRIDES_PATH, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            gid = (row.get("Game_ID") or "").strip()
            if gid:
                out[gid] = row
    return out


def select_line(game: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    usable = [x for x in (game.get("lines") or []) if x.get("spread") is not None]
    if not usable:
        return None, "NO_USABLE_SPREAD"

    by_clean: Dict[str, Dict[str, Any]] = {}
    for line in usable:
        by_clean.setdefault(clean_provider(line.get("provider", "")), line)

    for target in PROVIDER_PRIORITY:
        if target in by_clean:
            return dict(by_clean[target]), "FROZEN_PRIORITY"

    # Keep visible for review, but do not silently pretend the provider rule was frozen.
    return dict(usable[0]), "OUTSIDE_FROZEN_PROVIDER_PRIORITY"


def local_day_utc_bounds(d: date) -> Tuple[str, str]:
    start_local = datetime(d.year, d.month, d.day, tzinfo=LOCAL_TZ)
    end_local = start_local + timedelta(days=1) - timedelta(seconds=1)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)
    return start_utc.isoformat().replace("+00:00", "Z"), end_utc.isoformat().replace("+00:00", "Z")


def fixed_chunks(start: date, end: date, days: int = 10) -> Iterable[Tuple[date, date]]:
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=days - 1), end)
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)


def fetch_teamstats_history(target_date: date, key: str, refresh: bool = False) -> List[Dict[str, Any]]:
    end = target_date - timedelta(days=1)
    if end < SEASON_START:
        return []

    all_rows: List[Dict[str, Any]] = []
    hist_dir = CACHE_DIR / "teamstats"
    hist_dir.mkdir(exist_ok=True)

    for start, stop in fixed_chunks(SEASON_START, end, 10):
        fn = hist_dir / f"teamstats_{start.isoformat()}_to_{stop.isoformat()}.json"
        if fn.exists() and not refresh:
            try:
                with open(fn, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    all_rows.extend(data)
                    continue
            except Exception:
                pass

        data = cbbd_get(
            "/games/teams",
            {
                "season": SEASON,
                "startDateRange": start.isoformat() + "T00:00:00Z",
                "endDateRange": stop.isoformat() + "T23:59:59Z",
            },
            key,
        )
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected CBBD team stats response for {start} to {stop}.")
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(data, f)
        all_rows.extend(data)
        time.sleep(0.25)
    return all_rows


RAW_KEYS = [
    "games", "poss", "opp_poss", "pts", "opp_pts", "fgm", "fga", "opp_fgm", "opp_fga",
    "three_m", "three_a", "opp_three_m", "opp_three_a", "orb", "drb", "opp_orb", "opp_drb",
    "tov", "opp_tov", "ast", "opp_ast", "stl", "opp_stl"
]


def raw_zero() -> Dict[str, float]:
    return {k: 0.0 for k in RAW_KEYS}


def raw_from_teamstat(row: Dict[str, Any]) -> Dict[str, float]:
    t = row.get("teamStats") or {}
    o = row.get("opponentStats") or {}
    return {
        "games": 1.0,
        "poss": get_nested(t, "possessions"),
        "opp_poss": get_nested(o, "possessions"),
        "pts": get_nested(t, "points", "total"),
        "opp_pts": get_nested(o, "points", "total"),
        "fgm": get_nested(t, "fieldGoals", "made"),
        "fga": get_nested(t, "fieldGoals", "attempted"),
        "opp_fgm": get_nested(o, "fieldGoals", "made"),
        "opp_fga": get_nested(o, "fieldGoals", "attempted"),
        "three_m": get_nested(t, "threePointFieldGoals", "made"),
        "three_a": get_nested(t, "threePointFieldGoals", "attempted"),
        "opp_three_m": get_nested(o, "threePointFieldGoals", "made"),
        "opp_three_a": get_nested(o, "threePointFieldGoals", "attempted"),
        "orb": get_nested(t, "rebounds", "offensive"),
        "drb": get_nested(t, "rebounds", "defensive"),
        "opp_orb": get_nested(o, "rebounds", "offensive"),
        "opp_drb": get_nested(o, "rebounds", "defensive"),
        "tov": get_nested(t, "turnovers", "total"),
        "opp_tov": get_nested(o, "turnovers", "total"),
        "ast": get_nested(t, "assists"),
        "opp_ast": get_nested(o, "assists"),
        "stl": get_nested(t, "steals"),
        "opp_stl": get_nested(o, "steals"),
    }


def add_raw(a: Dict[str, float], b: Dict[str, float]) -> Dict[str, float]:
    return {k: a.get(k, 0.0) + b.get(k, 0.0) for k in RAW_KEYS}


def metrics_from_raw(s: Dict[str, float]) -> Dict[str, float]:
    games = s["games"]
    return {
        "games": games,
        "pace": s["poss"] / games if games else math.nan,
        "efg": pct(s["fgm"] + 0.5 * s["three_m"], s["fga"]),
        "opp_efg": pct(s["opp_fgm"] + 0.5 * s["opp_three_m"], s["opp_fga"]),
        "opp_to_pct": pct(s["opp_tov"], s["opp_poss"]),
        "three_pct": pct(s["three_m"], s["three_a"]),
        "opp_three_pct": pct(s["opp_three_m"], s["opp_three_a"]),
        "orb_pct": pct(s["orb"], s["orb"] + s["opp_drb"]),
        "opp_orb_pct": pct(s["opp_orb"], s["opp_orb"] + s["drb"]),
        "assist_rate": pct(s["ast"], s["fgm"]),
        "opp_assist_rate": pct(s["opp_ast"], s["opp_fgm"]),
        "nonsteal_to_pct": pct(max(0.0, s["tov"] - s["opp_stl"]), s["poss"]),
        "forced_nonsteal_to_pct": pct(max(0.0, s["opp_tov"] - s["stl"]), s["opp_poss"]),
        "ptdist_3": pct(3.0 * s["three_m"], s["pts"]),
        "opp_ptdist_3": pct(3.0 * s["opp_three_m"], s["opp_pts"]),
    }


def build_snapshots(team_rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Dict[str, float]]]:
    grouped: Dict[str, List[Tuple[str, Dict[str, float]]]] = defaultdict(list)
    for row in team_rows:
        # Require a played box score, not a schedule artifact.
        tpts = get_nested(row.get("teamStats") or {}, "points", "total", default=0.0)
        opts = get_nested(row.get("opponentStats") or {}, "points", "total", default=0.0)
        if tpts == 0 and opts == 0:
            continue
        tid = str(row.get("teamId"))
        grouped[tid].append((row.get("startDate") or "", raw_from_teamstat(row)))

    out: Dict[str, Dict[str, Dict[str, float]]] = {}
    for tid, games in grouped.items():
        games.sort(key=lambda x: x[0])
        total = raw_zero()
        for _, raw in games:
            total = add_raw(total, raw)
        last5_total = raw_zero()
        for _, raw in games[-5:]:
            last5_total = add_raw(last5_total, raw)
        out[tid] = {"season": metrics_from_raw(total), "last5": metrics_from_raw(last5_total)}
    return out


def empty_snapshot() -> Dict[str, Dict[str, float]]:
    keys = [
        "games", "pace", "efg", "opp_efg", "opp_to_pct", "three_pct", "opp_three_pct",
        "orb_pct", "opp_orb_pct", "assist_rate", "opp_assist_rate", "nonsteal_to_pct",
        "forced_nonsteal_to_pct", "ptdist_3", "opp_ptdist_3"
    ]
    nanmetrics = {k: math.nan for k in keys}
    return {"season": dict(nanmetrics), "last5": dict(nanmetrics)}


def archive_lookup(archive: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {norm_name(x.get("TeamName", "")): x for x in archive}


def fanmatch_index(fanmatch: List[Dict[str, Any]]) -> Dict[frozenset, List[Dict[str, Any]]]:
    idx: Dict[frozenset, List[Dict[str, Any]]] = defaultdict(list)
    for g in fanmatch:
        key = frozenset([norm_name(g.get("Visitor", "")), norm_name(g.get("Home", ""))])
        idx[key].append(g)
    return idx


def match_fanmatch(game: Dict[str, Any], aliases: Dict[str, str], idx: Dict[frozenset, List[Dict[str, Any]]]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    away_kp = kp_name_for_cbbd(game.get("awayTeam", ""), aliases)
    home_kp = kp_name_for_cbbd(game.get("homeTeam", ""), aliases)
    key = frozenset([norm_name(away_kp), norm_name(home_kp)])
    candidates = idx.get(key, [])
    if len(candidates) != 1:
        return None, f"FanMatch pair match count={len(candidates)}"
    fm = candidates[0]
    if norm_name(fm.get("Home", "")) == norm_name(home_kp) and norm_name(fm.get("Visitor", "")) == norm_name(away_kp):
        return fm, "NORMAL"
    if norm_name(fm.get("Home", "")) == norm_name(away_kp) and norm_name(fm.get("Visitor", "")) == norm_name(home_kp):
        return fm, "REVERSED"
    return None, "FanMatch orientation unresolved"


def get_archive_team(name: str, aliases: Dict[str, str], lookup: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    kp_name = kp_name_for_cbbd(name, aliases)
    return lookup.get(norm_name(kp_name))


def derive_features(
    game: Dict[str, Any],
    line: Dict[str, Any],
    fm: Dict[str, Any],
    orientation: str,
    home_arch: Dict[str, Any],
    away_arch: Dict[str, Any],
    home_snap: Dict[str, Dict[str, float]],
    away_snap: Dict[str, Dict[str, float]],
    target_date: date,
) -> Dict[str, float]:
    hs = safe_float(line.get("spread"))
    mt = safe_float(line.get("overUnder"))

    if orientation == "NORMAL":
        kp_home_pred = safe_float(fm.get("HomePred"))
        kp_away_pred = safe_float(fm.get("VisitorPred"))
        kp_wp = safe_float(fm.get("HomeWP"))
    else:
        kp_home_pred = safe_float(fm.get("VisitorPred"))
        kp_away_pred = safe_float(fm.get("HomePred"))
        kp_wp_raw = safe_float(fm.get("HomeWP"))
        kp_wp = 100.0 - kp_wp_raw if not math.isnan(kp_wp_raw) else math.nan

    kp_margin = kp_home_pred - kp_away_pred
    kp_total = kp_home_pred + kp_away_pred

    h_em = safe_float(home_arch.get("AdjEM")); a_em = safe_float(away_arch.get("AdjEM"))
    h_oe = safe_float(home_arch.get("AdjOE")); a_oe = safe_float(away_arch.get("AdjOE"))
    h_de = safe_float(home_arch.get("AdjDE")); a_de = safe_float(away_arch.get("AdjDE"))
    h_t = safe_float(home_arch.get("AdjTempo")); a_t = safe_float(away_arch.get("AdjTempo"))
    adj_diff = h_em - a_em

    home_imp = implied_prob_american(line.get("homeMoneyline"))
    away_imp = implied_prob_american(line.get("awayMoneyline"))
    novig = math.nan
    if not math.isnan(home_imp) and not math.isnan(away_imp) and (home_imp + away_imp) > 0:
        novig = home_imp / (home_imp + away_imp)

    same_conf = 1.0 if game.get("homeConference") and game.get("homeConference") == game.get("awayConference") else 0.0
    postseason = 1.0 if (game.get("seasonType") or "").lower() == "postseason" else 0.0
    season_progress = float((target_date - SEASON_START).days)

    f: Dict[str, float] = {
        "home_spread": hs,
        "abs_spread": abs(hs),
        "market_total": mt,
        "kp_margin": kp_margin,
        "kp_total": kp_total,
        "kp_wp": kp_wp,
        "kp_edge_home": kp_margin + hs,
        "abs_kp_edge": abs(kp_margin + hs),
        "adjEM_diff": adj_diff,
        "market_vs_adjEM": (-hs) - adj_diff,
        "same_conference": same_conf,
        "postseason": postseason,
        "season_progress": season_progress,
        "home_adjOE": h_oe,
        "away_adjOE": a_oe,
        "home_adjDE": h_de,
        "away_adjDE": a_de,
        "home_adjTempo": h_t,
        "away_adjTempo": a_t,
        "adjOE_diff": h_oe - a_oe,
        "adjDE_diff": h_de - a_de,
        "tempo_diff": h_t - a_t,
        "home_off_vs_away_def": h_oe - a_de,
        "away_off_vs_home_def": a_oe - h_de,
        "matchup_net": (h_oe - a_de) - (a_oe - h_de),
        "market_home_wp_novig": novig,
        "kp_market_wp_gap": kp_wp / 100.0 - novig if not math.isnan(novig) else math.nan,
    }

    hs_season = home_snap["season"]
    as_season = away_snap["season"]
    hs_l5 = home_snap["last5"]
    as_l5 = away_snap["last5"]

    f.update({
        "std_three_pct_diff": hs_season["three_pct"] - as_season["three_pct"],
        "std_opp_three_pct_diff": hs_season["opp_three_pct"] - as_season["opp_three_pct"],
        "match_three_net": (hs_season["three_pct"] - as_season["opp_three_pct"]) - (as_season["three_pct"] - hs_season["opp_three_pct"]),
        "std_orb_pct_diff": hs_season["orb_pct"] - as_season["orb_pct"],
        "std_opp_orb_pct_diff": hs_season["opp_orb_pct"] - as_season["opp_orb_pct"],
        "match_orb_net": (hs_season["orb_pct"] - as_season["opp_orb_pct"]) - (as_season["orb_pct"] - hs_season["opp_orb_pct"]),
        "form_opp_to_pct_home": hs_l5["opp_to_pct"] - hs_season["opp_to_pct"],
        "form_opp_to_pct_away": as_l5["opp_to_pct"] - as_season["opp_to_pct"],
        "last5_opp_to_pct_diff": hs_l5["opp_to_pct"] - as_l5["opp_to_pct"],
        "std_efg_diff": hs_season["efg"] - as_season["efg"],
        "std_opp_efg_diff": hs_season["opp_efg"] - as_season["opp_efg"],
        "match_efg_net": (hs_season["efg"] - as_season["opp_efg"]) - (as_season["efg"] - hs_season["opp_efg"]),
        "std_assist_rate_diff": hs_season["assist_rate"] - as_season["assist_rate"],
        "std_opp_assist_rate_diff": hs_season["opp_assist_rate"] - as_season["opp_assist_rate"],
        "match_assist_net": (hs_season["assist_rate"] - as_season["opp_assist_rate"]) - (as_season["assist_rate"] - hs_season["opp_assist_rate"]),
        "std_pace_diff": hs_season["pace"] - as_season["pace"],
        "std_nonsteal_to_pct_diff": hs_season["nonsteal_to_pct"] - as_season["nonsteal_to_pct"],
        "std_forced_nonsteal_to_pct_diff": hs_season["forced_nonsteal_to_pct"] - as_season["forced_nonsteal_to_pct"],
        "std_ptdist_3_diff": hs_season["ptdist_3"] - as_season["ptdist_3"],
        "std_opp_ptdist_3_diff": hs_season["opp_ptdist_3"] - as_season["opp_ptdist_3"],
    })
    return f


def apply_neutral_override(game: Dict[str, Any], line: Dict[str, Any], overrides: Dict[str, Dict[str, str]]) -> Tuple[Dict[str, Any], str, str]:
    """Return possibly modified line, QC status, source."""
    neutral = bool(game.get("neutralSite"))
    if not neutral:
        # Flag gross line/ML contradictions for manual review rather than auto-correct.
        spread = safe_float(line.get("spread"))
        hm = safe_float(line.get("homeMoneyline"))
        if not math.isnan(spread) and not math.isnan(hm) and abs(spread) >= 4:
            contradiction = (spread < 0 and hm > 0) or (spread > 0 and hm < 0)
            if contradiction:
                return line, "REVIEW_LINE_ML_CONTRADICTION", ""
        return line, "OK_NON_NEUTRAL", ""

    gid = str(game.get("gameId"))
    ov = overrides.get(gid)
    if not ov:
        return line, "NEUTRAL_REVIEW_REQUIRED", ""

    spread_txt = (ov.get("Home_Spread_Override") or "").strip()
    if not spread_txt:
        return line, "NEUTRAL_OVERRIDE_MISSING_SPREAD", (ov.get("Source_URL") or "").strip()

    new_line = dict(line)
    new_line["spread"] = float(spread_txt)

    # If the override does not explicitly verify moneylines, blank them so a
    # possibly reversed ML cannot contaminate the model's no-vig features.
    hml_txt = (ov.get("Home_Moneyline_Override") or "").strip()
    aml_txt = (ov.get("Away_Moneyline_Override") or "").strip()
    new_line["homeMoneyline"] = float(hml_txt) if hml_txt else None
    new_line["awayMoneyline"] = float(aml_txt) if aml_txt else None
    return new_line, "NEUTRAL_VERIFIED_OVERRIDE", (ov.get("Source_URL") or "").strip()


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fieldnames})


def append_snapshots(rows: List[Dict[str, Any]]) -> None:
    path = OUTPUT_DIR / "line_snapshots.csv"
    fields = [
        "Run_Timestamp_PT", "Game_Date", "Game_ID", "Away_Team", "Home_Team", "Neutral_Site",
        "Provider", "Home_Spread", "Model_Bet", "Bet_Spread", "V4_Edge", "V4_Confidence",
        "Tier", "Profile_Tag", "Actionable", "QC_Status"
    ]
    exists = path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def create_override_template_if_missing() -> None:
    if OVERRIDES_PATH.exists():
        return
    with open(OVERRIDES_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Game_ID", "Home_Spread_Override", "Home_Moneyline_Override", "Away_Moneyline_Override", "Source_URL", "Notes"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the frozen 2026-27 CBB Tier A daily scanner.")
    parser.add_argument("--date", help="Game date YYYY-MM-DD. Defaults to today's date in America/Los_Angeles.")
    parser.add_argument("--refresh-history", action="store_true", help="Re-download prior 10-day CBBD team-stat chunks.")
    parser.add_argument("--no-snapshots", action="store_true", help="Do not append this run to output/line_snapshots.csv.")
    args = parser.parse_args()

    target_date = datetime.now(LOCAL_TZ).date() if not args.date else date.fromisoformat(args.date)
    if target_date < SEASON_START:
        print(f"2026-27 scanner is configured for dates on/after {SEASON_START.isoformat()}.")
        print(f"Requested date: {target_date.isoformat()}")
        return 0

    cbbd_key = os.environ.get("CBBD_API_KEY", "").strip()
    kenpom_key = os.environ.get("KENPOM_API_KEY", "").strip()
    if not cbbd_key or not kenpom_key:
        print("ERROR: API keys are not set in this Terminal session.")
        print("Set both environment variables, then rerun:")
        print("  export CBBD_API_KEY='YOUR_KEY'")
        print("  export KENPOM_API_KEY='YOUR_KEY'")
        return 2

    if not MODEL_PATH.exists():
        print(f"ERROR: missing model file: {MODEL_PATH}")
        return 2

    create_override_template_if_missing()
    aliases = load_aliases()
    overrides = load_overrides()
    bundle = joblib.load(MODEL_PATH)
    model = bundle["regressor"]
    features: List[str] = bundle["features"]
    sigma = float(bundle["probability_sigma"])

    print("=" * 70)
    print("2026-27 CBB TIER A DAILY SCANNER")
    print("=" * 70)
    print("Date:", target_date.isoformat())
    print("Frozen rule: V4 ATS edge >= 1.75 AND model side underdog +4 or more")
    print("Profile tag: AWAY_4_TO_6_5 is descriptive only; it is not a higher-confidence tier.")
    print()

    start_utc, end_utc = local_day_utc_bounds(target_date)
    today_lines = cbbd_get(
        "/lines",
        {"season": SEASON, "startDateRange": start_utc, "endDateRange": end_utc},
        cbbd_key,
    )
    if not isinstance(today_lines, list):
        raise RuntimeError("Unexpected CBBD lines response.")

    fanmatch = kenpom_get("fanmatch", {"d": target_date.isoformat()}, kenpom_key)
    if fanmatch is None:
        fanmatch = []
    if not isinstance(fanmatch, list):
        raise RuntimeError("Unexpected KenPom FanMatch response.")

    if not today_lines and not fanmatch:
        print("No CBBD/KenPom games found for this date.")
        return 0

    archive = kenpom_get("archive", {"d": target_date.isoformat()}, kenpom_key)
    if not isinstance(archive, list):
        raise RuntimeError("KenPom archive is unavailable for this date. Do not score games until the same-day archive is available.")

    prior_rows = fetch_teamstats_history(target_date, cbbd_key, refresh=args.refresh_history)
    snapshots = build_snapshots(prior_rows)
    fm_idx = fanmatch_index(fanmatch)
    arch_idx = archive_lookup(archive)

    scored: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []
    audit: List[str] = []
    counts = defaultdict(int)

    for game in today_lines:
        counts["line_games"] += 1
        # D-I proxy used in development: both conference fields are populated.
        if game.get("homeConference") is None or game.get("awayConference") is None:
            counts["non_d1_proxy"] += 1
            continue

        selected, provider_status = select_line(game)
        if selected is None:
            counts["no_spread"] += 1
            continue

        selected, qc_status, qc_source = apply_neutral_override(game, selected, overrides)
        if provider_status != "FROZEN_PRIORITY":
            qc_status = qc_status + "; REVIEW_PROVIDER"

        fm, orientation = match_fanmatch(game, aliases, fm_idx)
        if fm is None:
            counts["fanmatch_unmatched"] += 1
            audit.append(f"FanMatch unmatched: {game.get('gameId')} {game.get('awayTeam')} @ {game.get('homeTeam')} ({orientation})")
            continue

        harch = get_archive_team(game.get("homeTeam", ""), aliases, arch_idx)
        aarch = get_archive_team(game.get("awayTeam", ""), aliases, arch_idx)
        if harch is None or aarch is None:
            counts["archive_unmatched"] += 1
            audit.append(f"Archive unmatched: {game.get('gameId')} {game.get('awayTeam')} @ {game.get('homeTeam')}")
            continue

        hsnap = snapshots.get(str(game.get("homeTeamId")), empty_snapshot())
        asnap = snapshots.get(str(game.get("awayTeamId")), empty_snapshot())

        fv = derive_features(game, selected, fm, orientation, harch, aarch, hsnap, asnap, target_date)
        X = np.array([[fv.get(feat, math.nan) for feat in features]], dtype=float)
        mu = float(model.predict(X)[0])
        p_home = float(norm.cdf(mu / sigma))
        confidence = max(p_home, 1.0 - p_home)

        home_spread = float(selected["spread"])
        if mu >= 0:
            bet_team = game.get("homeTeam")
            bet_side = "Home"
            bet_spread = home_spread
        else:
            bet_team = game.get("awayTeam")
            bet_side = "Away"
            bet_spread = -home_spread

        is_underdog = bet_spread > 0
        edge = abs(mu)
        tier_a = edge >= 1.75 and is_underdog and bet_spread >= 4.0
        profile_tag = "AWAY_4_TO_6_5" if (tier_a and bet_side == "Away" and 4.0 <= bet_spread <= 6.5 and not bool(game.get("neutralSite"))) else ""

        actionable = tier_a
        if "REVIEW" in qc_status or "REQUIRED" in qc_status or "MISSING" in qc_status:
            actionable = False

        tier = "A" if tier_a else ""
        if tier_a and not actionable:
            tier = "A-PROVISIONAL"

        result = {
            "Run_Timestamp_PT": datetime.now(LOCAL_TZ).isoformat(timespec="seconds"),
            "Game_Date": target_date.isoformat(),
            "Game_ID": game.get("gameId"),
            "Start_UTC": game.get("startDate"),
            "Away_Team": game.get("awayTeam"),
            "Home_Team": game.get("homeTeam"),
            "Neutral_Site": bool(game.get("neutralSite")),
            "Provider": selected.get("provider"),
            "Provider_Status": provider_status,
            "Home_Spread": home_spread,
            "Market_Total": selected.get("overUnder"),
            "Home_Moneyline": selected.get("homeMoneyline"),
            "Away_Moneyline": selected.get("awayMoneyline"),
            "Model_Bet": bet_team,
            "Bet_Side": bet_side,
            "Bet_Spread": bet_spread,
            "V4_Predicted_ATS_Edge": mu,
            "V4_Edge": edge,
            "V4_Home_Cover_Prob": p_home,
            "V4_Confidence": confidence,
            "Tier": tier,
            "Profile_Tag": profile_tag,
            "Actionable": actionable,
            "QC_Status": qc_status,
            "QC_Source": qc_source,
            "Home_Prior_Games": hsnap["season"].get("games", math.nan),
            "Away_Prior_Games": asnap["season"].get("games", math.nan),
            "KP_Orientation": orientation,
            "KP_Home_WP": fv["kp_wp"],
            "KP_Pred_Home_Margin": fv["kp_margin"],
        }
        scored.append(result)
        counts["scored"] += 1
        if tier_a:
            candidates.append(result)
            counts["tier_a"] += 1
            if actionable:
                counts["actionable"] += 1
            if bool(game.get("neutralSite")) and not actionable:
                counts["neutral_review"] += 1

    scored.sort(key=lambda r: (-float(r["V4_Edge"]), str(r["Start_UTC"])))
    candidates.sort(key=lambda r: (not bool(r["Actionable"]), -float(r["V4_Edge"]), str(r["Start_UTC"])))

    fields = [
        "Run_Timestamp_PT", "Game_Date", "Game_ID", "Start_UTC", "Away_Team", "Home_Team",
        "Neutral_Site", "Provider", "Provider_Status", "Home_Spread", "Market_Total",
        "Home_Moneyline", "Away_Moneyline", "Model_Bet", "Bet_Side", "Bet_Spread",
        "V4_Predicted_ATS_Edge", "V4_Edge", "V4_Home_Cover_Prob", "V4_Confidence",
        "Tier", "Profile_Tag", "Actionable", "QC_Status", "QC_Source", "Home_Prior_Games", "Away_Prior_Games",
        "KP_Orientation", "KP_Home_WP", "KP_Pred_Home_Margin"
    ]

    all_path = OUTPUT_DIR / f"all_scored_{target_date.isoformat()}.csv"
    cand_path = OUTPUT_DIR / f"tierA_candidates_{target_date.isoformat()}.csv"
    audit_path = OUTPUT_DIR / f"audit_{target_date.isoformat()}.txt"
    write_csv(all_path, scored, fields)
    write_csv(cand_path, candidates, fields)

    if not args.no_snapshots:
        append_snapshots(scored)

    with open(audit_path, "w", encoding="utf-8") as f:
        f.write("2026-27 TIER A SCANNER AUDIT\n")
        f.write("=" * 40 + "\n")
        f.write(f"Date: {target_date.isoformat()}\n")
        for key in ["line_games", "non_d1_proxy", "no_spread", "fanmatch_unmatched", "archive_unmatched", "scored", "tier_a", "actionable", "neutral_review"]:
            f.write(f"{key}: {counts[key]}\n")
        if audit:
            f.write("\nDETAILS\n")
            for line in audit:
                f.write(line + "\n")

    print("AUDIT")
    print("-" * 70)
    print(f"CBBD line games:           {counts['line_games']}")
    print(f"Scored D-I games:          {counts['scored']}")
    print(f"FanMatch unmatched:        {counts['fanmatch_unmatched']}")
    print(f"Archive unmatched:         {counts['archive_unmatched']}")
    print(f"Tier A candidates:         {counts['tier_a']}")
    print(f"Actionable Tier A:         {counts['actionable']}")
    print(f"Neutral review required:   {counts['neutral_review']}")
    print()

    if candidates:
        print("TIER A CANDIDATES")
        print("-" * 70)
        for r in candidates:
            status = "BET" if r["Actionable"] else "REVIEW"
            print(
                f"[{status:6}] {r['Tier']:<14} {r['Model_Bet']:<26} "
                f"{float(r['Bet_Spread']):+5.1f} | edge {float(r['V4_Edge']):4.2f} | "
                f"{r['Away_Team']} @ {r['Home_Team']} | {r['QC_Status']}"
            )
    else:
        print("No Tier A candidates today.")

    print()
    print("Files written:")
    print(" ", cand_path)
    print(" ", all_path)
    print(" ", audit_path)
    if not args.no_snapshots:
        print(" ", OUTPUT_DIR / "line_snapshots.csv")
    print()
    print("Neutral-site rule: a neutral Tier A candidate is NOT actionable until its")
    print("home-team spread orientation is verified in neutral_overrides.csv and the")
    print("scanner is rerun.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nStopped by user.")
        raise SystemExit(130)
    except Exception as exc:
        print(f"\nERROR: {exc}")
        raise SystemExit(1)
