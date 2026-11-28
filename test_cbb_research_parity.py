"""API-free integration test: frozen model predictions and QC must be identical."""
import csv
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import cbb_tierA_scanner as scanner
from cbb_research import summarize


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        instant = datetime(2026, 11, 28, 16, 0, tzinfo=timezone.utc)
        return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)


class ScannerParityTests(unittest.TestCase):
    def test_opt_in_ledger_and_archive_cache_preserve_all_outputs(self):
        archive = [
            dict(TeamName=name, AdjEM=oe-de, AdjOE=oe, AdjDE=de, AdjTempo=tempo)
            for name, oe, de, tempo in [
                ("Test Home", 110, 99, 68), ("Test Away", 103, 101, 72),
                ("Neutral Home", 115, 102, 66), ("Neutral Away", 108, 105, 70),
            ]
        ]
        games, fanmatch = [], []
        for gid, home, away, location in [
            (101, "Test Home", "Test Away", "Home"),
            (102, "Neutral Home", "Neutral Away", "Neutral"),
        ]:
            games.append(dict(gameId=gid, homeTeam=home, awayTeam=away,
                              homeTeamId=gid*2, awayTeamId=gid*2+1,
                              homeConference="Test", awayConference="Test",
                              neutralSite=False, seasonType="regular",
                              startDate="2026-11-28T23:00:00Z",
                              lines=[dict(provider="Draft Kings", spread=5.5,
                                          overUnder=142.5, homeMoneyline=175,
                                          awayMoneyline=-205)]))
            fanmatch.append(dict(Home=home, Visitor=away, Location=location,
                                 HomePred=73, VisitorPred=69, HomeWP=63))
        api_calls = {"archive": 0, "fanmatch": 0, "lines": 0}
        def kp(endpoint, params, key):
            api_calls[endpoint] += 1
            if endpoint == "archive":
                self.assertEqual(params["d"], "2026-11-27")
                return archive
            self.assertEqual(endpoint, "fanmatch")
            return fanmatch
        def cbbd(endpoint, params, key):
            self.assertEqual(endpoint, "/lines")
            api_calls["lines"] += 1
            return games

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "research.sqlite"
            cache = root / "archive"
            def run(name, extra):
                out = root / name
                out.mkdir()
                with patch.object(scanner, "OUTPUT_DIR", out), \
                     patch.object(scanner, "datetime", FixedDateTime), \
                     patch.object(scanner, "cbbd_get", side_effect=cbbd), \
                     patch.object(scanner, "kenpom_get", side_effect=kp), \
                     patch.object(scanner, "fetch_teamstats_history", return_value=[]), \
                     patch.object(scanner, "load_aliases", return_value={}), \
                     patch.object(scanner, "load_overrides", return_value={}), \
                     patch.object(scanner, "create_override_template_if_missing"), \
                     patch.dict(os.environ, {"CBBD_API_KEY":"synthetic", "KENPOM_API_KEY":"synthetic"}), \
                     patch("sys.argv", ["scanner", "--date", "2026-11-28", "--no-snapshots", *extra]), \
                     redirect_stdout(io.StringIO()):
                    self.assertEqual(scanner.main(), 0)
                return {p.name: p.read_bytes() for p in out.glob("*.csv")}

            legacy = run("legacy", [])
            options = ["--research-db", str(db), "--pregame-archive-cache", str(cache)]
            cold = run("cold", options)
            warm = run("warm", options)
            self.assertEqual(legacy, cold)
            self.assertEqual(legacy, warm)
            self.assertEqual(api_calls, {"archive": 2, "fanmatch": 3, "lines": 3})
            self.assertEqual(summarize(db)["games"], 2)
            self.assertEqual(summarize(db)["snapshots"], 2)
            with (root/"legacy"/"all_scored_2026-11-28.csv").open() as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 2)
            self.assertEqual({r["KP_Location"] for r in rows}, {"Home", "Neutral"})


if __name__ == "__main__":
    unittest.main()
