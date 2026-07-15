# -*- coding: utf-8 -*-
"""Memory PR 고정 KAT drift 검증 (v0.1-draft).

실제 binggupack 함수로 재계산해 vectors/kat/*.json 의 expected 와 대조한다. CI 에서
canonicalization 산식이 바뀌면(= spec 과 구현의 drift) 즉시 FAIL 한다.

경계(설계 원칙):
- 이 도구는 **고정 KAT 비교 + (선택) 기존 selftest 호출**만 담당한다. 상태기계를 재구현하지 않는다.
- illustrative-only vector 는 사람기원(SAVE n hook·approve TTY)·실서비스 의존이라 CI 재현 불가 →
  검증하지 않고 개수만 보고(UNSUPPORTED 정직 표기).

사용: python docs/memory-pr/tools/check_vectors.py
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))                 # docs/memory-pr/tools
VROOT = os.path.normpath(os.path.join(BASE, "..", "vectors"))
ROOT = os.path.normpath(os.path.join(BASE, "..", "..", ".."))     # repo root
for _p in (ROOT, os.path.join(ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _compute(func, inp):
    """KAT func 키 → 실제 구현 함수 재계산. impl 경로는 KAT json 의 'impl' 필드에 문서화."""
    from binggupack.safety import gate_log, trusted_approval as ta
    import openbinggu_conversation_candidate_save as cs
    import openbinggu_save_intent_outbox_runner as ob
    if func == "preview_ref":
        return gate_log.preview_ref_for_candidates(inp["candidates"])
    if func == "node_id":
        return "node:CONV:" + cs._sent_hash(inp["sentence"])
    if func == "canonical_payload_digest":
        return ta.canonical_payload_digest(inp["operation"], inp["payload"])
    if func == "compute_request_id":
        return ta.compute_request_id(inp["operation"], inp["payload_digest"], inp["ledger_identity"])
    if func == "intent_hash":
        return ob.intent_hash(inp["text"], inp["indices"], inp["confirm"])
    raise ValueError("unknown func: %s" % func)


def main():
    manifest = json.load(open(os.path.join(VROOT, "manifest.json"), encoding="utf-8"))
    fails, total = [], 0
    for kf in manifest.get("kat", []):
        data = json.load(open(os.path.join(VROOT, kf), encoding="utf-8"))
        for v in data.get("vectors", []):
            total += 1
            try:
                got = _compute(v["func"], v["input"])
            except Exception as e:  # noqa: BLE001 — 계산 실패도 drift 로 보고
                fails.append("%s: 계산 실패 (%s)" % (v["id"], e))
                continue
            if got != v["expected"]:
                fails.append("%s: expected=%s got=%s" % (v["id"], v["expected"], got))
    ill = len(manifest.get("illustrative", []))
    if fails:
        print("KAT DRIFT: FAIL (%d/%d 불일치)" % (len(fails), total))
        for f in fails:
            print("  -", f)
        return 1
    print("KAT: GO — %d vectors 일치. illustrative-only %d 스킵 "
          "(사람기원/실서비스 의존 · CI 재현 불가 · UNSUPPORTED)." % (total, ill))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
