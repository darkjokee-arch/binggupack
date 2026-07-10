"""빙구팩 캡처 버퍼 + batch preview 렌더 — 메모리 내만 (v1.11.0 strangler phase5 이관).

scripts/binggu_capture_buffer.py 에서 이 모듈로 이관했다. scripts 파일은 backward-compatible
thin wrapper(sys.path bootstrap + 전체 심볼 re-export + __main__ _selftest)로 유지되며
공개 심볼(CaptureBuffer)/동작은 byte-identical 하다(기능 변경 0).

classify import 는 binggupack 정본(binggupack.classifier.classify) 경유로 고정한다
(scripts 역참조 안 함 — strangler 단방향 원칙). classify 함수 자체는 phase4 에서 이관된
binggupack.classifier.capture_classifier.classify 와 동일 객체다.

설계: BINGGUPACK_USER_ONTOLOGY_EVENT_SCHEMA_DESIGN.md §4
- 메모리 buffer만 / ledger write 0 / 파일 저장 0 / OpenCrab 0
- active 저장 0 / owner approval 처리 0 / candidate→confirmed 전이 0
- preview_trigger 또는 세션말 시 후보 리스트 렌더(버퍼 삭제 안 함)
"""

from binggupack.classifier import classify

# 시스템 주입 텍스트 마커 — task-notification·hook feedback·command·reminder 등은 사용자 판단이
# 아니라 하네스가 주입한 텍스트다. capture 오염(preview 에 시스템 메시지 원문이 candidate 로 뜸)
# 차단을 위해 하나라도 포함되면 capture skip(2026-07-10).
_SYSTEM_NOISE_MARKERS = (
    "<task-notification>", "</task-notification>", "<task-id>", "<tool-use-id>",
    "<output-file>", "<command-message>", "<command-name>", "<local-command",
    "<system-reminder>", "</system-reminder>", "hook feedback", "PreToolUse:",
    "hook additional context", "Stop hook", "<result>\n<name>", "task-notification>",
)

# 대화 덩어리/붙여넣기/AI 응답문 veto 임계 — binggu_capture_persist 와 동일 정본 값(2026-07-10 Fable5 2각 수렴).
#   실측: owner 판단 med 91자·줄바꿈 0 / noise+AI응답 med 1000자·줄바꿈 17. 길이+줄바꿈 게이트로 분리.
BULK_SOFT_LEN = 300
BULK_NL_MIN = 3
BULK_HARD_LEN = 2000


def _is_system_noise(utterance):
    """시스템 주입 텍스트(알림·hook·command·reminder)인지 — 사용자 판단 아님(capture 오염 차단)."""
    if not utterance or not str(utterance).strip():
        return True
    u = str(utterance)
    return any(m in u for m in _SYSTEM_NOISE_MARKERS)


def _is_bulk_text(utterance):
    """대화 덩어리/붙여넣기/AI 응답문 판정 — 길이 + 줄바꿈 밀도(persist 와 동일 로직).
    C(문장 발췌)는 AI 응답문을 owner 판단으로 둔갑시키는 화자축 오염이라 기각 → 덩어리는 veto(안 담음).
    명시저장은 feed 에서 우회(owner 의도)."""
    t = str(utterance or "")
    n = len(t)
    return n > BULK_HARD_LEN or (n > BULK_SOFT_LEN and t.count("\n") >= BULK_NL_MIN)


class CaptureBuffer:
    """메모리 내 candidate 누적 버퍼. 영속화 일절 없음."""

    def __init__(self):
        self._candidates = []  # 메모리 리스트만
        self._bulk_vetoed = 0  # 대화 덩어리 veto 카운트(무음 폐기 방지 — preview 노출)

    def feed(self, utterance, prev_turn=None):
        """발화 1건 처리. captured_candidate면 누적, preview_trigger면 렌더 반환.
        반환: {"action": "captured"|"preview"|"ignored"|"system_noise"|"bulk_veto", "verdict": <classify dict>, "preview": <list|None>}"""
        if _is_system_noise(utterance):
            return {"action": "system_noise", "verdict": {"state": "system_noise"}, "preview": None}
        v = classify(utterance, prev_turn)
        # 명시(preview_trigger / explicit pinned)는 bulk veto 우회 — owner 의도. classify 정본으로 판정(중복 정의 회피).
        explicit = v["state"] == "preview_trigger" or (v["state"] == "captured_candidate" and v["pinned"])
        if not explicit and _is_bulk_text(utterance):
            self._bulk_vetoed += 1
            return {"action": "bulk_veto", "verdict": {"state": "bulk_veto"}, "preview": None}
        if v["state"] == "preview_trigger":
            return {"action": "preview", "verdict": v, "preview": self.render_preview()}
        if v["state"] == "captured_candidate":
            self._candidates.append({
                "text": utterance.strip(),
                "pinned": v["pinned"],
                "confidence": v["confidence"],
                "signals": list(v["signals"]),
                "state": "captured_candidate",  # active/confirmed 절대 아님
            })
            return {"action": "captured", "verdict": v, "preview": None}
        return {"action": "ignored", "verdict": v, "preview": None}

    def _ordered(self):
        # pinned 상단 → 그 외. 안정 정렬(입력 순 보존)
        return sorted(self._candidates, key=lambda c: 0 if c["pinned"] else 1)

    def render_preview(self):
        """현재 buffer를 preview 리스트로 렌더. 버퍼 삭제 안 함. write 0."""
        lines = []
        for i, c in enumerate(self._ordered(), 1):
            tags = []
            if c["pinned"]:
                tags.append("PINNED")
            if c["confidence"] == "weak":
                tags.append("약함")
            tag = f" [{' · '.join(tags)}]" if tags else ""
            lines.append({
                "idx": i,
                "text": c["text"],
                "pinned": c["pinned"],
                "confidence": c["confidence"],
                "label": f"{i}. {c['text']}{tag}",
                "state": c["state"],  # 항상 captured_candidate
            })
        return {"count": len(lines), "items": lines, "bulk_vetoed": self._bulk_vetoed,
                "note": "owner 승인 전 candidate (active 아님)"}

    @property
    def size(self):
        return len(self._candidates)


# ---------------- 셀프테스트 (메모리만, write 0) ----------------
def _selftest():
    buf = CaptureBuffer()
    ok = True

    def check(cond, msg):
        nonlocal ok
        mark = "PASS" if cond else "FAIL"
        if not cond:
            ok = False
        print(f"  [{mark}] {msg}")
        return cond

    # 3개 후보: pinned(명시), 일반(결정), weak(추측+판단)
    r1 = buf.feed("이거 저장해")
    check(r1["action"] == "captured" and r1["verdict"]["pinned"], "T1 명시저장 → captured + pinned")
    r2 = buf.feed("B안으로 결정")
    check(r2["action"] == "captured" and r2["verdict"]["confidence"] == "normal", "T2 결정 → captured normal")
    r3 = buf.feed("아마 이게 더 맞을 거야, 캐시 때문에")
    check(r3["action"] == "captured" and r3["verdict"]["confidence"] == "weak", "T3 추측+판단 → weak 누적")

    # ignored는 누적 안 됨
    buf.feed("ㅋㅋ 웃기네")
    check(buf.size == 3, "T4 ignored는 버퍼에 안 들어감 (size=3)")

    # preview 트리거
    r5 = buf.feed("빙구팩 저장해")
    pv = r5["preview"]
    check(r5["action"] == "preview", "T5 '빙구팩 저장해' → preview 액션")
    check(pv["count"] == 3, "T6 preview 리스트 3개")
    check(pv["items"][0]["pinned"], "T7 pinned가 맨 위")
    check(any(it["confidence"] == "weak" for it in pv["items"]), "T8 weak 표시 존재")
    check("약함" in [t for it in pv["items"] for t in [it["label"]] if "약함" in it["label"]] or
          any("약함" in it["label"] for it in pv["items"]), "T9 weak 라벨 '약함' 표기")

    # 렌더 후에도 버퍼 유지 + candidate 그대로
    check(buf.size == 3, "T10 render 후 버퍼 유지 (size=3)")
    check(all(it["state"] == "captured_candidate" for it in pv["items"]), "T11 candidate→active/confirmed 전이 0")

    # T12~T15 대화 덩어리/붙여넣기 veto (길이+줄바꿈) — persist 와 대칭
    r12 = buf.feed("이건 붙여넣기 " + ("가나다 결정한다 위험 항상\n" * 25))  # ~383자·줄바꿈 25
    check(r12["action"] == "bulk_veto" and buf.size == 3, "T12 긴 붙여넣기(>300자+줄바꿈3+) → bulk_veto·size 불변")
    r13 = buf.feed("이 방법이 더 낫다 " + ("가" * 400))  # ~409자·줄바꿈 0 < HARD
    check(r13["action"] == "captured" and buf.size == 4, "T13 긴 단일문(줄바꿈0) → bulk 아님·captured(T7b류 보존)")
    r14 = buf.feed("이거 저장해\n" + ("결정 위험\n" * 60))  # 명시저장 + 긴 덩어리
    check(r14["action"] == "captured" and buf.size == 5, "T14 명시저장+긴 덩어리 → explicit 우회 captured")
    pv2 = buf.render_preview()
    check(pv2.get("bulk_vetoed", 0) >= 1, "T15 preview bulk_vetoed 카운트 노출")

    print("\n  preview 렌더 결과:")
    for it in pv["items"]:
        print("   ", it["label"])

    gate = "GO" if ok else "NO-GO"
    print(f"\nGATE={gate}")
    return ok
