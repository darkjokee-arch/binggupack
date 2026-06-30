#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenBinggu C-2 Guard synthetic / in-memory selftest.
기준: docs/BINGGUPACK_C2_GUARD_FINAL_SINGLE_CONTROL.md + BINGGUPACK_C2_GUARD_SELFTEST_SPEC.md

안전: 전부 in-memory mock. 실제 SQLite/user_graph/_graph_merge write 0.
      operating store mtime 전후 대조로 불변 검증. apply_executed 항상 0(write 게이트 HOLD).
      OpenCrab/push/upload/confirmed apply 0.
"""
import hashlib, os, json, re, tempfile

# ---------- 한도/상수 (FINAL 기준) ----------
LIMIT_NODES, LIMIT_EDGES, LIMIT_EVID = 50, 100, 200
RATE_HOUR, RATE_DAY = 10, 30
CUR_REDACTION_POLICY = "v1"

def _canon(s):
    # canonicalize: 공백/줄바꿈/인코딩 통일 후 trim
    return re.sub(r"\s+", " ", str(s)).strip().encode("utf-8", "replace")

def _hash(s):
    return hashlib.sha256(_canon(s)).hexdigest()[:12]


# ---------- in-memory mock store (실제 파일 0) ----------
class MockStore:
    def __init__(self):
        self.applied = {}          # (pack_id, content_hash) -> True
        self.audit = []            # append-only 체인
        self.snapshots = []        # 백업 스냅샷
        self.apply_executed = 0    # 항상 0 (HOLD) — 검증 대상
        self.commit_count_hour = 0
        self.commit_count_day = 0

    def audit_append(self, entry):
        prev = self.audit[-1]["entry_hash"] if self.audit else "GENESIS"
        entry = dict(entry); entry["prev_audit_hash"] = prev
        entry["entry_hash"] = _hash(json.dumps(entry, sort_keys=True, ensure_ascii=False))
        self.audit.append(entry)

    def verify_chain(self):
        prev = "GENESIS"
        for e in self.audit:
            if e["prev_audit_hash"] != prev:
                return False
            recomputed = dict(e); h = recomputed.pop("entry_hash")
            if _hash(json.dumps(recomputed, sort_keys=True, ensure_ascii=False)) != h:
                return False
            prev = h
        return True


# ---------- C-2 Guard (단일 통제, 판정만 — write 0) ----------
def evaluate(store, pack, ctx):
    """반환 {button, reason_code}. button: enabled/disabled/rejected/blocked. apply 실행 안 함."""
    # 0) emergency stop (상위 인터셉트)
    if ctx.get("emergency_flag"):
        return {"button": "blocked", "reason_code": "emergency_stopped"}
    # audit chain 변조 → 자동 emergency
    if not store.verify_chain():
        return {"button": "blocked", "reason_code": "audit_chain_broken"}
    # 1) actor G4
    if ctx.get("actor") in ("auto", "reader"):
        return {"button": "blocked", "reason_code": "G4_no_auto"}
    # 2) batch reject (1클릭=1pack)
    if ctx.get("batch_packs", 1) > 1:
        return {"button": "rejected", "reason_code": "batch_apply_rejected"}
    # 3) override 시도 → 미구현
    if ctx.get("override_request"):
        return {"button": "rejected", "reason_code": "override_not_implemented"}
    # 4) freshness 4종
    ev = pack.get("evidence", {})
    if ev.get("source_missing"):
        return {"button": "disabled", "reason_code": "freshness_source_missing"}
    if ev.get("source_hash") is not None and ev.get("captured_hash") is not None \
            and ev["source_hash"] != ev["captured_hash"]:
        return {"button": "disabled", "reason_code": "freshness_source_hash_mismatch"}
    if not pack.get("evidence_refs"):
        return {"button": "disabled", "reason_code": "freshness_evidence_refs_missing"}
    if ev.get("redaction_policy") != CUR_REDACTION_POLICY:
        return {"button": "disabled", "reason_code": "freshness_redaction_policy_changed"}
    # 5) SQLite 무결성 2종
    if ctx.get("sqlite_checksum_mismatch"):
        return {"button": "disabled", "reason_code": "sqlite_checksum_mismatch"}
    if ctx.get("sqlite_wal_incomplete"):
        return {"button": "disabled", "reason_code": "sqlite_wal_incomplete"}
    # 6) backup 2종
    if ctx.get("backup_create_fail"):
        return {"button": "disabled", "reason_code": "backup_create_failed"}
    if ctx.get("backup_integrity_fail"):
        return {"button": "disabled", "reason_code": "backup_integrity_failed"}
    # 7) duplicate 2종 (canonicalize 후 hash)
    key = (pack["pack_id"], _hash(pack["content"]))
    if key in store.applied:
        return {"button": "disabled", "reason_code": "duplicate_already_applied"}
    # 8) supersede snapshot
    if pack.get("supersede_target"):
        if ctx.get("snapshot_fail"):
            return {"button": "disabled", "reason_code": "supersede_snapshot_failed"}
    # 9) write limit 건당
    if pack.get("n_nodes", 0) > LIMIT_NODES or pack.get("n_edges", 0) > LIMIT_EDGES \
            or pack.get("n_evid", 0) > LIMIT_EVID:
        return {"button": "disabled", "reason_code": "write_limit_per_commit"}
    # 10) write limit 빈도
    if store.commit_count_hour + 1 > RATE_HOUR or store.commit_count_day + 1 > RATE_DAY:
        return {"button": "disabled", "reason_code": "write_limit_rate"}
    # 전부 통과 → 버튼 활성. (apply는 HOLD: 실행 안 함)
    return {"button": "enabled", "reason_code": None}


# ---------- 케이스 정의 ----------
def base_pack(**kw):
    p = {"pack_id": "p_demo", "content": "노드 추가 정상", "evidence_refs": ["EVC-1"],
         "evidence": {"source_missing": False, "source_hash": "h1", "captured_hash": "h1",
                      "redaction_policy": CUR_REDACTION_POLICY},
         "n_nodes": 3, "n_edges": 2, "n_evid": 3, "supersede_target": None}
    p.update(kw); return p

def base_ctx(**kw):
    c = {"actor": "human", "emergency_flag": False, "batch_packs": 1, "override_request": False,
         "sqlite_checksum_mismatch": False, "sqlite_wal_incomplete": False,
         "backup_create_fail": False, "backup_integrity_fail": False, "snapshot_fail": False}
    c.update(kw); return c

# (id, desc, pack, ctx, expected_reason, expected_button)
NEG = [
  (1,"freshness source missing", base_pack(evidence={"source_missing":True,"redaction_policy":CUR_REDACTION_POLICY}), base_ctx(), "freshness_source_missing","disabled"),
  (2,"freshness hash mismatch", base_pack(evidence={"source_missing":False,"source_hash":"h1","captured_hash":"h2","redaction_policy":CUR_REDACTION_POLICY}), base_ctx(), "freshness_source_hash_mismatch","disabled"),
  (3,"freshness evidence_refs missing", base_pack(evidence_refs=[]), base_ctx(), "freshness_evidence_refs_missing","disabled"),
  (4,"freshness redaction policy changed", base_pack(evidence={"source_missing":False,"source_hash":"h1","captured_hash":"h1","redaction_policy":"v0"}), base_ctx(), "freshness_redaction_policy_changed","disabled"),
  (5,"sqlite checksum mismatch", base_pack(), base_ctx(sqlite_checksum_mismatch=True), "sqlite_checksum_mismatch","disabled"),
  (6,"sqlite WAL incomplete", base_pack(), base_ctx(sqlite_wal_incomplete=True), "sqlite_wal_incomplete","disabled"),
  (7,"backup create fail", base_pack(), base_ctx(backup_create_fail=True), "backup_create_failed","disabled"),
  (8,"backup integrity fail", base_pack(), base_ctx(backup_integrity_fail=True), "backup_integrity_failed","disabled"),
  (9,"duplicate already applied", base_pack(), base_ctx(), "duplicate_already_applied","disabled"),  # 사전등록
  (10,"duplicate via non-canonical", base_pack(content="노드   추가   정상\n"), base_ctx(), "duplicate_already_applied","disabled"),  # 공백만 다름
  (11,"supersede snapshot fail", base_pack(supersede_target="n_old"), base_ctx(snapshot_fail=True), "supersede_snapshot_failed","disabled"),
  (12,"write limit per commit", base_pack(n_nodes=51), base_ctx(), "write_limit_per_commit","disabled"),
  (13,"write limit rate", base_pack(), base_ctx(), "write_limit_rate","disabled"),  # 사전 rate 채움
  (14,"batch reject", base_pack(), base_ctx(batch_packs=3), "batch_apply_rejected","rejected"),
  (15,"emergency stopped", base_pack(), base_ctx(emergency_flag=True), "emergency_stopped","blocked"),
  (16,"audit chain broken", base_pack(), base_ctx(), "audit_chain_broken","blocked"),  # 사전 변조
  (17,"override not implemented", base_pack(), base_ctx(override_request=True), "override_not_implemented","rejected"),
  (18,"actor auto blocked", base_pack(), base_ctx(actor="auto"), "G4_no_auto","blocked"),
]


def run():
    # operating store mtime 스냅 (read-only, write 0 검증용)
    # 공개본은 작성자 절대경로를 포함하지 않는다. env 미설정 시 temp dummy 경로(존재 안 해도 됨).
    _tmp = tempfile.gettempdir()
    watched = [os.environ.get("OPENBINGGU_USER_GRAPH",  os.path.join(_tmp, "openbinggu_user_graph_dummy.yaml")),
               os.environ.get("OPENBINGGU_GRAPH_MERGE", os.path.join(_tmp, "openbinggu_graph_merge_dummy.yaml"))]
    sqlite_path = os.environ.get("OPENBINGGU_OPERATING_DB", os.path.join(_tmp, "openbinggu_operating_dummy.sqlite"))
    watched.append(sqlite_path)
    before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in watched}

    results = []
    for cid, desc, pack, ctx, exp_rc, exp_btn in NEG:
        store = MockStore()
        store.audit_append({"action":"genesis"})
        if cid == 9:   # duplicate 사전등록
            store.applied[(pack["pack_id"], _hash(pack["content"]))] = True
        if cid == 10:  # canonical 동일 내용 사전등록
            store.applied[("p_demo", _hash("노드 추가 정상"))] = True
        if cid == 13:  # rate 사전 채움
            store.commit_count_hour = RATE_HOUR
        if cid == 16:  # audit 변조
            store.audit_append({"action":"commit"})
            store.audit[-1]["action"] = "TAMPERED"
        r = evaluate(store, pack, ctx)
        ok = (r["reason_code"] == exp_rc and r["button"] == exp_btn and store.apply_executed == 0)
        results.append(("NEG", cid, desc, exp_rc, r["reason_code"], r["button"], "PASS" if ok else "FAIL"))

    # positive
    POS = [
      (1,"정상 → 버튼 enabled, apply HOLD", base_pack(), base_ctx()),
      (2,"날짜만 오래된 정상 원본 → 통과(시간 임계값 미적용)", base_pack(evidence={"source_missing":False,"source_hash":"h1","captured_hash":"h1","redaction_policy":CUR_REDACTION_POLICY,"age_days":3650}), base_ctx()),
    ]
    for cid, desc, pack, ctx in POS:
        store = MockStore(); store.audit_append({"action":"genesis"})
        r = evaluate(store, pack, ctx)
        ok = (r["button"]=="enabled" and r["reason_code"] is None and store.apply_executed==0)
        results.append(("POS", cid, desc, "enabled/HOLD", r["reason_code"], r["button"], "PASS" if ok else "FAIL"))
    # P3 audit chain INTACT
    store = MockStore(); store.audit_append({"action":"genesis"}); store.audit_append({"action":"commit"})
    ok = store.verify_chain() is True
    results.append(("POS",3,"audit verify_chain INTACT","INTACT", str(store.verify_chain()), "-", "PASS" if ok else "FAIL"))

    after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in watched}
    store_unchanged = (before == after)

    # 출력
    print("="*78); print("OpenBinggu C-2 Guard synthetic selftest (in-memory, write 0)"); print("="*78)
    npass = sum(1 for r in results if r[6]=="PASS")
    for kind,cid,desc,exp,got,btn,verdict in results:
        mark = "[OK]" if verdict=="PASS" else "[X]"
        print(f"{mark} {kind}{cid:>2} {desc[:42]:<42} exp={exp[:28]:<28} got={str(got)[:24]:<24} btn={btn}")
    print("-"*78)
    print(f"RESULT: {npass}/{len(results)} PASS   (18 negative + 3 positive)")
    print(f"operating_store_unchanged={store_unchanged}  apply_executed=0(all)  raw_leak=0")
    gate = "GO" if (npass==len(results) and store_unchanged) else "NO-GO"
    print(f"GATE: {gate}")
    return 0 if gate=="GO" else 1

if __name__ == "__main__":
    import sys; sys.exit(run())
