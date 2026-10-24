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
                obj.setdefault("runs", [])
                obj.setdefault("version", 1)
                return obj
        except Exception:
            pass
    return {"version": 1, "last_updated_pt": None, "status": "New", "plays": {}, "runs": []}


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


def settle_unresolved(state: Dict[str, Any], key: str) -> int:
    unresolved_by_date: Dict[str, List[Dict[str, Any]]] = {}
    today = now_pt().date()
    for play in state["plays"].values():
        if play.get("result"):
            continue
        if play.get("entry_line") is None:
            continue
        try:
            gd = date.fromisoformat(play["game_date"])
        except Exception:
            continue
        if gd > today:
            continue
        unresolved_by_date.setdefault(play["game_date"], []).append(play)

    settled = 0
    for ds, plays in sorted(unresolved_by_date.items()):
        gd = date.fromisoformat(ds)
        start, end = date_bounds(gd)
        data = http_json(CBBD_BASE + "/lines", {"season": SEASON, "startDateRange": start, "endDateRange": end}, key)
        if not isinstance(data, list):
            continue
        by_id = {str(g.get("gameId")): g for g in data}
        for play in plays:
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
            entry = float(play["entry_line"])
            margin = (hs - aas) if play["bet_side"] == "Home" else (aas - hs)
            ats = margin + entry
            result = "W" if ats > 0 else ("L" if ats < 0 else "P")
            play["home_score"] = hs
            play["away_score"] = aas
            play["ats_margin_selected_side"] = round(ats, 3)
            play["result"] = result
            play["profit_units"] = round(100/110, 6) if result == "W" else (-1.0 if result == "L" else 0.0)
            play["settled_at_pt"] = now_pt().isoformat(timespec="seconds")
            settled += 1
    return settled


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


def main() -> int:
    target = now_pt().date().isoformat()
    if len(sys.argv) > 1 and sys.argv[1]:
        target = sys.argv[1]
    state = load_state()
    created = create_or_update_candidates(state, target)
    merge_current_snapshots(state)
    for p in state["plays"].values():
        update_closing_proxy(p)

    settled = 0
    key = os.environ.get("CBBD_API_KEY", "").strip()
    if key:
        settled = settle_unresolved(state, key)
    else:
        print("WARNING: CBBD_API_KEY not set; result settlement skipped.")

    for p in state["plays"].values():
        update_closing_proxy(p)

    stamp = now_pt().isoformat(timespec="seconds")
    state["last_updated_pt"] = stamp
    state["status"] = "OK"
    state["summary"] = summarize(state)
    state["runs"].append({"timestamp_pt": stamp, "date": target, "new_candidates": created, "newly_settled": settled})
    state["runs"] = state["runs"][-250:]

    text = json.dumps(state, indent=2, sort_keys=False)
    STATE_PATH.write_text(text + "\n", encoding="utf-8")
    SITE_STATE_PATH.write_text(text + "\n", encoding="utf-8")
    print(f"Dashboard sync: {created} new candidates, {settled} newly settled.")
    print(f"Tracked: {state['summary']['tracked_candidates']} | entered: {state['summary']['entered_plays']} | completed: {state['summary']['completed_plays']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
