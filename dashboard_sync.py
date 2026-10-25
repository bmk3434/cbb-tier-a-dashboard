#!/usr/bin/env python3
"""Persist scanner candidates, line snapshots, settle completed plays, and publish site data.

This script is intended to run immediately after cbb_tierA_scanner.py in GitHub Actions.
It never needs the KenPom key. The CBBD key is used only to settle past games.
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
DATA = ROOT / "data"
SITE_DATA = ROOT / "site" / "data"
STATE_PATH = DATA / "state.json"
SITE_STATE_PATH = SITE_DATA / "state.json"
LOCAL_TZ = ZoneInfo("America/Los_Angeles")
CBBD_BASE = "https://api.collegebasketballdata.com"
SEASON = 2027

DATA.mkdir(exist_ok=True)
SITE_DATA.mkdir(parents=True, exist_ok=True)


def now_pt() -> datetime:
    return datetime.now(LOCAL_TZ)


def safe_float(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except Exception:
        return None


def as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes"}


def load_state() -> Dict[str, Any]:
    if STATE_PATH.exists():
        try:
            obj = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                obj.setdefault("plays", {})
                obj.setdefault("totals_plays", {})
                obj.setdefault("totals_v2_plays", {})
                obj.setdefault("totals_v3_plays", {})
                obj.setdefault("runs", [])
                obj["version"] = max(int(obj.get("version", 1)), 4)
                return obj
        except Exception:
            pass
    return {"version": 4, "last_updated_pt": None, "status": "New", "plays": {}, "totals_plays": {}, "totals_v2_plays": {}, "totals_v3_plays": {}, "runs": []}


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def team_spread_from_home_spread(play: Dict[str, Any], home_spread: float) -> float:
    if play["bet_side"] == "Home":
        return float(home_spread)
    return -float(home_spread)


def append_snapshot(play: Dict[str, Any], row: Dict[str, Any]) -> None:
    ts = row.get("Run_Timestamp_PT") or now_pt().isoformat(timespec="seconds")
    snaps = play.setdefault("snapshots", [])
    if any(s.get("timestamp_pt") == ts for s in snaps):
        return
    hs = safe_float(row.get("Home_Spread"))
    if hs is None:
        return
    team_line = team_spread_from_home_spread(play, hs)
    snap = {
        "timestamp_pt": ts,
        "team_spread": team_line,
        "home_spread": hs,
        "provider": row.get("Provider") or "",
        "model_bet_now": row.get("Model_Bet") or "",
        "edge_now": safe_float(row.get("V4_Edge")),
        "tier_now": row.get("Tier") or "",
        "actionable_now": as_bool(row.get("Actionable")),
        "qc_status_now": row.get("QC_Status") or "",
    }
    snaps.append(snap)
    snaps.sort(key=lambda s: s["timestamp_pt"])
    play["latest_line"] = team_line
    play["latest_provider"] = snap["provider"]
    play["latest_edge"] = snap["edge_now"]
    play["latest_snapshot_pt"] = ts


def create_or_update_candidates(state: Dict[str, Any], target_date: str) -> int:
    cand_path = OUTPUT / f"tierA_candidates_{target_date}.csv"
    candidates = read_csv(cand_path)
    plays = state["plays"]
    created = 0
    for r in candidates:
        gid = str(r.get("Game_ID") or "").strip()
        if not gid:
            continue
        if gid not in plays:
            actionable = as_bool(r.get("Actionable"))
            bet_line = safe_float(r.get("Bet_Spread"))
            play = {
                "game_id": gid,
                "game_date": r.get("Game_Date") or target_date,
                "start_utc": r.get("Start_UTC") or "",
                "away_team": r.get("Away_Team") or "",
                "home_team": r.get("Home_Team") or "",
                "neutral_site": as_bool(r.get("Neutral_Site")),
                "bet_team": r.get("Model_Bet") or "",
                "bet_side": r.get("Bet_Side") or "",
                "first_seen_pt": r.get("Run_Timestamp_PT") or now_pt().isoformat(timespec="seconds"),
                "first_scanner_line": bet_line,
                "first_edge": safe_float(r.get("V4_Edge")),
                "first_confidence": safe_float(r.get("V4_Confidence")),
                "profile_tag": r.get("Profile_Tag") or "",
                "tier": r.get("Tier") or "",
                "qc_status": r.get("QC_Status") or "",
                "qc_source": r.get("QC_Source") or "",
                "first_actionable_pt": (r.get("Run_Timestamp_PT") or now_pt().isoformat(timespec="seconds")) if actionable else None,
                "entry_line": bet_line if actionable else None,
                "entry_edge": safe_float(r.get("V4_Edge")) if actionable else None,
                "entry_provider": r.get("Provider") if actionable else None,
                "result": None,
                "profit_units": None,
                "home_score": None,
                "away_score": None,
                "settled_at_pt": None,
                "closing_proxy_line": None,
                "clv_proxy_points": None,
                "snapshots": [],
            }
            plays[gid] = play
            created += 1
        play = plays[gid]
        actionable = as_bool(r.get("Actionable"))
        play["tier"] = r.get("Tier") or play.get("tier") or ""
        play["qc_status"] = r.get("QC_Status") or play.get("qc_status") or ""
        play["qc_source"] = r.get("QC_Source") or play.get("qc_source") or ""
        if actionable and play.get("entry_line") is None:
            play["first_actionable_pt"] = r.get("Run_Timestamp_PT") or now_pt().isoformat(timespec="seconds")
            play["entry_line"] = safe_float(r.get("Bet_Spread"))
            play["entry_edge"] = safe_float(r.get("V4_Edge"))
            play["entry_provider"] = r.get("Provider") or ""
        append_snapshot(play, r)
    return created


def merge_current_snapshots(state: Dict[str, Any]) -> None:
    path = OUTPUT / "line_snapshots.csv"
    rows = read_csv(path)
    if not rows:
        return
    by_id = {str(r.get("Game_ID") or ""): r for r in rows}
    for gid, play in state["plays"].items():
        r = by_id.get(gid)
        if r:
            append_snapshot(play, r)



def append_totals_snapshot(play: Dict[str, Any], row: Dict[str, Any]) -> None:
    ts = row.get("Run_Timestamp_PT") or now_pt().isoformat(timespec="seconds")
    snaps = play.setdefault("snapshots", [])
    if any(s.get("timestamp_pt") == ts for s in snaps):
        return
    total = safe_float(row.get("Market_Total"))
    if total is None:
        return
    snap = {
        "timestamp_pt": ts,
        "market_total": total,
        "provider": row.get("Provider") or "",
        "model_bet_now": row.get("Totals_Bet") or "",
        "model_total_now": safe_float(row.get("Totals_Model_Total")),
        "edge_now": safe_float(row.get("Totals_Edge")),
        "qualified_now": as_bool(row.get("Totals_Qualified")),
        "actionable_now": as_bool(row.get("Totals_Actionable")),
        "qc_status_now": row.get("Totals_QC_Status") or "",
    }
    snaps.append(snap)
    snaps.sort(key=lambda x: x["timestamp_pt"])
    play["latest_total"] = total
    play["latest_model_total"] = snap["model_total_now"]
    play["latest_edge"] = snap["edge_now"]
    play["latest_provider"] = snap["provider"]
    play["latest_snapshot_pt"] = ts


def create_or_update_totals_candidates(state: Dict[str, Any], target_date: str) -> int:
    cand_path = OUTPUT / f"totals_candidates_{target_date}.csv"
    candidates = read_csv(cand_path)
    plays = state["totals_plays"]
    created = 0
    for r in candidates:
        gid = str(r.get("Game_ID") or "").strip()
        if not gid:
            continue
        actionable = as_bool(r.get("Totals_Actionable"))
        total_line = safe_float(r.get("Market_Total"))
        if gid not in plays:
            play = {
                "game_id": gid,
                "game_date": r.get("Game_Date") or target_date,
                "start_utc": r.get("Start_UTC") or "",
                "away_team": r.get("Away_Team") or "",
                "home_team": r.get("Home_Team") or "",
                "neutral_site": as_bool(r.get("Neutral_Site")),
                "bet_side": r.get("Totals_Bet") or "",
                "first_seen_pt": r.get("Run_Timestamp_PT") or now_pt().isoformat(timespec="seconds"),
                "first_scanner_total": total_line,
                "first_model_total": safe_float(r.get("Totals_Model_Total")),
                "first_edge": safe_float(r.get("Totals_Edge")),
                "qc_status": r.get("Totals_QC_Status") or "",
                "first_actionable_pt": (r.get("Run_Timestamp_PT") or now_pt().isoformat(timespec="seconds")) if actionable else None,
                "entry_total": total_line if actionable else None,
                "entry_edge": safe_float(r.get("Totals_Edge")) if actionable else None,
                "entry_model_total": safe_float(r.get("Totals_Model_Total")) if actionable else None,
                "entry_provider": r.get("Provider") if actionable else None,
                "result": None,
                "profit_units": None,
                "home_score": None,
                "away_score": None,
                "actual_total": None,
                "total_margin_selected_side": None,
                "regulation_home_score": None,
                "regulation_away_score": None,
                "regulation_total": None,
                "regulation_result": None,
                "regulation_profit_units": None,
                "went_overtime": None,
                "ot_assisted_win": None,
                "regulation_audited_at_pt": None,
                "settled_at_pt": None,
                "closing_proxy_total": None,
                "clv_proxy_points": None,
                "snapshots": [],
            }
            plays[gid] = play
            created += 1
        play = plays[gid]
        play["qc_status"] = r.get("Totals_QC_Status") or play.get("qc_status") or ""
        if actionable and play.get("entry_total") is None:
            play["first_actionable_pt"] = r.get("Run_Timestamp_PT") or now_pt().isoformat(timespec="seconds")
            play["bet_side"] = r.get("Totals_Bet") or play.get("bet_side") or ""
            play["entry_total"] = total_line
            play["entry_edge"] = safe_float(r.get("Totals_Edge"))
            play["entry_model_total"] = safe_float(r.get("Totals_Model_Total"))
            play["entry_provider"] = r.get("Provider") or ""
        append_totals_snapshot(play, r)
    return created


def merge_current_totals_snapshots(state: Dict[str, Any]) -> None:
    path = OUTPUT / "line_snapshots.csv"
    rows = read_csv(path)
    if not rows:
        return
    by_id = {str(r.get("Game_ID") or ""): r for r in rows}
    for gid, play in state["totals_plays"].items():
        r = by_id.get(gid)
        if r:
            append_totals_snapshot(play, r)


def append_totals_v2_snapshot(play: Dict[str, Any], row: Dict[str, Any]) -> None:
    ts = row.get("Run_Timestamp_PT") or now_pt().isoformat(timespec="seconds")
    snaps = play.setdefault("snapshots", [])
    if any(s.get("timestamp_pt") == ts for s in snaps):
        return
    total = safe_float(row.get("Market_Total"))
    if total is None:
        return
    snap = {
        "timestamp_pt": ts,
        "market_total": total,
        "provider": row.get("Provider") or "",
        "model_bet_now": row.get("Totals_V2_Bet") or "",
        "model_total_now": safe_float(row.get("Totals_V2_Model_Total")),
        "edge_now": safe_float(row.get("Totals_V2_Edge")),
        "qualified_now": as_bool(row.get("Totals_V2_Qualified_Shadow")),
        "trackable_now": as_bool(row.get("Totals_V2_Trackable_Shadow")),
        "qc_status_now": row.get("Totals_V2_QC_Status") or "",
    }
    snaps.append(snap)
    snaps.sort(key=lambda x: x["timestamp_pt"])
    play["latest_total"] = total
    play["latest_model_total"] = snap["model_total_now"]
    play["latest_edge"] = snap["edge_now"]
    play["latest_provider"] = snap["provider"]
    play["latest_snapshot_pt"] = ts


def create_or_update_totals_v2_candidates(state: Dict[str, Any], target_date: str) -> int:
    cand_path = OUTPUT / f"totals_v2_shadow_candidates_{target_date}.csv"
    candidates = read_csv(cand_path)
    plays = state["totals_v2_plays"]
    created = 0
    for r in candidates:
        gid = str(r.get("Game_ID") or "").strip()
        if not gid:
            continue
        trackable = as_bool(r.get("Totals_V2_Trackable_Shadow"))
        total_line = safe_float(r.get("Market_Total"))
        if gid not in plays:
            play = {
                "game_id": gid,
                "game_date": r.get("Game_Date") or target_date,
                "start_utc": r.get("Start_UTC") or "",
                "away_team": r.get("Away_Team") or "",
                "home_team": r.get("Home_Team") or "",
                "neutral_site": as_bool(r.get("Neutral_Site")),
                "bet_side": r.get("Totals_V2_Bet") or "",
                "shadow_only": True,
                "first_seen_pt": r.get("Run_Timestamp_PT") or now_pt().isoformat(timespec="seconds"),
                "first_scanner_total": total_line,
                "first_model_total": safe_float(r.get("Totals_V2_Model_Total")),
                "first_edge": safe_float(r.get("Totals_V2_Edge")),
                "qc_status": r.get("Totals_V2_QC_Status") or "",
                "first_actionable_pt": (r.get("Run_Timestamp_PT") or now_pt().isoformat(timespec="seconds")) if trackable else None,
                "entry_total": total_line if trackable else None,
                "entry_edge": safe_float(r.get("Totals_V2_Edge")) if trackable else None,
                "entry_model_total": safe_float(r.get("Totals_V2_Model_Total")) if trackable else None,
                "entry_provider": r.get("Provider") if trackable else None,
                "result": None,
                "profit_units": None,
                "home_score": None,
                "away_score": None,
                "actual_total": None,
                "total_margin_selected_side": None,
                "regulation_home_score": None,
                "regulation_away_score": None,
                "regulation_total": None,
                "regulation_result": None,
                "regulation_profit_units": None,
                "went_overtime": None,
                "ot_assisted_win": None,
                "regulation_audited_at_pt": None,
                "settled_at_pt": None,
                "closing_proxy_total": None,
                "clv_proxy_points": None,
                "snapshots": [],
            }
            plays[gid] = play
            created += 1
        play = plays[gid]
        play["qc_status"] = r.get("Totals_V2_QC_Status") or play.get("qc_status") or ""
        if trackable and play.get("entry_total") is None:
            play["first_actionable_pt"] = r.get("Run_Timestamp_PT") or now_pt().isoformat(timespec="seconds")
            play["bet_side"] = r.get("Totals_V2_Bet") or play.get("bet_side") or ""
            play["entry_total"] = total_line
            play["entry_edge"] = safe_float(r.get("Totals_V2_Edge"))
            play["entry_model_total"] = safe_float(r.get("Totals_V2_Model_Total"))
            play["entry_provider"] = r.get("Provider") or ""
        append_totals_v2_snapshot(play, r)
    return created


def merge_current_totals_v2_snapshots(state: Dict[str, Any]) -> None:
    path = OUTPUT / "line_snapshots.csv"
    rows = read_csv(path)
    if not rows:
        return
    by_id = {str(r.get("Game_ID") or ""): r for r in rows}
    for gid, play in state["totals_v2_plays"].items():
        r = by_id.get(gid)
        if r:
            append_totals_v2_snapshot(play, r)


def append_totals_v3_snapshot(play: Dict[str, Any], row: Dict[str, Any]) -> None:
    ts = row.get("Run_Timestamp_PT") or now_pt().isoformat(timespec="seconds")
    snaps = play.setdefault("snapshots", [])
    if any(s.get("timestamp_pt") == ts for s in snaps):
        return
    total = safe_float(row.get("Market_Total"))
    if total is None:
        return
    snap = {
        "timestamp_pt": ts,
        "market_total": total,
        "provider": row.get("Provider") or "",
        "model_bet_now": row.get("Totals_V3_Bet") or "",
        "model_total_now": safe_float(row.get("Totals_V3_Model_Total")),
        "edge_now": safe_float(row.get("Totals_V3_Edge")),
        "qualified_now": as_bool(row.get("Totals_V3_Qualified_Shadow")),
        "trackable_now": as_bool(row.get("Totals_V3_Trackable_Shadow")),
        "qc_status_now": row.get("Totals_V3_QC_Status") or "",
    }
    snaps.append(snap)
    snaps.sort(key=lambda x: x["timestamp_pt"])
    play["latest_total"] = total
    play["latest_model_total"] = snap["model_total_now"]
    play["latest_edge"] = snap["edge_now"]
    play["latest_provider"] = snap["provider"]
    play["latest_snapshot_pt"] = ts


def create_or_update_totals_v3_candidates(state: Dict[str, Any], target_date: str) -> int:
    cand_path = OUTPUT / f"totals_v3_shadow_candidates_{target_date}.csv"
    candidates = read_csv(cand_path)
    plays = state["totals_v3_plays"]
    created = 0
    for r in candidates:
        gid = str(r.get("Game_ID") or "").strip()
        if not gid:
            continue
        trackable = as_bool(r.get("Totals_V3_Trackable_Shadow"))
        total_line = safe_float(r.get("Market_Total"))
        if gid not in plays:
            play = {
                "game_id": gid,
                "game_date": r.get("Game_Date") or target_date,
                "start_utc": r.get("Start_UTC") or "",
                "away_team": r.get("Away_Team") or "",
                "home_team": r.get("Home_Team") or "",
                "neutral_site": as_bool(r.get("Neutral_Site")),
                "bet_side": r.get("Totals_V3_Bet") or "",
                "shadow_only": True,
                "experimental_over_thesis": True,
                "first_seen_pt": r.get("Run_Timestamp_PT") or now_pt().isoformat(timespec="seconds"),
                "first_scanner_total": total_line,
                "first_model_total": safe_float(r.get("Totals_V3_Model_Total")),
                "first_edge": safe_float(r.get("Totals_V3_Edge")),
                "qc_status": r.get("Totals_V3_QC_Status") or "",
                "first_actionable_pt": (r.get("Run_Timestamp_PT") or now_pt().isoformat(timespec="seconds")) if trackable else None,
                "entry_total": total_line if trackable else None,
                "entry_edge": safe_float(r.get("Totals_V3_Edge")) if trackable else None,
                "entry_model_total": safe_float(r.get("Totals_V3_Model_Total")) if trackable else None,
                "entry_provider": r.get("Provider") if trackable else None,
                "result": None,
                "profit_units": None,
                "home_score": None,
                "away_score": None,
                "actual_total": None,
                "total_margin_selected_side": None,
                "regulation_home_score": None,
                "regulation_away_score": None,
                "regulation_total": None,
                "regulation_result": None,
                "regulation_profit_units": None,
                "went_overtime": None,
                "ot_assisted_win": None,
                "regulation_audited_at_pt": None,
                "settled_at_pt": None,
                "closing_proxy_total": None,
                "clv_proxy_points": None,
                "snapshots": [],
            }
            plays[gid] = play
            created += 1
        play = plays[gid]
        play["qc_status"] = r.get("Totals_V3_QC_Status") or play.get("qc_status") or ""
        if trackable and play.get("entry_total") is None:
            play["first_actionable_pt"] = r.get("Run_Timestamp_PT") or now_pt().isoformat(timespec="seconds")
            play["bet_side"] = r.get("Totals_V3_Bet") or play.get("bet_side") or ""
            play["entry_total"] = total_line
            play["entry_edge"] = safe_float(r.get("Totals_V3_Edge"))
            play["entry_model_total"] = safe_float(r.get("Totals_V3_Model_Total"))
            play["entry_provider"] = r.get("Provider") or ""
        append_totals_v3_snapshot(play, r)
    return created


def merge_current_totals_v3_snapshots(state: Dict[str, Any]) -> None:
    path = OUTPUT / "line_snapshots.csv"
    rows = read_csv(path)
    if not rows:
        return
    by_id = {str(r.get("Game_ID") or ""): r for r in rows}
    for gid, play in state["totals_v3_plays"].items():
        r = by_id.get(gid)
        if r:
            append_totals_v3_snapshot(play, r)

def parse_iso(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return None


def update_closing_proxy(play: Dict[str, Any]) -> None:
    entry = safe_float(play.get("entry_line"))
    start = parse_iso(play.get("start_utc") or "")
    if entry is None or start is None:
        return
    eligible = []
    for s in play.get("snapshots", []):
        ts = parse_iso(s.get("timestamp_pt") or "")
        line = safe_float(s.get("team_spread"))
        if ts is not None and line is not None and ts.astimezone(timezone.utc) <= start.astimezone(timezone.utc):
            eligible.append((ts, line))
    if not eligible:
        return
    eligible.sort(key=lambda x: x[0])
    close = eligible[-1][1]
    play["closing_proxy_line"] = close
    play["clv_proxy_points"] = round(entry - close, 3)



def update_totals_closing_proxy(play: Dict[str, Any]) -> None:
    entry = safe_float(play.get("entry_total"))
    start = parse_iso(play.get("start_utc") or "")
    if entry is None or start is None:
        return
    eligible = []
    for snap in play.get("snapshots", []):
        ts = parse_iso(snap.get("timestamp_pt") or "")
        total = safe_float(snap.get("market_total"))
        if ts is not None and total is not None and ts.astimezone(timezone.utc) <= start.astimezone(timezone.utc):
            eligible.append((ts, total))
    if not eligible:
        return
    eligible.sort(key=lambda x: x[0])
    close = eligible[-1][1]
    play["closing_proxy_total"] = close
    if play.get("bet_side") == "Over":
        play["clv_proxy_points"] = round(close - entry, 3)
    elif play.get("bet_side") == "Under":
        play["clv_proxy_points"] = round(entry - close, 3)

def http_json(url: str, params: Dict[str, Any], key: str) -> Any:
    full = url + "?" + urlencode(params)
    req = Request(full, headers={"Authorization": f"Bearer {key}", "User-Agent": "CBB-TierA-Dashboard/1.0"})
    try:
        with urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"CBBD HTTP {e.code}: {body[:300]}") from e
    except URLError as e:
        raise RuntimeError(f"CBBD network error: {e}") from e


def date_bounds(d: date) -> tuple[str, str]:
    start = datetime(d.year, d.month, d.day, tzinfo=LOCAL_TZ)
    end = start + timedelta(days=1) - timedelta(seconds=1)
    return (start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"))


def regulation_score_from_plays(game_id: str, key: str) -> Optional[tuple[int, int, bool]]:
    """Return end-of-regulation home/away score and OT flag from CBBD play-by-play."""
    try:
        plays = http_json(CBBD_BASE + f"/plays/game/{game_id}", {}, key)
    except Exception as exc:
        print(f"WARNING: regulation audit unavailable for game {game_id}: {exc}")
        return None
    if not isinstance(plays, list) or not plays:
        return None

    usable = []
    max_period = 0
    for p in plays:
        try:
            period = int(p.get("period"))
            max_period = max(max_period, period)
        except Exception:
            continue
        if period != 2:
            continue
        hs = p.get("homeScore")
        aas = p.get("awayScore")
        if hs is None or aas is None:
            continue
        try:
            sec = int(p.get("secondsRemaining"))
            hs_i = int(hs)
            aas_i = int(aas)
            pid = int(p.get("id") or 0)
        except Exception:
            continue
        usable.append((sec, hs_i + aas_i, pid, hs_i, aas_i))

    if not usable:
        return None

    # Closest recorded play to the end of period 2. If several share the same
    # clock, keep the one with the most advanced score / play id.
    min_sec = min(x[0] for x in usable)
    final_period_rows = [x for x in usable if x[0] == min_sec]
    _, _, _, reg_home, reg_away = max(final_period_rows, key=lambda x: (x[1], x[2]))
    return reg_home, reg_away, max_period > 2


def backfill_totals_regulation_audit(state: Dict[str, Any], key: str) -> int:
    audited = 0
    for collection in ("totals_plays", "totals_v2_plays", "totals_v3_plays"):
        for play in state[collection].values():
            if play.get("entry_total") is None or play.get("result") not in {"W", "L", "P"}:
                continue
            if play.get("regulation_result") in {"W", "L", "P"}:
                continue

            reg = regulation_score_from_plays(str(play.get("game_id")), key)
            if reg is None:
                continue
            rh, ra, went_ot = reg
            entry = float(play["entry_total"])
            reg_total = rh + ra
            raw_margin = reg_total - entry
            selected_margin = raw_margin if play.get("bet_side") == "Over" else -raw_margin
            reg_result = "W" if selected_margin > 0 else ("L" if selected_margin < 0 else "P")

            play["regulation_home_score"] = rh
            play["regulation_away_score"] = ra
            play["regulation_total"] = reg_total
            play["regulation_result"] = reg_result
            play["regulation_profit_units"] = round(100/110, 6) if reg_result == "W" else (-1.0 if reg_result == "L" else 0.0)
            play["went_overtime"] = bool(went_ot)
            play["ot_assisted_win"] = bool(went_ot and play.get("result") == "W" and reg_result != "W")
            play["regulation_audited_at_pt"] = now_pt().isoformat(timespec="seconds")
            audited += 1
    return audited


def settle_unresolved(state: Dict[str, Any], key: str) -> tuple[int, int, int, int]:
    unresolved_by_date: Dict[str, List[tuple[str, Dict[str, Any]]]] = {}
    today = now_pt().date()

    for play in state["plays"].values():
        if play.get("result") or play.get("entry_line") is None:
            continue
        try:
            gd = date.fromisoformat(play["game_date"])
        except Exception:
            continue
        if gd <= today:
            unresolved_by_date.setdefault(play["game_date"], []).append(("ats", play))

    for play in state["totals_plays"].values():
        if play.get("result") or play.get("entry_total") is None:
            continue
        try:
            gd = date.fromisoformat(play["game_date"])
        except Exception:
            continue
        if gd <= today:
            unresolved_by_date.setdefault(play["game_date"], []).append(("total", play))

    for play in state["totals_v2_plays"].values():
        if play.get("result") or play.get("entry_total") is None:
            continue
        try:
            gd = date.fromisoformat(play["game_date"])
        except Exception:
            continue
        if gd <= today:
            unresolved_by_date.setdefault(play["game_date"], []).append(("total_v2", play))

    for play in state["totals_v3_plays"].values():
        if play.get("result") or play.get("entry_total") is None:
            continue
        try:
            gd = date.fromisoformat(play["game_date"])
        except Exception:
            continue
        if gd <= today:
            unresolved_by_date.setdefault(play["game_date"], []).append(("total_v3", play))

    settled_ats = 0
    settled_totals = 0
    settled_totals_v2 = 0
    settled_totals_v3 = 0
    for ds, items in sorted(unresolved_by_date.items()):
        gd = date.fromisoformat(ds)
        start, end = date_bounds(gd)
        data = http_json(CBBD_BASE + "/lines", {"season": SEASON, "startDateRange": start, "endDateRange": end}, key)
        if not isinstance(data, list):
            continue
        by_id = {str(g.get("gameId")): g for g in data}
        for kind, play in items:
            g = by_id.get(str(play["game_id"]))
            if not g:
                continue
            hs = g.get("homeScore")
            aas = g.get("awayScore")
            if hs is None or aas is None:
                continue
            try:
                hs = int(hs); aas = int(aas)
            except Exception:
                continue
            if hs == 0 and aas == 0:
                continue

            play["home_score"] = hs
            play["away_score"] = aas
            play["settled_at_pt"] = now_pt().isoformat(timespec="seconds")

            if kind == "ats":
                entry = float(play["entry_line"])
                margin = (hs - aas) if play["bet_side"] == "Home" else (aas - hs)
                ats = margin + entry
                result = "W" if ats > 0 else ("L" if ats < 0 else "P")
                play["ats_margin_selected_side"] = round(ats, 3)
                play["result"] = result
                play["profit_units"] = round(100/110, 6) if result == "W" else (-1.0 if result == "L" else 0.0)
                settled_ats += 1
            else:
                actual_total = hs + aas
                entry = float(play["entry_total"])
                raw_margin = actual_total - entry
                selected_margin = raw_margin if play["bet_side"] == "Over" else -raw_margin
                result = "W" if selected_margin > 0 else ("L" if selected_margin < 0 else "P")
                play["actual_total"] = actual_total
                play["total_margin_selected_side"] = round(selected_margin, 3)
                play["result"] = result
                play["profit_units"] = round(100/110, 6) if result == "W" else (-1.0 if result == "L" else 0.0)
                if kind == "total_v2":
                    settled_totals_v2 += 1
                elif kind == "total_v3":
                    settled_totals_v3 += 1
                else:
                    settled_totals += 1
    return settled_ats, settled_totals, settled_totals_v2, settled_totals_v3

def summarize(state: Dict[str, Any]) -> Dict[str, Any]:
    plays = list(state["plays"].values())
    entered = [p for p in plays if p.get("entry_line") is not None]
    done = [p for p in entered if p.get("result") in {"W", "L", "P"}]
    w = sum(p.get("result") == "W" for p in done)
    l = sum(p.get("result") == "L" for p in done)
    pushes = sum(p.get("result") == "P" for p in done)
    profit = sum(float(p.get("profit_units") or 0) for p in done)
    roi = profit / len(done) if done else None
    win_pct = w / (w + l) if w + l else None
    clvs = [safe_float(p.get("clv_proxy_points")) for p in entered]
    clvs = [x for x in clvs if x is not None]
    avg_clv = sum(clvs)/len(clvs) if clvs else None
    pos_clv = sum(x > 0 for x in clvs)/len(clvs) if clvs else None
    today = now_pt().date().isoformat()
    today_plays = [p for p in plays if p.get("game_date") == today]
    return {
        "tracked_candidates": len(plays),
        "entered_plays": len(entered),
        "completed_plays": len(done),
        "wins": w,
        "losses": l,
        "pushes": pushes,
        "win_pct": win_pct,
        "profit_units": round(profit, 4),
        "roi": roi,
        "avg_clv_proxy": avg_clv,
        "positive_clv_proxy_pct": pos_clv,
        "today_candidates": len(today_plays),
        "today_actionable": sum(p.get("entry_line") is not None for p in today_plays),
        "today_review": sum(p.get("entry_line") is None for p in today_plays),
    }



def summarize_totals(state: Dict[str, Any]) -> Dict[str, Any]:
    plays = list(state["totals_plays"].values())
    entered = [p for p in plays if p.get("entry_total") is not None]
    done = [p for p in entered if p.get("result") in {"W", "L", "P"}]
    w = sum(p.get("result") == "W" for p in done)
    l = sum(p.get("result") == "L" for p in done)
    pushes = sum(p.get("result") == "P" for p in done)
    profit = sum(float(p.get("profit_units") or 0) for p in done)
    roi = profit / len(done) if done else None
    win_pct = w / (w + l) if w + l else None

    reg_done = [p for p in entered if p.get("regulation_result") in {"W", "L", "P"}]
    rw = sum(p.get("regulation_result") == "W" for p in reg_done)
    rl = sum(p.get("regulation_result") == "L" for p in reg_done)
    rp = sum(p.get("regulation_result") == "P" for p in reg_done)
    reg_profit = sum(float(p.get("regulation_profit_units") or 0) for p in reg_done)
    reg_roi = reg_profit / len(reg_done) if reg_done else None
    reg_win_pct = rw / (rw + rl) if rw + rl else None
    ot_games = sum(bool(p.get("went_overtime")) for p in reg_done)
    ot_assisted = sum(bool(p.get("ot_assisted_win")) for p in reg_done)

    clvs = [safe_float(p.get("clv_proxy_points")) for p in entered]
    clvs = [x for x in clvs if x is not None]
    avg_clv = sum(clvs) / len(clvs) if clvs else None
    pos_clv = sum(x > 0 for x in clvs) / len(clvs) if clvs else None
    today = now_pt().date().isoformat()
    today_plays = [p for p in plays if p.get("game_date") == today]
    return {
        "tracked_candidates": len(plays),
        "entered_plays": len(entered),
        "completed_plays": len(done),
        "wins": w,
        "losses": l,
        "pushes": pushes,
        "win_pct": win_pct,
        "profit_units": round(profit, 4),
        "roi": roi,
        "regulation_audited_plays": len(reg_done),
        "regulation_wins": rw,
        "regulation_losses": rl,
        "regulation_pushes": rp,
        "regulation_win_pct": reg_win_pct,
        "regulation_profit_units": round(reg_profit, 4),
        "regulation_roi": reg_roi,
        "overtime_games": ot_games,
        "ot_assisted_wins": ot_assisted,
        "avg_clv_proxy": avg_clv,
        "positive_clv_proxy_pct": pos_clv,
        "today_candidates": len(today_plays),
        "today_actionable": sum(p.get("entry_total") is not None for p in today_plays),
    }


def summarize_totals_v2(state: Dict[str, Any]) -> Dict[str, Any]:
    plays = list(state["totals_v2_plays"].values())
    entered = [p for p in plays if p.get("entry_total") is not None]
    done = [p for p in entered if p.get("result") in {"W", "L", "P"}]
    w = sum(p.get("result") == "W" for p in done)
    l = sum(p.get("result") == "L" for p in done)
    pushes = sum(p.get("result") == "P" for p in done)
    profit = sum(float(p.get("profit_units") or 0) for p in done)
    roi = profit / len(done) if done else None
    win_pct = w / (w + l) if w + l else None

    reg_done = [p for p in entered if p.get("regulation_result") in {"W", "L", "P"}]
    rw = sum(p.get("regulation_result") == "W" for p in reg_done)
    rl = sum(p.get("regulation_result") == "L" for p in reg_done)
    rp = sum(p.get("regulation_result") == "P" for p in reg_done)
    reg_profit = sum(float(p.get("regulation_profit_units") or 0) for p in reg_done)
    reg_roi = reg_profit / len(reg_done) if reg_done else None
    reg_win_pct = rw / (rw + rl) if rw + rl else None

    clvs = [safe_float(p.get("clv_proxy_points")) for p in entered]
    clvs = [x for x in clvs if x is not None]
    avg_clv = sum(clvs) / len(clvs) if clvs else None
    today = now_pt().date().isoformat()
    today_plays = [p for p in plays if p.get("game_date") == today]
    return {
        "shadow_only": True,
        "tracked_candidates": len(plays),
        "entered_plays": len(entered),
        "completed_plays": len(done),
        "wins": w,
        "losses": l,
        "pushes": pushes,
        "win_pct": win_pct,
        "profit_units": round(profit, 4),
        "roi": roi,
        "regulation_wins": rw,
        "regulation_losses": rl,
        "regulation_pushes": rp,
        "regulation_win_pct": reg_win_pct,
        "regulation_roi": reg_roi,
        "overtime_games": sum(bool(p.get("went_overtime")) for p in reg_done),
        "ot_assisted_wins": sum(bool(p.get("ot_assisted_win")) for p in reg_done),
        "avg_clv_proxy": avg_clv,
        "today_candidates": len(today_plays),
        "today_trackable": sum(p.get("entry_total") is not None for p in today_plays),
    }


def summarize_totals_v3(state: Dict[str, Any]) -> Dict[str, Any]:
    plays = list(state["totals_v3_plays"].values())
    entered = [p for p in plays if p.get("entry_total") is not None]
    done = [p for p in entered if p.get("result") in {"W", "L", "P"}]
    w = sum(p.get("result") == "W" for p in done)
    l = sum(p.get("result") == "L" for p in done)
    pushes = sum(p.get("result") == "P" for p in done)
    profit = sum(float(p.get("profit_units") or 0) for p in done)
    roi = profit / len(done) if done else None
    win_pct = w / (w + l) if w + l else None

    reg_done = [p for p in entered if p.get("regulation_result") in {"W", "L", "P"}]
    rw = sum(p.get("regulation_result") == "W" for p in reg_done)
    rl = sum(p.get("regulation_result") == "L" for p in reg_done)
    rp = sum(p.get("regulation_result") == "P" for p in reg_done)
    reg_profit = sum(float(p.get("regulation_profit_units") or 0) for p in reg_done)
    reg_roi = reg_profit / len(reg_done) if reg_done else None
    reg_win_pct = rw / (rw + rl) if rw + rl else None

    clvs = [safe_float(p.get("clv_proxy_points")) for p in entered]
    clvs = [x for x in clvs if x is not None]
    avg_clv = sum(clvs) / len(clvs) if clvs else None
    today = now_pt().date().isoformat()
    today_plays = [p for p in plays if p.get("game_date") == today]
    return {
        "shadow_only": True,
        "experimental_over_thesis": True,
        "tracked_candidates": len(plays),
        "entered_plays": len(entered),
        "completed_plays": len(done),
        "wins": w,
        "losses": l,
        "pushes": pushes,
        "win_pct": win_pct,
        "profit_units": round(profit, 4),
        "roi": roi,
        "regulation_wins": rw,
        "regulation_losses": rl,
        "regulation_pushes": rp,
        "regulation_win_pct": reg_win_pct,
        "regulation_roi": reg_roi,
        "overtime_games": sum(bool(p.get("went_overtime")) for p in reg_done),
        "ot_assisted_wins": sum(bool(p.get("ot_assisted_win")) for p in reg_done),
        "avg_clv_proxy": avg_clv,
        "today_candidates": len(today_plays),
        "today_trackable": sum(p.get("entry_total") is not None for p in today_plays),
    }


def summarize_combined(state: Dict[str, Any]) -> Dict[str, Any]:
    ats_entered = [p for p in state["plays"].values() if p.get("entry_line") is not None]
    totals_entered = [p for p in state["totals_plays"].values() if p.get("entry_total") is not None]
    entered = ats_entered + totals_entered
    done = [p for p in entered if p.get("result") in {"W", "L", "P"}]

    w = sum(p.get("result") == "W" for p in done)
    l = sum(p.get("result") == "L" for p in done)
    pushes = sum(p.get("result") == "P" for p in done)
    profit = sum(float(p.get("profit_units") or 0) for p in done)
    roi = profit / len(done) if done else None
    win_pct = w / (w + l) if w + l else None

    clvs = [safe_float(p.get("clv_proxy_points")) for p in entered]
    clvs = [x for x in clvs if x is not None]
    avg_clv = sum(clvs) / len(clvs) if clvs else None
    pos_clv = sum(x > 0 for x in clvs) / len(clvs) if clvs else None

    today = now_pt().date().isoformat()
    ats_today = [p for p in ats_entered if p.get("game_date") == today]
    totals_today = [p for p in totals_entered if p.get("game_date") == today]

    return {
        "entered_plays": len(entered),
        "completed_plays": len(done),
        "wins": w,
        "losses": l,
        "pushes": pushes,
        "win_pct": win_pct,
        "profit_units": round(profit, 4),
        "roi": roi,
        "avg_clv_proxy": avg_clv,
        "positive_clv_proxy_pct": pos_clv,
        "today_actionable": len(ats_today) + len(totals_today),
        "ats_entered": len(ats_entered),
        "totals_entered": len(totals_entered),
    }

def main() -> int:
    target = now_pt().date().isoformat()
    if len(sys.argv) > 1 and sys.argv[1]:
        target = sys.argv[1]

    state = load_state()
    created = create_or_update_candidates(state, target)
    totals_created = create_or_update_totals_candidates(state, target)
    totals_v2_created = create_or_update_totals_v2_candidates(state, target)
    totals_v3_created = create_or_update_totals_v3_candidates(state, target)
    merge_current_snapshots(state)
    merge_current_totals_snapshots(state)
    merge_current_totals_v2_snapshots(state)
    merge_current_totals_v3_snapshots(state)

    for p in state["plays"].values():
        update_closing_proxy(p)
    for p in state["totals_plays"].values():
        update_totals_closing_proxy(p)
    for p in state["totals_v2_plays"].values():
        update_totals_closing_proxy(p)
    for p in state["totals_v3_plays"].values():
        update_totals_closing_proxy(p)

    settled = 0
    totals_settled = 0
    totals_v2_settled = 0
    totals_v3_settled = 0
    regulation_audited = 0
    key = os.environ.get("CBBD_API_KEY", "").strip()
    if key:
        settled, totals_settled, totals_v2_settled, totals_v3_settled = settle_unresolved(state, key)
        regulation_audited = backfill_totals_regulation_audit(state, key)
    else:
        print("WARNING: CBBD_API_KEY not set; result settlement skipped.")

    for p in state["plays"].values():
        update_closing_proxy(p)
    for p in state["totals_plays"].values():
        update_totals_closing_proxy(p)
    for p in state["totals_v2_plays"].values():
        update_totals_closing_proxy(p)
    for p in state["totals_v3_plays"].values():
        update_totals_closing_proxy(p)

    stamp = now_pt().isoformat(timespec="seconds")
    state["last_updated_pt"] = stamp
    state["status"] = "OK"
    state["summary"] = summarize(state)
    state["totals_summary"] = summarize_totals(state)
    state["totals_v2_summary"] = summarize_totals_v2(state)
    state["totals_v3_summary"] = summarize_totals_v3(state)
    state["combined_summary"] = summarize_combined(state)
    state["runs"].append({
        "timestamp_pt": stamp,
        "date": target,
        "new_candidates": created,
        "newly_settled": settled,
        "new_totals_candidates": totals_created,
        "newly_settled_totals": totals_settled,
        "new_totals_v2_shadow_candidates": totals_v2_created,
        "newly_settled_totals_v2_shadow": totals_v2_settled,
        "new_totals_v3_shadow_candidates": totals_v3_created,
        "newly_settled_totals_v3_shadow": totals_v3_settled,
        "new_regulation_audits": regulation_audited,
    })
    state["runs"] = state["runs"][-250:]

    text = json.dumps(state, indent=2, sort_keys=False)
    STATE_PATH.write_text(text + "\n", encoding="utf-8")
    SITE_STATE_PATH.write_text(text + "\n", encoding="utf-8")
    print(f"Dashboard sync: ATS {created} new / {settled} settled; Totals v1 {totals_created} new / {totals_settled} settled; Totals v2 shadow {totals_v2_created} new / {totals_v2_settled} settled; Totals v3 shadow {totals_v3_created} new / {totals_v3_settled} settled; regulation audits {regulation_audited}.")
    print(f"ATS tracked: {state['summary']['tracked_candidates']} | Totals v1 tracked: {state['totals_summary']['tracked_candidates']} | Totals v2 shadow tracked: {state['totals_v2_summary']['tracked_candidates']} | Totals v3 shadow tracked: {state['totals_v3_summary']['tracked_candidates']} | Combined entered: {state['combined_summary']['entered_plays']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
