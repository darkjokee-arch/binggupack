"""Characterization selftest — owner 발화 a0 형식게이트 면제 (commit 7d48b39).

_pick_one_node 의 a0 FAIL 면제가:
  ① speaker=="owner" 이고 a0 guard ∈ {node_1_word, node_1_meaning}(형식 게이트)일 때만 발동(node 반환)
  ② speaker!="owner"(ai/reader/None)면 동일 입력에서도 a0_fail (owner-scoped, 누설 없음)
  ③ 정상 완결 문장은 면제와 무관하게 owner/ai 모두 통과(면제가 정상 경로를 바꾸지 않음)
임을 고정한다.

안전 게이트 무영향 근거(이 테스트가 다루지 않는 부분):
  - PII/secret 거부(_pick_one_node line 206-208)는 면제 뒤에 무조건 실행되며 미변경 →
    PII/secret 회귀는 기존 openbinggu_conversation_candidate_save.py --selftest(19/19)가 커버.
  - G4_no_auto / actor / confirm 게이트는 save_selected 호출부(미변경) 책임.
  - 본 파일은 PII-shape literal 을 포함하지 않는다(트리거 입력은 전부 비-PII 구어체).
"""
import os
import sys
import tempfile

os.environ.setdefault("BINGGU_HOME", tempfile.mkdtemp(prefix="binggu_owner_exempt_test_"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from openbinggu_conversation_candidate_save import _pick_one_node  # noqa: E402

# a0 node_1_word/node_1_meaning FAIL 을 유발하는 비종결·구어체 입력(실증 발견, PII 아님).
FORM_FAIL = ["그 입찰 관련 어떤", "이거 좀 그런데"]
# a0 PASS 되는 정상 완결 문장(면제와 무관 경로).
NORMAL = "이 입찰은 마진이 낮아 보류한다."


def _selftest():
    results = []

    def rec(n, desc, ok):
        results.append(bool(ok))
        print("  [%s] %d %s" % ("OK" if ok else "FAIL", n, desc))

    n = 0
    for p in FORM_FAIL:
        n += 1
        r_owner = _pick_one_node(p, 1, "owner")
        rec(n, "owner + a0형식FAIL(%r) → 면제 node" % p,
            isinstance(r_owner, dict) and r_owner.get("speaker") == "owner")

    for p in FORM_FAIL:
        n += 1
        r_ai = _pick_one_node(p, 1, "ai")
        rec(n, "ai + 동일입력(%r) → a0_fail (owner-scoped)" % p, r_ai == "a0_fail")

    for p in FORM_FAIL:
        n += 1
        r_reader = _pick_one_node(p, 1, "reader")
        r_none = _pick_one_node(p, 1, None)
        rec(n, "reader/None + 동일입력(%r) → a0_fail (면제 누설 없음)" % p,
            r_reader == "a0_fail" and r_none == "a0_fail")

    # 정상 완결 문장: 면제와 무관하게 owner/ai 모두 통과(면제가 정상 경로를 바꾸지 않음)
    n += 1
    r_own_norm = _pick_one_node(NORMAL, 1, "owner")
    r_ai_norm = _pick_one_node(NORMAL, 1, "ai")
    rec(n, "정상문장 → owner/ai 모두 node (면제 무관)",
        isinstance(r_own_norm, dict) and isinstance(r_ai_norm, dict))

    ok = all(results)
    print("\nRESULT: %d/%d %s" % (sum(results), len(results), "PASS" if ok else "FAIL"))
    print("GATE:", "GO" if ok else "BLOCK")
    return ok


if __name__ == "__main__":
    sys.exit(0 if _selftest() else 1)
