"""P4 selftest — data_class 인자화 + candidate/active 라벨 분리 + 실 ledger 라벨 정정.

temp 전용(합성 ledger는 로직 검증용·실 ledger 무접촉). cloud/DB 0 / 실 ledger write 0 / mtime 불변.
GATE=GO 조건: 전 항목 PASS.
"""
import os
import sqlite3
import sys
import tempfile

# selftest 결정성 — semantic(canonical/Ollama·임베딩) 유사도는 머신/부하마다 달라
# build_cloud_pack 의 graph/canonical 경로가 비결정적이 된다(case 14 실 ledger 빌드).
# 운영 build_real_pack 경로는 불변(semantic ON 그대로) — selftest 진입부에서만 강제 OFF.
# 자매 선례: binggu_rationale_suggest._selftest / watcher_pack_builder_m0.run_selftest 동일 패턴.
# (known_match 결정성은 hash 시드가 아니라 _retrieval_eval 의 sorted(ct)[:3] + self pre-seed 로 확보 —
#  실제 case 14 flaky 원인은 hash 랜덤화가 아닌 tie-break 아티팩트였고 export 쪽에서 해소됨.)
os.environ["BINGGU_SEMANTIC_OFF"] = "1"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binggu_publish_p4_label as P4
import binggu_cloud_pack_export as EXP
from binggu_schema import apply_schema  # 정본 스키마 (Phase1)
import binggu_platform as _plat  # default_ledger(BINGGU_HOME 존중 · 격리 판단 일치)

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")


def _make_ledger(path, nodes):
    conn = sqlite3.connect(path)
    apply_schema(conn)
    for i, (nid, ntype, sent, cand, state) in enumerate(nodes):
        conn.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,state,content_hash,created_at)"
                     " VALUES(?,?,?,?,?,?,?)", (nid, ntype, sent, cand, state, "h%d" % i, "t0"))
    conn.commit(); conn.close()


def main():
    tmp = tempfile.mkdtemp(prefix="bgp_p4_")
    # 격리 존중: build_real_pack 이 실제 읽는 ledger(default_ledger·BINGGU_HOME 우선)와 동일 경로로
    # 분기 판단해야 격리(BINGGU_HOME=temp)에서 오탐 없음 — expanduser 하드코딩 제거(P4 gate 14 수정).
    real_led = _plat.default_ledger()
    real_mtime = os.path.getmtime(real_led) if os.path.exists(real_led) else None

    # ── 1. data_class 인자화 4종 (build_cloud_pack) ──
    n, ev, g, c = EXP.synthetic_approved()
    def _dc(dc):
        out = tempfile.mkdtemp(prefix="p4dc_")
        return EXP.build_cloud_pack(out, n, ev, g, c, data_class=dc)["manifest"]
    check("1.synthetic_fixture release_ready False", _dc("synthetic_fixture")["release_ready"] is False)
    check("2.real_candidate release_ready False", _dc("real_candidate")["release_ready"] is False)
    check("3.real_active release_ready True(통과시)", _dc("real_active")["release_ready"] is True)
    try:
        _dc("real_release"); check("4.비허용 data_class 거부", False)
    except ValueError:
        check("4.비허용 data_class 거부", True)

    # ── 5. candidate ledger → real_candidate 빌드, release 금지 ──
    led_c = os.path.join(tmp, "cand.sqlite")
    _make_ledger(led_c, [
        ("N1", "증거", "[합성] 로그에 오타가 세 번 찍혔다", 1, "candidate"),
        ("N2", "상태", "[합성] 빌드가 깨져 있다", 1, "candidate"),
        ("N3", "판단", "[합성] 배포 전 한 번 더 확인하자", 1, "candidate"),
    ])
    out5 = os.path.join(tmp, "out5"); db5 = os.path.join(tmp, "q5.sqlite")
    r5 = P4.build_real_pack(led_c, out_dir=out5, db_path=db5, state="candidate")
    check("5.candidate → DRYRUN_OK", r5["status"] == "DRYRUN_OK")
    check("6.data_class=real_candidate", r5.get("data_class") == "real_candidate")
    check("7.candidate release_ready False(release 금지)", r5.get("release_ready") is False)
    check("8.real_active/real_release 라벨 아님", r5.get("data_class") not in ("real_active", "real_release"))
    check("9.cloud/db/upload False",
          r5["cloud_upload"] is False and r5["db_insert"] is False and r5["upload_executed"] is False)
    check("10.ZIP/hash/plan 보고 존재",
          bool(r5.get("bundle_hash")) and "deploy_plan" in r5.get("deploy", {})
          and r5["deploy"]["deploy_plan"]["executed"] is False)

    # ── 11. 같은 ledger state=active → active 0 → NO_REAL_LEDGER_DATA ──
    r11 = P4.build_real_pack(led_c, out_dir=os.path.join(tmp, "o11"), db_path=os.path.join(tmp, "q11.sqlite"),
                             state="active")
    check("11.candidate-only에서 active 요청 → NO_REAL_LEDGER_DATA",
          r11["status"] == "BLOCK" and r11["reason"] == "NO_REAL_LEDGER_DATA")

    # ── 12. active ledger → real_active 빌드 ──
    led_a = os.path.join(tmp, "active.sqlite")
    _make_ledger(led_a, [
        ("A1", "증거", "[합성] 로그에 오타가 세 번 찍혔다", 0, "confirmed"),
        ("A2", "상태", "[합성] 빌드가 깨져 있다", 0, "confirmed"),
        ("A3", "판단", "[합성] 배포 전 한 번 더 확인하자", 0, "confirmed"),
    ])
    out12 = os.path.join(tmp, "out12"); db12 = os.path.join(tmp, "q12.sqlite")
    r12 = P4.build_real_pack(led_a, out_dir=out12, db_path=db12, state="active")
    check("12.active → DRYRUN_OK + data_class=real_active",
          r12["status"] == "DRYRUN_OK" and r12.get("data_class") == "real_active")

    # ── 13. empty ledger → NO_REAL_LEDGER_DATA ──
    led_e = os.path.join(tmp, "empty.sqlite"); _make_ledger(led_e, [])
    r13 = P4.build_real_pack(led_e, out_dir=os.path.join(tmp, "o13"), db_path=os.path.join(tmp, "q13.sqlite"),
                             state="candidate")
    check("13.empty → NO_REAL_LEDGER_DATA", r13["status"] == "BLOCK" and r13["reason"] == "NO_REAL_LEDGER_DATA")

    # ── 14. 실 ledger 라벨 정정 (현 상태 candidate 4) ──
    out14 = os.path.join(tmp, "out14"); db14 = os.path.join(tmp, "q14.sqlite")
    # 상태 독립 — candidate>0이면 real_candidate, active>0이면 real_active, 0이면 NO_REAL
    _cn = P4.extract_by_state(real_led)
    if _cn["candidate_rows"]:
        r_real = P4.build_real_pack(out_dir=out14, db_path=db14, state="candidate")
        check("14.실 ledger candidate → real_candidate DRYRUN_OK",
              r_real["status"] == "DRYRUN_OK" and r_real.get("data_class") == "real_candidate"
              and r_real.get("release_ready") is False)
    elif _cn["active_rows"]:
        r_real = P4.build_real_pack(out_dir=out14, db_path=db14, state="active")
        check("14.실 ledger active → real_active DRYRUN_OK",
              r_real["status"] == "DRYRUN_OK" and r_real.get("data_class") == "real_active")
    else:
        r_real = P4.build_real_pack(out_dir=out14, db_path=db14, state="candidate")
        check("14.실 ledger 비어있음 → NO_REAL_LEDGER_DATA",
              r_real["status"] == "BLOCK" and r_real["reason"] == "NO_REAL_LEDGER_DATA")

    # ── 15. 실 ledger 무접촉 mtime ──
    if real_mtime is not None:
        check("15.실 ledger 무접촉(mtime 불변)", abs(os.path.getmtime(real_led) - real_mtime) < 1e-6)
    else:
        check("15.실 ledger 무접촉(파일 없음)", True)

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n=== {passed}/{total} ===")
    gate = "GO" if passed == total else "BLOCK"
    print(f"GATE={gate}")
    print("REAL_LEDGER_P4:", r_real.get("ledger_stats"), "->", r_real.get("data_class"), r_real.get("release_status"))
    return 0 if gate == "GO" else 1


if __name__ == "__main__":
    # selftest 결정성 — PYTHONHASHSEED 고정(테스트 전용, belt-and-suspenders).
    # ※ 과거엔 _retrieval_eval 이 set 순회(list(set(...))[:3])라 hash 랜덤화로 known_match_failures 가
    #    0/1 흔들린다고 봤으나, 실제 case 14 flaky 원인은 결정적 tie-break 아티팩트였다:
    #    q_terms 는 sorted(ct)[:3](이미 hash 무관)인데 export 의 검색 루프가 self overlap 동률(1.0)을
    #    먼저 나온 chunk 로 고정 → 뒤 chunk self-query 가 자기를 못 집어 허위 known_fail=1.
    #    → export._retrieval_eval 에서 self chunk pre-seed 로 해소. seed 고정은 잔여 결정성 보강용으로 유지.
    if os.environ.get("PYTHONHASHSEED") != "0":
        os.environ["PYTHONHASHSEED"] = "0"
        import subprocess
        sys.exit(subprocess.run([sys.executable, *sys.argv]).returncode)
    sys.exit(main())
