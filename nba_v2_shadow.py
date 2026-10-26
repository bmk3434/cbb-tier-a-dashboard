#!/usr/bin/env python3
"""Keyless live NBA v2 challenger from prior team games; shadow only.

Downloads BoxScore Lab's free daily team-game CSV. Uses same prior-ten feature
recipe as the independently trained v2 artifact, but estimated ratings/pace
are proxies and v2 forecast remains unvalidated for sportsbook betting.
"""
from __future__ import annotations
from collections import defaultdict, deque
from datetime import date
import csv
import io
import json
import math
from pathlib import Path
from urllib.request import Request, urlopen

from nba_feed import TEAM_NAMES

ROOT=Path(__file__).resolve().parent
MODEL_PATH=ROOT/"nba_model_v2_challenger.json"
SOURCE="https://boxscorelab.com/downloads/2026-27-team-game-results.csv"
MIN_PREVIOUS_CURRENT_SEASON_GAMES=5

def fetch_stats() -> list[dict]:
    request=Request(SOURCE,headers={"User-Agent":"Mozilla/5.0 BasketballModelResearch/2.0"})
    with urlopen(request,timeout=35) as f:
        csv_text=f.read().decode("utf-8-sig")
    reader=csv.DictReader(io.StringIO(csv_text))
    required={"season","team","game_date","opponent","result","field_goal_attempts",
              "three_point_attempts","free_throws_made","free_throw_attempts",
              "offensive_rebounds","defensive_rebounds","turnovers",
              "effective_fg_pct","off_rating_est","def_rating_est"}
    if not required.issubset(set(reader.fieldnames or [])):
        raise ValueError("Upstream NBA boxscore columns changed; no silent fallback")
    return [r for r in reader if r.get("season")=="2026-27" and
            r.get("result") in ("W","L")]

def row_stats(row:dict,pairs:dict,defaults:dict)->dict:
    def n(k):
        try:return float(row[k])
        except (KeyError,ValueError,TypeError):return float("nan")
    fga=n("field_goal_attempts");fta=n("free_throw_attempts")
    tov=n("turnovers");orb=n("offensive_rebounds");drb=n("defensive_rebounds")
    other=pairs.get((row["game_date"],row["opponent"],row["team"]))
    opp_drb=float(other.get("defensive_rebounds",0)) if other else 0
    poss=fga-orb+tov+0.44*fta
    if not math.isfinite(poss) or poss<=0:raise ValueError("Invalid possession estimate")
    three=n("three_point_attempts")/fga if fga else defaults["three_pa_rate"]
    ft_pct=n("free_throws_made")/fta if fta else defaults["ft_pct"]
    orebound=orb/(orb+opp_drb) if orb+opp_drb>0 else defaults["oreb_pct"]
    points=float(row.get("points") or 0)
    if points<=0:raise ValueError("Unverified box-score points")
    def ok(v,baseline):
        return v if math.isfinite(v) else baseline
    return {
       "off_rating":ok(n("off_rating_est"),defaults["off_rating"]),
       "def_rating":ok(n("def_rating_est"),defaults["def_rating"]),
       "pace":ok(poss,defaults["pace"]), # approximate single-game possessions
       "efg_pct":ok(n("effective_fg_pct"),defaults["efg_pct"]),
       "tm_tov_pct":ok(tov/poss,defaults["tm_tov_pct"]),
       "three_pa_rate":ok(three,defaults["three_pa_rate"]),
       "ft_pct":ok(ft_pct,defaults["ft_pct"]),
       "oreb_pct":ok(orebound,defaults["oreb_pct"]),
       "win":1.0 if row["result"]=="W" else 0.0
    }

def build_team_history(rows:list[dict], defaults:dict)->dict:
    by_team=defaultdict(lambda:deque(maxlen=10))
    pairs={(r["game_date"],r["team"],r["opponent"]):r for r in rows}
    for row in sorted(rows,key=lambda x:(x["game_date"],x["team"],x["opponent"])):
        if row["team"] not in TEAM_NAMES:continue
        # Only complete games before today's match date can be features.
        st=row_stats(row,pairs,defaults)
        by_team[row["team"]].append({"date":row["game_date"],"stats":st})
    return by_team

def predict_pair(g, history, model):
    if g.get("model_away_win_probability") is None:
        return None
    preferred={"Golden State Warriors":"GSW","New Orleans Pelicans":"NOP",
               "New York Knicks":"NYK","San Antonio Spurs":"SAS",
               "Utah Jazz":"UTA","Washington Wizards":"WAS"}
    home=preferred.get(g["home_team"]) or next((a for a,n in TEAM_NAMES.items() if n==g["home_team"]),None)
    away=preferred.get(g["away_team"]) or next((a for a,n in TEAM_NAMES.items() if n==g["away_team"]),None)
    if home is None or away is None:return None
    # Remove same-day and future source rows, preserving strictly pregame info.
    hh=[entry["stats"] for entry in history[home] if entry["date"]<g["game_date"]]
    ah=[entry["stats"] for entry in history[away] if entry["date"]<g["game_date"]]
    n=min(len(hh),len(ah))
    if n<MIN_PREVIOUS_CURRENT_SEASON_GAMES:return None
    p_home=1-float(g["model_away_win_probability"])
    p_home=min(.99999,max(.00001,p_home))
    x=[math.log(p_home/(1-p_home))]
    for key in ("off_rating","def_rating","pace","efg_pct","tm_tov_pct",
                "three_pa_rate","ft_pct","oreb_pct","win"):
        h=sum(v[key] for v in hh)/len(hh)
        a=sum(v[key] for v in ah)/len(ah)
        x.append((h-a)/float(model["rolling_scales"][key]))
    x.append(n/10)
    if len(x)!=len(model["features"]):raise ValueError("v2 historical / live feature mismatch")
    z=float(model["logistic_intercept"])
    for v,mean,scale,coef in zip(x,model["scaler_mean"],model["scaler_scale"],model["logistic_coef"]):
        z+=float(coef)*(v-float(mean))/float(scale)
    home_p=1/(1+math.exp(-max(-30,min(30,z))))
    return {"model_v2_id":model["model_id"],
            "model_v2_away_win_probability":round(1-home_p,5),
            "model_v2_home_win_probability":round(home_p,5),
            "model_v2_confidence":40,
            "model_v2_market_validated":False,
            "model_v2_status":"EARLY PROSPECTIVE SHADOW - UNVALIDATED VS ODDS",
            "model_v2_source":"Free BoxScore Lab game logs; estimated possession/ratings proxies",
            "model_v2_min_prior_games":n}

def enrich_v2(games:list[dict])->dict:
    try:
        model=json.loads(MODEL_PATH.read_text(encoding="utf-8"))
        if not model.get("status","").startswith("EXPERIMENTAL SHADOW") or model.get("promotion_allowed") is not False:
            raise ValueError("V2 model must remain shadow-only")
        rows=fetch_stats()
        history=build_team_history(rows,model["rolling_defaults"])
        count=0
        for g in games:
            if g.get("status")!="Scheduled":continue
            result=predict_pair(g,history,model)
            if result:
                g.update(result)
                count+=1
        return {"status":"OK","games_with_v2_predictions":count,
                "source_completed_team_games":len(rows),
                "min_prior_games_per_team":MIN_PREVIOUS_CURRENT_SEASON_GAMES,
                "source":"BoxScore Lab daily team-game CSV (no odds API credits)",
                "market_edge_validated":False}
    except Exception as exc:
        return {"status":"SOURCE_UNAVAILABLE","reason":str(exc)[:190],
                "games_with_v2_predictions":0,"market_edge_validated":False}
