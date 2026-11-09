#!/usr/bin/env python3
"""Verify real KenPom FanMatch location and neutral gating for Nov 8 Tahoe."""
import os

from cbb_tierA_scanner import (
    apply_neutral_override,
    kenpom_get,
    kenpom_location,
    norm_name,
    venue_qc_allows_bet,
    verify_game_venue,
)


def check():
    key = os.environ.get("KENPOM_API_KEY", "").strip()
    if not key:
        raise RuntimeError("KENPOM_API_KEY is required for the live venue probe")
    slate = kenpom_get("fanmatch", {"d": "2026-11-08"}, key)
    if not isinstance(slate, list):
        raise AssertionError("KenPom FanMatch did not return a list")
    for kp in slate:
        names = {norm_name(kp.get("Home", "")), norm_name(kp.get("Visitor", ""))}
        if "usc" not in names or not ({"wichitastate", "wichitast"} & names):
            continue
        site = kenpom_location(kp)
        print("KenPom 2026-11-08 USC–Wichita Location =", repr(kp.get("Location")))
        if site != "Neutral":
            raise AssertionError("Wichita at Tahoe must be KenPom Neutral; verify source")
        # Recorded CBBD flag is false; KenPom must override it.
        game = {"gameId": 374768, "neutralSite": False}
        verified, loc, qc = verify_game_venue(game, kp)
        if not verified or loc != "Neutral":
            raise AssertionError(f"KenPom was not authoritative: {loc} {qc}")
        corrected = dict(game, neutralSite=True)
        _, override_qc, _ = apply_neutral_override(corrected, {"spread": 6.5}, {})
        if override_qc != "NEUTRAL_REVIEW_REQUIRED" or venue_qc_allows_bet(verified, override_qc):
            raise AssertionError("Wichita State neutral spread was approved without a verified override")
        print("PASS: KenPom Neutral overrides CBBD false; wager held for neutral line verification")
        return
    raise AssertionError("USC–Wichita State was not found on 2026-11-08 FanMatch")


if __name__ == "__main__":
    check()
