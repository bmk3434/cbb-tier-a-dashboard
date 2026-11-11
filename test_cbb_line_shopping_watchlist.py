"""Regression checks for the watch-only near-miss / line-shopping feed."""
import csv
import tempfile
import unittest
from pathlib import Path

import dashboard_sync as sync


class LineShoppingWatchlistTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.previous_output = sync.OUTPUT
        sync.OUTPUT = Path(self.temp.name)
        self.addCleanup(setattr, sync, "OUTPUT", self.previous_output)

    def row(self, **changes):
        result = {
            "Game_Date": "2026-11-11",
            "Game_ID": "1001",
            "Start_UTC": "2026-11-12T01:00:00Z",
            "Run_Timestamp_PT": "2026-11-11T08:03:37-08:00",
            "Away_Team": "Away Team",
            "Home_Team": "Home Team",
            "Neutral_Site": "False",
            "Venue_Verified": "True",
            "KP_Location": "Home",
            "Provider": "Draft Kings",
            "Provider_Status": "FROZEN_PRIORITY",
            "QC_Status": "OK_NON_NEUTRAL",
            "Model_Bet": "Away Team",
            "Bet_Spread": "6.5",
            "V4_Edge": "1.25",
            "Market_Total": "150.5",
            "Totals_Bet": "Over",
            "Totals_Edge": "1.40",
            "Totals_QC_Status": "OK_TOTAL",
        }
        result.update(changes)
        return result

    def run_watchlist(self, rows):
        path = sync.OUTPUT / "all_scored_2026-11-11.csv"
        if rows:
            fields = list(rows[0].keys())
            with path.open("w", newline="", encoding="utf-8") as fd:
                writer = csv.DictWriter(fd, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
        return sync.build_line_shopping_watchlist("2026-11-11")

    def test_near_misses_are_watch_only_with_better_line_examples(self):
        r = self.run_watchlist([self.row()])
        self.assertEqual(r["status"], "OK")
        self.assertEqual(len(r["near_miss_ats"]), 1)
        self.assertEqual(len(r["near_miss_totals"]), 1)
        self.assertEqual(len(r["qualified_line_shop_totals"]), 0)
        ats = r["near_miss_ats"][0]
        totals = r["near_miss_totals"][0]
        self.assertEqual(ats["edge_gap_pts"], 0.5)
        self.assertEqual(ats["one_point_better_example"], 7.5)
        self.assertEqual(totals["edge_gap_pts"], 0.6)
        self.assertEqual(totals["half_point_better_example"], 150.0)
        self.assertEqual(totals["one_point_better_example"], 149.5)
        self.assertTrue(totals["watch_only"])
        self.assertTrue(totals["rescore_required"])
        self.assertFalse(totals["actual_price_verified"])
        self.assertNotIn("test_grade", totals)

    def test_qualified_low_edge_totals_shop_without_becoming_near_miss(self):
        r = self.run_watchlist([self.row(V4_Edge="2.1", Totals_Edge="2.3")])
        self.assertEqual(len(r["near_miss_ats"]), 0)
        self.assertEqual(len(r["near_miss_totals"]), 0)
        self.assertEqual(len(r["qualified_line_shop_totals"]), 1)
        self.assertEqual(r["qualified_line_shop_totals"][0]["edge_gap_pts"], 0.0)

    def test_under_better_line_is_higher(self):
        r = self.run_watchlist([self.row(Totals_Bet="Under", Totals_Edge="1.8")])
        p = r["near_miss_totals"][0]
        self.assertEqual(p["one_point_better_example"], 151.5)
        self.assertEqual(p["half_point_better_example"], 151.0)
        self.assertAlmostEqual(p["edge_gap_pts"], 0.2)

    def test_near_underdog_minimum_is_watch_only(self):
        r = self.run_watchlist([self.row(Bet_Spread="3.5", V4_Edge="2.2", Totals_Edge="0.3")])
        p = r["near_miss_ats"][0]
        self.assertEqual(p["edge_gap_pts"], 0)
        self.assertEqual(p["underdog_gap_pts"], 0.5)
        self.assertEqual(p["one_point_better_example"], 4.5)

    def test_exclude_bad_qc_provider_and_post_tip(self):
        rows = [
            self.row(Game_ID="1", Venue_Verified="False"),
            self.row(Game_ID="2", Provider_Status="OUTSIDE_FROZEN_PROVIDER_PRIORITY"),
            self.row(Game_ID="3", QC_Status="NEUTRAL_REVIEW_REQUIRED", Totals_QC_Status="NEUTRAL_REVIEW_REQUIRED", Neutral_Site="True"),
            self.row(Game_ID="4", Run_Timestamp_PT="2026-11-11T18:00:00-08:00"),
            self.row(Game_ID="5", KP_Location="MISSING"),
            self.row(Game_ID="6", Game_Date="2026-11-10"),
        ]
        r = self.run_watchlist(rows)
        self.assertEqual(len(r["near_miss_ats"]), 0)
        self.assertEqual(len(r["near_miss_totals"]), 0)
        self.assertEqual(len(r["qualified_line_shop_totals"]), 0)

    def test_verified_neutral_game_can_be_watched_without_ats_wager(self):
        r = self.run_watchlist([self.row(Neutral_Site="True", KP_Location="Neutral",
            QC_Status="NEUTRAL_VERIFIED_OVERRIDE", Totals_QC_Status="OK_TOTAL")])
        self.assertEqual(len(r["near_miss_ats"]), 1)
        self.assertEqual(r["near_miss_ats"][0]["qc_status"], "NEUTRAL_VERIFIED_OVERRIDE")

    def test_missing_scanner_output_is_explicit(self):
        r = sync.build_line_shopping_watchlist("2026-11-11")
        self.assertEqual(r["status"], "MISSING_SCANNER_OUTPUT")
        self.assertEqual(r["near_miss_totals"], [])

    def test_missing_market_total_not_added(self):
        r = self.run_watchlist([self.row(Market_Total="", Totals_Edge="1.5", V4_Edge="0.2")])
        self.assertEqual(len(r["near_miss_totals"]), 0)


if __name__ == "__main__":
    unittest.main()
