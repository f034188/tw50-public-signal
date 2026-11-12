#!/usr/bin/env python3
"""N45 append-only PRIVATE local ledger reference; never save into public repo.

This is a simple integrity-checked local JSON workflow. It is NOT encrypted,
is NOT connected to ChatGPT tasks or broker APIs, and it never sends data to
GitHub. Owner controls the output filename and private directory.
"""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from friend_advanced_accounting_reference import VERSION, evaluate, day, LedgerError

STORE_VERSION="FRIEND-PRIVATE-LOCAL-CHECKPOINT-v1"
PUBLIC_ROOT=Path(__file__).resolve().parents[1]

def canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode("utf-8")

def sha(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()

def safe_path(path):
    p=Path(path).expanduser().resolve()
    if p==PUBLIC_ROOT or PUBLIC_ROOT in p.parents:
        raise LedgerError("REFUSE_PERSONAL_DATA_IN_PUBLIC_GIT_REPOSITORY")
    if p.is_symlink() or not p.parent.is_dir():
        raise LedgerError("REQUIRE_PREEXISTING_PRIVATE_DIRECTORY")
    if p.parent.stat().st_mode & 0o077:
        raise LedgerError("PRIVATE_DIRECTORY_MUST_NOT_BE_GROUP_OR_WORLD_READABLE")
    if p.suffix.lower()!=".json":
        raise LedgerError("PRIVATE_LEDGER_MUST_USE_JSON_EXTENSION")
    return p

def atomic_write(p, obj):
    blob=json.dumps(obj,ensure_ascii=False,indent=2,sort_keys=True).encode("utf-8")+b"\n"
    temp=None
    try:
        with tempfile.NamedTemporaryFile(mode="wb",dir=p.parent,prefix=".private-ledger-",delete=False) as f:
            temp=Path(f.name)
            os.fchmod(f.fileno(),0o600)
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp,p)
        os.chmod(p,0o600)
    finally:
        if temp and temp.exists():temp.unlink()

class FileLock:
    def __init__(self,p):
        self.lock=Path(str(p)+".lock")
        self.fd=None
    def __enter__(self):
        try:
            self.fd=os.open(str(self.lock),os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            os.write(self.fd,b"Friend local ledger write in progress\n")
            return self
        except FileExistsError:
            raise LedgerError("LEDGER_LOCKED_ANOTHER_WRITER_OR_STALE_LOCK") from None
    def __exit__(self,*args):
        if self.fd is not None:
            os.close(self.fd)
            self.lock.unlink(missing_ok=True)

def verify_owner_confirmations(ledger):
    if ledger.get("synthetic_only") is True:
        return
    if ledger.get("owner_verified_starting_positions") is not True:
        raise LedgerError("STARTING_POSITIONS_NOT_CONFIRMED_BY_OWNER")
    for row in ledger.get("days",[])[1:]:
        for e in row.get("events",[]):
            typ=e.get("type")
            if typ in ("BUY","SELL") and e.get("owner_confirmed_fill") is not True:
                raise LedgerError("MODEL_SUGGESTION_IS_NOT_CONFIRMED_FILL")
            if typ in ("DEPOSIT","WITHDRAW") and e.get("owner_confirmed_cash_flow") is not True:
                raise LedgerError("EXTERNAL_CASHFLOW_NOT_CONFIRMED")

def checked_report(ledger):
    if ledger.get("schema_version")!=VERSION:
        raise LedgerError("PERSONAL_LEDGER_VERSION_MISMATCH")
    verify_owner_confirmations(ledger)
    result=evaluate(ledger)
    return result

def read_locked_state(path):
    p=safe_path(path)
    if not p.is_file():raise LedgerError("NO_LOCAL_LEDGER")
    obj=json.loads(p.read_text(encoding="utf-8"))
    if obj.get("format")!=STORE_VERSION:
        raise LedgerError("UNKNOWN_PRIVATE_CHECKPOINT_FORMAT")
    if sha(obj.get("ledger"))!=obj.get("ledger_sha256"):
        raise LedgerError("CHECKPOINT_HASH_MISMATCH_MANUAL_EDIT_DETECTED")
    if not isinstance(obj.get("revision"),int) or obj["revision"]<1:
        raise LedgerError("INVALID_CHECKPOINT_REVISION")
    checked_report(obj["ledger"])
    return obj

def initialize(path, ledger):
    p=safe_path(path)
    with FileLock(p):
        if p.exists():raise LedgerError("LEDGER_ALREADY_EXISTS_NO_OVERWRITE")
        report=checked_report(ledger)
        checkpoint={
            "format":STORE_VERSION,
            "revision":1,"previous_revision_sha256":None,
            "ledger_sha256":sha(ledger),
            "ledger":copy.deepcopy(ledger)}
        atomic_write(p,checkpoint)
        return receipt(checkpoint,report)

def receipt(obj,report):
    # Never return full holdings in generic CLI printout.
    return {
        "status":"LOCAL_PRIVATE_ACCOUNTING_CHECKPOINT_ONLY",
        "revision":obj["revision"],"ledger_sha256":obj["ledger_sha256"],
        "data_through":report["data_through"],
        "cumulative_twr_pct":report["cumulative_twr_pct"],
        "net_pnl_twd":report["net_pnl_twd"],
        "not_encrypted":True,"not_uploaded_to_github":True,
        "not_automatically_read_by_scheduled_chatgpt":True
    }

def append_day(path, day_row, *, expected_sha256):
    p=safe_path(path)
    with FileLock(p):
        obj=read_locked_state(p)
        if not isinstance(expected_sha256,str) or expected_sha256!=obj["ledger_sha256"]:
            raise LedgerError("STALE_OR_MISSING_EXPECTED_SHA_REJECTED")
        if not isinstance(day_row,dict):
            raise LedgerError("NEXT_DAY_MUST_BE_OBJECT")
        last=day(obj["ledger"]["days"][-1].get("date"))
        this=day(day_row.get("date"))
        if this<=last:raise LedgerError("DUPLICATE_OR_OUT_OF_ORDER_DAY_REJECTED")
        ledger=copy.deepcopy(obj["ledger"])
        ledger["days"].append(copy.deepcopy(day_row))
        report=checked_report(ledger)
        next_obj={
            "format":STORE_VERSION,
            "revision":obj["revision"]+1,
            "previous_revision_sha256":obj["ledger_sha256"],
            "ledger_sha256":sha(ledger),
            "ledger":ledger}
        atomic_write(p,next_obj)
        return receipt(next_obj,report)

def main():
    import argparse
    cli=argparse.ArgumentParser(description="OFFLINE private file; never commit real holdings")
    cmd=cli.add_subparsers(dest="cmd",required=True)
    create=cmd.add_parser("init")
    create.add_argument("--private-file",required=True)
    create.add_argument("--initial-json",required=True,
                        help="PRIVATE starting snapshot JSON; never use public repo")
    append=cmd.add_parser("append")
    append.add_argument("--private-file",required=True)
    append.add_argument("--day-json",required=True)
    append.add_argument("--expected-sha256",required=True)
    p=cli.parse_args()
    if p.cmd=="init":
        data=json.loads(Path(p.initial_json).read_text(encoding="utf-8"))
        out=initialize(p.private_file,data)
    else:
        data=json.loads(Path(p.day_json).read_text(encoding="utf-8"))
        out=append_day(p.private_file,data,expected_sha256=p.expected_sha256)
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=="__main__":main()
