#!/usr/bin/env python3
"""Free-data NBA Elo/logistic probability engine.

Uses only a frozen JSON model, the prior published feed state, and ESPN final games.
No sportsbook endpoints, paid API calls, training at runtime, or hindsight inputs.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

MODEL_PATH = Path(__file__).resolve().parent / "nba_model_v1.json"
NBA_2026_SEASON_START = "2026-10-20"

def read_model(path: Path = MODEL_PATH) -> Dict[str, Any]:
    model = json.loads(path.read_text(encoding="utf-8"))
    if not model.get("model_id") or len(model.get("logistic_coef", [])) != 4:
        raise ValueError("Frozen NBA model configuration is invalid")
    if model.get("market_edge_validated") is not False:
        raise ValueError("Do not promote an NBA betting edge without independent evidence")
    return model

def as_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)

def initial_state(model: Dict[str, Any]) -> Dict[str, Any]:
    return {"model_id": model["model_id"],
            "ratings": dict(model["preseason_2026_elo"]),
            "processed_game_ids": [],
            "last_processed_game_start_utc": None,
            "status": "INITIALIZED_FROM_FROZEN_2025_26"}

def advance_state(games: List[Dict[str, Any]], previous: Dict[str, Any],
                  as_of: datetime, model: Dict[str, Any]) -> Dict[str, Any]:
    state = previous.get("nba_elo_state")
    if not isinstance(state, dict) or state.get("model_id") != model["model_id"]:
        state = initial_state(model)
    else:
        state = {"model_id": model["model_id"],
                 "ratings": dict(state.get("ratings") or {}),
                 "processed_game_ids": list(state.get("processed_game_ids") or []),
                 "last_processed_game_start_utc": state.get("last_processed_game_start_utc"),
                 "status": "UPDATING"}
    ratings = state["ratings"]
    processed = set(state["processed_game_ids"])
    for g in sorted(games, key=lambda x: (x["start_utc"], str(x["game_id"]))):
        gid = str(g["game_id"])
        if gid in processed or g.get("game_date", "") < NBA_2026_SEASON_START:
            continue
        if g.get("status") != "Final" or g.get("winner") not in (g["home_team"], g["away_team"]):
            continue
        if as_utc(g["start_utc"]) >= as_of:
            continue
        hname, aname = g["home_team"], g["away_team"]
        h = float(ratings.get(hname, model["elo_initial"]))
        a = float(ratings.get(aname, model["elo_initial"]))
        baseline = 1.0 / (1.0 + 10.0 ** (-(h-a+model["elo_home_advantage_points"])/400.0))
        actual = 1.0 if g["winner"] == hname else 0.0
        increment = float(model["elo_k"]) * (actual-baseline)
        ratings[hname] = h+increment
        ratings[aname] = a-increment
        processed.add(gid)
        state["last_processed_game_start_utc"] = g["start_utc"]
    state["processed_game_ids"] = sorted(processed)
    state["status"] = "OK"
    return state

def predict(g: Dict[str, Any], state: Dict[str, Any],
            model: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if g.get("home_team") == g.get("away_team") or not g.get("home_team") or not g.get("away_team"):
        return None
    ratings = state["ratings"]
    home = float(ratings.get(g["home_team"], model["elo_initial"]))
    away = float(ratings.get(g["away_team"], model["elo_initial"]))
    x = [1, (home-away)/400.0, int(g.get("home_b2b") is True),
         int(g.get("away_b2b") is True)]
    z = sum(float(b)*v for b,v in zip(model["logistic_coef"], x))
    prob = 1/(1+math.exp(-max(-30.0,min(30.0,z))))
    return {
        "model_id":model["model_id"],
        "model_home_win_probability":round(prob,5),
        "model_away_win_probability":round(1-prob,5),
        "model_confidence":model["model_evidence_confidence_100"],
        "model_market_validated":False,
        "model_elo_home":round(home,2),
        "model_elo_away":round(away,2),
        "model_status":"PROSPECTIVE_RESEARCH_NOT_VALIDATED_EDGE",
    }

def enrich_upcoming(games: List[Dict[str, Any]], state: Dict[str, Any],
                    model: Dict[str, Any], as_of: datetime) -> int:
    count=0
    for g in games:
        if g.get("status")!="Scheduled" or as_utc(g["start_utc"])<=as_of:
            continue
        forecast = predict(g,state,model)
        if forecast:
            g.update(forecast)
            count+=1
    return count
