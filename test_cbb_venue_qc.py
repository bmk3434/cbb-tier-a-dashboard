#!/usr/bin/env python3
"""Regression coverage for KenPom/CBBD venue verification (no API keys needed)."""
import unittest

from cbb_tierA_scanner import (
    apply_neutral_override,
    kenpom_location,
    venue_qc_allows_bet,
    verify_game_venue,
)


class TestVenueQC(unittest.TestCase):
    def test_wichita_usc_cbbd_false_kenpom_neutral_is_held(self):
        # Regression for the Nov 8, 2026 Tahoe neutral-site game (CBBD falsely
        # labeled Wichita State the home team). The API test checks the live
        # FanMatch Location; this unit-test fixture enforces the gating rule.
        game = {"gameId": 374768, "homeTeam": "Wichita State", "awayTeam": "USC", "neutralSite": False}
        fm = {"Home": "Wichita St.", "Visitor": "USC", "Location": "Neutral"}
        verified, location, qc = verify_game_venue(game, fm)
        self.assertFalse(verified)
        self.assertEqual(location, "Neutral")
        self.assertEqual(qc, "VENUE_SOURCE_CONFLICT_REVIEW_REQUIRED")
        self.assertFalse(venue_qc_allows_bet(verified, qc))

    def test_true_home_verified(self):
        verified, location, qc = verify_game_venue({"neutralSite": False}, {"Location": "Home"})
        self.assertTrue(verified)
        self.assertEqual(location, "Home")
        self.assertTrue(venue_qc_allows_bet(verified, "OK_NON_NEUTRAL"))

    def test_true_neutral_still_requires_spread_orientation_override(self):
        game = {"gameId": 55, "neutralSite": True}
        verified, location, qc = verify_game_venue(game, {"Location": "Neutral"})
        self.assertTrue(verified)
        self.assertEqual(location, "Neutral")
        _, override_qc, _ = apply_neutral_override(game, {"spread": 6.0}, {})
        self.assertEqual(override_qc, "NEUTRAL_REVIEW_REQUIRED")
        self.assertFalse(venue_qc_allows_bet(verified, override_qc))
        updated, override_qc, source = apply_neutral_override(game, {"spread": 6.0}, {
            "55": {"Home_Spread_Override": "6.5", "Source_URL": "https://example.org/verified"}
        })
        self.assertEqual(updated["spread"], 6.5)
        self.assertEqual(override_qc, "NEUTRAL_VERIFIED_OVERRIDE")
        self.assertTrue(venue_qc_allows_bet(verified, override_qc))

    def test_conflict_both_directions_are_held(self):
        for flag, kp in [(True, "Home"), (False, "Neutral")]:
            with self.subTest(flag=flag, kp=kp):
                verified, _, qc = verify_game_venue({"neutralSite": flag}, {"Location": kp})
                self.assertFalse(verified)
                self.assertIn("CONFLICT", qc)
                self.assertFalse(venue_qc_allows_bet(verified, qc))

    def test_semi_home_fails_closed(self):
        for cb_flag in (False, True):
            verified, location, qc = verify_game_venue({"neutralSite": cb_flag}, {"Location": "Semi-Home"})
            self.assertFalse(verified)
            self.assertEqual(location, "Semi-Home")
            self.assertIn("SEMI_HOME", qc)

    def test_missing_unexpected_fields_fail_closed(self):
        for kp in ({}, {"Location": None}, {"Location": ""}, {"Location": "Away"}, {"Location": False}):
            verified, loc, qc = verify_game_venue({"neutralSite": False}, kp)
            self.assertFalse(verified)
            self.assertIsNone(loc)
            self.assertIn("MISSING", qc)
        for value in (None, "", "false", 0):
            verified, loc, qc = verify_game_venue({"neutralSite": value}, {"Location": "Home"})
            self.assertFalse(verified)
            self.assertIn("CBBD", qc)

    def test_location_normalization(self):
        self.assertEqual(kenpom_location({"Location": " semi home "}), "Semi-Home")
        self.assertEqual(kenpom_location({"Location": "neutral"}), "Neutral")
        self.assertEqual(kenpom_location({"Location": "HOME"}), "Home")

    def test_provider_review_blocks_all_models(self):
        self.assertFalse(venue_qc_allows_bet(True, "OK_NON_NEUTRAL; REVIEW_PROVIDER"))


if __name__ == "__main__":
    unittest.main()
