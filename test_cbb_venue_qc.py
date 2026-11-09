#!/usr/bin/env python3
"""Regression coverage for KenPom-authoritative venue selection (no API keys)."""
import unittest

from cbb_tierA_scanner import (
    apply_neutral_override,
    kenpom_location,
    venue_qc_allows_bet,
    verify_game_venue,
)


class TestVenueQC(unittest.TestCase):
    def test_wichita_usc_kenpom_neutral_overrides_incorrect_cbbd_flag(self):
        # Nov 8 Tahoe: CBBD false, FanMatch Neutral. The venue MUST be neutral,
        # but it still needs line-orientation verification before wagering.
        game = {"gameId": 374768, "homeTeam": "Wichita State", "awayTeam": "USC", "neutralSite": False}
        fm = {"Home": "Wichita St.", "Visitor": "USC", "Location": "Neutral"}
        verified, location, qc = verify_game_venue(game, fm, "NORMAL")
        self.assertTrue(verified)
        self.assertEqual(location, "Neutral")
        self.assertEqual(qc, "VENUE_KENPOM_VERIFIED")
        corrected = dict(game, neutralSite=(location == "Neutral"))
        _, override_qc, _ = apply_neutral_override(corrected, {"spread": 6.5}, {})
        self.assertEqual(override_qc, "NEUTRAL_REVIEW_REQUIRED")
        self.assertFalse(venue_qc_allows_bet(verified, override_qc))

    def test_real_home_kenpom_is_authoritative_even_if_cbbd_true(self):
        game = {"gameId": 7, "neutralSite": True}
        verified, location, qc = verify_game_venue(game, {"Location": "Home"}, "NORMAL")
        self.assertTrue(verified)
        self.assertEqual(location, "Home")
        corrected = dict(game, neutralSite=False)
        _, home_qc, _ = apply_neutral_override(corrected, {"spread": -6, "homeMoneyline": -250}, {})
        self.assertEqual(home_qc, "OK_NON_NEUTRAL")
        self.assertTrue(venue_qc_allows_bet(verified, home_qc))

    def test_cbbd_missing_does_not_block_if_kenpom_known(self):
        for cb in (None, "", "false", 0):
            for site in ("Home", "Neutral"):
                with self.subTest(cb=cb, site=site):
                    verified, loc, qc = verify_game_venue({"neutralSite": cb}, {"Location": site})
                    self.assertTrue(verified)
                    self.assertEqual(loc, site)
                    self.assertEqual(qc, "VENUE_KENPOM_VERIFIED")

    def test_confirmed_neutral_requires_verified_spread_orientation(self):
        game = {"gameId": 55, "neutralSite": True}
        verified, location, qc = verify_game_venue(game, {"Location": "Neutral"})
        self.assertTrue(verified)
        self.assertEqual(location, "Neutral")
        _, override_qc, _ = apply_neutral_override(game, {"spread": 6}, {})
        self.assertEqual(override_qc, "NEUTRAL_REVIEW_REQUIRED")
        self.assertFalse(venue_qc_allows_bet(verified, override_qc))
        new_line, override_qc, source = apply_neutral_override(game, {"spread": 6}, {
            "55": {"Home_Spread_Override": "6.5", "Source_URL": "https://example.org/verified"}
        })
        self.assertEqual(new_line["spread"], 6.5)
        self.assertEqual(override_qc, "NEUTRAL_VERIFIED_OVERRIDE")
        self.assertTrue(venue_qc_allows_bet(verified, override_qc))

    def test_home_designation_reversed_is_held(self):
        verified, site, qc = verify_game_venue({"neutralSite": False}, {"Location": "Home"}, "REVERSED")
        self.assertFalse(verified)
        self.assertEqual(site, "Home")
        self.assertEqual(qc, "VENUE_HOME_TEAM_ORIENTATION_REVIEW_REQUIRED")
        self.assertFalse(venue_qc_allows_bet(verified, qc))

    def test_semi_home_is_held_without_validated_adjustment(self):
        for cb in (None, False, True):
            verified, loc, qc = verify_game_venue({"neutralSite": cb}, {"Location": "Semi-Home"})
            self.assertFalse(verified)
            self.assertEqual(loc, "Semi-Home")
            self.assertIn("SEMI_HOME", qc)

    def test_missing_kenpom_location_fails_closed(self):
        for kp in ({}, {"Location": None}, {"Location": ""}, {"Location": "Away"}, {"Location": False}):
            verified, loc, qc = verify_game_venue({"neutralSite": False}, kp)
            self.assertFalse(verified)
            self.assertIsNone(loc)
            self.assertIn("MISSING", qc)
            self.assertFalse(venue_qc_allows_bet(verified, qc))

    def test_location_normalization(self):
        self.assertEqual(kenpom_location({"Location": " semi home "}), "Semi-Home")
        self.assertEqual(kenpom_location({"Location": "neutral"}), "Neutral")
        self.assertEqual(kenpom_location({"Location": "HOME"}), "Home")

    def test_provider_review_remains_blocked(self):
        self.assertFalse(venue_qc_allows_bet(True, "OK_NON_NEUTRAL; REVIEW_PROVIDER"))


if __name__ == "__main__":
    unittest.main()
