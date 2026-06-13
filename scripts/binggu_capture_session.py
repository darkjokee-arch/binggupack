"""빙구팩 캡처 세션 entrypoint — buffer/classifier를 감싸는 트리거 함수.

설계: BINGGUPACK_USER_ONTOLOGY_EVENT_SCHEMA_DESIGN.md §4
- 메모리만 / ledger write 0 / 파일 저장 0 / OpenCrab 0 / active·confirmed·approval 0
- 신규 hook 등록 0 (수동 호출 가능한 entrypoint 함수까지만).

연결 지점(확인만, 등록은 미래 별도 GO):
- UserPromptSubmit hook → on_user_prompt(utterance)  (발화 캡처 + "빙구팩 저장해" preview)
- Stop hook            → on_session_end()            (세션말 preview)
- "빙구팩 저장해" 감지 = 기존 detect-bingoo-trigger.sh 패턴 재사용 가능
"""

from binggu_capture_buffer import CaptureBuffer


class CaptureSession:
    """세션 1개 동안의 캡처 흐름. 전부 메모리. 영속화 0."""

    def __init__(self):
        self.buf = CaptureBuffer()

    def on_user_prompt(self, utterance, prev_turn=None):
        """발화 1건 진입점(UserPromptSubmit 연결 후보).
        captured면 누적, '빙구팩 저장해'면 preview 반환."""
        return self.buf.feed(utterance, prev_turn)

    def on_session_end(self):
        """세션말 진입점(Stop hook 연결 후보). preview 반환, buffer 삭제 안 함."""
        return {"action": "preview", "trigger": "session_end", "preview": self.buf.render_preview()}

    @property
    def size(self):
        return self.buf.size


# ---------------- 셀프테스트 (메모리만, write 0) ----------------
def _selftest():
    ok = True

    def check(cond, msg):
        nonlocal ok
        if not cond:
            ok = False
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")

    s = CaptureSession()

    # 1) 일반 후보 2개 누적
    s.on_user_prompt("로컬 정본으로 가자")
    s.on_user_prompt("이건 절대 운영DB 건들지 마")
    check(s.size == 2, "T1 일반 후보 2개 누적")

    # 2) 명시저장 pinned 누적
    s.on_user_prompt("이거 저장해")
    check(s.size == 3, "T2 '이거 저장해' pinned 누적 (size=3)")

    # 3) "빙구팩 저장해" preview 반환
    r = s.on_user_prompt("빙구팩 저장해")
    check(r["action"] == "preview" and r["preview"]["count"] == 3, "T3 '빙구팩 저장해' → preview 3개")
    check(r["preview"]["items"][0]["pinned"], "T3b pinned 맨 위")

    # 4) 세션말 함수 호출 preview 반환
    e = s.on_session_end()
    check(e["action"] == "preview" and e["trigger"] == "session_end" and e["preview"]["count"] == 3,
          "T4 세션말 함수 → preview 3개")

    # 5) preview 후 buffer 유지
    check(s.size == 3, "T5 preview 후 buffer 유지 (size=3)")

    # 6) active/confirmed 전이 0
    check(all(it["state"] == "captured_candidate" for it in e["preview"]["items"]),
          "T6 active/confirmed 전이 0")

    # 7) 실발화 20개 경계 보정 (캡처 10 + 제외 10 기대)
    print("\n  [실발화 20개 흘림]")
    capture_expect = [
        "로컬 정본으로 가자", "이건 절대 운영DB 건들지 마", "마감 임박건 우선 처리해",
        "난 짧은 보고를 선호해", "나중에 이거 유료로 팔 거야", "그게 아니라 캐시부터 확인해야지",
        "이거 저장해", "테스트는 항상 먼저 돌려", "아마 B가 더 나을 거야, 비용 때문에",
        "위험해, 마감 직전에 터질 수 있어",
    ]
    ignore_expect = [
        "ㅋㅋ 그래", "지금 진행상황 보여줘", "와 잘됐다", "테스트 한번 돌려봐", "음 그렇구나",
        "고마워 수고했어", "아마 그럴걸", "이거 뭐였지?", "좀 피곤하네", "로그 확인 좀",
    ]
    s2 = CaptureSession()
    cap_hit = ign_hit = 0
    mis = []
    for u in capture_expect:
        st = s2.on_user_prompt(u)["verdict"]["state"]
        if st == "captured_candidate":
            cap_hit += 1
        else:
            mis.append(("누락", u, st))
    base = s2.size
    for u in ignore_expect:
        st = s2.on_user_prompt(u)["verdict"]["state"]
        if st == "ignored":
            ign_hit += 1
        else:
            mis.append(("오탐", u, st))
    print(f"    캡처 적중 {cap_hit}/10, 제외 적중 {ign_hit}/10, 버퍼 누적={s2.size}")
    for kind, u, st in mis:
        print(f"    - [{kind}] {u!r} → {st}")
    check(cap_hit >= 9 and ign_hit >= 9, "T7 실발화 경계: 캡처/제외 각 9/10 이상")

    print(f"\nGATE={'GO' if ok else 'NO-GO'}")
    return ok


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
