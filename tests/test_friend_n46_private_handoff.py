"""N46 private handoff fake-only tests. No personal holdings in public repo."""
import copy,json,tempfile,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from friend_private_local_ledger import initialize, read_locked_state, LedgerError
from friend_n46_private_handoff import (
    handoff,validate_private_snapshot,morning_readiness,VERSION
)

def marked_public(on="2026-11-11",status="SHADOW_OK",trend="防守",event="無新切換"):
    return {"schema_version":"N43-PUBLIC-MARKET-OBSERVATION-v1",
        "data_date":on,"generated_at_taipei":"2026-11-11T18:00:00+08:00",
        "display_status":status,"trend":trend,"trend_change":event,
        "l2_trading":"正常交易",
        "corporate_action_discovery_complete":False,
        "is_actionable":False}

def fake_ledger(*,synthetic=False):
    repo=Path(__file__).resolve().parents[1]
    x=json.loads((repo/"examples/FICTIONAL_friend_account_only.json").read_text(encoding="utf-8"))
    x["start_date"]="2026-11-09"
    for row,ds in zip(x["days"],["2026-11-09","2026-11-10","2026-11-11"]):
        row["date"]=ds
        for event in row["events"]:
            if event["type"] in ("DEPOSIT","WITHDRAW"):
                event["owner_confirmed_cash_flow"]=True
            elif event["type"] in ("BUY","SELL"):
                event["owner_confirmed_fill"]=True
    if synthetic:
        x["synthetic_only"]=True
    else:
        x.pop("synthetic_only")
        x["owner_verified_starting_positions"]=True
    return x

class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.ledger=self.base/"private-ledger.json"
        self.out=self.base/"private-handoff.json"
    def tearDown(self):
        self.temp.cleanup()
    def make(self,*,synthetic=False,confirmed_on="2026-11-11"):
        initialize(self.ledger,fake_ledger(synthetic=synthetic))
        report=handoff(self.ledger,self.out,confirmed_on=confirmed_on)
        snap=json.loads(self.out.read_text(encoding="utf-8"))
        return report,snap

    def test_owner_confirmed_handoff_private_and_no_network(self):
        r,s=self.make()
        self.assertTrue(r["private_only"])
        self.assertTrue(r["not_sent_to_github"])
        self.assertTrue(r["not_sent_to_chatgpt"])
        self.assertEqual(s["body"]["last_owner_confirmed_holdings_date"],"2026-11-11")
        self.assertFalse(s["body"]["orders_submitted"])
        self.assertFalse(s["body"]["is_broker_synced"])
        self.assertEqual(s["body"]["source_revision"],1)
    def test_private_digest_detects_financial_tampering(self):
        _,s=self.make()
        s["body"]["holdings"]["F"]["cash_twd"]="123456789"
        with self.assertRaisesRegex(LedgerError,"INTEGRITY_FAILED"):
            validate_private_snapshot(s)
    def test_export_rejects_public_git_folder_even_if_filename_private(self):
        self.make()
        pub=Path(__file__).resolve().parents[1]/"DO_NOT_SAVE_PERSONAL_LEDGER.json"
        with self.assertRaisesRegex(LedgerError,"REFUSE_PERSONAL_DATA_IN_PUBLIC"):
            handoff(self.ledger,pub,confirmed_on="2026-11-11")
    def test_export_never_overwrites_source_checkpoint(self):
        self.make()
        with self.assertRaisesRegex(LedgerError,"SOURCE_AND_DESTINATION"):
            handoff(self.ledger,self.ledger,confirmed_on="2026-11-11")
    def test_report_does_not_log_shares_or_money(self):
        report,s=self.make()
        self.assertNotIn("holdings",report)
        self.assertNotIn("valuation",report)
        self.assertNotIn("source_ledger_sha256",report)
        self.assertEqual(s["body"]["valuation"]["nav_twd"],"25213.00")
    def test_verified_market_without_friend_private_data_is_basic_only(self):
        r=morning_readiness(marked_public(),None,on_date="2026-11-12")
        self.assertEqual(r["personal_status"],"BASIC_ONLY_NO_PRIVATE_HANDOFF")
        self.assertEqual(r["daily_action"],"不動")
        self.assertFalse(r["individual_nav_display_allowed"])
    def test_full_verified_private_handoff_can_show_model_estimate(self):
        _,s=self.make()
        r=morning_readiness(marked_public(),s,on_date="2026-11-12")
        self.assertEqual(r["personal_status"],"READY_FOR_PRIVATE_MODEL_ESTIMATE")
        self.assertTrue(r["individual_shares_advice_allowed"])
        self.assertTrue(r["individual_nav_display_allowed"])
        self.assertEqual(r["daily_action"],"不動")
    def test_public_new_bull_is_only_add_candidate(self):
        _,s=self.make()
        r=morning_readiness(marked_public(trend="偏多",event="新轉多"),s,
                            on_date="2026-11-12")
        self.assertEqual(r["daily_action"],"加碼候選")
        self.assertNotIn("BUY",json.dumps(r))
    def test_public_new_bear_is_only_reduce_candidate(self):
        _,s=self.make()
        r=morning_readiness(marked_public(event="新轉防守"),s,
                            on_date="2026-11-12")
        self.assertEqual(r["daily_action"],"減碼候選")
    def test_synthetic_snapshot_cannot_be_claimed_real(self):
        _,s=self.make(synthetic=True)
        r=morning_readiness(marked_public(),s,on_date="2026-11-12")
        self.assertEqual(r["personal_status"],"SYNTHETIC_EXAMPLE_NOT_A_REAL_ACCOUNT")
        self.assertFalse(r["individual_shares_advice_allowed"])
    def test_wrong_market_status_disables_all_new_actions(self):
        _,s=self.make()
        r=morning_readiness(marked_public(status="BLOCKED"),s,on_date="2026-11-12")
        self.assertEqual(r["daily_action"],"資料待查")
        self.assertFalse(r["individual_shares_advice_allowed"])
    def test_market_stale_or_future_disables_all_actions(self):
        _,s=self.make()
        for dat in ("2026-11-01","2026-11-14"):
            r=morning_readiness(marked_public(on=dat),s,on_date="2026-11-12")
            self.assertEqual(r["daily_action"],"資料待查")
    def test_private_record_is_old_so_nav_is_not_fresh(self):
        _,s=self.make()
        r=morning_readiness(marked_public(on="2026-11-12"),s,
                            on_date="2026-11-12")
        self.assertEqual(r["personal_status"],"VALUATION_STALE_HOLDINGS_UNVERIFIED_FOR_MARKET_DAY")
        self.assertFalse(r["individual_nav_display_allowed"])
    def test_owner_cannot_confirm_before_valuation(self):
        initialize(self.ledger,fake_ledger())
        with self.assertRaisesRegex(LedgerError,"OWNER_CANNOT_CONFIRM_BEFORE"):
            handoff(self.ledger,self.out,confirmed_on="2026-11-10")
    def test_private_checksum_missing_disables_personalization(self):
        _,s=self.make()
        s.pop("body_sha256")
        r=morning_readiness(marked_public(),s,on_date="2026-11-12")
        self.assertEqual(r["personal_status"],"PRIVATE_HANDOFF_NOT_VALID")
        self.assertFalse(r["individual_shares_advice_allowed"])
    def test_00631l_halt_disables_personal_action(self):
        _,s=self.make()
        m=marked_public()
        m["l2_trading"]="分割停牌"
        r=morning_readiness(m,s,on_date="2026-11-12")
        self.assertEqual(r["daily_action"],"資料待查")
        self.assertFalse(r["individual_nav_display_allowed"])
    def test_public_data_version_rejected(self):
        _,s=self.make()
        m=marked_public()
        m["schema_version"]="unknown"
        r=morning_readiness(m,s,on_date="2026-11-12")
        self.assertEqual(r["market_status"],"MARKET_DATA_UNAVAILABLE")
    def test_snapshot_has_no_private_repo_url_or_access_key(self):
        _,s=self.make()
        raw=json.dumps(s)
        for part in ("github.com/f034188","ghp_","N43_PUBLIC_REPO_TOKEN",
                     "Authorization","account_number","real_name"):
            self.assertNotIn(part,raw)

if __name__=="__main__":unittest.main()
