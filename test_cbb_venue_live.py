#!/usr/bin/env python3
"""One-time live KenPom FanMatch regression check for the Nov 8 Tahoe game."""
import os

from cbb_tierA_scanner import kenpom_get, kenpom_location, norm_name, verify_game_venue

def check():
    key = os.environ.get("KENPOM_API_KEY", "").strip()
    if not key:
        raise RuntimeError("KENPOM_API_KEY is required for the live venue probe")
    slate = kenpom_get("fanmatch", {"d": "2026-11-08"}, key)
    if not isinstance(slate, list):
        raise AssertionError("KenPom FanMatch did not return a list")
    for game in slate:
        names = {norm_name(game.get("Home", "")), norm_name(game.get("Visitor", ""))}
        if "usc" not in names or not ({"wichitastate", "wichitast"} & names):
            continue
        loc = kenpom_location(game)
        print("KenPom 2026-11-08 USC–Wichita Location =", repr(game.get("Location")))
        print("Normalized site =", repr(loc))
        if loc not in ("Neutral", "Semi-Home"):
            raise AssertionError("Expected Tahoe game to be marked Neutral or Semi-Home by KenPom; investigate source!")
        # The recorded CBBD value was false. It must never be actionable.
        cbbd = {"gameId": 374768, "neutralSite": False}
        verified, _, qc = verify_game_venue(cbbd, game)
        if verified:
            raise AssertionError("Wichita State was improperly venue-verified")
        print("PASS: Wichita State QC =", qc)
        return
    raise AssertionError("USC–Wichita State was not found on the 2026-11-08 FanMatch slate")

if __name__ == "__main__":
    check()
