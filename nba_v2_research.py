#!/usr/bin/env python3
"""NBA v2 shadow challenger, leak-free development on free historical game logs.

Train 2016-17..2023-24, choose ridge C on 2024-25 validation,
evaluate once on untouched 2025-26 holdout, compare market on same games.
This program does not query The Odds API and cannot alter frozen v1.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen
import json
import math

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import brier_score_loss, log_loss, accuracy_score

from nba_feed import TEAM_NAMES
from nba_predictor import read_model

ROOT=Path(__file__).resolve().parent
GAME_LOG_URL="https://raw.githubusercontent.com/llimllib/nba_data/main/data/gamelogs.parquet"
HISTORICAL_ODDS_URL="https://raw.githubusercontent.com/nikhilNathwani/NBA-Moneyline/main/tableau/games.csv"
V2_PATH=ROOT/"nba_model_v2_challenger.json"

STATS=["off_rating","def_rating","pace","efg_pct","tm_tov_pct","three_pa_rate","ft_pct","oreb_pct","win"]
DEFAULTS={"off_rating":111.0,"def_rating":111.0,"pace":99.0,"efg_pct":0.535,
          "tm_tov_pct":0.135,"three_pa_rate":0.40,"ft_pct":0.77,"oreb_pct":0.275,"win":0.50}
SCALES={"off_rating":20.0,"def_rating":20.0,"pace":15.0,"efg_pct":0.15,
        "tm_tov_pct":0.10,"three_pa_rate":0.22,"ft_pct":0.20,"oreb_pct":0.16,"win":0.42}
FEATURES=["v1_pregame_logit"]+["prior_10_"+n+"_home_minus_away" for n in STATS]+["min_prior_games_over_10"]

def get_bytes(url):
    req=Request(url,headers={"User-Agent":"BasketballResearch/2.0","Accept":"application/octet-stream"})
    with urlopen(req,timeout=90) as response:
        return response.read()

def read_inputs():
    raw=get_bytes(GAME_LOG_URL)
    logs=pd.read_parquet(BytesIO(raw))
    odds=pd.read_csv(BytesIO(get_bytes(HISTORICAL_ODDS_URL)))
    print("SOURCE game logs:",len(raw),"bytes;",len(logs),"team-game rows")
    return logs,odds

def select_games(df):
    df=df.copy()
    df["game_id"]=df["game_id"].astype(str).str.zfill(10)
    df=df[df["game_id"].str.startswith("002")].copy() # exclude play-in, playoff, all-star
    df["season_start"]=df["season_year"].str.slice(0,4).astype(int)
    df=df[df.season_start.between(2016,2025)].copy()
    df["date"]=pd.to_datetime(df["game_date"]).dt.strftime("%Y-%m-%d")
    df=df[df["wl"].isin(["W","L"])]
    df=df.sort_values(["date","game_id","team_abbreviation"])
    # Only complete, unambiguous home/away pairs; no playoff contamination.
    recs=[]
    for gid,grp in df.groupby("game_id",sort=False):
        if len(grp)!=2: continue
        ht=grp[grp["matchup"].astype(str).str.contains("vs.",regex=False)]
        at=grp[grp["matchup"].astype(str).str.contains("@",regex=False)]
        if len(ht)!=1 or len(at)!=1:continue
        h,a=ht.iloc[0],at.iloc[0]
        if h["season_start"]!=a["season_start"] or h["date"]!=a["date"]:continue
        if TEAM_NAMES.get(h["team_abbreviation"]) is None or TEAM_NAMES.get(a["team_abbreviation"]) is None:continue
        if (h["wl"]=="W")==(a["wl"]=="W"): continue
        recs.append((h,a))
    recs.sort(key=lambda t:(t[0]["date"],t[0]["game_id"]))
    print("SELECTED regular season games:",len(recs),
          "by season:",dict(pd.Series([x[0]["season_start"] for x in recs]).value_counts().sort_index()))
    return recs

def score_row(row):
    def finite(v,default):
        try:
            n=float(v)
            return n if np.isfinite(n) else default
        except (TypeError,ValueError):
            return default
    fga=finite(row["fga"],0.0)
    return {
        "off_rating":finite(row["off_rating"],DEFAULTS["off_rating"]),
        "def_rating":finite(row["def_rating"],DEFAULTS["def_rating"]),
        "pace":finite(row["pace"],DEFAULTS["pace"]),
        "efg_pct":finite(row["efg_pct"],DEFAULTS["efg_pct"]),
        "tm_tov_pct":finite(row["tm_tov_pct"],DEFAULTS["tm_tov_pct"]),
        "three_pa_rate":finite(row["fg3a"],0.0)/fga if fga>0 else DEFAULTS["three_pa_rate"],
        "ft_pct":finite(row["ft_pct"],DEFAULTS["ft_pct"]),
        "oreb_pct":finite(row["oreb_pct"],DEFAULTS["oreb_pct"]),
        "win":1.0 if row["wl"]=="W" else 0.0,
    }

def build_examples(recs, v1):
    # Ratings are updated exactly after completed games, and season histories
    # are rolled after the current matchup's features are generated.
    elo={}
    history=defaultdict(lambda:deque(maxlen=10))
    current_season=None
    examples=[]
    for home,away in recs:
        season=int(home["season_start"])
        if season!=current_season:
            if current_season is not None:
                elo={team:1500+float(v1["elo_season_reversion"])*(rating-1500)
                     for team,rating in elo.items()}
            history=defaultdict(lambda:deque(maxlen=10))
            current_season=season
        ht,at=home["team_abbreviation"],away["team_abbreviation"]
        hr=elo.get(ht,1500.0);ar=elo.get(at,1500.0)
        hh=history[ht];ah=history[at]
        hdate=pd.Timestamp(home["date"])
        last_home=hh[-1]["date"] if hh else None
        last_away=ah[-1]["date"] if ah else None
        home_b2b=(hdate-last_home).days==1 if last_home is not None else False
        away_b2b=(hdate-last_away).days==1 if last_away is not None else False
        xb=[1,(hr-ar)/400,int(home_b2b),int(away_b2b)]
        z=float(np.dot(xb,v1["logistic_coef"]))
        vals=[z]
        for key in STATS:
            hv=np.mean([q["stats"][key] for q in hh]) if hh else DEFAULTS[key]
            av=np.mean([q["stats"][key] for q in ah]) if ah else DEFAULTS[key]
            vals.append((hv-av)/SCALES[key])
        vals.append(min(len(hh),len(ah),10)/10.0)
        y=1 if home["wl"]=="W" else 0
        p1=1/(1+math.exp(-z))
        examples.append({"season_start":season,"date":home["date"],"game_id":home["game_id"],
                         "home_team":TEAM_NAMES[ht],"away_team":TEAM_NAMES[at],
                         "home_abbrev":ht,"away_abbrev":at,"x":vals,"y":y,"v1":p1})
        base=1/(1+10**(-(hr-ar+v1["elo_home_advantage_points"])/400))
        delta=v1["elo_k"]*(y-base)
        elo[ht]=hr+delta;elo[at]=ar-delta
        hs=score_row(home);ass=score_row(away)
        hh.append({"date":hdate,"stats":hs})
        ah.append({"date":hdate,"stats":ass})
    return examples,history

def market_prices(odds):
    odds=odds[odds.Location=="Home"]
    mapping={}
    def implied(ml):
        ml=float(ml)
        return -ml/(100-ml) if ml<0 else 100/(ml+100)
    for t in odds.itertuples(index=False):
        try:
            win_ml=float(getattr(t,"_4"))  # pandas may rename spaces
            lose_ml=float(getattr(t,"_5"))
        except (ValueError,TypeError,AttributeError):
            # deterministic fallback based on raw column positions (below)
            continue
    for fields in odds.itertuples(index=False,name=None):
        team,season,_,_,win_ml,lose_ml,opponent,date,location=fields[:9]
        try:
            h=implied(win_ml);a=implied(lose_ml)
            mapping[(str(date)[:10],str(team),str(opponent))]=h/(h+a)
        except (ValueError,TypeError,ZeroDivisionError):
            pass
    return mapping

def metrics(es,pred,benchmark=None):
    if not len(es):return None
    p=np.asarray(pred,dtype=float);y=np.asarray([e["y"] for e in es],dtype=int)
    result={"games":len(es),"accuracy_pct":round(100*accuracy_score(y,p>=.5),3),
            "brier":round(brier_score_loss(y,p),5),
            "log_loss":round(log_loss(y,p,labels=[0,1]),5)}
    if benchmark is not None:
        result["market_coverage"]=int(np.isfinite(benchmark).sum())
    return result

def main():
    logs,odds=read_inputs()
    v1=read_model()
    examples,_=build_examples(select_games(logs),v1)
    train=[e for e in examples if e["season_start"]<=2023]
    val=[e for e in examples if e["season_start"]==2024]
    hold=[e for e in examples if e["season_start"]==2025]
    if len(train)<8500 or len(val)<1100 or len(hold)<1100:
        raise ValueError("Missing a complete NBA chronological training/validation/holdout split")
    Xtr=np.asarray([e["x"] for e in train]);ytr=np.asarray([e["y"] for e in train])
    Xv=np.asarray([e["x"] for e in val]);yv=np.asarray([e["y"] for e in val])
    Xt=np.asarray([e["x"] for e in hold]);yt=np.asarray([e["y"] for e in hold])
    scaler=StandardScaler().fit(Xtr)
    candidates=[]
    for c in (0.03,0.1,0.3,1.0):
        classifier=LogisticRegression(C=c,max_iter=1000,solver="lbfgs").fit(scaler.transform(Xtr),ytr)
        p=classifier.predict_proba(scaler.transform(Xv))[:,1]
        candidates.append((float(brier_score_loss(yv,p)),c,classifier))
    candidates.sort(key=lambda v:(v[0],v[1]))
    selected_brier,c,model=candidates[0]
    # HOLDOUT is scored ONCE after C is selected solely on 2024-25.
    pred_train=model.predict_proba(scaler.transform(Xtr))[:,1]
    pred_val=model.predict_proba(scaler.transform(Xv))[:,1]
    pred_hold=model.predict_proba(scaler.transform(Xt))[:,1]
    p1_train=np.asarray([e["v1"] for e in train])
    p1_val=np.asarray([e["v1"] for e in val])
    p1_hold=np.asarray([e["v1"] for e in hold])
    odds_map=market_prices(odds)
    hold_market=np.asarray([odds_map.get((e["date"],e["home_team"],e["away_team"]),np.nan) for e in hold])
    mask=np.isfinite(hold_market)
    if mask.sum()<1050:
        raise ValueError("Market matched only "+str(mask.sum())+" of "+str(len(hold))+" holdout games")
    hold_matched=[e for e,k in zip(hold,mask) if k]
    results={
      "train":{"v1":metrics(train,p1_train),"v2":metrics(train,pred_train)},
      "validation":{"v1":metrics(val,p1_val),"v2":metrics(val,pred_val)},
      "holdout":{"v1":metrics(hold,p1_hold),"v2":metrics(hold,pred_hold)},
      "holdout_matched_market":{
          "v1":metrics(hold_matched,p1_hold[mask]),
          "v2":metrics(hold_matched,pred_hold[mask]),
          "market_no_vig":metrics(hold_matched,hold_market[mask])
      }
    }
    artifact={
      "model_id":"nba-log-rolling-four-factors-v2-2026-10-26",
      "status":"EXPERIMENTAL SHADOW ONLY - DO NOT BET AUTOMATICALLY",
      "generated_at_utc":datetime.now(timezone.utc).isoformat(),
      "source":"llimllib/nba_data, data/gamelogs.parquet (regular-season logs only)",
      "market_source":"nikhilNathwani/NBA-Moneyline tableau/games.csv historical bookmaker-average",
      "seasons_train":"2016-17 through 2023-24",
      "season_validation":"2024-25",
      "season_untouched_holdout":"2025-26",
      "features":FEATURES,
      "rolling_window":10,
      "rolling_defaults":DEFAULTS,
      "rolling_scales":SCALES,
      "random_seed":0,
      "selected_l2_C":c,
      "validation_candidates":[{"C":a,"validation_brier":round(b,6)} for b,a,_ in candidates],
      "scaler_mean":scaler.mean_.tolist(),
      "scaler_scale":scaler.scale_.tolist(),
      "logistic_coef":model.coef_[0].tolist(),
      "logistic_intercept":float(model.intercept_[0]),
      "metrics":results,
      "holdout_matched_games":int(mask.sum()),
      "v2_beats_v1_holdout_brier":bool(results["holdout"]["v2"]["brier"]<results["holdout"]["v1"]["brier"]),
      "v2_beats_market_holdout_brier":bool(results["holdout_matched_market"]["v2"]["brier"]<results["holdout_matched_market"]["market_no_vig"]["brier"]),
      "confidence_status":"NOT VALIDATED AGAINST MARKET",
      "promotion_allowed":False,
      "injury_feature_included":False,
      "notes":["Strict per-team prior-ten-game features; current game added only after pregame projection.",
               "All regular-season games only; excludes 004 playoff and other non-regular IDs.",
               "Hyperparameter selection uses 2024-25 validation only.",
               "Market odds are reference outcomes, not predictive features.",
               "No verified lineup/injury availability; do not infer or fabricate player data.",
               "2026-27 live predictions require at least five completed current-season games per team and refreshed free game logs."]
    }
    print("V2 VALIDATION CANDIDATES",json.dumps(artifact["validation_candidates"]))
    print("V2 RESULTS",json.dumps(results,sort_keys=True))
    print("V2 HOLDOUT MARKET COVERAGE",int(mask.sum()),"/",len(hold))
    V2_PATH.write_text(json.dumps(artifact,indent=2,sort_keys=True)+"\n",encoding="utf8")
    print("WROTE",str(V2_PATH),V2_PATH.stat().st_size)

if __name__=="__main__":
    main()
