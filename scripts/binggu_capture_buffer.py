"""빙구팩 캡처 버퍼 + batch preview 렌더 — 메모리 내만.

설계: BINGGUPACK_USER_ONTOLOGY_EVENT_SCHEMA_DESIGN.md §4
- 메모리 buffer만 / ledger write 0 / 파일 저장 0 / OpenCrab 0
- active 저장 0 / owner approval 처리 0 / candidate→confirmed 전이 0
- preview_trigger 또는 세션말 시 후보 리스트 렌더(버퍼 삭제 안 함)
"""

from binggu_capture_classifier import classify


class CaptureBuffer:
    """메모리 내 candidate 누적 버퍼. 영속화 일절 없음."""

    def __init__(self):
        self._candidates = []  # 메모리 리스트만

    def feed(self, utterance, prev_turn=None):
        """발화 1건 처리. captured_candidate면 누적, preview_trigger면 렌더 반환.
        반환: {"action": "captured"|"preview"|"ignored", "verdict": <classify dict>, "preview": <list|None>}"""
        v = classify(utterance, prev_turn)
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
        return {"count": len(lines), "items": lines, "note": "owner 승인 전 candidate (active 아님)"}

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

    print("\n  preview 렌더 결과:")
    for it in pv["items"]:
        print("   ", it["label"])

    gate = "GO" if ok else "NO-GO"
    print(f"\nGATE={gate}")
    return ok


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
