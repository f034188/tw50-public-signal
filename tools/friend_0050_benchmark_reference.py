#!/usr/bin/env python3
"""Auditable 0050 buy-and-hold benchmark, SAME start date and external flows.

This intentionally uses official UNADJUSTED 0050 closes and separately verified
ETF split/dividend actions so distributions are never counted twice. Missing
corporate-action verification fails closed. Prices, cash flows and events are
INPUTS, never looked up/fabricated. This does not trade or publish private data.

Convention shared with friend_advanced_accounting_reference:
* starting portfolio NAV (at start close) becomes initial benchmark capital;
* 0050 is bought with whole shares and documented all-in per-side rate;
* outside deposits/withdrawals effective at close; internal F/A transfers and
  portfolio BUY/SELL events do NOT change benchmark external cash flow;
* cash dividends become receivable on ex-date, paid on pay-date, reinvestable
  at that day's verified close. Any unfilled cash remains cash.
* benchmark TWR includes initial entry cost; friend's pre-existing initial
  snapshot is market-marked without retrospective acquisition cost. Disclose.
"""
from __future__ import annotations
from datetime import date
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING, ROUND_HALF_UP
from friend_advanced_accounting_reference import (
    VERSION as LEDGER_VERSION, LedgerError, load_snapshot, marks, total,
    evaluate, dnum, day, dated_rate, money, pct,
)
BMARK_VERSION="FRIEND-0050-CASHFLOW-MATCHED-v1"
BUY_FEE=Decimal(".001625")
SELL_FEE=Decimal(".002625")
ONE=Decimal(1)
CENT=Decimal(".01")

def fee(gross, rate):
    return (gross*rate).quantize(CENT, rounding=ROUND_HALF_UP)

def buy_with_cash(state, price):
    available=state["cash"]
    unit_cost=price*(ONE+BUY_FEE)
    if unit_cost<=0:raise LedgerError("nonpositive benchmark buying cost")
    n=int((available/unit_cost).to_integral_value(rounding=ROUND_FLOOR))
    while n>0 and price*Decimal(n)+fee(price*Decimal(n),BUY_FEE)>available:
        n-=1
    if n>0:
        gross=price*Decimal(n)
        paid=gross+fee(gross,BUY_FEE)
        state["units"]+=n
        state["cash"]-=paid
        state["transaction_fees"]+=paid-gross
    return n

def ensure_cash_for_withdrawal(state, price, need):
    if state["cash"]>=need:return 0
    unit_proceeds=price*(ONE-SELL_FEE)
    if unit_proceeds<=0:raise LedgerError("nonpositive benchmark net sell price")
    shortage=need-state["cash"]
    units=int((shortage/unit_proceeds).to_integral_value(rounding=ROUND_CEILING))
    if units>state["units"]:
        raise LedgerError("benchmark cannot fund matched external withdrawal")
    while True:
        gross=Decimal(units)*price
        proceeds=gross-fee(gross,SELL_FEE)
        if state["cash"]+proceeds>=need:break
        units+=1
        if units>state["units"]:
            raise LedgerError("benchmark cannot fund withdrawal after transaction costs")
    state["units"]-=units
    state["cash"]+=proceeds
    state["transaction_fees"]+=fee(gross,SELL_FEE)
    return units

def nav(state, price):
    return state["cash"]+Decimal(state["units"])*price+sum(
        state["receivables"].values(),Decimal(0))

def check_raw_day(row, old_price=None):
    if row.get("benchmark_0050_price_basis")!="OFFICIAL_RAW_CLOSE":
        raise LedgerError("benchmark requires unadjusted 0050 official raw close")
    if row.get("benchmark_0050_actions_checked") is not True:
        raise LedgerError("0050 dividends and splits not independently checked")
    p=marks(row)["0050"]
    events=row.get("events",[])
    if not isinstance(events,list) or any(not isinstance(e,dict) for e in events):
        raise LedgerError("events must be list of objects")
    split=any(e.get("type")=="SPLIT" and e.get("symbol")=="0050" for e in events)
    if old_price is not None and not split and abs(p/old_price-ONE)>Decimal(".40"):
        raise LedgerError("suspicious 0050 price gap; verify split and raw quote")
    return p

def benchmark(data):
    if not isinstance(data,dict) or data.get("schema_version")!=LEDGER_VERSION:
        raise LedgerError("unknown friend ledger schema")
    # First verify every F/A transaction and both ETF prices with the shared
    # reference engine. Actual private ledgers never leave the caller.
    personal=evaluate(data)
    rows=data["days"]
    start=day(rows[0]["date"])
    price=check_raw_day(rows[0])
    opening=dnum(personal["initial_nav_twd"],"opening_nav",allow_zero=False)
    state={"units":0,"cash":opening,"receivables":{},"transaction_fees":Decimal(0)}
    purchased=buy_with_cash(state,price)
    if purchased==0:raise LedgerError("initial portfolio is too small to buy a 0050 share")
    prev=nav(state,price)
    factor=prev/opening   # include initial purchase fee
    external_net=Decimal(0)
    flows=[(start,-opening)]
    records=[]
    def receipt(at, ext, bought, sold):
        value=nav(state,price)
        records.append({
            "date":at.isoformat(),"shares_0050":state["units"],
            "cash_twd":money(state["cash"]),
            "unpaid_dividends_twd":money(sum(state["receivables"].values(),Decimal(0))),
            "nav_twd":money(value),"external_net_flow_twd":money(ext),
            "bought_shares":bought,"sold_shares":sold,
            "all_in_fees_to_date_twd":money(state["transaction_fees"])
        })
    receipt(start,Decimal(0),purchased,0)
    last=start
    for row in rows[1:]:
        at=day(row["date"])
        if at<=last:raise LedgerError("nonmonotonic benchmark dates")
        raw_price=check_raw_day(row,price)
        actions=row.get("events",[])
        # Verified company actions become effective before close; 0050-only.
        split_today=False
        exdiv_today=False
        payment_today=False
        for e in actions:
            if e.get("symbol")!="0050":continue
            kind=e.get("type")
            if kind not in ("SPLIT","EX_DIV","DIVIDEND_PAID"):continue
            if e.get("issuer_verified") is not True:
                raise LedgerError("unverified benchmark corporate action")
            if kind=="SPLIT":
                if split_today:raise LedgerError("duplicate 0050 split")
                ratio=dnum(e.get("new_units_per_old"),"benchmark_split_ratio",allow_zero=False)
                if ratio==1:raise LedgerError("invalid 0050 split ratio")
                new_units=Decimal(state["units"])*ratio
                if new_units!=new_units.to_integral_value():
                    raise LedgerError("fractional benchmark shares require cash-in-lieu")
                state["units"]=int(new_units)
                split_today=True
            elif kind=="EX_DIV":
                if exdiv_today:raise LedgerError("duplicate 0050 exdiv")
                pay=day(e.get("payable_date"))
                if pay<at:raise LedgerError("benchmark dividend pay date is before ex-date")
                dividend=dnum(e.get("amount_per_share"),"0050 dividend")
                key="0050|"+pay.isoformat()
                if key in state["receivables"]:
                    raise LedgerError("duplicate benchmark dividend receivable")
                state["receivables"][key]=Decimal(state["units"])*dividend
                exdiv_today=True
            else:
                if payment_today:raise LedgerError("duplicate 0050 dividend payment")
                key="0050|"+at.isoformat()
                if key not in state["receivables"]:
                    raise LedgerError("benchmark received unearned dividend")
                state["cash"]+=state["receivables"].pop(key)
                payment_today=True
        before=nav(state,raw_price)
        if before<=0:raise LedgerError("benchmark NAV not positive")
        ext=Decimal(0)
        flow_events=[e for e in actions if e.get("type") in ("DEPOSIT","WITHDRAW")]
        for e in flow_events:
            sleeve=e.get("sleeve")
            if sleeve not in ("F","A"):raise LedgerError("unassigned external cash flow")
            amount=dnum(e.get("amount"),"external flow",allow_zero=False)
            ext+=amount if e["type"]=="DEPOSIT" else -amount
        state["cash"]+=ext
        if state["cash"]<0:
            sold=ensure_cash_for_withdrawal(state,raw_price,-state["cash"])
        else:
            sold=0
        # Negative cash after funding the withdrawal means impossible.
        if state["cash"]<0:raise LedgerError("benchmark short cash")
        bought=buy_with_cash(state,raw_price) if ext>0 or payment_today else 0
        after=nav(state,raw_price)
        if after<=0 or before+ext<=0:
            raise LedgerError("benchmark NAV nonpositive")
        factor*=(before/prev)*(after/(before+ext))
        prev=after
        external_net+=ext
        if ext:flows.append((at,-ext))
        receipt(at,ext,bought,sold)
        last=at
        price=raw_price
    elapsed=(last-start).days
    annual=None
    xirr=None
    if elapsed>=365:
        annual=float(factor)**(365.25/elapsed)-1
        xirr=dated_rate(flows+[(last,prev)])
    # Independent audit that both sleeves and benchmark received exactly
    # the same dated EXTERNAL contributions and withdrawals.
    if money(external_net)!=personal["external_net_contributions_twd"]:
        raise LedgerError("portfolio-vs-benchmark external flow mismatch")
    delta=Decimal(pct(factor-ONE))-Decimal(personal["cumulative_twr_pct"])
    return {
        "schema_version":BMARK_VERSION,
        "status":"CASHFLOW_MATCHED_REFERENCE_NOT_BROKER_VERIFIED",
        "data_from":start.isoformat(),"data_through":last.isoformat(),
        "same_date_and_external_flows":True,
        "0050_price_basis":"OFFICIAL_RAW_CLOSE_PLUS_DIVIDENDS_AS_CASH_EVENTS",
        "0050_dividends_reinvested_on_confirmed_payment_day":True,
        "trading_fee_assumption":{"buy_percent":"0.1625","sell_percent":"0.2625"},
        "opening_capital_twd":money(opening),
        "ending_value_twd":money(prev),
        "external_net_contributions_twd":money(external_net),
        "net_pnl_twd":money(prev-opening-external_net),
        "cumulative_twr_pct":pct(factor-ONE),
        "annualized_twr_pct":None if annual is None else round(annual*100,6),
        "xirr_pct":None if xirr is None else round(xirr*100,6),
        "under_one_year_annualization_suppressed":elapsed<365,
        "personal_cumulative_twr_pct":personal["cumulative_twr_pct"],
        "benchmark_minus_personal_twr_pp":str(delta),
        "benchmark_entry_fee_included":True,
        "personal_initial_positions_historical_cost_not_reconstructed":True,
        "known_comparison_limit":"Existing personal holdings are marked at start; benchmark enters at same date with new purchase cost. For a fair fully new portfolio both must apply initial entry costs.",
        "actual_market_source_attested_by_input_not_independently_fetched":True,
        "orders_placed":False,
        "records":records
    }

def main():
    import argparse,json
    from pathlib import Path
    p=argparse.ArgumentParser(description="Offline, fictional/private data only")
    p.add_argument("ledger",help="private local F/A ledger; never publish")
    p.add_argument("--output",default="-")
    args=p.parse_args()
    out=json.dumps(benchmark(json.loads(Path(args.ledger).read_text(encoding="utf-8"))),
                   ensure_ascii=False,indent=2)
    if args.output=="-":print(out)
    else:Path(args.output).write_text(out+"\n",encoding="utf-8")

if __name__=="__main__":main()
