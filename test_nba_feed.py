"""Offline smoke tests for the automatic NBA paper-tracker."""
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from nba_feed import annotate, assemble, parse_game, match_odds, MONTHLY_ODDS_CREDIT_LIMIT, RESEARCH_CONFIDENCE


class NBAFeedTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
        self.yesterday = {
            "game_id": "game-1", "game_date": "2026-10-25",
            "start_utc": "2026-10-25T22:00:00Z",
            "home_team": "Atlanta Hawks", "away_team": "Chicago Bulls",
            "home_score": 116, "away_score": 106,
            "status": "Final", "winner": "Atlanta Hawks"
        }
        self.today = {
            "game_id": "game-2", "game_date": "2026-10-26",
            "start_utc": "2026-10-27T01:00:00Z",
            "home_team": "Chicago Bulls", "away_team": "Miami Heat",
            "home_score": None, "away_score": None,
            "status": "Scheduled", "winner": None, "away_moneyline": -300
        }

    def test_previous_loss_and_single_sided_back_to_back(self):
        games = annotate([dict(self.yesterday), dict(self.today)])
        target = next(g for g in games if g["game_id"] == "game-2")
        self.assertTrue(target["home_b2b"])
        self.assertEqual(target["home_previous_result"], "Loss")
        self.assertFalse(target["away_b2b"])
        self.assertTrue(target["setup_qualified"])

    def test_bets_only_from_pretip_verified_prices(self):
        games = annotate([dict(self.yesterday), dict(self.today)])
        result = assemble(games, {}, self.now, "CONNECTED", "draftkings")
        self.assertEqual(len(result["plays"]), 1)
        play = result["plays"][0]
        self.assertEqual(play["moneyline"], -300)
        self.assertIsNone(play["model_win_probability"])
        self.assertIsNone(play["historical_out_of_sample"])
        self.assertEqual(result["summary"]["tracked_plays"], 1)

    def test_missing_price_is_not_bet(self):
        current = dict(self.today)
        current.pop("away_moneyline")
        result = assemble(annotate([dict(self.yesterday), current]), {}, self.now,
                          "MISSING_API_KEY", "draftkings")
        self.assertEqual(result["summary"]["qualified_setups"], 1)
        self.assertEqual(result["summary"]["tracked_plays"], 0)

    def test_started_game_never_creates_retroactive_entry(self):
        result = assemble(annotate([dict(self.yesterday), dict(self.today)]), {},
                          datetime(2026, 10, 27, 2, 0, tzinfo=timezone.utc),
                          "CONNECTED", "draftkings")
        self.assertFalse(result["plays"])

    def test_first_qualified_price_is_frozen_and_final_is_graded(self):
        games = annotate([dict(self.yesterday), dict(self.today)])
        first = assemble(games, {}, self.now, "CONNECTED", "draftkings")
        completed = dict(self.today)
        completed.update(status="Final", winner="Miami Heat", home_score=99,
                         away_score=110, away_moneyline=-500)
        second = assemble(annotate([dict(self.yesterday), completed]), first,
                          datetime(2026, 10, 27, 4, 0, tzinfo=timezone.utc),
                          "CONNECTED", "draftkings")
        self.assertEqual(second["plays"][0]["moneyline"], -300)
        self.assertEqual(second["plays"][0]["result"], "W")
        self.assertEqual(second["summary"]["wins"], 1)
        self.assertAlmostEqual(second["summary"]["units"], 1/3, places=3)


    def test_research_rating_is_low_and_not_a_probability(self):
        games = annotate([dict(self.yesterday), dict(self.today)])
        target = next(g for g in games if g["game_id"]=="game-2")
        self.assertEqual(target["research_confidence"], RESEARCH_CONFIDENCE)
        self.assertEqual(RESEARCH_CONFIDENCE, 24)

    def test_no_odds_api_call_without_qualifying_setup(self):
        with patch("nba_feed.request_moneyline_quotes",
                   side_effect=AssertionError("Spent API credit without a setup")):
            status,quotes,budget = match_odds(
                annotate([dict(self.yesterday)]), "secret", "draftkings",
                {}, datetime(2026,10,26,20,0,tzinfo=timezone.utc))
        self.assertEqual(status, "SKIPPED_NO_READY_SETUP")
        self.assertEqual(budget["credits_this_month"], 0)
        self.assertEqual(quotes, [])

    def test_no_odds_api_call_more_than_ten_hours_from_tip(self):
        with patch("nba_feed.request_moneyline_quotes",
                   side_effect=AssertionError("Requested odds too early")):
            status,_,budget = match_odds(
                annotate([dict(self.yesterday),dict(self.today)]),
                "secret","draftkings",{},self.now)
        self.assertEqual(status,"SKIPPED_NO_READY_SETUP")
        self.assertEqual(budget["calls_today"],0)

    def test_single_odds_call_with_recorded_provider_quota(self):
        scheduled=annotate([dict(self.yesterday),dict(self.today)])
        quote=[{"home_team":"Chicago Bulls","away_team":"Miami Heat",
                "bookmakers":[{"key":"draftkings",
                               "markets":[{"key":"h2h",
                                           "last_update":"2026-10-26T19:50:00Z",
                                           "outcomes":[{"name":"Miami Heat","price":-300}]}]}]}]
        headers={"x-requests-remaining":"479","x-requests-used":"21","x-requests-last":"1"}
        with patch("nba_feed.request_moneyline_quotes",return_value=(quote,headers)) as fn:
            status,hits,budget=match_odds(scheduled,"secret","draftkings",{},
                    datetime(2026,10,26,20,0,tzinfo=timezone.utc))
        self.assertEqual(fn.call_count,1)
        self.assertEqual(status,"CONNECTED")
        self.assertEqual(hits,["game-2"])
        self.assertEqual(scheduled[1]["away_moneyline"],-300)
        self.assertEqual(budget["credits_this_month"],1)
        self.assertEqual(budget["provider_remaining"],479)

    def test_monthly_cap_prevents_spending(self):
        old={"odds_budget":{"month":"2026-10","day":"2026-10-26",
                            "calls_today":0,
                            "credits_this_month":MONTHLY_ODDS_CREDIT_LIMIT,
                            "provider_remaining":300}}
        with patch("nba_feed.request_moneyline_quotes",
                   side_effect=AssertionError("Exceeded budget")):
            status,_,budget=match_odds(
                annotate([dict(self.yesterday),dict(self.today)]),
                "secret","draftkings",old,
                datetime(2026,10,26,20,0,tzinfo=timezone.utc))
        self.assertEqual(status,"SKIPPED_MONTHLY_LIMIT")
        self.assertEqual(budget["credits_this_month"],120)

    def test_odds_error_uses_one_attempt_with_no_retries(self):
        with patch("nba_feed.request_moneyline_quotes",
                   side_effect=OSError("Odds provider unavailable")) as fn:
            status,_,budget=match_odds(
                annotate([dict(self.yesterday),dict(self.today)]),
                "secret","draftkings",{},
                datetime(2026,10,26,20,0,tzinfo=timezone.utc))
        self.assertEqual(status,"ODDS_ERROR")
        self.assertEqual(fn.call_count,1)
        self.assertEqual(budget["credits_this_month"],1)

    def test_espn_preseason_not_included(self):
        event = {"id": "1234", "season": {"type": 1},
                 "date": "2026-10-10T23:00:00Z", "competitions": []}
        self.assertIsNone(parse_game(event))


if __name__ == "__main__":
    unittest.main()
