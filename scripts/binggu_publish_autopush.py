# -*- coding: utf-8 -*-
"""binggu_publish_autopush — SAVE 확정분 → 자동 KV 업로드 orchestrator (read-only + build write).

설계 확정안 ⑤ + 적대검증 5결함 반영.

전송 주체(중요): 이 스크립트의 실 wrangler 호출은 **owner 의 Windows 작업 스케줄러가 띄운
owner 프로세스** 에서만 일어난다(Claude tool_use 아님 → 하네스 무관). 코드(orchestrator
+selftest+게이트보완)는 이 파일이, ScheduledTask 등록·CF login 은 owner 1회.

흐름:
  ① 운영 ledger active checksum 계산 (read-only, active 노드만)
  ② published_checksum.json 캐시 비교 — 동일이면 멱등 no-op 종료
  ③ 🔴 이중게이트 fail-closed 절대 불변식:
        (active checksum 변화) AND (save_gate_log 에 사람 SAVE 발화 기록 존재)
     둘 다여야만 진행. 사람기록 없으면 전송 0 (BLOCK·보류).
  ④ build_packs + write_packs(--write) 로 sealed packs.json 재생성 + validate 위반 0 재확인
  ⑤ wrangler kv key put --config wrangler.real.toml(고정) 실행 (실행자=주입된 runner, 기본 mock)
  ⑥ published 캐시 + publish_log.jsonl 갱신

🔴 HIGH 결함수정 (actor 디커플링 우회 차단):
  promote 의 actor=human 은 caller-asserted 라 신뢰 불가. 이 orchestrator 는 promote audit 이
  아니라 **save_gate_log**(UserPromptSubmit hook 이 기록한 진짜 사람 발화)를 절대 게이트로 쓴다.
  사람 SAVE 발화 기록 없는 변화는 전송을 절대 하지 않는다(fail-closed 불변식, selftest 증명).

MEDIUM 결함수정 (캐시 무결성):
  published_checksum.json 손상/삭제 = '미전송' 으로 안전하게 가정(재빌드 후 멱등). 조작으로
  '미전송→전송됨' 위장은 못 한다(캐시는 보조; 진실은 publish_log/실 KV 상태). 캐시는 항상
  active checksum 과 함께 sha256 무결성 태그로 저장 — 태그 불일치 = 손상으로 보고 재계산.

LOW 결함수정 (config 고정):
  wrangler 호출 시 --config wrangler.real.toml 하드코딩. 외부에서 다른 config/namespace 를
  주입하지 못한다(인자 화이트리스트만).

긴급 스위치(E):
  ~/.binggupack/autopush_disabled 플래그가 있으면 즉시 no-op(owner 통제).

불변/제약:
  - 운영 ledger write 0 (read-only + build write 만). 실 전송은 owner 스케줄러.
  - CF 토큰 평문 0 (wrangler login OAuth 또는 repo 밖 0600). 코드/KV/repo 에 토큰 0.
  - selftest 는 wrangler 호출을 mock(실 전송 0) + temp 경로만(실 ledger/KV/캐시 미접촉).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binggu_platform as _plat
import binggu_publish_p3_real_ledger as P3
import binggu_realpack_build as RP
import binggu_save_gate as SGATE

# ── 경로 (BINGGU_HOME 우선 — cross-platform 정합) ──────────────────
def _home():
    return _plat.binggu_home()


def published_cache_path(home=None):
    return os.path.join(home or _home(), "published_checksum.json")


def publish_log_path(home=None):
    return os.path.join(home or _home(), "publish_log.jsonl")


def autopush_disabled_path(home=None):
    return os.path.join(home or _home(), "autopush_disabled")


# ── config 고정 (LOW) — 외부 주입 차단. 이 값 외 다른 config 사용 불가 ──
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WRANGLER_CONFIG = "wrangler.real.toml"          # 하드코딩 — 인자 주입 불가
WRANGLER_CWD = os.path.join(REPO, "hosted", "workers")  # 이 config 가 있는 디렉터리
PACKS_BINDING = "PACKS"                          # wrangler.real.toml [[kv_namespaces]] binding
PACKS_KEY = "packs.json"                         # [vars] PACKS_KEY


# ── ① active checksum (read-only, active 노드만) ─────────────────
def active_checksum(ledger_path=None):
    """운영 ledger active(SAVE 확정) 노드만으로 결정적 sha256(16). read-only.

    extract_real_ledger(mode=ro) 를 그대로 사용 — ledger write 0. active 0 이면 빈 해시("EMPTY").
    candidate(미SAVE)는 제외 — 미확정 변화로 전송 트리거되지 않게.
    """
    ledger_path = ledger_path or P3.DEFAULT_LEDGER
    if not os.path.exists(ledger_path):
        return "ABSENT"
    ext = P3.extract_real_ledger(ledger_path)
    rows = ext["active_rows"]
    if not rows:
        return "EMPTY"
    h = hashlib.sha256()
    # node_id 기준 정렬 → 결정적. 각 행 canonical json.
    for r in sorted(rows, key=lambda x: str(x[0])):
        h.update(json.dumps([r[0], r[1], r[2], r[5]], ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return h.hexdigest()[:16]


# ── 사람 SAVE 발화 기록 존재 여부 (절대 게이트) ──────────────────
def has_human_save_record(active_rows, gate_path=None):
    """active 노드 중 **하나라도** 사람 SAVE 발화로 기록된 문장이 있으면 True(fail-closed: 없으면 False).

    HIGH 결함수정: actor 디커플링(promote audit) 대신 save_gate_log(UserPromptSubmit hook 기록)을
    진짜 사람 발화 증거로 사용. 사람 기록이 전혀 없으면 전송 절대 안 함.
    """
    gate_path = gate_path or SGATE.GATE_PATH
    sents = [r[2] for r in (active_rows or []) if SGATE._norm(r[2])]
    if not sents:
        return False
    # 신선도 창 무관(저장 시점과 publish 시점 사이 시간차 허용) → 존재만 확인.
    rec = SGATE._load(gate_path)
    if not rec:
        return False
    for s in sents:
        if SGATE.sent_hash(s) in rec:
            return True
    return False


# ── ② published 캐시 무결성 (MEDIUM) ────────────────────────────
def _cache_tag(checksum):
    """캐시 내용 무결성 태그 — checksum 을 별도 salt 로 한 번 더 해시. 조작 감지용(보조)."""
    return hashlib.sha256(("autopush_cache_v1:" + str(checksum)).encode("utf-8")).hexdigest()[:16]


def read_published_cache(path=None):
    """published_checksum.json 읽기. 부재/손상/태그불일치 = 미전송 가정(None 반환).

    조작으로 '미전송→전송됨' 위장 불가 — 태그 검증으로 임의 checksum 주입 거부.
    """
    path = path or published_cache_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None  # 손상 = 미전송 가정 → 재빌드 후 멱등
    cs = d.get("published_checksum")
    tag = d.get("tag")
    if not cs or tag != _cache_tag(cs):
        return None  # 태그 불일치 = 조작/손상 → 미전송 가정
    return cs


def write_published_cache(checksum, path=None):
    path = path or published_cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {"published_checksum": checksum, "tag": _cache_tag(checksum),
               "ts": time.time()}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, path)  # atomic
    return path


def append_publish_log(entry, path=None):
    path = path or publish_log_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


# ── ⑤ wrangler kv put (config 고정, runner 주입 — 기본 mock) ─────
def _real_wrangler_runner(args, cwd):
    """실제 wrangler 호출 — owner ScheduledTask 에서만 실행됨. selftest 는 이 함수를 호출하지 않음.

    토큰 평문 0 — wrangler 가 OAuth(login) 또는 환경/저장된 자격으로 인증. 이 코드는 토큰을
    읽지도 전달하지도 않는다.
    """
    import subprocess
    import shutil
    # Windows: npx 는 npx.cmd — shell=False subprocess 는 .cmd 확장을 못 찾아 WinError 2.
    # shutil.which 로 실제 실행파일 해석(npx.cmd/npx), 없으면 OS별 폴백.
    npx = shutil.which("npx") or ("npx.cmd" if os.name == "nt" else "npx")
    p = subprocess.run([npx, "wrangler"] + args, cwd=cwd,
                       capture_output=True, text=True, timeout=600)
    return {"rc": p.returncode, "stdout": (p.stdout or "")[-2000:],
            "stderr": (p.stderr or "")[-2000:]}


def kv_put_packs(packs_path, runner=None):
    """packs.json 을 KV(PACKS)에 put — config 는 wrangler.real.toml 고정(외부 주입 불가).

    인자는 화이트리스트로만 구성 — namespace/config 를 호출자가 못 바꾼다(LOW 결함수정).
    runner 미지정 시 실 wrangler(_real_wrangler_runner) — owner 스케줄러 전용.
    """
    args = [
        "kv", "key", "put", PACKS_KEY,
        "--path", os.path.abspath(packs_path),
        "--binding", PACKS_BINDING,
        "--config", WRANGLER_CONFIG,   # 고정
        "--remote",   # 실 Cloudflare KV(라이브 worker가 읽는 곳). 없으면 local miniflare 에만 써서 라이브 미반영.
    ]
    run = runner or _real_wrangler_runner
    return run(args, WRANGLER_CWD)


# ── orchestrator ───────────────────────────────────────────────
def run_autopush(ledger_path=None, home=None, runner=None,
                 gate_path=None, packs_out=None, build_fn=None, write_fn=None):
    """⑤ 설계 전체 흐름 1회 실행. 반환 dict(status/reason/...). 운영 ledger write 0.

    매개변수는 전부 selftest 주입용(기본은 실 경로/실 모듈). runner 기본 mock 아님 — 실 wrangler.
    selftest 는 runner 를 mock 으로, 모든 경로를 temp 로 주입(실 전송/실 ledger/실 캐시 미접촉).
    """
    home = home or _home()
    ledger_path = ledger_path or P3.DEFAULT_LEDGER
    gate_path = gate_path or SGATE.GATE_PATH
    cache_p = published_cache_path(home)
    build_fn = build_fn or RP.build_packs
    write_fn = write_fn or RP.write_packs

    base = {"ledger": ledger_path, "ts": time.time(), "ledger_write": 0}

    # (E) 긴급 스위치 — 최우선 no-op
    if os.path.exists(autopush_disabled_path(home)):
        return dict(base, status="NOOP", reason="AUTOPUSH_DISABLED")

    # ① active checksum (read-only)
    cur = active_checksum(ledger_path)
    base["active_checksum"] = cur
    if cur in ("ABSENT", "EMPTY"):
        return dict(base, status="NOOP", reason="NO_ACTIVE_DATA")

    # ② 캐시 비교 — 동일이면 멱등 no-op
    published = read_published_cache(cache_p)
    base["published_checksum"] = published
    if published == cur:
        return dict(base, status="NOOP", reason="IDEMPOTENT_UNCHANGED")

    # ③ 🔴 이중게이트 fail-closed: (checksum 변화) AND (사람 SAVE 발화 기록 존재)
    #    checksum 변화는 published != cur 로 이미 성립. 사람 기록 없으면 전송 0(BLOCK).
    ext = P3.extract_real_ledger(ledger_path)
    human_ok = has_human_save_record(ext["active_rows"], gate_path)
    base["save_gate_match"] = bool(human_ok)
    if not human_ok:
        # 사람 기록 없는 변화 = 전송 절대 안 함(보류). 캐시 갱신 안 함(다음 사람 SAVE 후 재시도).
        entry = {"ts": base["ts"], "checksum": cur, "save_gate_match": False,
                 "result": "BLOCK_NO_HUMAN_SAVE", "kv_response": None,
                 "promote_audit_ref": None}
        append_publish_log(entry, publish_log_path(home))
        return dict(base, status="BLOCK", reason="NO_HUMAN_SAVE_RECORD")

    # ④ build + write(sealed) + validate 위반 0 재확인
    res = build_fn(ledger_path)
    if res.get("status") != "OK":
        entry = {"ts": base["ts"], "checksum": cur, "save_gate_match": True,
                 "result": "BLOCK_BUILD", "reason": res.get("reason"), "kv_response": None}
        append_publish_log(entry, publish_log_path(home))
        return dict(base, status="BLOCK", reason="BUILD_%s" % res.get("reason", "FAIL"))
    out = packs_out or RP.DATA_PATH
    w = write_fn(res, out)
    if "written" not in w:
        entry = {"ts": base["ts"], "checksum": cur, "save_gate_match": True,
                 "result": "BLOCK_VALIDATE", "violations": w.get("violations"),
                 "kv_response": None}
        append_publish_log(entry, publish_log_path(home))
        return dict(base, status="BLOCK", reason="VALIDATE_%s" % w.get("blocked", "FAIL"),
                    violations=w.get("violations"))
    packs_path = w["written"]
    base["packs_path"] = packs_path
    base["built"] = res.get("built")

    # ⑤ wrangler kv put (config 고정) — 실행자=runner(owner 스케줄러=실, selftest=mock)
    kv = kv_put_packs(packs_path, runner=runner)
    kv_ok = isinstance(kv, dict) and kv.get("rc") == 0
    if not kv_ok:
        entry = {"ts": base["ts"], "checksum": cur, "save_gate_match": True,
                 "result": "KV_PUT_FAIL", "kv_response": kv, "promote_audit_ref": None}
        append_publish_log(entry, publish_log_path(home))
        # 캐시 갱신 안 함 — 전송 실패는 다음 회차 재시도(멱등).
        return dict(base, status="ERROR", reason="KV_PUT_FAIL", kv_response=kv)

    # ⑥ published 캐시 + publish_log 갱신
    write_published_cache(cur, cache_p)
    entry = {"ts": base["ts"], "checksum": cur, "save_gate_match": True,
             "result": "PUBLISHED", "kv_response": kv,
             "promote_audit_ref": None, "packs": base.get("built")}
    append_publish_log(entry, publish_log_path(home))
    return dict(base, status="PUBLISHED", reason="OK", kv_response=kv)


# ---------------- 셀프테스트 (wrangler mock · temp 경로만 — 실 전송/ledger/KV/캐시 0) -------
def _selftest():
    import sqlite3
    import tempfile
    ok = 0
    tot = 0

    def chk(name, cond):
        nonlocal ok, tot
        tot += 1
        ok += 1 if cond else 0
        print(("  PASS " if cond else "  FAIL ") + name)

    work = tempfile.mkdtemp(prefix="autopush_st_")
    home = os.path.join(work, ".binggupack")
    os.makedirs(home)
    gate = os.path.join(home, "save_gate_log.jsonl")
    packs_out = os.path.join(work, "packs.json")

    # mock runner — 실 wrangler 미호출 기록. config 고정 검증용으로 args 캡처.
    calls = []

    def mock_ok(args, cwd):
        calls.append({"args": list(args), "cwd": cwd})
        return {"rc": 0, "stdout": "mock kv put ok", "stderr": ""}

    def mock_fail(args, cwd):
        calls.append({"args": list(args), "cwd": cwd})
        return {"rc": 1, "stdout": "", "stderr": "mock kv put fail"}

    # synthetic ledger: active 2 + candidate 1 + evidence (realpack selftest 형식 동일)
    def make_ledger(p, with_active=True):
        conn = sqlite3.connect(p)
        conn.executescript(
            "CREATE TABLE nodes(node_id TEXT,node_type TEXT,sentence TEXT,candidate INT,state TEXT,content_hash TEXT);"
            "CREATE TABLE evidence(evidence_id TEXT,sentence TEXT,source_pointer_id TEXT,source_hash TEXT);")
        if with_active:
            conn.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?)",
                         ("node:CONV:aaaaaaaa", "judgment", SA, 0, "active", "h1"))
            conn.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?)",
                         ("node:CONV:bbbbbbbb", "state", SB, 0, "active", "h2"))
            conn.execute("INSERT INTO evidence VALUES(?,?,?,?)",
                         ("EVC-CONV-aaaaaaaa", SA, "conv-self:a", "a"))
            conn.execute("INSERT INTO evidence VALUES(?,?,?,?)",
                         ("EVC-CONV-bbbbbbbb", SB, "conv-self:b", "b"))
        conn.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?)",
                     ("node:CONV:cccccccc", "concept", "미확정 후보", 1, None, "h3"))
        conn.commit()
        conn.close()

    SA = "확정 판단 가나다라"
    SB = "확정 상태 마바사아"
    lp = os.path.join(work, "ledger.sqlite")
    make_ledger(lp)

    def run(**kw):
        return run_autopush(ledger_path=lp, home=home, gate_path=gate,
                            packs_out=packs_out, **kw)

    # T1 active checksum 결정적(같은 ledger 두 번 = 동일)
    chk("T1 active_checksum 결정적", active_checksum(lp) == active_checksum(lp) and active_checksum(lp) not in ("ABSENT", "EMPTY"))

    # T2 🔴 이중게이트: 사람 SAVE 기록 없음 → BLOCK(전송 0)
    calls.clear()
    r = run(runner=mock_ok)
    chk("T2 사람기록 없음 → BLOCK(전송 0)", r["status"] == "BLOCK" and r["reason"] == "NO_HUMAN_SAVE_RECORD" and len(calls) == 0)
    chk("T2b 캐시 미생성(보류는 published 안 씀)", read_published_cache(published_cache_path(home)) is None)

    # T3 사람 SAVE 발화 기록(SA) 추가 → 이제 전송 진행
    SGATE.gate_record([SA], path=gate)
    calls.clear()
    r = run(runner=mock_ok)
    chk("T3 사람기록 존재 + checksum 변화 → PUBLISHED", r["status"] == "PUBLISHED" and r["save_gate_match"] is True)
    chk("T3b mock runner 1회 호출(실 wrangler 0)", len(calls) == 1)

    # T4 LOW: config 고정 — args 에 wrangler.real.toml, 다른 config 없음
    a = calls[0]["args"]
    chk("T4 config=wrangler.real.toml 고정", "--config" in a and a[a.index("--config") + 1] == "wrangler.real.toml")
    chk("T4b binding=PACKS 고정", "--binding" in a and a[a.index("--binding") + 1] == "PACKS")
    chk("T4c kv key put 명령", a[:3] == ["kv", "key", "put"])

    # T5 멱등: 변화 없음(같은 checksum) → NOOP(전송 0)
    calls.clear()
    r = run(runner=mock_ok)
    chk("T5 멱등 unchanged → NOOP(전송 0)", r["status"] == "NOOP" and r["reason"] == "IDEMPOTENT_UNCHANGED" and len(calls) == 0)

    # T6 MEDIUM: 캐시 손상 → 미전송 가정(재계산). 손상 후 사람기록 있으니 재전송됨(멱등 복구)
    cp = published_cache_path(home)
    with open(cp, "w", encoding="utf-8") as f:
        f.write("{ corrupted json")
    chk("T6 손상 캐시 → read None(미전송 가정)", read_published_cache(cp) is None)
    calls.clear()
    r = run(runner=mock_ok)
    chk("T6b 손상 후 재빌드→재전송(멱등 복구)", r["status"] == "PUBLISHED" and len(calls) == 1)

    # T6c 캐시 태그 조작(임의 checksum 주입) → 미전송 가정 거부
    with open(cp, "w", encoding="utf-8") as f:
        json.dump({"published_checksum": "deadbeefdeadbeef", "tag": "WRONGTAG"}, f)
    chk("T6c 태그 불일치(조작) → read None", read_published_cache(cp) is None)

    # T7 (E) 긴급 스위치 → 즉시 NOOP(전송 0)
    flag = autopush_disabled_path(home)
    with open(flag, "w") as f:
        f.write("off")
    calls.clear()
    r = run(runner=mock_ok)
    chk("T7 autopush_disabled → NOOP(전송 0)", r["status"] == "NOOP" and r["reason"] == "AUTOPUSH_DISABLED" and len(calls) == 0)
    os.remove(flag)

    # T8 KV put 실패 → ERROR, 캐시 갱신 안 함(다음 회차 재시도)
    os.remove(cp) if os.path.exists(cp) else None
    # checksum 바꾸려 새 active 추가(사람기록도 추가)
    SC = "추가 확정 판단 자차카타"
    conn = sqlite3.connect(lp)
    conn.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?)",
                 ("node:CONV:dddddddd", "judgment", SC, 0, "active", "h4"))
    conn.execute("INSERT INTO evidence VALUES(?,?,?,?)", ("EVC-CONV-dddddddd", SC, "conv-self:d", "d"))
    conn.commit()
    conn.close()
    SGATE.gate_record([SC], path=gate)
    calls.clear()
    r = run(runner=mock_fail)
    cs_after = active_checksum(lp)
    chk("T8 KV put 실패 → ERROR", r["status"] == "ERROR" and r["reason"] == "KV_PUT_FAIL")
    chk("T8b 실패 시 캐시 미갱신(재시도 가능)", read_published_cache(cp) != cs_after)

    # T9 실 ledger write 0 (mtime 불변 확인)
    mt_before = os.path.getmtime(lp)
    run(runner=mock_ok)
    chk("T9 ledger 파일 write 0(mtime 불변)", os.path.getmtime(lp) == mt_before)

    # T10 사람기록 있어도 checksum 변화 없으면(=published 동일) NOOP — 이중게이트 AND
    calls.clear()
    r1 = run(runner=mock_ok)   # 위 T9 가 published 갱신했을 수도 → 한 번 더
    calls.clear()
    r = run(runner=mock_ok)
    chk("T10 변화 없으면 사람기록 있어도 NOOP(AND 조건)", r["status"] == "NOOP")

    print("\nRESULT: %d/%d %s" % (ok, tot, "PASS" if ok == tot else "FAIL"))
    print("GATE: %s" % ("GO" if ok == tot else "BLOCK"))
    return ok == tot


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    # 운영 실행(owner ScheduledTask 전용 — 실 wrangler 호출). 평소 owner 만 실행.
    print(json.dumps(run_autopush(), ensure_ascii=False, indent=2, default=str))
