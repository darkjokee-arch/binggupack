# -*- coding: utf-8 -*-
"""binggupack.mcp.approval_gate — trusted approval event 소비 게이트 (owner CLI 전용).

정본 설계: docs/BINGGUPACK_TRUSTED_APPROVAL_EVENT_RFC.md §18.
2026-07-13 스코프 축소(owner 결정): MCP write 핸들러 배선은 제거됐다 — MCP 도구 호출은 approval 로
승격되지 않는다(fail-closed). 이 모듈의 현행 소비자는 **owner CLI 경로뿐**이다:
binggu.py `_mutation_via_approval`(accept/unaccept/due/resolve `--approval-id`).
(hag_sync_adapter `--import-edges` 는 trusted_approval core 를 직접 쓴다 — 본 모듈 미경유.)

사용 계약(불변):

    with approval_gate.authorize(operation, params, home, db) as auth:
        r = core_mutation(db, ..., {"actor": auth.actor, "confirm": confirm}, ...)
        auth.settle(r)
    return {..., "executed_write": bool(r.get("applied")), **auth.response_extra()}

불변:
  - provider 미구성/유효 approval 부재 → auth.actor = "reader"(정확히 이 문자열) → core fail-closed.
  - authorize 는 EVENT store 를 read-only 조회만 한다 — approval 생성/append 0(그건 CLI/hook 전용).
  - 응답에 approval_nonce/secret 0(TAE-6). receipt = node_id/decision_id/request_id 만.
  - 같은 db.con 에 one-time consume(reserve/finalize) 을 커밋 → replay/동시 consume 정확히 1회.
"""
from __future__ import annotations

import time
from contextlib import contextmanager

from binggupack.safety import trusted_approval as ta

# 서버 결정 ledger identity 헬퍼(패키지 shim 경유 · scripts 정본 re-export).
from binggupack.storage.schema import ledger_id as _ledger_id


class _Auth:
    def __init__(self, operation, home, db):
        self.operation = operation
        self.home = home
        self.db = db
        self.actor = "reader"          # 기본 fail-closed
        self.reason = None
        self.request_id = None
        self.receipt = None
        self.write_available = False
        self._nonce = None             # 예약 성공(armed) 시에만 설정 → settle 이 소비
        self._payload = None
        self._digest = None

    def settle(self, core_result):
        """mutation 결과로 approval 을 CONSUMED/RELEASE 처리(§14 분할표). actor='reader'였으면 no-op."""
        core_result = core_result or {}
        if self._nonce is None:
            # approval 미사용. 단 save_candidate 는 save_selected 내부 save_gate 앵커(owner 키보드 SAVE)로
            # authorize 와 무관하게 정당 승격·write 될 수 있다(§22 backward compat). 그 경우 실제 결과를
            # 반영 — "provider 미구성" 으로 오표기하지 않는다(CC-1 / smoke 9c 응답 정합).
            if core_result.get("applied"):
                self.write_available = True
                self.reason = None
            return
        applied = bool(core_result.get("applied"))
        reason = core_result.get("reason")
        now = time.time()
        con = self.db.con
        idempotent = ta.is_idempotent_done(reason, core_result)
        receipt = ta.derive_receipt(self.operation, self._payload, core_result, self.request_id)
        if idempotent and reason == "duplicate_already_applied":
            # R2-02/TAE-4: applied_registry 는 남았으나 node 는 없을 수 있음(외부 prune 등) → node 존재
            # 재조정. 부재면 IDEMPOTENT 아님 → RELEASE(승인 재사용 가능·"lost write 를 성공 보고" 방지).
            nids = receipt.get("node_ids") or []
            if nids and not ta.any_node_exists(con, nids):
                idempotent = False
        if applied or idempotent:
            ta.finalize_consumed(con, self._nonce, self.request_id, receipt, now)
            self.receipt = {"request_id": receipt.get("request_id"),
                            "node_ids": receipt.get("node_ids"),
                            "decision_id": receipt.get("decision_id")}
            self.write_available = True
            self.reason = None if applied else "idempotent_already_applied"
            # 요청 완료 → PENDING 상태/검토 레코드 정리
            try:
                con.execute("UPDATE approval_requests SET state='consumed' WHERE request_id=?",
                            (self.request_id,))
                con.commit()
            except Exception:
                # Consumption already succeeded; stale-state cleanup remains best-effort.
                pass
            ta.purge_review(self.home, self.request_id)
            # tamper-evident receipt(mark 패리티 포함 — hit_events 는 audit_log row 없음).
            try:
                ck = self.db.store_checksum()
                self.db.audit_append("human", "approval_consume:%s" % self.operation,
                                     self.request_id, "ALLOW",
                                     "receipt=%s" % (self.receipt.get("node_ids") or self.request_id),
                                     ck, ck)
            except Exception:
                # Audit receipt failure cannot retroactively change the bound approval result.
                pass
        elif ta.is_transient(reason):
            ta.release(con, self._nonce)     # 재시도 가능 — 승인 소각 0
            self.reason = reason
        else:
            ta.release(con, self._nonce)     # HARD_BLOCK(pii/a0/confirm/…) — 승인 소각 0 · 사유 반환
            self.reason = reason
        self._nonce = None

    def response_extra(self):
        out = {"write_available": self.write_available}
        if self.request_id:
            out["request_id"] = self.request_id
        if self.reason:
            out["reason"] = self.reason
            if self.reason == "approval_required":
                out["approval_required"] = True
                out["owner_action"] = "use_local_cli"
                out["guidance"] = ("owner 가 로컬에서 검토·승인해야 저장됩니다: "
                                   "binggu approval show %s → binggu approval approve %s"
                                   % (self.request_id, self.request_id))
        if self.receipt:
            out["receipt"] = self.receipt        # nonce 미포함(TAE-6)
        return out


@contextmanager
def authorize(operation, params, home, db):
    """write 핸들러용 승인 게이트. db = open_g3/open_accept 결과(StagingDB). db.con 에 consume 커밋."""
    params = params or {}
    auth = _Auth(operation, home, db)
    provider = ta.provider_for(home)
    if provider is None:
        auth.actor = "reader"
        auth.reason = "provider_not_configured"
        auth.write_available = False
        yield auth
        return

    # canonical 바인딩(금지 control/bidi 포함 시 fail-closed).
    try:
        digest = ta.canonical_payload_digest(operation, params)
    except ta.ControlCharReject:
        auth.actor = "reader"
        auth.reason = "binding_reject:control_char"
        yield auth
        return
    except Exception:
        auth.actor = "reader"
        auth.reason = "binding_error"
        yield auth
        return

    lid = _ledger_id(db.con)
    rid = ta.compute_request_id(operation, digest, lid)
    auth.request_id = rid
    auth._payload = params
    auth._digest = digest
    approval_id = params.get("approval_id")
    now = time.time()

    if not approval_id:
        # 승인 미제시 → owner 가 승인할 수 있도록 PENDING 요청 + 검토 레코드 기록(cap/PII 게이트). fail-closed.
        up = ta.upsert_request(db.con, rid, ta.PROTOCOL_VERSION, operation, digest, lid,
                               ta.summary_for(operation, params, lid), now,
                               provider.ttl_seconds, provider.pending_cap)
        if up.get("ok"):
            try:
                review_payload = dict(params)
                # deprecate/replace: 대상 노드의 실제 문장을 review 에 담아 owner 가 무엇을 기각/교체하는지
                # 직접 보게 한다(R3-01). digest 는 params 기준이라 불변(binding_fields 는 _target_sentence 무시).
                if operation in ("deprecate", "replace", "accept", "unaccept"):
                    # index 기반 대상 문장 주입 — owner 가 무엇을 기각/교체/수용하는지 직접 보게(R3-01).
                    try:
                        import os as _os
                        import sys as _sys
                        _scripts = _os.path.join(_os.path.dirname(_os.path.dirname(
                            _os.path.dirname(_os.path.abspath(__file__)))), "scripts")
                        if _scripts not in _sys.path:
                            _sys.path.insert(0, _scripts)
                        from openbinggu_candidate_list_view import list_candidates
                        _rows = list_candidates(db)["rows"]
                        _i = params.get("index")
                        if isinstance(_i, int) and 1 <= _i <= len(_rows):
                            review_payload["_target_sentence"] = _rows[_i - 1].get("sentence")
                    except Exception:
                        # Human-readable target text is optional and excluded from the digest.
                        pass
                elif operation in ("due", "resolve"):
                    # node_id 기반(index 아님 · P1-B M2) — nodes.sentence 조회. digest 불변(_target_sentence 무시).
                    try:
                        _nid = params.get("node_id")
                        if _nid:
                            _row = db.con.execute("SELECT sentence FROM nodes WHERE node_id=?",
                                                  (_nid,)).fetchone()
                            if _row:
                                review_payload["_target_sentence"] = _row[0]
                    except Exception:
                        # Human-readable target text is optional and excluded from the digest.
                        pass
                ta.write_review(home, rid, operation, review_payload, digest)
            except Exception:
                # Review-file UX is advisory; authorization stays denied without approval.
                pass
            auth.reason = "approval_required"
        else:
            auth.reason = up.get("reason", "approval_required")
        auth.actor = "reader"
        yield auth
        return

    if approval_id != rid:
        # 모델이 제 payload 와 다른 승인 id 를 제시 = payload 바인딩 불일치.
        auth.actor = "reader"
        auth.reason = "binding_mismatch:request_id"
        yield auth
        return

    v = ta.verify_event(home, rid, operation, digest, lid, now)
    if not v.get("ok"):
        auth.actor = "reader"
        auth.reason = v.get("reason")
        yield auth
        return

    nonce = v["nonce"]
    res = ta.reserve(db.con, nonce, now)
    st = res.get("status")
    if st == "already_consumed":
        auth.actor = "reader"
        auth.reason = "approval_already_consumed"
        rc = res.get("receipt")
        if rc:
            try:
                import json as _json
                auth.receipt = _json.loads(rc)
                auth.receipt.pop("nonce", None)
            except Exception:
                auth.receipt = None
        yield auth
        return
    if st == "in_progress":
        auth.actor = "reader"
        auth.reason = "approval_in_progress"
        yield auth
        return

    # reserved(승자 or takeover). ★mutate 직전 tombstone 재확인(TAE-P2-06): verify 후~mutate 전에 landing
    # 한 revoke 는 차단한다. 단 mutate 도중 landing 한 revoke 는 잡지 못한다 — 비-가역 core 는 되돌릴 수
    # 없어 이미 owner 가 승인한 작업이 커밋된다(문서화된 한계·§15). write-forge 아님(사전 승인된 작업).
    tomb, treason = ta.is_tombstoned(home, rid)
    if tomb:
        ta.release(db.con, nonce)
        auth.actor = "reader"
        auth.reason = treason
        yield auth
        return

    auth.actor = "human"
    auth._nonce = nonce
    yield auth
    # 핸들러가 settle() 을 호출하지 않았으면(예외 등) 예약 해제(승인 소각 0).
    if auth._nonce is not None:
        ta.release(db.con, auth._nonce)
        auth._nonce = None
