"""P1 selftest — publish_queue + 멱등 잠금 + 상태머신 + APPROVE.

temp DB 전용. 실 ledger write 0 / cloud 0 / 운영 DB insert 0.
GATE=GO 조건: 전 항목 PASS.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binggu_publish_queue_p1 as Q

H_NODE = Q.content_hash(b"node-A-content")
H_EVID = Q.content_hash(b"evidence-A-content")
H_BUNDLE = Q.content_hash(b"bundle-zip-content")
H_NODE2 = Q.content_hash(b"node-A-tampered")

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")


def expect_raise(name, fn, exc=Q.QueueError):
    try:
        fn()
        check(name, False)
    except exc:
        check(name, True)
    except Exception as e:  # noqa
        print(f"   (wrong exc: {type(e).__name__}: {e})")
        check(name, False)


def main():
    tmp = tempfile.mkdtemp(prefix="bgp_pq_p1_")
    db = os.path.join(tmp, "pq_temp.sqlite")
    conn = Q.open_queue(db)

    # 1. 테이블 생성
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='publish_queue'")
    check("1.publish_queue 테이블 생성", cur.fetchone() is not None)

    # 2. enqueue 정상
    Q.enqueue(conn, "q1", "NODE:1", H_NODE, H_EVID)
    check("2.enqueue 정상(evidence 연결)", Q._status(conn, "q1") == "queued")

    # 3. enqueue evidence 없음 → BLOCK
    expect_raise("3.evidence 없음 enqueue → BLOCK",
                 lambda: Q.enqueue(conn, "q2", "NODE:2", H_NODE, ""))

    # 4. 중복 enqueue(같은 queue_id) → 멱등 차단
    expect_raise("4.중복 enqueue 차단(멱등)",
                 lambda: Q.enqueue(conn, "q1", "NODE:1", H_NODE, H_EVID))

    # 5. node_hash 비-sha256 → reject
    expect_raise("5.node_hash 비-full-sha256 → reject",
                 lambda: Q.enqueue(conn, "q3", "NODE:3", "abc123", H_EVID))

    # 6. acquire_lock 첫 성공
    check("6.acquire_lock 첫 성공", Q.acquire_lock(conn, "q1", "watcherA") is True)

    # 7. 다른 owner 잠금 시도 → 중복 차단
    expect_raise("7.다른 owner 잠금 → 중복 차단",
                 lambda: Q.acquire_lock(conn, "q1", "watcherB"))

    # 8. 같은 owner 재요청 → 멱등 OK
    check("8.같은 owner 재요청 멱등 OK", Q.acquire_lock(conn, "q1", "watcherA") is True)

    # 9~12. 정상 전이 체인
    Q.transition(conn, "q1", "building")
    check("9.queued→building", Q._status(conn, "q1") == "building")
    Q.transition(conn, "q1", "candidate_ready")
    check("10.building→candidate_ready", Q._status(conn, "q1") == "candidate_ready")

    # 11. APPROVE 정상 파싱
    parsed = Q.parse_approve(f"APPROVE q1 {H_BUNDLE}")
    check("11.APPROVE 정상 파싱", parsed["queue_id"] == "q1" and parsed["bundle_full_hash"] == H_BUNDLE)

    # 12. approve 수행 → approved
    Q.approve(conn, f"APPROVE q1 {H_BUNDLE}", H_BUNDLE)
    check("12.승인 → approved", Q._status(conn, "q1") == "approved")
    Q.transition(conn, "q1", "deploying")
    Q.transition(conn, "q1", "deployed")
    check("13.deploying→deployed(종단)", Q._status(conn, "q1") == "deployed")

    # 14. 불법 전이: deployed→building → ABORT
    expect_raise("14.deployed→building 불법 전이 ABORT",
                 lambda: Q.transition(conn, "q1", "building"), Q.IllegalTransition)

    # 15. 불법 전이: queued→deployed 점프 → ABORT
    Q.enqueue(conn, "q5", "NODE:5", H_NODE, H_EVID)
    expect_raise("15.queued→deployed 점프 ABORT",
                 lambda: Q.transition(conn, "q5", "deployed"), Q.IllegalTransition)

    # 16. APPROVE 형식 오류(인자 부족) → reject
    expect_raise("16.APPROVE 인자 부족 → reject",
                 lambda: Q.parse_approve("APPROVE q1"))

    # 17. APPROVE bundle이 hash8(짧음) → reject (full sha256 요구)
    expect_raise("17.APPROVE hash8 짧음 → reject",
                 lambda: Q.parse_approve(f"APPROVE q1 {H_BUNDLE[:8]}"))

    # 18. 안전토큰 형태 → reject
    expect_raise("18.안전토큰 승인 → reject",
                 lambda: Q.parse_approve("APPROVE q1 " + "TOKEN" + "=deadbeef"))

    # 19. hash 3중 검증: 일치 OK / node 불일치 ABORT
    check("19a.hash 3중 일치 OK",
          Q.verify_hash_triple(H_NODE, H_EVID, H_BUNDLE, H_NODE, H_EVID, H_BUNDLE) is True)
    expect_raise("19b.node_hash 불일치 → ABORT",
                 lambda: Q.verify_hash_triple(H_NODE, H_EVID, H_BUNDLE, H_NODE2, H_EVID, H_BUNDLE),
                 Q.IllegalTransition)

    # 20. 검증기 fail-closed (미실행/에러/빈입력/증거없음 → 전부 BLOCK→failed)
    Q.enqueue(conn, "q6", "NODE:6", H_NODE, H_EVID)
    Q.transition(conn, "q6", "building")
    check("20a.검증기 미실행(None) → BLOCK",
          Q.validation_passes(None, True) is False)
    check("20b.검증기 에러 → BLOCK",
          Q.validation_passes({"error": "timeout"}, True) is False)
    check("20c.빈 입력 → BLOCK",
          Q.validation_passes({"ok": True, "count": 0}, True) is False)
    check("20d.증거파일 없음 → BLOCK",
          Q.validation_passes({"ok": True, "count": 3}, False) is False)
    check("20e.정상 검증 → PASS",
          Q.validation_passes({"ok": True, "count": 3}, True) is True)
    Q.mark_block(conn, "q6", "검증기 미실행")
    check("20f.BLOCK → failed 귀결", Q._status(conn, "q6") == "failed")

    # 21. 운영 ledger 경로 거부 (P1 fail-closed)
    # BINGGU_HOME 우선 실 장부 경로 — 가드(_OPERATIONAL_LEDGER=default_ledger())와 동일 기준.
    op = Q._plat.default_ledger()
    expect_raise("21.운영 ledger 경로 거부", lambda: Q.open_queue(op))

    conn.close()

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n=== {passed}/{total} ===")
    gate = "GO" if passed == total else "BLOCK"
    print(f"GATE={gate}")
    return 0 if gate == "GO" else 1


if __name__ == "__main__":
    sys.exit(main())
