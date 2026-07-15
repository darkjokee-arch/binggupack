# -*- coding: utf-8 -*-
"""toy_conforming — 최소 계약을 올바르게 구현한 참조 adapter(in-memory).

runner/시나리오 계약이 BingguPack 전용 경로(CLI 이름·SQLite 스키마)에 의존하지 않음을 보여준다.
운영 정본이 없으므로 operating_fingerprint 는 None(MGB-12 자명 통과). 12 시나리오 전부 PASS 기대.
"""
import hashlib
import os
import shutil
import tempfile

from benchmark.adapters.base import HomeHandle
from benchmark.contracts import Cap, Observation


def _pid(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


class ToyConformingAdapter:
    name = "toy_conforming"

    def capabilities(self) -> set[str]:
        return {
            Cap.INIT, Cap.PREVIEW, Cap.SAVE, Cap.LIST_ACTIVE, Cap.RECALL, Cap.RECALL_FRESH,
            Cap.EXPLAIN, Cap.SUPERSEDE, Cap.PAIR, Cap.REMOTE_INTENT, Cap.CAPTURE_CANDIDATE,
            Cap.UNAUTHORIZED_WRITE, Cap.EXACT_BINDING, Cap.STALE_FRESHNESS,
            Cap.REPLAY_APPROVAL, Cap.INTEGRITY_PUBLIC,
        }

    def new_home(self, root: str) -> HomeHandle:
        d = os.path.realpath(tempfile.mkdtemp(prefix="mgb_toyc_", dir=root))
        return HomeHandle(root=d, adapter_name=self.name,
                          meta={"active": [], "deprecated": [], "consumed": set(), "index": {}})

    def cleanup(self, home: HomeHandle) -> None:
        shutil.rmtree(home.root, ignore_errors=True)

    def operating_fingerprint(self):
        return None  # 운영 정본 없음

    def _active(self, st) -> int:
        return len(st["active"])

    def observe(self, home: HomeHandle, op: str, **kw) -> Observation:
        st = home.meta
        if op == Cap.INIT:
            return Observation(op, exit_code=0, state={"active_count": self._active(st)})
        if op == Cap.PREVIEW:
            return Observation(op, exit_code=0, state={"preview_id": _pid(kw["text"])})
        if op == Cap.SAVE:
            t = kw["text"]; pid = _pid(t); nid = "toy:" + pid
            st["active"].append(t); st["index"][nid] = t; st["consumed"].add(pid)
            return Observation(op, exit_code=0, state={
                "active_count": self._active(st), "node_ids": [nid], "saved": 1, "preview_id": pid})
        if op == Cap.LIST_ACTIVE:
            return Observation(op, exit_code=0, state={"active_count": self._active(st)})
        if op in (Cap.RECALL, Cap.RECALL_FRESH):
            # 전체 dump 가 아니라 질의 어휘와 가장 많이 겹치는 top-1 만(특이성 시뮬).
            q = set(kw.get("query", "").split())
            scored = sorted(((len(q & set(t.split())), t) for t in st["active"]), reverse=True)
            scored = [(n, t) for n, t in scored if n > 0]
            out = scored[0][1] if scored else ""
            return Observation(op, exit_code=0, stdout=out, state={})
        if op == Cap.EXPLAIN:
            nid = kw["node_id"]
            if nid in st["index"]:
                return Observation(op, exit_code=0, state={},
                                   stdout="root: %s -evidence_supports-> 근거(%s)"
                                   % (nid, st["index"][nid][:20]))
            return Observation(op, exit_code=0, stdout="노드를 찾을 수 없습니다", state={})
        if op == Cap.SUPERSEDE:
            n = kw.get("n", 1)
            if st["active"]:
                idx = n - 1 if 0 <= n - 1 < len(st["active"]) else len(st["active"]) - 1
                st["deprecated"].append(st["active"].pop(idx))
            return Observation(op, exit_code=0, state={
                "target_state_after": "deprecated", "target_present_after": True})
        if op == Cap.PAIR:
            st["active"].append(kw["owner_text"]); st["active"].append(kw["ai_text"])
            return Observation(op, exit_code=0, stdout="OK: 저장 2건 (ai_accepts 연결)",
                               state={"active_count": self._active(st)})
        if op == Cap.REMOTE_INTENT:
            b = self._active(st)
            return Observation(op, exit_code=0, state={"active_before": b, "active_after": b})
        if op == Cap.CAPTURE_CANDIDATE:
            return Observation(op, exit_code=0,
                               state={"active_count": self._active(st), "candidate_count": 0})
        if op == Cap.UNAUTHORIZED_WRITE:
            b = self._active(st)  # 비승인 → 거부(활성 불변)
            return Observation(op, exit_code=1, state={"active_before": b, "active_after": b})
        if op == Cap.EXACT_BINDING:
            # baseline: 유효 preview 로 정상 저장 성공 · mutation: 내용 변조는 거부(active·digest 불변)
            st["active"].append(kw["text_a"]); a_base = self._active(st)
            return Observation(op, exit_code=1, state={
                "preview_id_valid": True, "baseline_exit": 0,
                "active_before": a_base - 1, "active_after_baseline": a_base,
                "mutation_exit": 1, "mutation_error_code": "content_binding_mismatch",
                "active_after_mutation": a_base, "mutation_digest_present": False})
        if op == Cap.STALE_FRESHNESS:
            # 시간·상태 신선도 만료 후 옛 승인 거부(active 불변·digest 미생성)
            a = self._active(st)
            return Observation(op, exit_code=1, state={
                "stale_rejected": True, "error_code": "stale_freshness",
                "active_before": a, "active_after": a, "digest_present": False})
        if op == Cap.REPLAY_APPROVAL:
            t = kw["text"]; pid = _pid(t)
            st["active"].append(t); st["consumed"].add(pid); a1 = self._active(st)
            a2 = a1  # 2회차: 동일 승인 재사용 → 거부(무증가)
            return Observation(op, exit_code=1, state={
                "first_exit": 0, "active_after_first": a1, "active_after_second": a2,
                "preview_id": pid})
        if op == Cap.INTEGRITY_PUBLIC:
            tampered = bool(kw.get("tamper"))
            return Observation(op, exit_code=1 if tampered else 0,
                               state={"tamper_detected": tampered})
        raise ValueError("toy_conforming 미지원 op: %s" % op)
