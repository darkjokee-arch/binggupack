"""빙구팩 자동 캡처 판정기 — 순수 함수만.

설계: BINGGUPACK_USER_ONTOLOGY_EVENT_SCHEMA_DESIGN.md §4
- write 0 / ledger import 0 / OpenCrab import 0 / 파일 저장·변환·승격 0
- 입력: 발화 텍스트 (+직전 1턴 optional, 무상태 1턴만 참조)
- 출력: 판정 dict {state, confidence, pinned, reasons, signals, vetoes}
- 캡처 트리거 != preview 트리거. evidence는 캡처 게이트 아님(여기서 안 봄).
"""

import re

# preview 트리거 (저장 아님 — 후보 리스트 여는 동작)
PREVIEW_TRIGGER = [r"빙구팩\s*저장", r"빙구팩\s*열", r"후보\s*(리스트|목록)\s*(보여|열)"]

# 명시 저장 요청 (= pinned candidate, veto 예외, 즉시 preview 아님)
EXPLICIT_SAVE = [r"이거\s*저장", r"이건\s*저장", r"이거\s*기억", r"기억해\s*둬", r"방금\s*(이|그)\s*판단", r"이거\s*박제"]

# 판단 신호 (유형: 패턴)
SIGNAL_PATTERNS = {
    "방향결정": [r"(으로|로)\s*(가자|간다|결정|하자)", r"결정했", r"이걸로", r"단일\s*정본"],
    "선택판단": [r"더\s*맞", r"이게\s*맞", r"(가|이)\s*낫", r"더\s*나[은아을]", r"이게\s*더"],
    "선긋기금지": [r"안\s*[돼되]", r"[가-힣]지\s*마(라|요)?(?:\s|$|[.,])", r"금지", r"절대", r"나누지\s*마"],
    "리스크감지": [r"위험", r"터질\s*수", r"문제될", r"조심", r"깨질\s*수", r"깨져"],
    "AI교정": [r"그게\s*아니라", r"왜\s*그렇게\s*생각", r"이\s*방향이\s*맞", r"^아니[,\s]"],
    "선호스타일": [r"선호", r"이\s*스타일로", r"이렇게\s*(계속|항상)\s*해"],
    "반복기준": [r"항상", r"무조건", r"매번", r"늘\s", r"언제나"],
    "장기의도": [r"나중에.*(팔|만들|할)", r"목표는", r"로드맵", r"팔\s*거야", r"유료"],
    "우선순위": [r"우선\s*(처리|순위)", r"먼저\s*해", r"먼저\s*처리"],
}

# 추측 마커 (단독이면 ignored, 판단신호 동반이면 weak)
HEDGE = [r"아마", r"모르겠지만", r"일\s*수[도도]", r"인\s*것\s*같", r"겠지\b", r"~?일지도"]

# veto (농담/감탄/단순질문/단순조회/임시감정/자문자답)
VETO_PATTERNS = {
    "농담": [r"ㅋㅋ", r"ㅎㅎ", r"농담"],
    "감탄": [r"^(와|헐|대박|오+|우와)[\s!.]*$", r"^(와|헐|대박)\b"],
    "단순조회": [r"보여\s*줘", r"확인해\s*줘", r"상태\s*(는|보)", r"뭐야\??$", r"어디(야|에)\??$", r"로그\s*(확인|봐)"],
    "임시감정": [r"피곤", r"짜증", r"힘들다", r"지친다"],
    "인사": [r"고마워", r"수고", r"고맙"],
}

# 운영 명령/보고/메타 노이즈 (should_capture hard veto) — Claude에게 주는 작업 지시·진행 확인·운영 보고.
# 사용자의 일반 판단/규칙/사실이 아니므로 후보 풀에서 제외. cos 단독 금지 → 명시 규칙으로 거른다.
# traj_20260614 should_capture 교훈 흡수: cos band 로는 못 가르던 지시문/잡담을 규칙 신호로 분리.
OPS_VERBS = (r"(커밋|commit|푸시|push|풀\s*리퀘|pull|머지|merge|배포|deploy|롤백|rollback|"
             r"재시작|restart|재배포|리부트|빌드|build|설치|install|실행|구동|기동|테스트|test|"
             r"돌려|적용|착수|반영|동기화|sync|업로드|업데이트|패치|디버그|스캔)")
OPS_IMPERATIVE = [OPS_VERBS + r".{0,6}(해라|하라|하세요|해\s*줘|해\s*봐|하자|진행|시작|할까요|할까|돌려|하지\s*마)"]
OPS_REPORT = [OPS_VERBS + r".{0,8}(완료|했[다어]\b|함\b|끝났|성공|실패|마쳤|마무리)"]
META_CONFIRM = [r"할까요\s*\??$", r"할까\s*\??$", r"괜찮(아|을까|나)\s*\??$",
                r"확인했[어나]\s*\??$", r"맞(나|아|지)\s*\??$", r"될까\s*\??$", r"해도\s*(돼|될까|되나)\s*\??$"]
# 반복기준(영구 규칙) 동반 시 운영 veto 면제 — "배포는 항상 두 번 확인해라" 같은 규칙은 후보로 통과.
GENERALIZE_EXEMPT = [r"항상", r"무조건", r"매번", r"늘\s", r"언제나"]


def _hits(text, patterns):
    return [name for name, pats in patterns.items() if any(re.search(p, text) for p in pats)]


def _any(text, pats):
    return [p for p in pats if re.search(p, text)]


def classify(utterance, prev_turn=None):
    """발화 1건 판정. prev_turn은 직전 1턴(optional, 모호질문 보조용)."""
    text = (utterance or "").strip()
    reasons, signals, vetoes = [], [], []

    out = {"state": "ignored", "confidence": "normal", "pinned": False,
           "reasons": reasons, "signals": signals, "vetoes": vetoes}

    if not text:
        reasons.append("empty")
        return out

    # 1) preview 트리거 우선 (저장 아님, 리스트 여는 동작)
    if _any(text, PREVIEW_TRIGGER):
        out["state"] = "preview_trigger"
        reasons.append("preview_trigger")
        return out

    # 2) 명시 저장 요청 = pinned candidate (veto 예외)
    if _any(text, EXPLICIT_SAVE):
        out["state"] = "captured_candidate"
        out["pinned"] = True
        reasons.append("explicit_save")
        return out

    # 2.5) 운영 명령/보고/메타 = should_capture hard veto (signal 보다 우선).
    #      명령형 종결("…해라/하지마/돌려봐"), 운영 보고("…완료/했다"), 진행 확인("…할까요?")을
    #      명시 규칙으로 거른다. 반복기준 동반(항상/무조건…)이면 영구 규칙으로 보고 면제.
    generalized = bool(_any(text, GENERALIZE_EXEMPT))
    if _any(text, META_CONFIRM):
        vetoes.append("meta_confirm")
        reasons.append("ops/meta noise veto")
        return out
    if not generalized and (_any(text, OPS_IMPERATIVE) or _any(text, OPS_REPORT)):
        vetoes.append("ops_imperative" if _any(text, OPS_IMPERATIVE) else "ops_report")
        reasons.append("ops noise veto")
        return out

    # 3) 판단 신호 / 추측마커 / veto 검출
    signals.extend(_hits(text, SIGNAL_PATTERNS))
    hedged = bool(_any(text, HEDGE))
    vetoes.extend(_hits(text, VETO_PATTERNS))
    # 단순 질문: ?로 끝 + 판단신호 0 (단, 직전이 AI 제안이면 약한 교정 보류 — 무상태 1턴)
    if text.endswith("?") and not signals:
        if prev_turn and re.search(r"(제안|추천|할까요|어떨까|봐야)", prev_turn):
            signals.append("약한교정(맥락1턴)")
        else:
            vetoes.append("단순질문")

    # 4) 종합
    if signals:
        out["state"] = "captured_candidate"
        if hedged:
            out["confidence"] = "weak"
            reasons.append("signal+hedge→weak")
        else:
            reasons.append("signal")
        return out

    # 판단신호 없음 → ignored
    if hedged:
        reasons.append("hedge_only")
    if vetoes:
        reasons.append("veto:" + ",".join(vetoes))
    if not reasons:
        reasons.append("no_signal")
    return out


# ---------------- 셀프테스트 (write 0) ----------------
def _selftest():
    cases = [
        ("이거 저장해", None, dict(state="captured_candidate", pinned=True), "preview_trigger 아님"),
        ("빙구팩 저장해", None, dict(state="preview_trigger"), None),
        ("B안으로 결정", None, dict(state="captured_candidate", confidence="normal"), "evidence 없는 정상 판단"),
        ("아마 이게 더 맞을 거야, 캐시 때문에", None, dict(state="captured_candidate", confidence="weak"), "추측+판단"),
        ("아마 되겠지", None, dict(state="ignored"), "추측 단독"),
        ("ㅋㅋㅋ 그거 웃기네", None, dict(state="ignored"), "농담"),
        ("와 대박", None, dict(state="ignored"), "감탄"),
        ("상태 보여줘", None, dict(state="ignored"), "단순조회"),
        ("테스트 돌려봐", None, dict(state="ignored"), "단순 작업지시"),
        ("테스트는 항상 돌려", None, dict(state="captured_candidate"), "반복기준"),
        # --- should_capture 노이즈 차단 (traj_20260614 should_capture 해결) ---
        ("commit 진행해라", None, dict(state="ignored"), "운영 명령 지시"),
        ("push 하지마", None, dict(state="ignored"), "운영 금지 지시(선긋기 signal 보다 ops veto 우선)"),
        ("착수할까요?", None, dict(state="ignored"), "진행 확인 메타"),
        ("배포해줘", None, dict(state="ignored"), "운영 명령"),
        ("커밋 완료했다", None, dict(state="ignored"), "운영 보고(1회성 사실)"),
        ("확인했어?", None, dict(state="ignored"), "메타 확인질문"),
        ("재시작해", None, dict(state="ignored"), "운영 명령 단독"),
        # --- 실제 캡처해야 하는 규칙/판단/사실은 통과 ---
        ("배포는 항상 두 번 확인해라", None, dict(state="captured_candidate"), "일반화 규칙(항상)=ops veto 면제"),
        ("백업은 항상 먼저 해 둔다", None, dict(state="captured_candidate"), "반복기준 규칙"),
        ("이 방식은 위험이 커서 기본 비활성으로 잠근다", None, dict(state="captured_candidate"), "리스크 판단/설계결정"),
    ]
    passed = 0
    for i, (utt, prev, expect, note) in enumerate(cases, 1):
        r = classify(utt, prev)
        ok = all(r.get(k) == v for k, v in expect.items())
        # 추가 검증: #1은 preview_trigger 아님 명시
        if i == 1 and r["state"] == "preview_trigger":
            ok = False
        mark = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        print(f"  [{mark}] T{i}: {utt!r} → state={r['state']} conf={r['confidence']} pinned={r['pinned']}"
              + ("" if ok else f"  EXPECT={expect}")
              + (f"  signals={r['signals']} reasons={r['reasons']}" if not ok else ""))
    gate = "GO" if passed == len(cases) else "NO-GO"
    print(f"\nGATE={gate}  {passed}/{len(cases)}")
    return passed == len(cases)


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
