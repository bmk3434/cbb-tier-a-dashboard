"""Offline tests for NBA v2 shadow input sufficiency, isolation and model parity."""
import unittest
from datetime import date,timedelta
from nba_v2_shadow import build_team_history,predict_pair,row_stats
import json
from pathlib import Path

class V2ShadowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model=json.loads((Path(__file__).parent/"nba_model_v2_challenger.json").read_text())

    def generate(self,n):
        rows=[]
        for i in range(n):
            day=(date(2026,10,20)+timedelta(days=i)).isoformat()
            for team,opp,win in [("BOS","CHI","W"),("CHI","BOS","L")]:
                rows.append({
                    "season":"2026-27","team":team,"game_date":day,
                    "opponent":opp,"result":win,"points":"110",
                    "field_goal_attempts":"88","three_point_attempts":"36",
                    "free_throws_made":"17","free_throw_attempts":"22",
                    "offensive_rebounds":"11","defensive_rebounds":"32",
                    "turnovers":"14","effective_fg_pct":"0.55",
                    "off_rating_est":"114.5","def_rating_est":"109.6"
                })
        return rows

    def game(self):
        return {"home_team":"Boston Celtics","away_team":"Chicago Bulls",
                "game_date":"2026-10-29",
                "model_away_win_probability":0.25,
                "status":"Scheduled","home_b2b":False,"away_b2b":False}

    def test_need_five_earlier_games_for_both_teams(self):
        h=build_team_history(self.generate(4),self.model["rolling_defaults"])
        self.assertIsNone(predict_pair(self.game(),h,self.model))

    def test_predict_v2_after_five_games(self):
        h=build_team_history(self.generate(6),self.model["rolling_defaults"])
        g=self.game()
        result=predict_pair(g,h,self.model)
        self.assertIsNotNone(result)
        self.assertGreater(result["model_v2_away_win_probability"],0.0)
        self.assertLess(result["model_v2_away_win_probability"],1.0)
        self.assertFalse(result["model_v2_market_validated"])
        self.assertEqual(result["model_v2_min_prior_games"],6)

    def test_future_boxscores_are_not_used(self):
        h=build_team_history(self.generate(6),self.model["rolling_defaults"])
        g=self.game()
        g["game_date"]="2026-10-23"
        self.assertIsNone(predict_pair(g,h,self.model))

    def test_model_never_promoted(self):
        self.assertEqual(self.model["status"].split()[0],"EXPERIMENTAL")
        self.assertFalse(self.model["promotion_allowed"])
        self.assertFalse(self.model["v2_beats_market_holdout_brier"])

if __name__=="__main__":
    unittest.main()
