"""Offline tests for the free-data, frozen NBA probability model."""
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from nba_predictor import read_model, initial_state, advance_state, predict, enrich_upcoming

class NBAModelTests(unittest.TestCase):
    def setUp(self):
        self.model=read_model()
        self.now=datetime(2026,10,26,18,tzinfo=timezone.utc)
        self.final={
            "game_id":"f1","game_date":"2026-10-25","start_utc":"2026-10-25T23:00:00Z",
            "home_team":"Boston Celtics","away_team":"Chicago Bulls",
            "status":"Final","winner":"Boston Celtics",
            "home_b2b":False,"away_b2b":False
        }
        self.upcoming={
            "game_id":"f2","game_date":"2026-10-26","start_utc":"2026-10-27T00:00:00Z",
            "home_team":"Chicago Bulls","away_team":"Boston Celtics",
            "status":"Scheduled","winner":None,"home_b2b":True,"away_b2b":False
        }

    def test_model_frozen_holdout_and_no_validated_edge(self):
        m=self.model
        self.assertEqual(m["training_season_years"][-1],2023)
        self.assertEqual(m["validation_season_year"],2024)
        self.assertEqual(m["holdout_season_year"],2025)
        self.assertEqual(m["evaluation"]["untouched_holdout"]["nba_model"]["games"],1225)
        self.assertGreater(m["evaluation"]["untouched_holdout"]["nba_model"]["brier"],
                           m["evaluation"]["untouched_holdout"]["historical_market_novig"]["brier"])
        self.assertFalse(m["market_edge_validated"])

    def test_predictions_between_zero_and_one(self):
        g=self.upcoming
        p=predict(g,initial_state(self.model),self.model)
        self.assertGreater(p["model_home_win_probability"],0)
        self.assertLess(p["model_home_win_probability"],1)
        self.assertAlmostEqual(p["model_home_win_probability"]+
                               p["model_away_win_probability"],1.0,places=4)
        self.assertEqual(p["model_confidence"],35)
        self.assertFalse(p["model_market_validated"])

    def test_rest_condition_changes_probability(self):
        state=initial_state(self.model)
        tired=predict(self.upcoming,state,self.model)["model_home_win_probability"]
        rested=dict(self.upcoming,home_b2b=False)
        fresh=predict(rested,state,self.model)["model_home_win_probability"]
        self.assertLess(tired,fresh)

    def test_previous_final_changes_rating_without_leaking_future(self):
        before=initial_state(self.model)
        updated=advance_state([self.final,self.upcoming],{},self.now,self.model)
        self.assertEqual(len(updated["processed_game_ids"]),1)
        self.assertGreater(updated["ratings"]["Boston Celtics"],
                           before["ratings"]["Boston Celtics"])
        self.assertLess(updated["ratings"]["Chicago Bulls"],
                        before["ratings"]["Chicago Bulls"])
        self.assertEqual(enrich_upcoming([self.upcoming],updated,self.model,self.now),1)
        self.assertIn("model_away_win_probability",self.upcoming)

    def test_unfinished_games_not_incorporated(self):
        g=dict(self.final,status="Scheduled",winner=None)
        state=advance_state([g],{},self.now,self.model)
        self.assertFalse(state["processed_game_ids"])

    def test_completed_game_not_applied_twice(self):
        first=advance_state([self.final],{},self.now,self.model)
        second=advance_state([self.final],{"nba_elo_state":first},self.now,self.model)
        self.assertEqual(first["ratings"],second["ratings"])
        self.assertEqual(len(second["processed_game_ids"]),1)

if __name__=="__main__":
    unittest.main()
