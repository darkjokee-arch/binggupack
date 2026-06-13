"""빙구팩 캡처 수동 호출 경로 (hook 없이) — stateless 배치.

설계: BINGGUPACK_CAPTURE_MCP_EXPOSURE_DESIGN.md §3
- 발화 리스트를 받아 CaptureSession에 순차 feed → preview 반환.
- 메모리만 / ledger write 0 / 파일 저장 0 / OpenCrab 0 / active·approval 0.
- MCP capture_preview(utterances[]) 무상태 노출의 로컬 검증판(동일 엔진).

사용(수동):
  echo 발화들 | python binggu_capture_cli.py        # stdin 줄단위
  python binggu_capture_cli.py --feed "B안으로 결정" --feed "이거 저장해"
  → preview JSON(stdout). "빙구팩 저장해" 만나면 그 시점 preview, 아니면 끝에서 preview.
"""

import json
from binggu_capture_session import CaptureSession


def run_batch(utterances):
    """발화 리스트를 무상태로 처리 → preview dict 반환. write 0."""
    s = CaptureSession()
    triggered = None
    for u in utterances:
        r = s.on_user_prompt(u)
        if r["action"] == "preview":          # "빙구팩 저장해" 시점
            triggered = r["preview"]
    # 명시 트리거 없었으면 세션말 preview
    preview = triggered if triggered is not None else s.on_session_end()["preview"]
    return {
        "trigger": "explicit" if triggered is not None else "session_end",
        "buffer_size": s.size,
        "preview": preview,
        "note": "candidate (owner 승인 전, active 아님). write 0.",
    }


def _read_args(argv):
    utts = []
    i = 0
    while i < len(argv):
        if argv[i] == "--feed" and i + 1 < len(argv):
            utts.append(argv[i + 1])
            i += 2
        else:
            i += 1
    return utts


# ---------------- 셀프테스트 (write 0) ----------------
def _selftest():
    ok = True

    def check(c, m):
        nonlocal ok
        ok = ok and c
        print(f"  [{'PASS' if c else 'FAIL'}] {m}")

    # 명시 트리거 경로
    out = run_batch(["B안으로 결정", "이거 저장해", "빙구팩 저장해", "이건 무시될 농담 ㅋㅋ"])
    check(out["trigger"] == "explicit", "T1 '빙구팩 저장해' → explicit 트리거")
    check(out["preview"]["count"] == 2, "T2 트리거 시점 후보 2개(결정+pinned)")
    check(out["preview"]["items"][0]["pinned"], "T3 pinned 맨 위")
    check(all(it["state"] == "captured_candidate" for it in out["preview"]["items"]), "T4 active 전이 0")

    # 세션말 경로(명시 트리거 없음)
    out2 = run_batch(["로컬로 가자", "아마 B가 더 나을 거야, 비용 때문에", "ㅋㅋ"])
    check(out2["trigger"] == "session_end", "T5 트리거 없으면 session_end")
    check(out2["preview"]["count"] == 2, "T6 세션말 후보 2개(결정+weak)")
    check(any(it["confidence"] == "weak" for it in out2["preview"]["items"]), "T7 weak 표시")
    check(out2["buffer_size"] == 2, "T8 ignored(ㅋㅋ) 누적 안 됨")

    print(f"\nGATE={'GO' if ok else 'NO-GO'}")
    return ok


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    # 수동 호출: --feed 인자 우선, 없으면 stdin 줄단위
    utts = _read_args(sys.argv[1:])
    if not utts and not sys.stdin.isatty():
        utts = [ln.strip() for ln in sys.stdin if ln.strip()]
    print(json.dumps(run_batch(utts), ensure_ascii=False, indent=2))
