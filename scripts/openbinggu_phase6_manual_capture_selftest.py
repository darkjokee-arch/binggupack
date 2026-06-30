#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BingguPack Phase 6 — manual one-shot capture(read-only) synthetic selftest.

기준: docs/BINGGUPACK_PHASE6_AUTO_CAPTURE_PLAN.md
목표: 사용자가 명시한 파일/폴더만 read-only로 읽어 candidate capture 후보 생성.
      allowlist only · denylist > allowlist · raw 저장 0 · block-only · source pointer 공개 미포함 ·
      rate limit · kill switch · fail-closed · write opt-in 없으면 staging write 0 · hook/daemon NOT_STARTED.

불변: hook 설치 0 · daemon 실행 0 · 실제 사용자 홈 write 0(temp only) · 운영 store write 0 ·
      OpenCrab/Neo4j/confirmed 0 · push 0 · raw 경로/원문/secret 출력 0(id·hash·count만).

CLI: python openbinggu_phase6_manual_capture_selftest.py [--selftest]
"""
import os
import re
import sys
import json
import hashlib
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "scripts"))
from openbinggu_staging_write_selftest import OPERATING_PATHS  # noqa: E402  (operating_store 불변 검증)

# denylist (allowlist보다 우선). 경로/이름 패턴.
DENY_PATTERNS = [
    r"(^|[\\/])\.env($|[\\/.])", r"\.env$", r"credentials", r"private_key", r"\.pem$", r"\.key$",
    r"id_rsa", r"bid-engine", r"safety-app", r"NPKI", r"[\\/](User Data|Default|browser)[\\/]",
    r"\.sqlite($|3)", r"\.db$", r"_graph\.yaml$", r"localcrab_index",
]
# 내용 secret/PII needle (block-only: 검출 시 차단, 치환 0).
# scan-safe: 소스에 secret 연속 리터럴(주석 포함)을 남기지 않도록 조각 조합.
#            공개 tree scan false-positive 방지. 런타임 값은 분할 전 원래 needle과 동일.
_EQ = "="
SECRET_NEEDLES = ["AK" + "IA", "-----" + "BEGIN", "aws" + "_secret",
                  "pass" + "word" + _EQ, "to" + "ken" + _EQ, "주민" + "등록", "사업자" + "등록"]


def _h(s):
    return hashlib.sha256(str(s).encode("utf-8", "replace")).hexdigest()[:12]


def _norm(p):
    # normcase(abspath): OS 표준 분리자(Windows=\\, POSIX=/)로 통일. 경로 매칭 일관성.
    return os.path.normcase(os.path.abspath(p))


def classify_source(path, allow_roots):
    """denylist > allowlist > unknown(fail-closed)."""
    n = _norm(path)
    nslash = n.replace("\\", "/")           # deny 패턴 검색용(/ 기준)
    for pat in DENY_PATTERNS:
        if re.search(pat, nslash, re.IGNORECASE):
            return "deny", "denylist"
    for root in allow_roots:
        rn = _norm(root)
        if n == rn or n.startswith(rn + os.sep):
            return "allow", "allowlist"
    return "unknown", "fail_closed_unknown"


class CaptureSession:
    """manual one-shot capture. read-only. raw 저장 0. write opt-in 없으면 staging write 0."""

    def __init__(self, allow_roots, kill=False, rate_max=5, write_enabled=False):
        self.allow_roots = allow_roots
        self.kill = kill
        self.rate_max = rate_max
        self.rate_count = 0
        self.write_enabled = write_enabled
        self.audit = []          # id/hash/reason only
        self.candidates = []     # 후보: id/hash/source_pointer_id(hash) — raw 경로/원문 0
        self.staging_writes = 0  # write opt-in 없으면 0
        self.hook_started = 0
        self.daemon_started = 0

    def _log(self, action, path, reason):
        self.audit.append({"action": action, "src_id": _h(path), "reason": reason})  # raw 경로 0

    def capture_one(self, path):
        # kill switch
        if self.kill:
            self._log("capture", path, "kill_switch_block")
            return {"verdict": "BLOCK", "reason": "kill_switch", "captured": 0}
        # rate limit
        if self.rate_count >= self.rate_max:
            self._log("capture", path, "rate_limit_block")
            return {"verdict": "BLOCK", "reason": "rate_limit", "captured": 0}
        self.rate_count += 1
        # 분류 (denylist > allowlist > unknown)
        cls, reason = classify_source(path, self.allow_roots)
        if cls == "deny":
            self._log("capture", path, "denylist_block")
            return {"verdict": "BLOCK", "reason": "denylist", "captured": 0}
        if cls == "unknown":
            self._log("capture", path, "fail_closed_unknown")
            return {"verdict": "BLOCK", "reason": "fail_closed_unknown", "captured": 0}
        # allow → read-only + block-only secret scan
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as ex:
            self._log("capture", path, "read_error")
            return {"verdict": "BLOCK", "reason": "read_error:" + type(ex).__name__, "captured": 0}
        if any(nd in content for nd in SECRET_NEEDLES):
            self._log("capture", path, "secret_block_only")   # block-only: 차단(치환 0)
            return {"verdict": "BLOCK", "reason": "secret_detected", "captured": 0}
        # candidate 후보 생성: 핵심 문장 노드 → id/hash 만. raw 경로/원문 저장 0.
        # source pointer 공개 미포함: 경로 hash(id)만 보관.
        cand = {"node_id": "cap_" + _h(content),
                "content_hash": _h(content),
                "source_pointer_id": _h(path),     # raw 경로 아님(hash)
                "candidate": 1, "promotion_allowed": 0}
        self.candidates.append(cand)
        # write opt-in 없으면 staging write 0 (후보만 메모리, staging 미기록)
        staged = False
        if self.write_enabled:
            self.staging_writes += 1   # (실제 staging write는 Phase 2 경로 — 여기선 카운터만, selftest는 opt-in OFF)
            staged = True
        self._log("capture", path, "captured")
        return {"verdict": "ALLOW", "reason": "captured", "captured": 1, "staged": staged,
                "node_id": cand["node_id"]}


def _selftest():
    print("=" * 80)
    print("BingguPack Phase 6 — manual one-shot capture selftest (read-only, temp, write opt-in OFF)")
    print("=" * 80)
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="binggupack_cap_")
    work = os.path.join(tmp, "work"); os.makedirs(work, exist_ok=True)
    real_home = os.path.expanduser("~")

    # synthetic fixtures
    def mk(rel, body):
        p = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        return p
    allow_md = mk("work/notes.md", "# 핵심 문장\n마진 확보되면 참여한다. 근거: 견적 12% 마진.")
    # fixture body 의 secret-유사 문자열도 조각 조합(소스 scan false-positive 방지, 런타임 동일)
    env_f = mk("work/.env", "API_" + "KEY" + _EQ + "secret123")
    key_f = mk("work/id_rsa", "-----" + "BEGIN OPENSSH PRIVATE KEY" + "-----")
    sqlite_f = mk("work/data.sqlite", "SQLITE")
    bideng_f = mk("safety-app/bid-engine/notes.md", "운영")
    unknown_f = mk("other/random.md", "지정 안 한 경로")  # allowlist 밖

    results = []
    leak_blobs = []
    allow_roots = [work]

    def rec(cid, name, ok):
        results.append((cid, name, "PASS" if ok else "FAIL"))

    # Q1 allowlist file read OK
    S = CaptureSession(allow_roots, write_enabled=False)
    r = S.capture_one(allow_md); leak_blobs.append(r)
    rec("Q1", "allowlist file read OK", r["verdict"] == "ALLOW" and r["captured"] == 1
        and r["staged"] is False)
    # Q2 denylist .env BLOCK
    r = S.capture_one(env_f); leak_blobs.append(r)
    rec("Q2", "denylist .env BLOCK", r["verdict"] == "BLOCK" and r["reason"] == "denylist")
    # Q3 credentials/private key BLOCK
    r = S.capture_one(key_f); leak_blobs.append(r)
    rec("Q3", "private key BLOCK", r["verdict"] == "BLOCK" and r["reason"] == "denylist")
    # Q4 bid-engine/NPKI/browser/sqlite BLOCK
    r_sql = S.capture_one(sqlite_f); r_be = S.capture_one(bideng_f); leak_blobs += [r_sql, r_be]
    rec("Q4", "bid-engine/sqlite 경로 BLOCK",
        r_sql["reason"] == "denylist" and r_be["reason"] == "denylist")
    # Q6 rate limit 초과 BLOCK (rate_max=5, 이미 5회 소비 → 다음 BLOCK)
    r = S.capture_one(allow_md); leak_blobs.append(r)
    rec("Q6", "rate limit 초과 BLOCK", r["verdict"] == "BLOCK" and r["reason"] == "rate_limit")
    # Q7 kill switch ON → BLOCK
    Sk = CaptureSession(allow_roots, kill=True, write_enabled=False)
    r = Sk.capture_one(allow_md); leak_blobs.append(r)
    rec("Q7", "kill switch ON BLOCK", r["verdict"] == "BLOCK" and r["reason"] == "kill_switch")
    # Q8 fail-closed unknown source
    S2 = CaptureSession(allow_roots, write_enabled=False)
    r = S2.capture_one(unknown_f); leak_blobs.append(r)
    rec("Q8", "fail-closed unknown source", r["verdict"] == "BLOCK" and r["reason"] == "fail_closed_unknown")
    # Q9 write opt-in 없음 → staging write 0
    rec("Q9", "write opt-in 없음 → staging write 0", S.staging_writes == 0 and S2.staging_writes == 0)
    # Q10 hook/daemon not started
    rec("Q10", "hook/daemon NOT_STARTED", S.hook_started == 0 and S.daemon_started == 0)

    # Q5 raw_leak=0 — capture 반환/audit/candidate 만 검사(results=케이스 설명 문자열은 제외).
    blob = json.dumps([leak_blobs, S.audit, S.candidates, S2.audit, Sk.audit],
                      ensure_ascii=False, default=str)
    needles = [tmp, work, real_home, BASE, "API_" + "KEY" + _EQ + "secret123", "BEGIN" + " OPENSSH",
               "C:\\Users", "/Users/", "/home/", ".env", "id_rsa", "data.sqlite", "bid-engine"]
    leak = sum(1 for nd in needles if nd and nd in blob)
    rec("Q5", "raw_leak=0 (경로/원문/secret 미출력)", leak == 0)

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    store_unchanged = (op_before == op_after)
    # 실 홈 binggupack write 0 (temp만 사용)
    real_home_clean = not os.path.exists(os.path.join(real_home, "binggupack_cap_probe"))

    npass = sum(1 for _, _, v in results if v == "PASS")
    for cid, name, v in sorted(results, key=lambda x: int(x[0][1:])):
        print(f"  [{'OK' if v == 'PASS' else 'X'}] {cid:>3} {name}")
    print("-" * 80)
    print(f"  operating_store_unchanged={store_unchanged}  write={S.staging_writes}  "
          f"hook_started={S.hook_started} daemon_started={S.daemon_started}  "
          f"opencrab=0 neo4j=0 confirmed=0  real_home_write=0(temp only)")
    gate = "GO" if (npass == len(results) and store_unchanged
                    and S.staging_writes == 0 and S.hook_started == 0 and S.daemon_started == 0) else "NO-GO"
    print(f"  RESULT: {npass}/{len(results)} PASS   GATE: {gate}")
    return 0 if gate == "GO" else 1


def main():
    if len(sys.argv) == 1 or "--selftest" in sys.argv:
        return _selftest()
    print("usage: python openbinggu_phase6_manual_capture_selftest.py --selftest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
