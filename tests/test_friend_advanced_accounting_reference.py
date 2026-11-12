"""Fake-only acceptance tests. No user's real holdings or private accounts."""
import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from friend_advanced_accounting_reference import evaluate, LedgerError, VERSION

def base(*,cash="1000",units=0,px="100",endpx="100",end_date="2025-11-13"):
    return {
      "schema_version":VERSION,"start_date":"2025-11-12",
      "initial":{
        "F":{"0050_units":units,"00631L_units":0,"cash":cash},
        "A":{"0050_units":0,"00631L_units":0,"cash":"0"}},
      "days":[
        {"date":"2025-11-12","prices":{"0050":px,"00631L":"20"},"source_verified":True,"events":[]},
        {"date":end_date,"prices":{"0050":endpx,"00631L":"20"},"source_verified":True,"events":[]}
      ]}

def event(type, **data):return {"type":type,**data}

class AccountingAcceptance(unittest.TestCase):
    def test_realistic_fake_fixture_with_two_deposits_and_fees(self):
        import json
        repo=Path(__file__).resolve().parents[1]
        fixture=json.loads((repo/"examples/FICTIONAL_friend_account_only.json").read_text(encoding="utf-8"))
        result=evaluate(fixture)
        self.assertEqual(result["initial_nav_twd"],"24000.00")
        self.assertEqual(result["ending_nav_twd"],"25213.00")
        self.assertEqual(result["external_net_contributions_twd"],"1000.00")
        self.assertEqual(result["net_pnl_twd"],"213.00")
        self.assertEqual(result["cumulative_twr_pct"],"0.8520")
        self.assertIsNone(result["xirr_pct"])
        self.assertEqual(result["records"][1]["units"]["F"]["0050"],105)
        self.assertEqual(result["records"][1]["units"]["A"]["00631L"],60)
        self.assertFalse(result["orders_placed"])

    def test_salary_cash_deposit_is_not_investment_profit(self):
        s=base()
        s["days"][1]["events"]=[event("DEPOSIT",sleeve="F",amount="500")]
        r=evaluate(s)
        self.assertEqual(r["ending_nav_twd"],"1500.00")
        self.assertEqual(r["net_pnl_twd"],"0.00")
        self.assertEqual(r["cumulative_twr_pct"],"0.0000")

    def test_withdrawal_is_not_a_market_loss(self):
        s=base()
        s["days"][1]["events"]=[event("WITHDRAW",sleeve="F",amount="100")]
        r=evaluate(s)
        self.assertEqual(r["ending_nav_twd"],"900.00")
        self.assertEqual(r["external_net_contributions_twd"],"-100.00")
        self.assertEqual(r["net_pnl_twd"],"0.00")
        self.assertEqual(r["cumulative_twr_pct"],"0.0000")

    def test_internal_F_A_transfer_not_external_salary(self):
        s=base()
        s["days"][1]["events"]=[event("INTERNAL_TRANSFER",from_sleeve="F",to_sleeve="A",amount="300")]
        r=evaluate(s)
        self.assertEqual(r["external_net_contributions_twd"],"0.00")
        self.assertEqual(r["records"][-1]["cash"]["F"],"700.00")
        self.assertEqual(r["records"][-1]["cash"]["A"],"300.00")
        self.assertEqual(r["F_weight_pct"],"70.0000")
        self.assertEqual(r["records"][-1]["F_A_check"],"REVIEW_5PP")

    def test_fill_fees_reduce_wealth_and_twr(self):
        s=base()
        s["days"][1]["events"]=[event("BUY",trade_id="fake1",sleeve="F",
                                      symbol="0050",units=5,fill_price="100",all_in_fees="5")]
        r=evaluate(s)
        self.assertEqual(r["net_pnl_twd"],"-5.00")
        self.assertEqual(r["cumulative_twr_pct"],"-0.5000")
        self.assertEqual(r["records"][-1]["cash"]["F"],"495.00")

    def test_trades_cannot_spend_more_than_cash(self):
        s=base(cash="100")
        s["days"][1]["events"]=[event("BUY",trade_id="fake1",sleeve="F",
                                      symbol="0050",units=2,fill_price="100",all_in_fees="0")]
        with self.assertRaisesRegex(LedgerError,"cash would go negative"):evaluate(s)

    def test_sell_more_than_confirmed_shares_blocks(self):
        s=base(units=2)
        s["days"][1]["events"]=[event("SELL",trade_id="fake1",sleeve="F",
                                      symbol="0050",units=3,fill_price="100",all_in_fees="0")]
        with self.assertRaisesRegex(LedgerError,"sell exceeds confirmed"):evaluate(s)

    def test_dividend_ex_date_receivable_offsets_price_drop(self):
        s=base(cash="0",units=100,px="100",endpx="99")
        s["days"][1]["events"]=[event("EX_DIV",symbol="0050",
                                      amount_per_share="1",payable_date="2025-11-14",
                                      issuer_verified=True)]
        r=evaluate(s)
        self.assertEqual(r["ending_nav_twd"],"10000.00")
        self.assertEqual(r["records"][-1]["dividend_receivable"],"100.00")
        self.assertEqual(r["cumulative_twr_pct"],"0.0000")

    def test_dividend_payment_moves_receivable_to_cash_without_new_profit(self):
        s=base(cash="0",units=100,px="100",endpx="99")
        s["days"][1]["events"]=[event("EX_DIV",symbol="0050",
                                      amount_per_share="1",payable_date="2025-11-14",
                                      issuer_verified=True)]
        s["days"].append({"date":"2025-11-14","prices":{"0050":"99","00631L":"20"},
                          "source_verified":True,
                          "events":[event("DIVIDEND_PAID",symbol="0050",issuer_verified=True)]})
        r=evaluate(s)
        self.assertEqual(r["records"][-1]["cash"]["F"],"100.00")
        self.assertEqual(r["records"][-1]["dividend_receivable"],"0.00")
        self.assertEqual(r["net_pnl_twd"],"0.00")

    def test_unmatched_dividend_payment_fails_closed(self):
        s=base(units=1)
        s["days"][1]["events"]=[event("DIVIDEND_PAID",symbol="0050",issuer_verified=True)]
        with self.assertRaisesRegex(LedgerError,"no matching ex-date"):evaluate(s)

    def test_split_doubles_units_no_artificial_50pct_drawdown(self):
        s=base(cash="0")
        s["initial"]["F"]["00631L_units"]=50
        s["days"][1]["prices"]["00631L"]="10"
        s["days"][1]["events"]=[event("SPLIT",symbol="00631L",issuer_verified=True,
                                      new_units_per_old="2")]
        r=evaluate(s)
        self.assertEqual(r["initial_nav_twd"],"1000.00")
        self.assertEqual(r["ending_nav_twd"],"1000.00")
        self.assertEqual(r["cumulative_twr_pct"],"0.0000")
        self.assertEqual(r["records"][-1]["units"]["F"]["00631L"],100)

    def test_reverse_split_fractional_without_cash_in_lieu_blocks(self):
        s=base(cash="0",units=3)
        s["days"][1]["prices"]["0050"]="200"
        s["days"][1]["events"]=[event("SPLIT",symbol="0050",issuer_verified=True,
                                      new_units_per_old="0.5")]
        with self.assertRaisesRegex(LedgerError,"fractional units"):evaluate(s)

    def test_unverified_split_does_not_modify_shares(self):
        s=base(units=2)
        s["days"][1]["events"]=[event("SPLIT",symbol="0050",issuer_verified=False,
                                      new_units_per_old="2")]
        with self.assertRaisesRegex(LedgerError,"unverified split"):evaluate(s)

    def test_unverified_market_quote_blocks(self):
        s=base()
        s["days"][1]["source_verified"]=False
        with self.assertRaisesRegex(LedgerError,"provenance not verified"):evaluate(s)

    def test_duplicate_date_or_trade_id_fails(self):
        s=base()
        s["days"].append(copy.deepcopy(s["days"][1]))
        with self.assertRaisesRegex(LedgerError,"strictly increase"):evaluate(s)
        s=base()
        s["days"][1]["events"]=[
            event("BUY",trade_id="duplicate",sleeve="F",symbol="0050",units=1,fill_price="100",all_in_fees="0"),
            event("BUY",trade_id="duplicate",sleeve="F",symbol="0050",units=1,fill_price="100",all_in_fees="0")]
        with self.assertRaisesRegex(LedgerError,"duplicate trade ID"):evaluate(s)

    def test_365_days_positive_return_yields_twr_and_xirr(self):
        s=base(cash="0",units=100,px="100",endpx="110",end_date="2026-11-12")
        r=evaluate(s)
        self.assertFalse(r["under_one_year_annualization_suppressed"])
        self.assertEqual(r["net_pnl_twd"],"1000.00")
        self.assertEqual(r["cumulative_twr_pct"],"10.0000")
        self.assertAlmostEqual(r["xirr_pct"],10.0,delta=.04)
        self.assertAlmostEqual(r["annualized_twr_pct"],10.0,delta=.04)

    def test_young_account_no_fake_annualized_return(self):
        s=base(cash="0",units=100,px="100",endpx="110")
        r=evaluate(s)
        self.assertEqual(r["cumulative_twr_pct"],"10.0000")
        self.assertIsNone(r["xirr_pct"])
        self.assertIsNone(r["annualized_twr_pct"])

    def test_absent_benchmark_labeled_unavailable(self):
        r=evaluate(base())
        self.assertTrue(r["benchmark_0050"].startswith("NOT_CALCULATED"))
        self.assertFalse(r["actual_broker_holdings_verified"])

    def test_requires_two_ledger_buckets_not_aggregate_portfolio_only(self):
        s=base()
        s["initial"].pop("A")
        with self.assertRaisesRegex(LedgerError,"A: require exact"):evaluate(s)

    def test_unknown_event_and_malformed_row_are_blocked(self):
        s=base()
        s["days"][1]["events"]=[event("FILL_MY_ORDER",symbol="0050")]
        with self.assertRaisesRegex(LedgerError,"unknown event type"):evaluate(s)
        s=base()
        s["days"][1]["events"]=[None]
        with self.assertRaisesRegex(LedgerError,"list of objects"):evaluate(s)

if __name__=="__main__":unittest.main()
