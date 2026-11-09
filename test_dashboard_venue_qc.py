#!/usr/bin/env python3
"""Prove historical venue QC exceptions cannot contaminate clean model results."""
import unittest

from dashboard_sync import mark_venue_qc_exceptions, summarize, summarize_combined


class TestDashboardVenueException(unittest.TestCase):
    def test_wichita_stays_in_audit_but_not_clean_record(self):
        state = {
            "plays": {
                "374768": {
                    "game_id": "374768",
                    "entry_line": 6.5, "result": "L", "profit_units": -1.0,
                    "game_date": "2026-11-08", "snapshots": [],
                },
                "other": {
                    "game_id": "other", "entry_line": 7.0, "result": "W",
                    "profit_units": 0.909090909, "game_date": "2026-11-09",
                    "snapshots": [],
                },
            },
            "totals_plays": {},
        }
        self.assertEqual(mark_venue_qc_exceptions(state), 1)
        self.assertTrue(state["plays"]["374768"]["qc_excluded"])
        self.assertEqual(state["plays"]["374768"]["entry_line"], 6.5)
        self.assertEqual(state["plays"]["374768"]["result"], "L")
        ats = summarize(state)
        combined = summarize_combined(state)
        self.assertEqual(ats["qc_excluded_entries"], 1)
        self.assertEqual(ats["entered_plays"], 1)
        self.assertEqual(ats["completed_plays"], 1)
        self.assertEqual(ats["wins"], 1)
        self.assertEqual(ats["losses"], 0)
        self.assertEqual(combined["ats_entered"], 1)
        self.assertEqual(combined["wins"], 1)
        self.assertEqual(combined["losses"], 0)


if __name__ == "__main__":
    unittest.main()
