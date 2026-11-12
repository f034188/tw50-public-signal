#!/usr/bin/env python3
"""Friend advanced-mode accounting: deterministic reference, NOT a brokerage bot.

All examples must be synthetic in the public repository. Personal ledgers belong
in the friend's own private environment, never in GitHub Pages, issues or chats
shared with the strategy author.

Convention:
  - The first observation establishes a price-marked starting NAV, NO trades.
  - Later rows use VERIFIED *trading-day* closes; registered splits/ex-divs/
    payments precede close-time deposits/withdrawals, followed by actual trades.
  - External cash flows are effective at the close, after that day's market
    movement. Trade costs are included in time-weighted returns.
  - Exact decimal share counts and TWD accounting internally; no borrowing.
  - Missing official data, timestamps, events or cash-flow history are not
    silently filled. It reports math, never routes an order.
"""
from __future__ import annotations
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json

VERSION="FRIEND-LEDGER-REFERENCE-v1"
SYMBOLS=("0050","00631L")
SLEEVES=("F","A")
CENTS=Decimal("0.01")

class LedgerError(ValueError):
    pass

def dnum(value, name, *, allow_zero=True):
    if isinstance(value,bool):
        raise LedgerError(f"{name}: boolean is not a number")
    try:
        n=Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError):
        raise LedgerError(f"{name}: invalid decimal") from None
    if not n.is_finite() or n<0 or (not allow_zero and n==0):
        raise LedgerError(f"{name}: must be finite nonnegative")
    return n

def whole(value, name):
    n=dnum(value,name)
    if n!=n.to_integral_value():
        raise LedgerError(f"{name}: fractional units need separate cash-in-lieu record")
    return int(n)

def day(value):
    try:
        return date.fromisoformat(value)
    except (ValueError,TypeError):
        raise LedgerError("invalid ISO date") from None

def money(v):
    return str(v.quantize(CENTS,rounding=ROUND_HALF_UP))

def pct(v):
    return str((v*Decimal("100")).quantize(Decimal("0.0001"),rounding=ROUND_HALF_UP))

def load_snapshot(s):
    if not isinstance(s,dict):
        raise LedgerError("missing F/A starting snapshot")
    out={}
    for sleeve in SLEEVES:
        x=s.get(sleeve)
        if not isinstance(x,dict) or set(x)!={"0050_units","00631L_units","cash"}:
            raise LedgerError(f"{sleeve}: require exact units and cash fields")
        out[sleeve]={
            "0050_units":whole(x["0050_units"],sleeve+"_0050"),
            "00631L_units":whole(x["00631L_units"],sleeve+"_00631L"),
            "cash":dnum(x["cash"],sleeve+"_cash"),
            "receivables":{}
        }
    return out

def marks(row):
    v=row.get("prices")
    if not isinstance(v,dict) or set(v)!=set(SYMBOLS):
        raise LedgerError("both verified daily closing prices required")
    if row.get("source_verified") is not True:
        raise LedgerError("market price provenance not verified")
    return {sym:dnum(v[sym],sym+"_close",allow_zero=False) for sym in SYMBOLS}

def sleeve_value(book, px):
    return (book["cash"]
      + sum(Decimal(book[sym+"_units"])*px[sym] for sym in SYMBOLS)
      + sum(book["receivables"].values(),Decimal("0")))

def total(book,px):
    return sum((sleeve_value(book[s],px) for s in SLEEVES),Decimal("0"))

def add_receivable(book, sym, pay_day, amount):
    key=sym+"|"+pay_day
    book["receivables"][key]=book["receivables"].get(key,Decimal("0"))+amount

def settled_dividend(book, sym, payday):
    key=sym+"|"+payday
    if key not in book["receivables"]:
        raise LedgerError("dividend paid but no matching ex-date receivable")
    book["cash"]+=book["receivables"].pop(key)

def apply_actions(actions, asof, books):
    seen_split=set()
    for event in actions:
        if not isinstance(event,dict):raise LedgerError("event must be object")
        kind=event.get("type")
        sym=event.get("symbol")
        if sym not in SYMBOLS:raise LedgerError("unexpected ETF symbol")
        if kind=="SPLIT":
            if sym in seen_split:raise LedgerError("duplicate same-day split")
            if event.get("issuer_verified") is not True:
                raise LedgerError("unverified split not auto-applied")
            ratio=dnum(event.get("new_units_per_old"),"split_ratio",allow_zero=False)
            if ratio==1:raise LedgerError("split ratio 1 is not a split")
            for sleeve in SLEEVES:
                prior=books[sleeve][sym+"_units"]
                books[sleeve][sym+"_units"]=whole(Decimal(prior)*ratio,"post_split_units")
            seen_split.add(sym)
        elif kind=="EX_DIV":
            if event.get("issuer_verified") is not True:raise LedgerError("unverified ex-dividend")
            payable=day(event.get("payable_date"))
            if payable<asof:raise LedgerError("pay date before ex-date")
            amount=dnum(event.get("amount_per_share"),"dividend")
            for sleeve in SLEEVES:
                add_receivable(books[sleeve],sym,payable.isoformat(),
                               Decimal(books[sleeve][sym+"_units"])*amount)
        elif kind=="DIVIDEND_PAID":
            if event.get("issuer_verified") is not True:
                raise LedgerError("unverified dividend payment")
            for sleeve in SLEEVES:
                settled_dividend(books[sleeve],sym,asof.isoformat())
        else:raise LedgerError("unknown corporate action type")

def cash_transaction(event, books):
    sleeve=event.get("sleeve")
    if sleeve not in SLEEVES:raise LedgerError("transaction sleeve missing")
    kind=event.get("type")
    amount=dnum(event.get("amount"),"external_amount",allow_zero=False)
    if kind=="DEPOSIT":
        books[sleeve]["cash"]+=amount
        return amount
    if kind=="WITHDRAW":
        if books[sleeve]["cash"]<amount:
            raise LedgerError("withdraw exceeds available cash; sell must be recorded separately")
        books[sleeve]["cash"]-=amount
        return -amount
    raise LedgerError("unknown external flow")

def internal_transfer(event, books):
    a=event.get("from_sleeve")
    b=event.get("to_sleeve")
    if a not in SLEEVES or b not in SLEEVES or a==b:
        raise LedgerError("internal transfer must have two different valid sleeves")
    amount=dnum(event.get("amount"),"transfer_amount",allow_zero=False)
    if books[a]["cash"]<amount:
        raise LedgerError("insufficient internal transfer cash")
    books[a]["cash"]-=amount
    books[b]["cash"]+=amount

def trade(event, books):
    sleeve=event.get("sleeve")
    sym=event.get("symbol")
    if sleeve not in SLEEVES or sym not in SYMBOLS:
        raise LedgerError("trade sleeve/symbol missing")
    kind=event.get("type")
    if kind not in ("BUY","SELL"):raise LedgerError("unknown trade type")
    qty=whole(event.get("units"),"trade_units")
    if qty<=0:raise LedgerError("trade must have positive units")
    fill=dnum(event.get("fill_price"),"fill_price",allow_zero=False)
    # One explicit all-in fee (includes commission, tax and slippage if recorded).
    cost=dnum(event.get("all_in_fees"),"all_in_fees")
    b=books[sleeve]
    gross=Decimal(qty)*fill
    if kind=="BUY":
        if b["cash"]<gross+cost:
            raise LedgerError("cash would go negative; no financing allowed")
        b["cash"]-=gross+cost
        b[sym+"_units"]+=qty
    else:
        if b[sym+"_units"]<qty:raise LedgerError("sell exceeds confirmed position")
        if cost>gross:raise LedgerError("fees greater than sale proceeds")
        b[sym+"_units"]-=qty
        b["cash"]+=gross-cost

def dated_rate(flows):
    """Solve dated XIRR by bisection; None if no sign change / nonunique root."""
    if len(flows)<2:return None
    start=flows[0][0]
    if any(v>0 for _,v in flows) is False or any(v<0 for _,v in flows) is False:
        return None
    def npv(rate):
        base=1+rate
        return sum(float(a)/(base**((at-start).days/365.25)) for at,a in flows)
    low=-0.9999
    high=100.0
    fa,fb=npv(low),npv(high)
    if fa==0:return low
    if fb==0:return high
    if fa*fb>0:return None
    for _ in range(130):
        mid=(low+high)/2
        fm=npv(mid)
        if fm==0:break
        if fa*fm>0:low,fa=mid,fm
        else:high=mid
    return (low+high)/2

def evaluate(data):
    if not isinstance(data,dict) or data.get("schema_version")!=VERSION:
        raise LedgerError("invalid reference schema")
    rows=data.get("days")
    if not isinstance(rows,list) or not rows:
        raise LedgerError("must provide initial mark and verified daily data")
    books=load_snapshot(data.get("initial"))
    first=rows[0]
    start=day(first.get("date"))
    if data.get("start_date")!=start.isoformat() or first.get("events"):
        raise LedgerError("first row is starting snapshot only, no cashflows/trades")
    px=marks(first)
    prev=total(books,px)
    if prev<=0:raise LedgerError("starting NAV zero")
    initial_nav=prev
    factor=Decimal(1)
    deposits=Decimal(0)
    cashflows=[(start,-initial_nav)]
    last=start
    observations=[]
    def stamp(at,flow,nav):
        F=sleeve_value(books["F"],px)
        w=F/nav
        observations.append({
            "date":at.isoformat(),"nav":money(nav),
            "F_value":money(F),"A_value":money(nav-F),
            "F_weight_pct":pct(w),
            "F_A_check":"REVIEW_5PP" if w<=Decimal(".45") or w>=Decimal(".55") else "IN_BAND",
            "external_net_flow":money(flow),
            "units":{"F":{s:books["F"][s+"_units"] for s in SYMBOLS},
                     "A":{s:books["A"][s+"_units"] for s in SYMBOLS}},
            "cash":{"F":money(books["F"]["cash"]),"A":money(books["A"]["cash"])},
            "dividend_receivable":money(sum((
                sum(books[sl]["receivables"].values(),Decimal(0)) for sl in SLEEVES),Decimal(0)))})
    stamp(start,Decimal(0),prev)
    ids=set()
    for row in rows[1:]:
        at=day(row.get("date"))
        if at<=last:raise LedgerError("dates must strictly increase; duplicate days blocked")
        px=marks(row)
        events=row.get("events",[])
        if not isinstance(events,list) or any(not isinstance(e,dict) for e in events):
            raise LedgerError("events must be a list of objects")
        actions=[e for e in events if e.get("type") in ("SPLIT","EX_DIV","DIVIDEND_PAID")]
        apply_actions(actions,at,books)
        before=total(books,px)
        if before<=0:raise LedgerError("nonpositive preflow NAV")
        flows=[e for e in events if e.get("type") in ("DEPOSIT","WITHDRAW")]
        transfers=[e for e in events if e.get("type")=="INTERNAL_TRANSFER"]
        close_trades=[e for e in events if e.get("type") in ("BUY","SELL")]
        if len(actions)+len(flows)+len(transfers)+len(close_trades)!=len(events):
            raise LedgerError("unknown event type")
        flow=Decimal(0)
        for event in flows:
            flow+=cash_transaction(event,books)
        for event in transfers:
            internal_transfer(event,books)
        for event in close_trades:
            tid=event.get("trade_id")
            if not isinstance(tid,str) or not tid or tid in ids:
                raise LedgerError("missing or duplicate trade ID")
            ids.add(tid)
            trade(event,books)
        after=total(books,px)
        if after<=0 or before+flow<=0:
            raise LedgerError("nonpositive postflow NAV")
        factor*=(before/prev)*(after/(before+flow))
        if flow:cashflows.append((at,-flow))
        deposits+=flow
        stamp(at,flow,after)
        prev=after
        last=at
    cashflows.append((last,prev))
    elapsed=(last-start).days
    annual=None
    xirr=None
    if elapsed>=365:
        annual=float(factor)**(365.25/elapsed)-1
        xirr=dated_rate(cashflows)
    F=sleeve_value(books["F"],px)
    def ret(x):
        return None if x is None else round(x*100,6)
    return {
        "schema_version":VERSION,
        "status":"REFERENCE_ACCOUNTING_ONLY_NO_TRADING",
        "data_from":start.isoformat(),"data_through":last.isoformat(),
        "days_elapsed":elapsed,
        "initial_nav_twd":money(initial_nav),
        "ending_nav_twd":money(prev),
        "external_net_contributions_twd":money(deposits),
        "net_pnl_twd":money(prev-initial_nav-deposits),
        "cumulative_twr_pct":pct(factor-1),
        "annualized_twr_pct":ret(annual),
        "xirr_pct":ret(xirr),
        "under_one_year_annualization_suppressed":elapsed<365,
        "F_weight_pct":pct(F/prev),
        "benchmark_0050":"NOT_CALCULATED_REQUIRES_SAME_DATED_FLOWS_AND_TOTAL_RETURN_PRICES",
        "actual_broker_holdings_verified":False,
        "orders_placed":False,
        "records":observations
    }

def main():
    import argparse
    from pathlib import Path
    p=argparse.ArgumentParser()
    p.add_argument("input",help="LOCAL PRIVATE fixture JSON; never commit personal holdings")
    p.add_argument("--output",default="-")
    opts=p.parse_args()
    result=evaluate(json.loads(Path(opts.input).read_text(encoding="utf-8")))
    raw=json.dumps(result,ensure_ascii=False,indent=2)
    if opts.output=="-":print(raw)
    else:Path(opts.output).write_text(raw+"\n",encoding="utf-8")

if __name__=="__main__":main()
