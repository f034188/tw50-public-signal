#!/usr/bin/env python3
"""N46 friend-only private daily handoff, strictly no public upload.

A FRIEND may store generated JSON only in their own restricted storage, optionally
in a GitHub PRIVATE repository which they themselves connect to ChatGPT.
This tool does not grant ChatGPT access, upload files, send notifications,
query prices, or mark any trade executed.

source: N45 private ledger JSON with an intact SHA256 checkpoint.
output: privacy-sensitive summary, also private, owner confirms data date.
integrity hashes are accidental-change detectors, NOT signatures or encryption.
"""
from __future__ import annotations
import copy
from datetime import date
from pathlib import Path
from friend_advanced_accounting_reference import LedgerError, day, money
from friend_private_local_ledger import (
    read_locked_state,safe_path,atomic_write,sha,checked_report,
)

VERSION="FRIEND-PRIVATE-DAILY-HANDOFF-v1"
PUBLIC_VERSION="N43-PUBLIC-MARKET-OBSERVATION-v1"
NORMAL_STATUS="SHADOW_OK"
KNOWN_TRENDS={"偏多","防守"}
KNOWN_EVENTS={"新轉多","新轉防守","無新切換"}

def handoff(source_path, destination_path, *, confirmed_on):
    src=safe_path(source_path)
    dst=safe_path(destination_path)
    if src==dst:
        raise LedgerError("SOURCE_AND_DESTINATION_MUST_BE_SEPARATE_PRIVATE_FILES")
    confirm_date=day(confirmed_on)
    state=read_locked_state(src)
    ledger=state["ledger"]
    report=checked_report(ledger)
    last_day=day(report["data_through"])
    if confirm_date<last_day:
        raise LedgerError("OWNER_CANNOT_CONFIRM_BEFORE_LAST_LEDGER_ENTRY")
    if confirm_date>date.today():
        raise LedgerError("FUTURE_OWNER_CONFIRMATION_REJECTED")
    # The owner must independently verify that portfolio positions really have
    # NOT changed through this date. A stock-market price mark alone is not proof.
    # User identity is never requested or written.
    observation=report["records"][-1]
    if observation["date"]!=last_day.isoformat():
        raise LedgerError("LEDGER_LAST_OBSERVATION_MISMATCH")
    body={
        "format":VERSION,
        "source_revision":state["revision"],
        "source_ledger_sha256":state["ledger_sha256"],
        "portfolio_valuation_date":last_day.isoformat(),
        "last_owner_confirmed_holdings_date":confirm_date.isoformat(),
        "owner_confirmation_scope":"POSITIONS_AND_POSTED_CASHFLOWS",
        "synthetic_only":ledger.get("synthetic_only") is True,
        "source_market_verified_to_valuation_day":True,
        "valuation":{"nav_twd":report["ending_nav_twd"],
                     "F_value_twd":observation["F_value"],
                     "A_value_twd":observation["A_value"],
                     "F_weight_percent":observation["F_weight_pct"],
                     "external_net_contributions_twd":report["external_net_contributions_twd"],
                     "net_pnl_twd":report["net_pnl_twd"],
                     "cumulative_twr_percent":report["cumulative_twr_pct"],
                     "annualized_twr_percent":report["annualized_twr_pct"],
                     "xirr_percent":report["xirr_pct"]},
        "holdings":{"F":{"0050_units":observation["units"]["F"]["0050"],
                         "00631L_units":observation["units"]["F"]["00631L"],
                         "cash_twd":observation["cash"]["F"]},
                    "A":{"0050_units":observation["units"]["A"]["0050"],
                         "00631L_units":observation["units"]["A"]["00631L"],
                         "cash_twd":observation["cash"]["A"]}},
        "is_broker_synced":False,
        "orders_submitted":False,
        "public_market_signal_included":False,
        "privacy":"PRIVATE_PERSONAL_FINANCIAL_INFORMATION_DO_NOT_PUBLISH"
    }
    result={"body":body,"body_sha256":sha(body)}
    atomic_write(dst,result)
    return {"status":"PRIVATE_HANDOFF_WRITTEN_ON_OWNER_CONFIRMATION",
            "data_through":last_day.isoformat(),"confirmed_on":confirm_date.isoformat(),
            "private_only":True,"not_sent_to_github":True,"not_sent_to_chatgpt":True}

def validate_private_snapshot(snapshot):
    if not isinstance(snapshot,dict) or set(snapshot)!={"body","body_sha256"}:
        raise LedgerError("HANDOFF_ENVELOPE_INVALID")
    body=snapshot["body"]
    if not isinstance(body,dict) or body.get("format")!=VERSION:
        raise LedgerError("HANDOFF_VERSION_INVALID")
    if sha(body)!=snapshot["body_sha256"]:
        raise LedgerError("HANDOFF_INTEGRITY_FAILED")
    if body.get("privacy")!="PRIVATE_PERSONAL_FINANCIAL_INFORMATION_DO_NOT_PUBLISH":
        raise LedgerError("HANDOFF_PRIVACY_MARKER_INVALID")
    if body.get("orders_submitted") is not False or body.get("is_broker_synced") is not False:
        raise LedgerError("UNSUPPORTED_BROKER_OR_ORDER_CLAIM")
    val_date=day(body.get("portfolio_valuation_date"))
    confirmed=day(body.get("last_owner_confirmed_holdings_date"))
    if confirmed<val_date:
        raise LedgerError("HANDOFF_OWNER_DATE_PREDATES_MARK")
    if not isinstance(body.get("holdings"),dict) or set(body["holdings"])!={"F","A"}:
        raise LedgerError("HANDOFF_F_A_MISSING")
    if not isinstance(body.get("valuation"),dict):
        raise LedgerError("HANDOFF_VALUATION_MISSING")
    return body

def morning_readiness(public_market, private_handoff, *, on_date):
    """Fail-closed mode selection for reminder. No private holdings in result."""
    now=day(on_date)
    out={"schema":"FRIEND-N46-MORNING-READINESS-v1",
         "asof":now.isoformat(),"market_status":"MARKET_DATA_UNAVAILABLE",
         "personal_status":"BASIC_ONLY_NOT_READY",
         "daily_action":"資料待查","individual_shares_advice_allowed":False,
         "individual_nav_display_allowed":False,
         "reason":"無可驗證的公開市場資料",
         "portfolio_valuation_date":None,
         "last_owner_confirmed_holdings_date":None}
    if not isinstance(public_market,dict):
        return out
    if public_market.get("schema_version")!=PUBLIC_VERSION:
        out["reason"]="公開訊號版本不符";return out
    if public_market.get("is_actionable") is not False:
        out["reason"]="公開訊號錯誤聲稱可直接操作";return out
    if public_market.get("corporate_action_discovery_complete") is not False:
        out["reason"]="公開公司行動來源聲明不符合已知限制";return out
    try:
        market_day=day(public_market.get("data_date"))
    except LedgerError:
        out["reason"]="市場資料沒有有效日期";return out
    lag=(now-market_day).days
    if lag<0 or lag>4:
        out["reason"]="市場資料日期在未來或已超過四日";return out
    if public_market.get("display_status")!="SHADOW_OK":
        out["reason"]="市場影子訊號未通過完整狀態驗證";return out
    if public_market.get("trend") not in KNOWN_TRENDS or public_market.get("trend_change") not in KNOWN_EVENTS:
        out["reason"]="公開訊號事件或趨勢值異常";return out
    if public_market.get("l2_trading")!="正常交易":
        out["reason"]="00631L非正常交易；停止新的個人調整估算";return out
    out["market_status"]="VERIFIED_SHADOW_MARKET_FOR_DATE"
    out["daily_action"]={"新轉多":"加碼候選","新轉防守":"減碼候選","無新切換":"不動"}[public_market["trend_change"]]
    out["reason"]="公開市場訊號有效；尚未驗證任何個人持股"
    if private_handoff is None:
        out["personal_status"]="BASIC_ONLY_NO_PRIVATE_HANDOFF"
        return out
    try:
        p=validate_private_snapshot(private_handoff)
        val_day=day(p["portfolio_valuation_date"])
        owned=day(p["last_owner_confirmed_holdings_date"])
    except (LedgerError,KeyError,TypeError,ValueError):
        out["personal_status"]="PRIVATE_HANDOFF_NOT_VALID"
        out["reason"]="私人帳本資料完整性或日期無法驗證；退回基礎版"
        return out
    out["portfolio_valuation_date"]=val_day.isoformat()
    out["last_owner_confirmed_holdings_date"]=owned.isoformat()
    if p.get("synthetic_only") is True:
        out["personal_status"]="SYNTHETIC_EXAMPLE_NOT_A_REAL_ACCOUNT"
        out["reason"]="測試用虛構帳本不得冒充個人持倉"
        return out
    if owned>now or val_day>market_day:
        out["personal_status"]="PRIVATE_DATES_OUT_OF_SEQUENCE"
        out["reason"]="私人確認或估值日期超過可驗證資料"
        return out
    if val_day!=market_day:
        out["personal_status"]="VALUATION_STALE_HOLDINGS_UNVERIFIED_FOR_MARKET_DAY"
        out["reason"]="私人估值不是最新有效收盤日：僅能顯示上次確認持倉，不得顯示最新個人報酬或調整張數"
        return out
    if owned<market_day:
        out["personal_status"]="HOLDINGS_UNCONFIRMED_SINCE_MARKET_DAY"
        out["reason"]="缺少本人確認無新交易／新入金，禁止個人化張數"
        return out
    out.update(personal_status="READY_FOR_PRIVATE_MODEL_ESTIMATE",
               individual_shares_advice_allowed=True,
               individual_nav_display_allowed=True,
               reason="市場與本人已確認的私人帳本均截至同一有效收盤日；僅供模型估算，非買賣委託")
    return out

def main():
    import argparse,json
    p=argparse.ArgumentParser(description="N46 privacy-first friend handoff; no network")
    sub=p.add_subparsers(dest="cmd",required=True)
    exp=sub.add_parser("export")
    exp.add_argument("--source",required=True)
    exp.add_argument("--private-output",required=True)
    exp.add_argument("--owner-confirmed-through",required=True)
    chk=sub.add_parser("check")
    chk.add_argument("--public-market-json",required=True)
    chk.add_argument("--private-snapshot",default=None)
    chk.add_argument("--on-date",required=True)
    args=p.parse_args()
    if args.cmd=="export":
        report=handoff(args.source,args.private_output,
                       confirmed_on=args.owner_confirmed_through)
    else:
        pub=json.loads(Path(args.public_market_json).read_text(encoding="utf-8"))
        private=json.loads(Path(args.private_snapshot).read_text(encoding="utf-8")) if args.private_snapshot else None
        report=morning_readiness(pub,private,on_date=args.on_date)
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=="__main__":main()
