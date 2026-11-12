"""N45: local PRIVATE ledgers only; test fixture explicitly fictitious."""
import copy,json,os,stat,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from friend_private_local_ledger import (
    initialize,append_day,read_locked_state,safe_path,sha,STORE_VERSION,LedgerError
)

def fake():
    root=Path(__file__).resolve().parents[1]
    return json.loads((root/"examples/FICTIONAL_friend_account_only.json").read_text(encoding="utf-8"))

def newday(date_="2025-11-17",events=None):
    return {"date":date_,"prices":{"0050":"101","00631L":"21"},
            "source_verified":True,"events":events or []}

class SafeLocalCheckpoint(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.file=self.root/"friend-private.json"
        self.base=fake()
    def tearDown(self):
        self.tmp.cleanup()

    def test_initialize_is_private_0600_with_hash_and_valid_accounting(self):
        x=initialize(self.file,self.base)
        self.assertEqual(x["revision"],1)
        self.assertTrue(self.file.exists())
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode),0o600)
        self.assertTrue(x["not_uploaded_to_github"])
        self.assertTrue(x["not_automatically_read_by_scheduled_chatgpt"])
        self.assertEqual(x["net_pnl_twd"],"213.00")

    def test_append_one_new_session_advances_revision_exactly_once(self):
        a=initialize(self.file,self.base)
        b=append_day(self.file,newday(),expected_sha256=a["ledger_sha256"])
        self.assertEqual(b["revision"],2)
        self.assertNotEqual(a["ledger_sha256"],b["ledger_sha256"])
        raw=read_locked_state(self.file)
        self.assertEqual(raw["previous_revision_sha256"],a["ledger_sha256"])
        self.assertEqual(raw["ledger"]["days"][-1]["date"],"2025-11-17")

    def test_retry_same_day_cannot_duplicate(self):
        a=initialize(self.file,self.base)
        b=append_day(self.file,newday(),expected_sha256=a["ledger_sha256"])
        with self.assertRaisesRegex(LedgerError,"DUPLICATE_OR_OUT_OF_ORDER"):
            append_day(self.file,newday(),expected_sha256=b["ledger_sha256"])
        self.assertEqual(read_locked_state(self.file)["revision"],2)

    def test_stale_expected_sha_is_rejected_without_rewrite(self):
        a=initialize(self.file,self.base)
        with self.assertRaisesRegex(LedgerError,"STALE_OR_MISSING"):
            append_day(self.file,newday(),expected_sha256="0"*64)
        self.assertEqual(read_locked_state(self.file)["revision"],1)

    def test_unconfirmed_real_fill_is_not_saved(self):
        initial=fake()
        initial.pop("synthetic_only")
        initial["owner_verified_starting_positions"]=True
        for row in initial["days"]:
            for e in row.get("events",[]):
                if e.get("type") in ("DEPOSIT","WITHDRAW"):
                    e["owner_confirmed_cash_flow"]=True
        # Trade on day 2 is unconfirmed, should block on init.
        with self.assertRaisesRegex(LedgerError,"MODEL_SUGGESTION_IS_NOT_CONFIRMED_FILL"):
            initialize(self.file,initial)
        self.assertFalse(self.file.exists())

    def test_real_cashflow_must_be_owner_confirmed(self):
        initial=fake()
        initial.pop("synthetic_only")
        initial["owner_verified_starting_positions"]=True
        for row in initial["days"]:
            for e in row.get("events",[]):
                if e["type"] in ("BUY","SELL"):e["owner_confirmed_fill"]=True
        with self.assertRaisesRegex(LedgerError,"EXTERNAL_CASHFLOW_NOT_CONFIRMED"):
            initialize(self.file,initial)

    def test_real_owner_confirmed_positions_events_can_save(self):
        initial=fake()
        initial.pop("synthetic_only")
        initial["owner_verified_starting_positions"]=True
        for row in initial["days"]:
            for e in row.get("events",[]):
                if e["type"] in ("BUY","SELL"):e["owner_confirmed_fill"]=True
                if e["type"] in ("DEPOSIT","WITHDRAW"):e["owner_confirmed_cash_flow"]=True
        x=initialize(self.file,initial)
        self.assertEqual(x["net_pnl_twd"],"213.00")

    def test_modified_account_file_is_detected_as_corrupt(self):
        initialize(self.file,self.base)
        x=json.loads(self.file.read_text(encoding="utf-8"))
        x["ledger"]["initial"]["F"]["cash"]="9999999"
        self.file.write_text(json.dumps(x),encoding="utf-8")
        with self.assertRaisesRegex(LedgerError,"CHECKPOINT_HASH_MISMATCH"):
            read_locked_state(self.file)

    def test_append_bad_market_date_does_not_destroy_old_file(self):
        a=initialize(self.file,self.base)
        bad=newday();bad["source_verified"]=False
        with self.assertRaisesRegex(LedgerError,"market price provenance"):
            append_day(self.file,bad,expected_sha256=a["ledger_sha256"])
        self.assertEqual(read_locked_state(self.file)["ledger_sha256"],a["ledger_sha256"])

    def test_unverified_suggested_trade_rejected_on_new_day(self):
        a=initialize(self.file,self.base)
        b=newday(events=[{"type":"BUY","sleeve":"F","symbol":"0050",
                          "units":1,"trade_id":"FAKE-NEW-1",
                          "fill_price":"101","all_in_fees":"1"}])
        c=copy.deepcopy(self.base)
        c.pop("synthetic_only")
        c["owner_verified_starting_positions"]=True
        for row in c["days"]:
            for e in row.get("events",[]):
                if e["type"] in ("DEPOSIT","WITHDRAW"):e["owner_confirmed_cash_flow"]=True
                if e["type"] in ("BUY","SELL"):e["owner_confirmed_fill"]=True
        other=self.root/"realconfirmed.json"
        start=initialize(other,c)
        with self.assertRaisesRegex(LedgerError,"MODEL_SUGGESTION_IS_NOT_CONFIRMED_FILL"):
            append_day(other,b,expected_sha256=start["ledger_sha256"])

    def test_file_cannot_be_created_in_public_repository(self):
        root=Path(__file__).resolve().parents[1]
        with self.assertRaisesRegex(LedgerError,"REFUSE_PERSONAL_DATA_IN_PUBLIC"):
            safe_path(root/"DO_NOT_CREATE_PERSONAL_DATA.json")

    def test_new_checkpoint_does_not_overwrite_existing(self):
        initialize(self.file,self.base)
        with self.assertRaisesRegex(LedgerError,"LEDGER_ALREADY_EXISTS"):
            initialize(self.file,self.base)

    def test_missing_or_wrong_schema_blocked(self):
        b=fake();b["schema_version"]="IMPERSONATOR"
        with self.assertRaisesRegex(LedgerError,"VERSION_MISMATCH"):
            initialize(self.file,b)

    def test_owner_denied_start_positions_blocks(self):
        b=fake();b.pop("synthetic_only")
        with self.assertRaisesRegex(LedgerError,"STARTING_POSITIONS_NOT_CONFIRMED"):
            initialize(self.file,b)

    def test_append_requires_existing_file(self):
        with self.assertRaisesRegex(LedgerError,"NO_LOCAL_LEDGER"):
            append_day(self.file,newday(),expected_sha256="x")

if __name__=="__main__":unittest.main()
