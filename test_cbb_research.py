import tempfile
import unittest
from datetime import date
from pathlib import Path
from cbb_research import agreement, ingest, import_results, summarize
from cbb_research_cache import read_or_fetch_archive


def row(gid="1", at="2026-11-28T08:00:00-08:00", **kwargs):
    return dict(Game_Date="2026-11-28", Game_ID=gid, Run_Timestamp_PT=at,
                Start_UTC="2026-11-28T20:00:00Z", Market_Total=145.5,
                Totals_Predicted_Edge=2.2, Totals_V2_Predicted_Edge=1.0,
                Totals_V3_Predicted_Edge=3.0, Totals_Qualified=True,
                Totals_Actionable=True, **kwargs)


class ResearchTests(unittest.TestCase):
    def test_agreement_distinguishes_direction_and_qualification(self):
        a = agreement(row())
        self.assertTrue(a["unanimous"])
        self.assertEqual(a["qualified_count"], 1)
        self.assertEqual(a["majority"], "Over")
        self.assertFalse(agreement({**row(), "Totals_V3_Predicted_Edge": float("nan")})["complete"])
        self.assertFalse(agreement({**row(), "Totals_V3_Predicted_Edge": -1})["unanimous"])
        self.assertIsNone(agreement({"Totals_Predicted_Edge": 0})["majority"])

    def test_ledger_pre_tip_dedup_and_passes(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "research.sqlite"
            good = row()
            passed = {**row("2"), "Totals_Actionable": False}
            later = row(at="2026-11-28T09:00:00-08:00")
            late = row("3", at="2026-11-28T12:00:00-08:00")
            self.assertEqual(ingest(p, [good, good, passed, later, late]), {"inserted": 3, "rejected": 1})
            self.assertEqual(ingest(p, [good])["inserted"], 0)
            self.assertEqual(import_results(p, [
                {"game_date": "2026-11-28", "game_id": "1", "status": "final", "home_score": 80, "away_score": 70},
                {"game_date": "2026-11-28", "game_id": "2", "status": "final", "home_score": 60, "away_score": 70},
                {"game_date": "2026-11-28", "game_id": "3", "status": "live", "home_score": 20, "away_score": 20},
            ]), 2)
            self.assertEqual(summarize(p), {"snapshots": 3, "games": 2, "v1_first_snapshot_agreement": {"unanimous": {"wins": 1, "losses": 0, "pushes": 0}}})

    def test_exact_date_cache_refresh_and_corruption(self):
        calls = []
        def fetch():
            calls.append(1)
            return [{"TeamName": "Example", "AdjOE": 100.0}]
        with tempfile.TemporaryDirectory() as d:
            day = date(2026, 11, 27)
            original, hit = read_or_fetch_archive(d, day, fetch)
            self.assertFalse(hit)
            reused, hit = read_or_fetch_archive(d, day, fetch)
            self.assertTrue(hit)
            self.assertEqual(original, reused)
            self.assertEqual(len(calls), 1)
            self.assertFalse(read_or_fetch_archive(d, day, fetch, refresh=True)[1])
            self.assertFalse(read_or_fetch_archive(d, date(2026, 11, 28), fetch)[1])
            (Path(d) / "kenpom_archive_2026-11-27.json").write_text("broken")
            self.assertFalse(read_or_fetch_archive(d, day, fetch)[1])
            self.assertEqual(len(calls), 4)

    def test_missing_archive_is_not_cached(self):
        with tempfile.TemporaryDirectory() as d:
            day = date(2026, 11, 27)
            self.assertEqual(read_or_fetch_archive(d, day, lambda: []), ([], False))
            self.assertEqual(list(Path(d).iterdir()), [])
            with self.assertRaises(RuntimeError):
                def fail():
                    raise RuntimeError("unavailable")
                read_or_fetch_archive(d, day, fail)
            self.assertEqual(list(Path(d).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
