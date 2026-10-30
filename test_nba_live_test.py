"""Offline tests protecting the one-month NBA prospective paper ledger."""
import unittest
from datetime import datetime, timezone
from nba_live_test import refresh_ledger,assess,paired_stats

class LiveTestTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,26,16,0,tzinfo=timezone.utc)
        self.game={"game_id":"live-1","game_date":"2026-10-26",
                   "start_utc":"2026-10-27T00:00:00Z",
                   "home_team":"Boston Celtics","away_team":"Miami Heat",
                   "status":"Scheduled","winner":None,"home_score":None,"away_score":None,
                   "model_id":"nba-v1","model_away_win_probability":0.35,
                   "model_v2_id":"nba-v2","model_v2_away_win_probability":0.4}
    def test_capture_only_before_tip(self):
        one=refresh_ledger([self.game],{},self.now)
        self.assertEqual(one["summary"]["games_recorded"],1)
        self.assertEqual(one["summary"]["v2"]["predictions"],1)
        self.assertEqual(one["summary"]["betting_plays"],0)
        self.assertLess(self.now,datetime.fromisoformat(one["records"][0]["start_utc"].replace("Z","+00:00")))
        after=refresh_ledger([self.game],{},datetime(2026,10,27,1,0,tzinfo=timezone.utc))
        self.assertEqual(after["summary"]["games_recorded"],0)
    def test_forecasts_are_frozen_on_next_refresh(self):
        first=refresh_ledger([self.game],{},self.now)
        changed=dict(self.game,model_away_win_probability=.88,
                     model_v2_away_win_probability=.93)
        later=refresh_ledger([changed],first,
                             datetime(2026,10,26,18,0,tzinfo=timezone.utc))
        self.assertEqual(later["records"][0]["v1_away_win_probability"],.35)
        self.assertEqual(later["records"][0]["v2_away_win_probability"],.4)
        self.assertEqual(later["summary"]["last_run_new"],0)
    def test_final_grade_and_brier(self):
        first=refresh_ledger([self.game],{},self.now)
        final=dict(self.game,status="Final",winner="Miami Heat",
                   away_score=110,home_score=101)
        result=refresh_ledger([final],first,datetime(2026,10,27,6,0,tzinfo=timezone.utc))
        self.assertEqual(result["summary"]["games_final"],1)
        self.assertEqual(result["summary"]["paired"]["same_games"],1)
        self.assertAlmostEqual(result["summary"]["v1"]["brier"],(.35-1)**2,places=4)
        self.assertAlmostEqual(result["summary"]["v2"]["brier"],(.4-1)**2,places=4)
        self.assertEqual(result["summary"]["last_run_graded"],1)
    def test_no_hindsight_backfill(self):
        final=dict(self.game,status="Final",winner="Miami Heat")
        result=refresh_ledger([final],{},datetime(2026,10,27,6,0,tzinfo=timezone.utc))
        self.assertEqual(result["summary"]["games_recorded"],0)
    def test_v2_can_first_become_eligible_after_frozen_v1_but_before_tip(self):
        no_v2=dict(self.game,model_v2_away_win_probability=None)
        first=refresh_ledger([no_v2],{},self.now)
        self.assertIsNone(first["records"][0]["v2_away_win_probability"])
        late=datetime(2026,10,26,18,0,tzinfo=timezone.utc)
        later=refresh_ledger([self.game],first,late)
        row=later["records"][0]
        self.assertEqual(row["v1_away_win_probability"],.35)
        self.assertEqual(row["v2_away_win_probability"],.4)
        self.assertEqual(row["v2_first_seen_utc"],"2026-10-26T18:00:00Z")
        self.assertFalse(row["v2_eligible_at_snapshot"])
        self.assertEqual(later["summary"]["v2"]["predictions"],1)
        self.assertEqual(later["summary"]["last_run_new_v2_on_existing"],1)
        self.assertEqual(later["summary"]["last_run_new"],0)
        # Later guesses must not overwrite either model's first recorded values.
        changed=dict(self.game,model_away_win_probability=.93,model_v2_away_win_probability=.95)
        twice=refresh_ledger([changed],later,datetime(2026,10,26,19,0,tzinfo=timezone.utc))
        self.assertEqual(twice["records"][0]["v1_away_win_probability"],.35)
        self.assertEqual(twice["records"][0]["v2_away_win_probability"],.4)
        self.assertEqual(twice["summary"]["last_run_new_v2_on_existing"],0)

    def test_v2_never_backfilled_after_tip_or_final(self):
        no_v2=dict(self.game,model_v2_away_win_probability=None)
        first=refresh_ledger([no_v2],{},self.now)
        after=refresh_ledger([self.game],first,datetime(2026,10,27,1,0,tzinfo=timezone.utc))
        self.assertIsNone(after["records"][0]["v2_away_win_probability"])
        self.assertEqual(after["summary"]["last_run_new_v2_on_existing"],0)
        final=dict(self.game,status="Final",winner="Miami Heat")
        finaled=refresh_ledger([final],first,datetime(2026,10,27,2,0,tzinfo=timezone.utc))
        self.assertIsNone(finaled["records"][0]["v2_away_win_probability"])
        self.assertEqual(finaled["summary"]["v2"]["graded"],0)

    def test_v2_withheld_when_game_changes_or_is_not_scheduled(self):
        no_v2=dict(self.game,model_v2_away_win_probability=None)
        first=refresh_ledger([no_v2],{},self.now)
        changed=dict(self.game,home_team="Different home")
        later=refresh_ledger([changed],first,datetime(2026,10,26,18,0,tzinfo=timezone.utc))
        self.assertIsNone(later["records"][0]["v2_away_win_probability"])
    def test_window_end_and_future_gates(self):
        out=dict(self.game,game_date="2026-11-27",start_utc="2026-11-28T00:00:00Z")
        a=refresh_ledger([out],{},self.now)
        self.assertFalse(a["records"])
        before=dict(self.game,game_date="2026-10-25",start_utc="2026-10-26T00:00:00Z")
        b=refresh_ledger([before],{},self.now)
        self.assertFalse(b["records"])
    def test_duplicate_updates_never_double_count(self):
        old=refresh_ledger([self.game],{},self.now)
        final=dict(self.game,status="Final",winner="Boston Celtics")
        date=datetime(2026,10,27,4,0,tzinfo=timezone.utc)
        twice=refresh_ledger([final],refresh_ledger([final],old,date),date)
        self.assertEqual(twice["summary"]["games_final"],1)
        self.assertEqual(twice["summary"]["last_run_graded"],0)

if __name__=="__main__":
    unittest.main()
