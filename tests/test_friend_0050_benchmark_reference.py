"""N45 fully synthetic cash-flow matched 0050 buy-and-hold tests."""
import copy,json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from friend_advanced_accounting_reference import VERSION, LedgerError
from friend_0050_benchmark_reference import benchmark

def case(initial_cash="10000",units=0,date2="2025-11-13",p0="100",p1="100",events=None):
    data={
      "schema_version":VERSION,"start_date":"2025-11-12",
      "initial":{
        "F":{"0050_units":units,"00631L_units":0,"cash":initial_cash},
        "A":{"0050_units":0,"00631L_units":0,"cash":"0"}},
      "days":[
        {"date":"2025-11-12","prices":{"0050":p0,"00631L":"20"},
         "source_verified":True,"benchmark_0050_price_basis":"OFFICIAL_RAW_CLOSE",
         "benchmark_0050_actions_checked":True,"events":[]},
        {"date":date2,"prices":{"0050":p1,"00631L":"20"},
         "source_verified":True,"benchmark_0050_price_basis":"OFFICIAL_RAW_CLOSE",
         "benchmark_0050_actions_checked":True,"events":events or []}
      ]}
    return data

class BenchmarkTests(unittest.TestCase):
    def test_matches_two_sleeve_salary_inflows_and_same_date(self):
        p=Path(__file__).resolve().parents[1]/"examples/FICTIONAL_friend_account_only.json"
        d=json.loads(p.read_text(encoding="utf-8"))
        for row in d["days"]:
            row.update(benchmark_0050_price_basis="OFFICIAL_RAW_CLOSE",
                       benchmark_0050_actions_checked=True)
        r=benchmark(d)
        self.assertTrue(r["same_date_and_external_flows"])
        self.assertEqual(r["opening_capital_twd"],"24000.00")
        self.assertEqual(r["external_net_contributions_twd"],"1000.00")
        self.assertEqual(r["ending_value_twd"],"25208.53")
        self.assertEqual(r["net_pnl_twd"],"208.53")
        self.assertEqual(r["personal_cumulative_twr_pct"],"0.8520")
        self.assertEqual(r["records"][-1]["shares_0050"],249)
        self.assertFalse(r["orders_placed"])

    def test_benchmark_initial_purchase_has_fees(self):
        d=case(initial_cash="1000")
        r=benchmark(d)
        self.assertLess(float(r["ending_value_twd"]),1000)
        self.assertEqual(r["records"][0]["shares_0050"],9)
        self.assertTrue(r["benchmark_entry_fee_included"])

    def test_deposit_added_to_benchmark_not_return(self):
        d=case(initial_cash="10000",events=[
            {"type":"DEPOSIT","sleeve":"F","amount":"500"}])
        r=benchmark(d)
        self.assertEqual(r["external_net_contributions_twd"],"500.00")
        self.assertEqual(r["records"][1]["external_net_flow_twd"],"500.00")
        self.assertEqual(r["records"][1]["bought_shares"],5)
        self.assertLess(float(r["net_pnl_twd"]),0.0)

    def test_internal_fa_transfer_not_external_contribution(self):
        d=case(initial_cash="1000")
        d["initial"]["F"]["cash"]="700"
        d["initial"]["A"]["cash"]="300"
        d["days"][1]["events"]=[{"type":"INTERNAL_TRANSFER","from_sleeve":"F","to_sleeve":"A","amount":"200"}]
        r=benchmark(d)
        self.assertEqual(r["external_net_contributions_twd"],"0.00")
        self.assertEqual(r["records"][1]["bought_shares"],0)

    def test_withdrawal_sells_only_enough_shares(self):
        d=case(initial_cash="20000",events=[
            {"type":"WITHDRAW","sleeve":"F","amount":"700"}])
        r=benchmark(d)
        self.assertEqual(r["records"][-1]["sold_shares"],7)
        self.assertEqual(r["external_net_contributions_twd"],"-700.00")
        self.assertGreaterEqual(float(r["records"][-1]["cash_twd"]),0)

    def test_withdrawal_exceeding_marked_assets_blocks(self):
        d=case(initial_cash="1000",events=[
            {"type":"WITHDRAW","sleeve":"F","amount":"5000"}])
        with self.assertRaises(LedgerError):
            benchmark(d)

    def test_verified_exdiv_creates_receivable_not_phantom_loss(self):
        d=case(initial_cash="0",units=100,p1="99",events=[
            {"type":"EX_DIV","symbol":"0050","issuer_verified":True,
             "amount_per_share":"1","payable_date":"2025-11-14"}])
        r=benchmark(d)
        self.assertEqual(r["records"][1]["unpaid_dividends_twd"],"99.00")
        self.assertEqual(r["records"][1]["bought_shares"],0)
        self.assertEqual(r["records"][0]["nav_twd"],r["records"][1]["nav_twd"])

    def test_dividend_payment_reinvests_only_once(self):
        d=case(initial_cash="0",units=100,p1="99",events=[
            {"type":"EX_DIV","symbol":"0050","issuer_verified":True,
             "amount_per_share":"1","payable_date":"2025-11-14"}])
        third=copy.deepcopy(d["days"][1])
        third["date"]="2025-11-14"
        third["events"]=[{"type":"DIVIDEND_PAID","symbol":"0050","issuer_verified":True}]
        d["days"].append(third)
        r=benchmark(d)
        self.assertEqual(r["records"][-1]["unpaid_dividends_twd"],"0.00")
        self.assertGreater(r["records"][-1]["bought_shares"],0)
        self.assertLess(r["records"][-1]["cash_twd"],"99.00")

    def test_2_to_1_split_no_fake_50pct_benchmark_loss(self):
        d=case(initial_cash="0",units=100,p1="50",events=[
            {"type":"SPLIT","symbol":"0050","issuer_verified":True,"new_units_per_old":"2"}])
        r=benchmark(d)
        self.assertEqual(r["records"][-1]["shares_0050"],r["records"][0]["shares_0050"]*2)
        self.assertEqual(r["records"][-1]["nav_twd"],r["records"][0]["nav_twd"])

    def test_missing_split_50pct_raw_jump_blocks(self):
        d=case(initial_cash="0",units=100,p1="50")
        # Personal ledger might still accept raw quote; benchmark must not.
        with self.assertRaisesRegex(LedgerError,"suspicious 0050 price gap"):benchmark(d)

    def test_0050_total_return_adjusted_price_is_rejected(self):
        d=case()
        d["days"][1]["benchmark_0050_price_basis"]="TOTAL_RETURN_ADJUSTED"
        with self.assertRaisesRegex(LedgerError,"unadjusted 0050"):benchmark(d)

    def test_missing_proof_of_actions_coverage_blocks(self):
        d=case()
        d["days"][1].pop("benchmark_0050_actions_checked")
        with self.assertRaisesRegex(LedgerError,"not independently checked"):benchmark(d)

    def test_unverified_exdiv_blocks(self):
        d=case(initial_cash="0",units=100,p1="99",events=[
            {"type":"EX_DIV","symbol":"0050","issuer_verified":False,
             "amount_per_share":"1","payable_date":"2025-11-14"}])
        with self.assertRaises(LedgerError):benchmark(d)

    def test_1_year_yield_and_xirr_are_dated(self):
        d=case(initial_cash="0",units=100,p1="110",date2="2026-11-12")
        r=benchmark(d)
        self.assertFalse(r["under_one_year_annualization_suppressed"])
        self.assertTrue(9 < r["xirr_pct"] < 11)
        self.assertTrue(9 < r["annualized_twr_pct"] < 11)

    def test_short_period_annualization_suppressed(self):
        d=case(initial_cash="0",units=100,p1="110")
        r=benchmark(d)
        self.assertTrue(r["under_one_year_annualization_suppressed"])
        self.assertIsNone(r["xirr_pct"])
        self.assertIsNone(r["annualized_twr_pct"])

    def test_market_data_must_be_verified_for_benchmark(self):
        d=case()
        d["days"][1]["source_verified"]=False
        with self.assertRaisesRegex(LedgerError,"market price provenance"):benchmark(d)

    def test_initial_benchmark_cannot_buy_0050_one_share(self):
        d=case(initial_cash="50")
        with self.assertRaisesRegex(LedgerError,"too small"):benchmark(d)

if __name__=="__main__":unittest.main()
