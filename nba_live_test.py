#!/usr/bin/env python3
"""Prospective NBA model comparison; one-month paper experiment.

Records v1/v2 probabilities ONLY from scheduled games before their tipoffs,
freezes them, and grades ESPN final outcomes. No sportsbook endpoint calls,
no wagers and no market ROI invented.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timezone
from pathlib import Path

START = date(2026, 10, 26)
END = date(2026, 11, 26)  # Inclusive game dates, Eastern Time
LEDGER_PATH = Path(__file__).resolve().parent / "site" / "data" / "nba_live_test.json"
MAX_ROWS = 700

def parse_dt(v):
    return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc)

def good_probability(v):
    try:
        p=float(v)
        return p if math.isfinite(p) and 0<p<1 else None
    except (ValueError,TypeError):
        return None

def read_ledger(path=LEDGER_PATH):
    try:
        data=json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data,dict) or not isinstance(data.get("records",[]),list):
            raise ValueError("Invalid ledger structure")
        return data
    except FileNotFoundError:
        return {"schema_version":1,"records":[]}

def grade_row(row,game):
    if row["status"]!="OPEN" or game.get("status")!="Final":
        return False
    winner=game.get("winner")
    if winner not in (row["home_team"],row["away_team"]):
        return False
    row["status"]="FINAL"
    row["winner"]=winner
    row["away_won"]=bool(winner==row["away_team"])
    row["home_score"]=game.get("home_score")
    row["away_score"]=game.get("away_score")
    return True

def assess(rows,field):
    graded=[r for r in rows if r.get("status")=="FINAL" and
            good_probability(r.get(field)) is not None and
            isinstance(r.get("away_won"),bool)]
    if not graded:
        return {"predictions":sum(good_probability(r.get(field)) is not None for r in rows),
                "graded":0,"accuracy_pct":None,"brier":None,"log_loss":None}
    brier=0.0
    loss=0.0
    correct=0
    for r in graded:
        p=good_probability(r[field])
        y=int(r["away_won"])
        correct+=int((p>=.5)==bool(y))
        brier+=(p-y)**2
        loss-=y*math.log(p)+(1-y)*math.log(1-p)
    n=len(graded)
    return {"predictions":sum(good_probability(r.get(field)) is not None for r in rows),
            "graded":n,"accuracy_pct":round(100*correct/n,2),
            "brier":round(brier/n,5),"log_loss":round(loss/n,5)}

def paired_stats(rows):
    pair=[r for r in rows if r.get("status")=="FINAL" and
          good_probability(r.get("v1_away_win_probability")) is not None and
          good_probability(r.get("v2_away_win_probability")) is not None]
    return {"same_games":len(pair),
            "v1":assess(pair,"v1_away_win_probability"),
            "v2":assess(pair,"v2_away_win_probability")}

def refresh_ledger(games, existing, now, odds_status=None):
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    old=existing if isinstance(existing,dict) else {"records":[]}
    records={str(p["game_id"]):dict(p) for p in old.get("records",[])
             if isinstance(p,dict) and p.get("game_id")}
    all_games={str(g["game_id"]):g for g in games if g.get("game_id")}

    # Never alter the first forecast or model IDs for an existing record.
    graded_count=0
    for gid,row in records.items():
        game=all_games.get(gid)
        if game and grade_row(row,game):
            graded_count+=1

    # V2 can first become eligible AFTER V1 was recorded, but still before tip.
    # Freeze the first independently observed V2 pregame forecast, never alter V1,
    # and never create a V2 forecast retrospectively for a started/final game.
    newly_added_v2=0
    for gid,row in records.items():
        if row.get("status")!="OPEN" or good_probability(row.get("v2_away_win_probability")) is not None:
            continue
        game=all_games.get(gid)
        if not game or game.get("status")!="Scheduled":
            continue
        try:
            tip=parse_dt(row["start_utc"])
        except (ValueError,TypeError,KeyError):
            continue
        if now>=tip or game.get("start_utc")!=row.get("start_utc"):
            continue
        if game.get("home_team")!=row.get("home_team") or game.get("away_team")!=row.get("away_team"):
            continue
        p2=good_probability(game.get("model_v2_away_win_probability"))
        if p2 is None or not game.get("model_v2_id"):
            continue
        row["v2_away_win_probability"]=p2
        row["v2_model_id"]=game["model_v2_id"]
        row["v2_eligible_at_snapshot"]=False
        row["v2_first_seen_utc"]=now.astimezone(timezone.utc).isoformat().replace("+00:00","Z")
        newly_added_v2+=1

    new_count=0
    for g in sorted(games,key=lambda t:(t.get("start_utc",""),str(t.get("game_id","")))):
        gid=str(g.get("game_id",""))
        if not gid or gid in records or len(records)>=MAX_ROWS:
            continue
        try:
            game_day=date.fromisoformat(g["game_date"])
            tip=parse_dt(g["start_utc"])
        except (ValueError,KeyError,TypeError):
            continue
        if game_day<START or game_day>END or g.get("status")!="Scheduled" or now>=tip:
            continue
        v1=good_probability(g.get("model_away_win_probability"))
        if v1 is None:
            continue
        v2=good_probability(g.get("model_v2_away_win_probability"))
        records[gid]={
            "game_id":gid,"game_date":g["game_date"],"start_utc":g["start_utc"],
            "first_seen_utc":now.astimezone(timezone.utc).isoformat().replace("+00:00","Z"),
            "home_team":g["home_team"],"away_team":g["away_team"],
            "v1_model_id":g.get("model_id"),
            "v1_away_win_probability":v1,
            "v2_model_id":g.get("model_v2_id") if v2 is not None else None,
            "v2_away_win_probability":v2,
            "v2_eligible_at_snapshot":v2 is not None,
            "v2_first_seen_utc":now.astimezone(timezone.utc).isoformat().replace("+00:00","Z") if v2 is not None else None,
            "status":"OPEN","winner":None,"away_won":None,
            "home_score":None,"away_score":None,
            "betting_status":"NO BET - PAPER MODEL EVALUATION",
        }
        new_count+=1

    rows=sorted(records.values(),key=lambda p:(p["game_date"],p["start_utc"],p["game_id"]),reverse=True)
    if len(rows)>MAX_ROWS:
        raise ValueError("NBA test ledger exceeds protective row limit")
    return {
        "schema_version":1,"experiment":"NBA v1 vs v2 untouched prospective paper trial",
        "start_game_date":START.isoformat(),"end_game_date_inclusive":END.isoformat(),
        "updated_at":now.astimezone(timezone.utc).isoformat().replace("+00:00","Z"),
        "status":"ACTIVE" if now.date()<=END else "COMPLETED_WINDOW",
        "protocol":"Freeze V1 at first pretip scan; independently freeze V2 at its first eligible pretip scan, possibly later. Never backfill started games or retune. No paid odds required.",
        "records":rows,
        "summary":{
            "games_recorded":len(rows),
            "games_final":sum(r["status"]=="FINAL" for r in rows),
            "games_open":sum(r["status"]=="OPEN" for r in rows),
            "v1":assess(rows,"v1_away_win_probability"),
            "v2":assess(rows,"v2_away_win_probability"),
            "paired":paired_stats(rows),
            "betting_plays":0,
            "odds_status":odds_status or "NOT_REQUESTED_FOR_TRIAL",
            "last_run_new":new_count,
            "last_run_new_v2_on_existing":newly_added_v2,
            "last_run_graded":graded_count
        }
    }

def save_ledger(data,path=LEDGER_PATH):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,indent=2,sort_keys=True)+"\n",encoding="utf-8")
