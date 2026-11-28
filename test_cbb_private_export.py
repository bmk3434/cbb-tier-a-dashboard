import base64
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from cbb_private_export import DEST, FIELDS, prepare_batch, export


class ExportTests(unittest.TestCase):
    def test_allowlist_and_pretip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/"batch.csv"
            row = dict.fromkeys(FIELDS, "")
            row.update(Game_Date="2026-11-28", Game_ID="1",
                       Run_Timestamp_PT="2026-11-28T08:00:00-08:00",
                       Start_UTC="2026-11-28T19:00:00Z", KP_Pred_Total="SECRET")
            def write():
                with p.open("w", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=[*FIELDS, "KP_Pred_Total"])
                    w.writeheader()
                    w.writerow(row)
            write()
            data, count = prepare_batch(p, "2026-11-28")
            self.assertEqual(count, 1)
            self.assertNotIn(b"SECRET", data)
            row["Start_UTC"] = "2026-11-28T16:00:00Z"
            write()
            with self.assertRaises(ValueError):
                prepare_batch(p, "2026-11-28")

    def test_private_only_and_idempotent(self):
        repo = dict(full_name=DEST, private=True, default_branch="main")
        with patch("cbb_private_export.request", return_value={**repo, "private":False}) as req:
            with self.assertRaises(ValueError):
                export("test", "2026-11-28", "1", "1", b"x")
            self.assertEqual(req.call_count, 1)
        with patch("cbb_private_export.request", side_effect=[repo,
              {"encoding":"base64","content":base64.b64encode(b"x").decode()}]) as req:
            self.assertEqual(export("test","2026-11-28","1","1",b"x"),"already_saved")
            self.assertEqual(req.call_count, 2)
        with patch("cbb_private_export.request", side_effect=[repo,
              {"encoding":"base64","content":"eQ=="}]):
            with self.assertRaises(ValueError):
                export("test","2026-11-28","1","1",b"x")
        with patch("cbb_private_export.request", side_effect=[repo,
              HTTPError("https://api.github.com",404,"missing",{},None),{}]) as req:
            self.assertEqual(export("test","2026-11-28","1","1",b"x"),"saved")
            self.assertEqual(req.call_args.args[1], "PUT")
            self.assertEqual(req.call_args.args[2], "/contents/snapshots/2026-11-28/1-1.csv")


if __name__ == "__main__":
    unittest.main()
