"""빙구팩 자동 캡처 판정기 — 순수 함수만 (v1.11.0 strangler phase4 이관).

scripts/binggu_capture_classifier.py 에서 이 모듈로 핵심 로직을 이관했다. scripts 파일은
backward-compatible thin wrapper(sys.path bootstrap + 전체 심볼 re-export + __main__ _selftest)
로 유지되며 공개 심볼/동작/분류 결과는 byte-identical 하다(기능 변경 0).

설계: BINGGUPACK_USER_ONTOLOGY_EVENT_SCHEMA_DESIGN.md §4
- write 0 / ledger import 0 / OpenCrab import 0 / 파일 저장·변환·승격 0
- 입력: 발화 텍스트 (+직전 1턴 optional, 무상태 1턴만 참조)
- 출력: 판정 dict {state, confidence, pinned, reasons, signals, vetoes}
- 캡처 트리거 != preview 트리거. evidence는 캡처 게이트 아님(여기서 안 봄).
- 이 모듈은 regex 정본(should_capture 게이트). LLM(advisory)은 이를 override 못 함.
"""

import re

# preview 트리거 (저장 아님 — 후보 리스트 여는 동작)
PREVIEW_TRIGGER = [r"빙구팩\s*저장", r"빙구팩\s*열", r"후보\s*(리스트|목록)\s*(보여|열)"]

# 명시 저장 요청 (= pinned candidate, veto 예외, 즉시 preview 아님)
EXPLICIT_SAVE = [r"이거\s*저장", r"이건\s*저장", r"이거\s*기억", r"기억해\s*둬", r"방금\s*(이|그)\s*판단", r"이거\s*박제"]

# 판단 신호 (유형: 패턴)
SIGNAL_PATTERNS = {
    "방향결정": [r"(으로|로)\s*(가자|간다|결정|하자)", r"결정했", r"이걸로", r"단일\s*정본",
                 r"기로\s*(했|한다|정했|결정|하기로)"],
    "선택판단": [r"더\s*맞", r"이게\s*맞", r"(가|이)\s*낫", r"더\s*나[은아을]", r"이게\s*더",
                 r"낫다", r"더\s*중요"],
    "선긋기금지": [r"안\s*[돼되]", r"[가-힣]지\s*마(라|요)?(?:\s|$|[.,])", r"금지", r"절대", r"나누지\s*마"],
    "리스크감지": [r"위험", r"터질\s*수", r"문제될", r"조심", r"깨질\s*수", r"깨져"],
    "AI교정": [r"그게\s*아니라", r"왜\s*그렇게\s*생각", r"이\s*방향이\s*맞", r"^아니[,\s]"],
    # 반문/why 신호 (설계 §9 Phase3·§5 L6 "사용자 의문 발화 자체가 why 신호"): owner 가 직전
    #   AI 말/판단에 던지는 반문 → 대화쌍(why-question) 후보. 질문형이라 단순질문 veto 보다 우선
    #   (signal 검출 시 captured). 좁게 유지(오탐 회피 · 순수 조회 "뭐야/어디"는 veto 로 분리).
    #   ★ "이게 맞나?"류는 META_CONFIRM(진행확인) 우선 veto 라 현재 미포착 — 직전 AI말 유무로
    #     반문 vs 진행확인을 구분하는 prev_turn 정밀조정은 후속(실로그 보고 후 · 노이즈 회피 좁게 시작).
    # 2026-08-01 실측 확장: 운영 장부의 owner 물음표 발화 40건에서 실제 어미를 뽑아 넓혔다
    #   (빈도순 — 아님 5 · 아냐 2 · 않아 2 · 되는거아냐 2 · 않나 1). 종전 "왜"류 6개로는
    #   "매칭이 안되는거 아니야?" · "db에 저장된건 그대로 해야하지않나?" 를 놓쳐 맥락이 안 붙었다.
    #   물음표로 끝나는 부정형 반문만 잡는다(평서문 "안 된다"는 선긋기금지 쪽이라 건드리지 않는다).
    "반문": [r"이상한데", r"무슨\s*근거", r"왜\s*이렇게", r"왜\s*이\s*방식", r"여긴\s*왜", r"왜\s*그렇게\s*해",
             r"아니(야|냐|가)\s*\?", r"아님\s*\?", r"않나\s*\?", r"않아\s*\?", r"않을까\s*\?",
             r"하지\s*않(나|아)\s*\?", r"거\s*아(냐|니야)\s*\?"],
    "선호스타일": [r"선호", r"이\s*스타일로", r"이렇게\s*(계속|항상)\s*해"],
    "반복기준": [r"항상", r"무조건", r"매번", r"(?:^|\s)늘\s", r"언제나"],
    "장기의도": [r"나중에.*(팔|만들|할)", r"목표는", r"로드맵", r"팔\s*거야", r"유료"],
    "우선순위": [r"우선\s*(처리|순위)", r"먼저\s*해", r"먼저\s*처리"],
    # 교훈규범(recall 보강): 학습/재발방지/선제습관 마커가 명확한 순수 교훈문.
    # veto(2.5)가 앞이므로 운영 지시는 여기 닿기 전 차단됨 → 일반 규범문만 흡수.
    # "해야 한다" 단독은 일회성 지시("지금 이거 해야 한다")까지 잡아 오탐 → 제거.
    # 선제습관은 "먼저/미리 + 동사 + 규범어미" 결합으로만(평서 습관문에 한정).
    "교훈규범": [r"다음\s*부터", r"이제\s*부터", r"앞으로[는]?\s",
                 r"재발\s*방지", r"놓치지\s*않", r"잊지\s*않", r"또\s*안\s*밟", r"안\s*밟", r"또\s*밟",
                 r"먼저\s*[가-힣을를\s]{0,8}(본다|봐야|확인|점검|챙긴다|챙긴|손댄다|손대|해야|둔다|들인다)",
                 r"미리\s*[가-힣]{0,6}(본다|봐야|확인|점검|챙긴다|해\s*둔다|둔다)"],
    # owner 원칙/단정 경계 표명 (2026-07-23): "확정하는건 없다·확정지을 수도 없다" 류 메타 판단.
    #   classify 가 no_signal 로 배제해 owner 원칙이 버퍼에 안 담기던 결함 수정(owner 반복 지적).
    #   좁게(확정/단정 + 없) — "확정 못했다"(완료 보고)는 '못'이라 미포착(오탐 회피). 실로그 보정.
    "원칙단정": [r"확정.{0,12}없", r"단정.{0,10}(않|없|마|말|못)"],
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
# ★C(2026-07-21): META_CONFIRM 중 확언성 확인 어미("맞나/맞아/맞지?")는 직전 AI 판단에 대한
#   owner 반문(dialectic)일 수 있다 — prev_turn 이 AI 제안/판단이면 veto 해제(약한교정 signal).
#   "할까요/괜찮나/될까/확인했어?"류(순수 진행확인)는 반문 대상 아님 → 그대로 veto.
META_CONFIRM_REBUTTABLE = [r"맞(나|아|지)\s*\??$"]
PREV_AI_STANCE = r"(제안|추천|할까요|어떨까|봐야|판단|하자|낫[다겠까]|것\s*같|권장|추정|하는\s*게)"
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
        # ★C(2026-07-21 prev_turn 정밀조정): "이게 맞나/맞아/맞지?"류 확언성 어미는 직전 AI
        #   판단·제안에 대한 owner 반문(dialectic)일 수 있다 → prev_turn 이 AI 제안/판단이면 veto
        #   해제하고 약한교정(맥락1턴) signal 로 포착(설계 §9 Phase3·§5 L6·dialectic ai_context).
        #   "할까요/괜찮나/될까/확인했어?"류·prev_turn 없음/무관은 그대로 veto. 좁게(노이즈 회피):
        #   반문 가능 어미 + prev_turn AI판단, 둘 다 성립할 때만 해제.
        if _any(text, META_CONFIRM_REBUTTABLE) and prev_turn and re.search(PREV_AI_STANCE, prev_turn):
            signals.append("약한교정(맥락1턴)")
            reasons.append("meta_confirm→반문(prev_turn AI판단)")
        else:
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
        if prev_turn and re.search(PREV_AI_STANCE, prev_turn):
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
        # --- recall 보강: 순수 교훈문 통과 (교훈규범 signal) ---
        ("비슷한 실수는 기록해야 안 밟는다", None, dict(state="captured_candidate"), "재발방지 교훈(안 밟)"),
        ("이런 경우는 먼저 확인해야 한다", None, dict(state="captured_candidate"), "선제확인 습관"),
        ("다음부터는 로그를 먼저 본다", None, dict(state="captured_candidate"), "시점학습(다음부터)"),
        ("재발 방지를 위해 체크리스트를 둔다", None, dict(state="captured_candidate"), "재발방지"),
        ("앞으로는 마감 하루 전에 손댄다", None, dict(state="captured_candidate"), "선제습관(앞으로)"),
        # --- recall 오탐 측정: 일회성 지시/상황은 여전히 차단 (해야 한다 단독 오탐 제거 검증) ---
        ("지금 이거 해야 한다", None, dict(state="ignored"), "일회성 지시(해야 한다 단독=오탐 제거됨)"),
        ("이 버그 지금 고쳐야 한다", None, dict(state="ignored"), "일회성 지시"),
        ("지금 서버가 죽었다", None, dict(state="ignored"), "일회성 상황 설명"),
        ("방금 빌드가 깨졌다", None, dict(state="ignored"), "일회성 상황 설명"),
        # --- regex 보정 (일치성 하네스 오탐/누락 회귀 방지) ---
        ("오늘 도와줘서 고마워", None, dict(state="ignored"), "인사('오늘'의 늘 오매치 제거 → 반복기준 오탐 X)"),
        ("다들 오늘 고생했어", None, dict(state="ignored"), "인사('오늘' 오매치 제거)"),
        ("이 방식으로 정리하기로 했다", None, dict(state="captured_candidate"), "방향결정(기로 했다)"),
        ("그 거래처는 우선 검토하기로 한다", None, dict(state="captured_candidate"), "방향결정(기로 한다)"),
        ("이 방법이 더 안전해서 낫다", None, dict(state="captured_candidate"), "선택판단(낫다)"),
        ("속도보다 안정성이 더 중요하다", None, dict(state="captured_candidate"), "선택판단(더 중요)"),
        ("이런 경우는 먼저 로그를 확인해야 한다", None, dict(state="captured_candidate"), "교훈규범(먼저 …를 확인)"),
        ("늘 백업을 먼저 잡는다", None, dict(state="captured_candidate"), "반복기준(독립 '늘 ')"),
        # --- ★C prev_turn 정밀조정: "맞나?"류 = 직전 AI판단이면 반문(dialectic), 없으면 진행확인 veto ---
        ("이게 맞나?", "B안을 추천합니다", dict(state="captured_candidate"), "반문(prev AI제안)→약한교정"),
        ("이게 맞나?", None, dict(state="ignored"), "진행확인(prev 없음)→veto 유지"),
        ("이게 맞나?", "상태를 보여드릴게요", dict(state="ignored"), "prev AI판단 무관→veto 유지"),
        ("할까요?", "B안을 추천합니다", dict(state="ignored"), "순수 진행확인(할까요)=prev 있어도 veto"),
        ("확인했어?", "B안을 추천합니다", dict(state="ignored"), "확인질문=rebuttable 아님 veto"),
        # --- ★원칙단정 (2026-07-23 owner 원칙 미포착 결함 수정) ---
        ("확정하는건 없다. 그리고 상한이라고 확정지을 수도 없다", None, dict(state="captured_candidate"), "원칙단정(확정…없)"),
        ("단정할 수 없다", None, dict(state="captured_candidate"), "원칙단정(단정…없)"),
        ("아직 확정 못했다", None, dict(state="ignored"), "일회성 보고(확정 못=원칙단정 오탐 아님)"),
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
