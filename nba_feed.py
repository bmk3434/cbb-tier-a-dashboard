#!/usr/bin/env python3
"""NBA research feed: ESPN calendar/results + optional The Odds API h2h quotes.

Produces only observational research data. No probabilities are inferred from prices,
no bets are placed, and the frozen CBB models and state remain untouched.
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from nba_predictor import read_model, advance_state, enrich_upcoming

ROOT = Path(__file__).resolve().parent
FEED = ROOT / "site" / "data" / "nba_plays.json"
ET = ZoneInfo("America/New_York")
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
ODDS = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds/"
TEAM_NAMES = {
 "ATL":"Atlanta Hawks","BOS":"Boston Celtics","BKN":"Brooklyn Nets",
 "CHA":"Charlotte Hornets","CHI":"Chicago Bulls","CLE":"Cleveland Cavaliers",
 "DAL":"Dallas Mavericks","DEN":"Denver Nuggets","DET":"Detroit Pistons",
 "GS":"Golden State Warriors","GSW":"Golden State Warriors",
 "HOU":"Houston Rockets","IND":"Indiana Pacers","LAC":"Los Angeles Clippers",
 "LAL":"Los Angeles Lakers","MEM":"Memphis Grizzlies","MIA":"Miami Heat",
 "MIL":"Milwaukee Bucks","MIN":"Minnesota Timberwolves",
 "NO":"New Orleans Pelicans","NOP":"New Orleans Pelicans",
 "NY":"New York Knicks","NYK":"New York Knicks","OKC":"Oklahoma City Thunder",
 "ORL":"Orlando Magic","PHI":"Philadelphia 76ers","PHX":"Phoenix Suns",
 "POR":"Portland Trail Blazers","SAC":"Sacramento Kings",
 "SA":"San Antonio Spurs","SAS":"San Antonio Spurs","TOR":"Toronto Raptors",
 "UTA":"Utah Jazz","UTAH":"Utah Jazz","WAS":"Washington Wizards",
 "WSH":"Washington Wizards"
}
ALIAS = {v.casefold():v for v in TEAM_NAMES.values()}
ALIAS.update({"la clippers":"Los Angeles Clippers","los angeles clippers":"Los Angeles Clippers",
              "ny knicks":"New York Knicks","golden state":"Golden State Warriors",
              "new orleans":"New Orleans Pelicans"})

def now_utc():
    return datetime.now(timezone.utc)

def timestamp(value):
    return datetime.fromisoformat(str(value).replace("Z","+00:00")).astimezone(timezone.utc)

def request_json(url, headers=None, tries=3):
    hdr = {"User-Agent":"BasketballResearchDashboard/1.0", "Accept":"application/json"}
    hdr.update(headers or {})
    for attempt in range(tries):
        try:
            with urlopen(Request(url, headers=hdr), timeout=22) as reply:
                return json.load(reply)
        except (HTTPError, URLError, TimeoutError, ValueError, OSError):
            if attempt == tries-1:
                raise
            time.sleep(1+attempt)

def parse_game(event):
    # ESPN NBA regular season: season.type == 2. Ignore preseason and playoffs.
    season_type = event.get("season",{}).get("type")
    if season_type is not None and int(season_type)!=2:
        return None
    comps = event.get("competitions") or []
    if not comps or not event.get("id"):
        return None
    c = comps[0]
    by_side = {x.get("homeAway"):x for x in c.get("competitors",[])}
    if "home" not in by_side or "away" not in by_side:
        return None
    def team(side):
        t = by_side[side].get("team") or {}
        abbrev = (t.get("abbreviation") or "").upper()
        return TEAM_NAMES.get(abbrev) or ALIAS.get(str(t.get("displayName") or "").casefold())
    home, away = team("home"), team("away")
    if not home or not away:
        return None
    when = timestamp(c.get("date") or event.get("date"))
    status_obj = c.get("status") or event.get("status") or {}
    status_type = status_obj.get("type") or {}
    completed = status_type.get("completed") is True
    name = (status_type.get("name") or "").upper()
    status = "Final" if completed else ("Live" if name in {"STATUS_IN_PROGRESS","STATUS_HALFTIME","STATUS_END_PERIOD"} else "Scheduled")
    def points(side):
        val = by_side[side].get("score")
        if isinstance(val, dict):
            val = val.get("value") or val.get("displayValue")
        try:
            return int(float(val)) if val is not None and val!="" else None
        except (TypeError, ValueError):
            return None
    hp, ap = points("home"), points("away")
    winner = (home if hp>ap else away if ap>hp else None) if completed and hp is not None and ap is not None else None
    return {"game_id":str(event["id"]), "game_date":when.astimezone(ET).date().isoformat(),
            "start_utc":when.isoformat().replace("+00:00","Z"),"home_team":home,
            "away_team":away,"home_score":hp,"away_score":ap,
            "status":status,"winner":winner}

def read_calendar(day):
    query = urlencode({"dates":day.strftime("%Y%m%d"),"limit":100})
    payload = request_json(ESPN+"?"+query)
    events = payload.get("events")
    if not isinstance(events,list):
        raise ValueError("ESPN scoreboard lacked an events array for "+str(day))
    return events

def calendar(anchor):
    collected = {}
    for days in range(-7,3):
        target = anchor+timedelta(days=days)
        for event in read_calendar(target):
            parsed = parse_game(event)
            if parsed:
                collected[parsed["game_id"]] = parsed
    return sorted(collected.values(), key=lambda x:(x["start_utc"],x["game_id"]))

def annotate(games):
    by_team = defaultdict(list)
    for game in games:
        by_team[game["home_team"]].append(game)
        by_team[game["away_team"]].append(game)
    prev = {}
    for team, series in by_team.items():
        series.sort(key=lambda x:x["start_utc"])
        for i,g in enumerate(series):
            before = series[i-1] if i>0 else None
            if before is None:
                prev[team,g["game_id"]] = None
            else:
                next_day = (date.fromisoformat(g["game_date"])-date.fromisoformat(before["game_date"])).days==1
                if next_day:
                    result = ("Win" if before["winner"]==team else
                              "Loss" if before["winner"] is not None else None)
                    prev[team,g["game_id"]] = {"date":before["game_date"],"result":result}
                else:
                    prev[team,g["game_id"]] = None
    for g in games:
        hp = prev[g["home_team"],g["game_id"]]
        ap = prev[g["away_team"],g["game_id"]]
        g["home_b2b"] = hp is not None
        g["away_b2b"] = ap is not None
        g["home_previous_result"] = hp["result"] if hp else None
        g["away_previous_result"] = ap["result"] if ap else None
        g["home_previous_date"] = hp["date"] if hp else None
        g["away_previous_date"] = ap["date"] if ap else None
        g["setup_qualified"] = bool(hp and hp["result"]=="Loss" and ap is None)
        g["research_confidence"] = RESEARCH_CONFIDENCE if g["setup_qualified"] else None
        g["research_confidence_label"] = "LOW / IN-SAMPLE" if g["setup_qualified"] else None
    return games

# Dedicated conservative credit budget for a 500-credit/month personal plan.
# The ledger is stored in the published NBA feed; never include API keys in it.
MONTHLY_ODDS_CREDIT_LIMIT = 120
DAILY_ODDS_CALL_LIMIT = 4
MIN_PROVIDER_CREDITS = 150
QUOTE_WINDOW_HOURS = 10
RESEARCH_CONFIDENCE = 24  # Heuristic evidence score, NOT a winning probability.
# Evidence breakdown: recent seasons 5/5 positive (20 pts),
# prior five seasons 1/5 positive (4 pts), ten-year ROI -1.36% (0 pts),
# independent out-of-sample validation unavailable (0 pts).

def budget_for(previous, current):
    month = current.astimezone(ET).strftime("%Y-%m")
    day = current.astimezone(ET).date().isoformat()
    old = previous.get("odds_budget") or {}
    if old.get("month") != month:
        return {"month":month, "day":day, "calls_today":0,
                "credits_this_month":0,"provider_remaining":None,
                "provider_used":None,"last_call_cost":None}
    return {"month":month,"day":day,
            "calls_today":int(old.get("calls_today",0)) if old.get("day")==day else 0,
            "credits_this_month":int(old.get("credits_this_month",0)),
            "provider_remaining":old.get("provider_remaining"),
            "provider_used":old.get("provider_used"),
            "last_call_cost":old.get("last_call_cost")}

def price_candidates(games, old, current):
    tracked = {str(p.get("game_id")) for p in old.get("plays",[])
               if isinstance(p,dict) and p.get("game_id")}
    ready = []
    for g in games:
        if not g["setup_qualified"] or g["game_id"] in tracked:
            continue
        hours = (timestamp(g["start_utc"])-current).total_seconds()/3600
        if g["status"]=="Scheduled" and 0<hours<=QUOTE_WINDOW_HOURS:
            ready.append(g)
    return ready

def budget_status(budget):
    if budget["credits_this_month"]>=MONTHLY_ODDS_CREDIT_LIMIT:
        return "SKIPPED_MONTHLY_LIMIT"
    if budget["calls_today"]>=DAILY_ODDS_CALL_LIMIT:
        return "SKIPPED_DAILY_LIMIT"
    remaining = budget.get("provider_remaining")
    if remaining is not None and int(remaining)<MIN_PROVIDER_CREDITS:
        return "SKIPPED_PROVIDER_RESERVE"
    return None

def request_moneyline_quotes(api_key, bookmaker):
    # ONE call, NO auto retries (including HTTP 4xx/5xx). Avoid multiple charges.
    params = urlencode({"bookmakers":bookmaker,"markets":"h2h",
                        "oddsFormat":"american","apiKey":api_key})
    headers={"User-Agent":"BasketballResearchDashboard/1.1",
             "Accept":"application/json"}
    with urlopen(Request(ODDS+"?"+params,headers=headers),timeout=25) as response:
        data=json.load(response)
        usage={k:response.headers.get(k) for k in
               ("x-requests-remaining","x-requests-used","x-requests-last")}
    if not isinstance(data,list):
        raise ValueError("Odds API unexpectedly returned a non-list payload")
    return data,usage

def apply_odds_quotes(games, events, bookmaker, generated):
    matches = {}
    for item in events:
        home = ALIAS.get(str(item.get("home_team") or "").casefold())
        away = ALIAS.get(str(item.get("away_team") or "").casefold())
        if home and away:
            matches[home,away] = item
    hits = []
    for g in games:
        if not g["setup_qualified"] or g["status"]!="Scheduled":
            continue
        if timestamp(g["start_utc"])<=generated:
            continue
        item = matches.get((g["home_team"],g["away_team"]))
        if not item:
            continue
        book = next((b for b in item.get("bookmakers",[])
                     if b.get("key")==bookmaker),None)
        if not book:
            continue
        market = next((m for m in book.get("markets",[])
                       if m.get("key")=="h2h"),None)
        if not market:
            continue
        # Market timestamp is more precise than deprecated bookmaker last_update.
        updated = market.get("last_update")
        if not updated:
            continue
        try:
            quote_time = timestamp(updated)
        except (ValueError,TypeError):
            continue
        if quote_time>generated+timedelta(minutes=5) or generated-quote_time>timedelta(hours=6):
            continue
        outcome = next((o for o in market.get("outcomes",[])
                        if ALIAS.get(str(o.get("name") or "").casefold())==g["away_team"]),None)
        if not outcome:
            continue
        try:
            ml=int(round(float(outcome["price"])))
        except (ValueError,TypeError,KeyError):
            continue
        if -100<ml<100:
            continue
        g["away_moneyline"]=ml
        g["moneyline_bookmaker"]=bookmaker
        g["moneyline_last_update"]=updated
        hits.append(g["game_id"])
    return hits

def match_odds(games, key, preferred_book, old=None, generated=None):
    old=old or {}
    generated=generated or now_utc()
    budget=budget_for(old,generated)
    if not key:
        return "MISSING_API_KEY",[],budget
    ready=price_candidates(games,old,generated)
    if not ready:
        return "SKIPPED_NO_READY_SETUP",[],budget
    blocked=budget_status(budget)
    if blocked:
        return blocked,[],budget
    # Budget an attempted call BEFORE the network request. One request at most.
    budget["calls_today"]+=1
    budget["credits_this_month"]+=1
    budget["last_call_cost"]=1
    try:
        quotes,headers=request_moneyline_quotes(key,preferred_book)
    except Exception:
        return "ODDS_ERROR",[],budget
    try:
        actual=int(headers.get("x-requests-last")) if headers.get("x-requests-last") is not None else 1
        budget["last_call_cost"]=max(actual,0)
        budget["credits_this_month"]+=max(actual-1,0)
        for name,header in (("provider_remaining","x-requests-remaining"),
                            ("provider_used","x-requests-used")):
            if headers.get(header) is not None:
                budget[name]=int(headers[header])
    except (TypeError,ValueError):
        pass
    return "CONNECTED",apply_odds_quotes(ready,quotes,preferred_book,generated),budget

def previous_feed():
    try:
        raw=json.loads(FEED.read_text(encoding="utf-8"))
        return raw if isinstance(raw,dict) else {}
    except (OSError,ValueError):
        return {}

def assemble(games, old, generated, odds_status, bookmaker, budget=None, elo_state=None, model=None, v2_summary=None):
    known = {str(p.get("game_id")):p for p in old.get("plays",[])
             if isinstance(p,dict) and p.get("game_id")}
    current = {g["game_id"]:g for g in games}
    for game_id,play in list(known.items()):
        match = current.get(game_id)
        if match and match["status"]=="Final" and match["winner"]:
            play["result"] = ("W" if match["winner"]==play.get("bet_team") else "L")
            play["away_score"] = match["away_score"]
            play["home_score"] = match["home_score"]
    for g in games:
        # Never create a wager on a started game. A setup alone is not a bet.
        if g["game_id"] in known or not g["setup_qualified"]:
            continue
        if g["status"]!="Scheduled" or timestamp(g["start_utc"])<=generated:
            continue
        ml=g.get("away_moneyline")
        if ml is None or ml>-250:
            continue
        known[g["game_id"]] = {
            "game_id":g["game_id"],"game_date":g["game_date"],
            "away_team":g["away_team"],"home_team":g["home_team"],
            "bet_team":g["away_team"],"moneyline":ml,"entry_moneyline":ml,
            "odds_source":bookmaker,"first_seen_utc":generated.isoformat(),
            "price_verified":True,"injuries_verified":False,
            "model_win_probability":g.get("model_away_win_probability"),
            "model_v2_win_probability":g.get("model_v2_away_win_probability"),
            "model_v2_id":g.get("model_v2_id"),
            "model_v2_market_validated":False,
            "model_id":g.get("model_id"),
            "model_market_validated":False,
            "model_confidence":g.get("model_confidence"),
            "historical_out_of_sample":True if g.get("model_id") else None,
            "historical_bets":1225 if g.get("model_id") else None,
            "historical_profitable_seasons":0 if g.get("model_id") else None,
            "historical_seasons":1 if g.get("model_id") else None,
            "rest_context_score":65 if g.get("model_id") else None,
            "status":"PAPER_TRACK","result":None
        }
    start_window=(generated.astimezone(ET).date()-timedelta(days=180)).isoformat()
    tracked=sorted((p for p in known.values() if str(p.get("game_date",""))>=start_window),
                   key=lambda p:(p.get("game_date",""),p.get("game_id","")),reverse=True)
    graded=[p for p in tracked if p.get("result") in ("W","L")]
    profit=sum((float(p["moneyline"])/100 if float(p["moneyline"])>0 else
                100/-float(p["moneyline"])) if p["result"]=="W" else -1 for p in graded)
    future=[g for g in games if g["game_date"]>=generated.astimezone(ET).date().isoformat()
            and g["game_date"]<=(generated.astimezone(ET).date()+timedelta(days=2)).isoformat()]
    return {
        "schema_version":4,
        "odds_budget":budget or {},
        "nba_elo_state":elo_state or {},
        "nba_v2_summary":v2_summary or {"status":"NOT_READY","market_edge_validated":False},
        "nba_model_metadata":{
            "model_id":model["model_id"] if model else None,
            "status":model["status"] if model else "Not connected",
            "holdout_games":model["evaluation"]["untouched_holdout"]["nba_model"]["games"] if model else None,
            "holdout_accuracy_pct":model["evaluation"]["untouched_holdout"]["nba_model"]["accuracy_pct"] if model else None,
            "holdout_brier":model["evaluation"]["untouched_holdout"]["nba_model"]["brier"] if model else None,
            "market_holdout_brier":model["evaluation"]["untouched_holdout"]["historical_market_novig"]["brier"] if model else None,
            "confidence_level":model["model_evidence_confidence_100"] if model else None,
            "market_edge_validated":False,
        },
        "research_confidence_method":"Heuristic 24/100: recent 5/5 positive (+20), prior 1/5 (+4), ten-year ROI negative (+0), independent holdout absent (+0). Not a predicted win probability.",
        "updated_at":generated.isoformat().replace("+00:00","Z"),
        "status":"OK",
        "schedule_source":"ESPN public NBA scoreboard",
        "odds_source":"The Odds API v4 (optional)",
        "odds_status":odds_status,
        "selected_bookmaker":bookmaker,
        "model_status":"Frozen Elo+rest NBA research model enabled; historical market outperformed model on untouched 2025-26 holdout",
        "summary":{"scheduled_games":len(future),"b2b_games":sum(g["home_b2b"] or g["away_b2b"] for g in future),
                   "qualified_setups":sum(g["setup_qualified"] for g in future),
                   "setups_with_moneyline":sum(g["setup_qualified"] and g.get("away_moneyline") is not None for g in future),
                   "tracked_plays":len(tracked),"settled_plays":len(graded),
                   "nba_model_predictions":sum(g.get("model_away_win_probability") is not None for g in future),
                   "wins":sum(p["result"]=="W" for p in graded),
                   "losses":sum(p["result"]=="L" for p in graded),
                   "units":round(profit,4),"roi":round(100*profit/len(graded),3) if graded else None},
        "games":future,"plays":tracked
    }

def main():
    generated=now_utc()
    anchor=generated.astimezone(ET).date()
    games=annotate(calendar(anchor))
    old=previous_feed()
    # Free ESPN results update last-known Elo before any new forecasts are made.
    model=read_model()
    elo_state=advance_state(games,old,generated,model)
    predictions=enrich_upcoming(games,elo_state,model,generated)
    from nba_v2_shadow import enrich_v2
    v2_summary=enrich_v2(games)
    bookmaker=os.environ.get("NBA_ODDS_BOOKMAKER","draftkings").strip().lower()
    key=os.environ.get("NBA_ODDS_API_KEY","").strip()
    status,_,budget=match_odds(games,key,bookmaker,old,generated)
    data=assemble(games,old,generated,status,bookmaker,budget,elo_state,model,v2_summary)
    FEED.parent.mkdir(parents=True,exist_ok=True)
    FEED.write_text(json.dumps(data,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print("NBA feed:",data["summary"],"odds:",status,
          "budget:",budget,"model forecasts:",predictions,
          "Elo games settled:",len(elo_state["processed_game_ids"]), "v2:",v2_summary)
    return 0

if __name__=="__main__":
    raise SystemExit(main())
