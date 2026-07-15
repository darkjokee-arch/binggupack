# -*- coding: utf-8 -*-
"""binggupack.safety.trusted_approval — P1-A Trusted Approval Event core.

정본 설계: docs/BINGGUPACK_TRUSTED_APPROVAL_EVENT_RFC.md (v2).

핵심(불변식):
  - MCP/model 은 approval REQUEST(PENDING)만 만들 수 있다. approval EVENT(APPROVED 앵커)는
    owner-controlled channel(CLI/hook)만 append 한다 — 이 모듈의 append_event 는 CLI/hook 전용이고
    approval_gate(MCP 경유)는 read_events/reserve/finalize 만 호출한다(append_event 미호출).
  - 승인은 (protocol_version, operation, canonical payload digest, ledger_id) 에 정확 바인딩.
  - one-time consume: approval_consumptions.approval_nonce PK = single-winner + reserved_at lease.
  - provider 미구성 → provider_for()==None → 호출부 fail-closed(actor='reader').
  - store 는 hash/digest + 최소 metadata 만(raw 대화 저장 0). owner 실내용 검토는 approval_review
    파일(PII 게이트·cap·TTL·결정 시 purge).

신뢰 경계 정직성(§4/§5 RFC): 이 통제는 "모델 tool surface 가 approval store 위치에 write 권한을
갖지 못한다" 가 성립하는 배포(웹/앱 커넥터·잠긴 에이전트)에서만 하드하다. Filesystem/Bash MCP 를
함께 물린 호스트는 위협 모델 밖(그 경우 fail-closed 보존 intent-routing). 여기 서명/HMAC 을 넣지
않는다(같은 머신 키 = 보안 연극·§6).
"""
from __future__ import annotations

import json
import os
import secrets
import time
import unicodedata

# ── 상수 ────────────────────────────────────────────────────────────────────────
PROTOCOL_VERSION = "tae-1"          # canonicalization 알고리즘 버전 포함. 변경 시 bump → 구 승인 무효(fail-closed).
_UNIT = "\x1f"                       # 필드 구분자(§9 · request_id 도 동일)
DEFAULT_TTL_SECONDS = 900            # 짧은 TTL(15분). owner config 로 조정.
DEFAULT_PENDING_CAP = 64             # ledger 당 PENDING 요청 상한.
LEASE_SECONDS = 120                  # reserve lease — 이 시간 내 CONSUMING 은 live 로 간주(재실행 금지).

# settle() reason 분할(§14). IDEMPOTENT_DONE = 효과 이미 반영(→CONSUMED). TRANSIENT = 재시도 가능(→RELEASE).
IDEMPOTENT_DONE_REASONS = frozenset({
    "duplicate_already_applied", "already_deprecated", "pair_partial_exists",
    "dup_decision", "replace_same_content", "duplicate_active_content",
})
TRANSIENT_REASONS = frozenset({
    "backup_create_failed", "sqlite_wal_incomplete", "sqlite_checksum_mismatch",
})

# 금지 codepoint(§9-3): bidi override/isolate + 기타 control/format(Cc/Cf). tab/newline 은 예외.
_BIDI = {"‪", "‫", "‬", "‭", "‮",
         "⁦", "⁧", "⁨", "⁩"}


class ControlCharReject(ValueError):
    """payload 에 금지 bidi/control codepoint 포함 → binding_reject:control_char."""


# ── 경로 ────────────────────────────────────────────────────────────────────────
def config_path(home):
    return os.path.join(home, "trusted_approval.json")


def event_store_path(home):
    """owner-only trusted approval EVENT 앵커(append-only). CLI/hook 만 write."""
    return os.path.join(home, "approvals.jsonl")


def review_dir(home):
    return os.path.join(home, "approval_review")


def _review_path(home, request_id):
    return os.path.join(review_dir(home), "%s.json" % request_id)


# ── config / provider ────────────────────────────────────────────────────────────
def load_config(home):
    """owner-controlled 설정 파일 로드. 부재/비활성 → None(provider 미구성=fail-closed).
    env boolean 로는 활성화되지 않는다(§6 · TAE env_spoof)."""
    p = config_path(home)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return None
    if not isinstance(cfg, dict) or cfg.get("enabled") is not True:
        return None
    return {
        "enabled": True,
        "kind": cfg.get("kind", "local_owner"),   # kind 없는 기존 config = local_owner(L1 하위호환·무회귀)
        "ttl_seconds": int(cfg.get("ttl_seconds", DEFAULT_TTL_SECONDS)),
        "pending_cap": int(cfg.get("pending_cap", DEFAULT_PENDING_CAP)),
    }


class LocalOwnerProvider:
    """로컬 owner 채널 provider. 승인 발행은 CLI(binggu approval approve)가, 조회/consume 은
    approval_gate(MCP)가 담당. 이 객체 자체는 config 존재 = 활성 신호 + 파라미터 보관."""

    kind = "local_owner"

    def __init__(self, home, cfg):
        self.home = home
        self.ttl_seconds = cfg["ttl_seconds"]
        self.pending_cap = cfg["pending_cap"]


class HostedProvider:  # pragma: no cover - placeholder interface only(P1-B)
    """hosted/web provider placeholder. 실행 adapter 미작성(§12 · out-of-scope)."""

    kind = "hosted"

    def __init__(self, *a, **k):
        raise NotImplementedError("hosted trusted approval provider is P1-B (not implemented)")


def provider_for(home):
    """구성된 provider 반환 or None(미구성 = fail-closed). 모델이 못 바꾸는 파일 신호로만 발견.

    kind 분기:
      - config 부재/disabled → None (호출부 provider_for is None 의존 · 오늘과 동일).
      - kind ∈ {local_owner, absent} → LocalOwnerProvider (byte-identical · 서명 계층 미개입).
      - kind ∈ SIGNING_KINDS(keychain 등) → signing_provider(Core1) 위임:
          backend 가용 → KeychainProvider · 미가용 → _UnavailableSigningProvider(fail-closed 스텁·None/raise 아님).
      - 그 외 미지정 kind → LocalOwnerProvider (kind 는 모델-writable 평문이라 신뢰 경계 밖 · §6).

    ★정직(§6): config 의 kind 는 owner 머신에서 모델-writable 평문이라 kind:local_owner 한 줄로 서명
      검증을 통째 skip 시킬 수 있다. L2(keychain)의 실질 값은 hosted/locked 배포에서만 나온다 — 여기
      서명 계층은 로컬 위조를 막지 못한다(같은 머신 키 = 보안 연극)."""
    cfg = load_config(home)
    if cfg is None:
        return None
    kind = cfg.get("kind", "local_owner")
    if kind in ("local_owner", "absent"):
        return LocalOwnerProvider(home, cfg)
    from binggupack.safety import signing_provider as _sp   # lazy(Core1 · import-time 부작용 0)
    if kind in _sp.SIGNING_KINDS:
        return _sp.signing_provider_for(home)               # Keychain | Unavailable(이 분기서 None 불가)
    return LocalOwnerProvider(home, cfg)                    # 미지정 kind → local(untrusted 평문 · §6)


# ── canonical digest (§9) ────────────────────────────────────────────────────────
def _nfc(s):
    return unicodedata.normalize("NFC", s if isinstance(s, str) else str(s))


def _reject_control(s):
    for ch in s:
        if ch in _BIDI:
            raise ControlCharReject("bidi")
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cf") and ch not in ("\t", "\n"):
            raise ControlCharReject(cat)
    return s


def _clean_str(s):
    """NFC 정규화 + 금지 codepoint 거부(포함 시 ControlCharReject)."""
    return _reject_control(_nfc(s))


def binding_fields(operation, payload):
    """operation 별 고정 binding schema(누락 optional = explicit null). 문자열은 NFC+control 검사.
    ★ save_candidate 는 explicit/speaker/due_date 도 바인딩(TAE-P2-04). mark 는 recall_nonce(TAE-P2-05)."""
    p = payload or {}

    def s(v):
        return _clean_str(v) if v is not None else None

    if operation == "save_candidate":
        idx = p.get("indices") or []
        return {"text": s(p.get("text", "")),
                "indices": sorted(int(i) for i in idx if isinstance(i, int) and not isinstance(i, bool)),
                "explicit": bool(p.get("explicit", False)),
                "speaker": s(p.get("speaker")),
                "due_date": s(p.get("due_date"))}
    if operation == "pair":
        # due_date 는 save_paired 가 judgment_reviews 로 쓰므로 반드시 바인딩(TA-ATK-1: 미바인딩 시
        # owner 미검토 리마인더 주입 가능). save_candidate 와 동형.
        return {"owner_text": s(p.get("owner_text", "")),
                "ai_text": s(p.get("ai_text")),
                "owner_pick": int(p.get("owner_pick", 1)),
                "ai_pick": int(p.get("ai_pick", 1)),
                "by": s(p.get("by", "ai")),
                "relation": s(p.get("relation", "accepts")),
                "due_date": s(p.get("due_date"))}
    if operation == "deprecate":
        return {"index": p.get("index"), "id8": s(p.get("id8", "")), "reason": s(p.get("reason", ""))}
    if operation == "replace":
        return {"index": p.get("index"), "id8": s(p.get("id8", "")),
                "new_sentence": s(p.get("new_sentence", "")), "reason": s(p.get("reason", ""))}
    if operation in ("mark_hit", "mark_miss"):
        return {"recall_query": s(p.get("recall_query", "")), "index": p.get("index"),
                "outcome": "hit" if operation == "mark_hit" else "miss",
                "domain": s(p.get("domain")), "recall_nonce": s(p.get("recall_nonce"))}
    if operation == "harvest_add":
        return {"kind": s(p.get("kind", "")), "url": s(p.get("url", "")), "keyword": s(p.get("keyword"))}
    if operation == "harvest_remove":
        return {"source_id": s(p.get("source_id", ""))}
    # P1-B Track A — CLI mutation surface closure(§2). write되는 값 전부 바인딩.
    if operation in ("accept", "unaccept"):
        # accept/unaccept 는 owner_acceptances event append. event 는 operation 이 구분(중복 제거).
        return {"index": p.get("index"), "id8": s(p.get("id8", "")), "reason": s(p.get("reason", ""))}
    if operation == "due":
        # set_review_due 가 judgment_reviews 에 (node_id, due_date) INSERT.
        return {"node_id": s(p.get("node_id", "")), "due_date": s(p.get("due_date", ""))}
    if operation == "resolve":
        # resolve_review 가 judgment_reviews 에 outcome/resolved_reason UPDATE.
        return {"node_id": s(p.get("node_id", "")), "outcome": s(p.get("outcome", "")),
                "reason": s(p.get("reason", ""))}
    if operation in ("confirm_edges", "import_edges"):
        # ★C1(Fable5 사전검증): edge_key(src|dst|rel|kmap)만으론 evidence_refs 미바인딩 →
        # 승인 후 evidence 위조가 운영 ledger provenance 를 오염(TA-ATK-1 클래스). evidence 도 바인딩.
        # ★M4: 실제 적재될 post-filter subset 기준(호출부가 필터 통과분만 넘긴다).
        def _norm_edge(e):
            ev = [s(x) for x in (e.get("evidence") or []) if x is not None]
            return {"src": s(e.get("src", "")), "dst": s(e.get("dst", "")),
                    "rel": s(e.get("rel", "")), "evidence": sorted(x for x in ev if x is not None)}
        edges = [_norm_edge(e) for e in (p.get("edges") or [])]
        edges.sort(key=lambda d: (d["src"] or "", d["dst"] or "", d["rel"] or ""))
        return {"edges": edges}
    if operation == "hosted_bundle":
        # 묶음 승인(owner 16계약 · A3). 선택 intent 집합에 immutable 바인딩 — 각 intent 의 save_candidate
        # digest + intent_id 정렬. 전체 bundle digest = canonical_payload_digest("hosted_bundle", …) 자체.
        # membership 수정 시 digest 변화 → request_id 변화 → 기존 승인 무효(계약 2·3). raw 미포함(digest 만).
        out = []
        for it in (p.get("items") or []):
            sub = {"text": s(it.get("text", "")),
                   "indices": sorted(int(i) for i in (it.get("indices") or [])
                                     if isinstance(i, int) and not isinstance(i, bool)),
                   "explicit": False,                    # H4: hosted 는 explicit=False 고정(3곳 동일)
                   "speaker": s(it.get("speaker")), "due_date": None}
            out.append({"intent_id": s(it.get("intent_id", "")),
                        "digest": canonical_payload_digest("save_candidate", sub)})
        out.sort(key=lambda d: d["intent_id"] or "")
        return {"items": out}
    raise ValueError("unknown operation: %s" % operation)


def canonical_payload_digest(operation, payload, protocol_version=PROTOCOL_VERSION):
    """versioned canonical digest. sort_keys JSON(concat 충돌 불가) + protocol/operation prefix.
    payload 에 금지 bidi/control 포함 시 ControlCharReject 전파(호출부 → binding_reject:control_char)."""
    import hashlib
    fields = binding_fields(operation, payload)
    canon = json.dumps(fields, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    material = "%s%s%s%s%s" % (protocol_version, _UNIT, operation, _UNIT, canon)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def compute_request_id(operation, payload_digest, ledger_identity, protocol_version=PROTOCOL_VERSION):
    """결정적 request_id — 같은 intent=같은 id(스팸 dedup). §9 와 동일 \x1f 구분자(TAE-P2-09)."""
    import hashlib
    material = _UNIT.join((protocol_version, operation, payload_digest, str(ledger_identity)))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


# ── request store (model-writable PENDING · db.con) ──────────────────────────────
def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def count_pending(con):
    row = con.execute("SELECT count(*) FROM approval_requests WHERE state='pending'").fetchone()
    return row[0] if row else 0


def summary_for(operation, payload, ledger_identity):
    """payload-agnostic 템플릿(handler 생성·모델 미제공·topic 텍스트 0 · TAE R3-02)."""
    p = payload or {}
    if operation == "save_candidate":
        n = len([i for i in (p.get("indices") or []) if isinstance(i, int)])
        detail = "%d item(s)" % n
    elif operation == "pair":
        detail = "owner+ai" if p.get("ai_text") else "owner solo"
    elif operation in ("deprecate", "replace"):
        detail = "node #%s" % p.get("index")
    elif operation in ("mark_hit", "mark_miss"):
        detail = "recall #%s" % p.get("index")
    elif operation in ("harvest_add", "harvest_remove"):
        detail = "source"
    elif operation in ("accept", "unaccept"):
        detail = "node #%s" % p.get("index")
    elif operation in ("due", "resolve"):
        detail = "node %s" % str(p.get("node_id"))[:12]
    elif operation in ("confirm_edges", "import_edges"):
        detail = "%d edge(s)" % len(p.get("edges") or [])
    elif operation == "hosted_bundle":
        detail = "%d intent(s)" % len(p.get("items") or [])
    else:
        detail = ""
    return "%s: %s -> ledger %s" % (operation, detail, str(ledger_identity)[:8])


def upsert_request(con, request_id, protocol_version, operation, payload_digest,
                   ledger_identity, summary, now, ttl_seconds, pending_cap):
    """PENDING 요청 생성/갱신(멱등). cap 초과 시 NEW request 거부(기존 owner 관련 요청 evict 0).
    반환 {ok, reason?}. raw payload 저장 0(summary=payload-agnostic·§17)."""
    existing = con.execute("SELECT request_id FROM approval_requests WHERE request_id=?",
                           (request_id,)).fetchone()
    if existing is None and count_pending(con) >= pending_cap:
        return {"ok": False, "reason": "pending_cap_reached"}
    con.execute(
        "INSERT OR REPLACE INTO approval_requests"
        "(request_id,protocol_version,operation,payload_digest,ledger_id,summary,state,created_at,expires_at)"
        " VALUES(?,?,?,?,?,?, 'pending', ?, ?)",
        (request_id, protocol_version, operation, payload_digest, str(ledger_identity), summary,
         _iso(now), _iso(now + ttl_seconds)))
    con.commit()
    return {"ok": True}


def list_requests(con):
    rows = con.execute(
        "SELECT request_id,operation,summary,state,created_at,expires_at FROM approval_requests"
        " ORDER BY created_at DESC").fetchall()
    return [{"request_id": r[0], "operation": r[1], "summary": r[2], "state": r[3],
             "created_at": r[4], "expires_at": r[5]} for r in rows]


def get_request(con, request_id):
    r = con.execute(
        "SELECT request_id,protocol_version,operation,payload_digest,ledger_id,summary,state,created_at,expires_at"
        " FROM approval_requests WHERE request_id=?", (request_id,)).fetchone()
    if not r:
        return None
    return {"request_id": r[0], "protocol_version": r[1], "operation": r[2], "payload_digest": r[3],
            "ledger_id": r[4], "summary": r[5], "state": r[6], "created_at": r[7], "expires_at": r[8]}


# ── review record (owner-facing · PII 게이트 · purge-on-decision) ─────────────────
def _pii_safe(text):
    """candidate 와 동일한 PII/개인사 제외 검사(batch_redact 만이 아님). 위험 시 False."""
    if not text:
        return True
    try:
        import sys
        _s = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts")
        if _s not in sys.path:
            sys.path.insert(0, _s)
        from watcher_batch_m1 import scan_residual_pii
        import openbinggu_incoming_to_staging as v011
        if scan_residual_pii(text):
            return False
        if any(rx.search(text) for rx in v011.SECRET_PATTERNS):
            return False
    except Exception:
        return True  # 게이트 부재 시 보수적으로 통과(요청 자체가 이미 candidate 게이트 후)
    return True


def render_review(operation, payload):
    """owner 가 승인 전 볼 실내용(exact payload). PII 위험 필드는 placeholder 로 대체."""
    p = payload or {}
    items = []

    def field(label, value):
        v = value if value is not None else ""
        items.append({"label": label, "value": v if _pii_safe(str(v)) else "[민감정보 가림 — CLI 로 직접 확인]"})

    if operation == "save_candidate":
        try:
            from binggupack.capture import preview as cvp
            cands = cvp.capture_preview(p.get("text", ""), explicit=bool(p.get("explicit", False)))["candidates"]
            for i in (p.get("indices") or []):
                if isinstance(i, int) and 1 <= i <= len(cands):
                    field("문장 #%d" % i, cands[i - 1]["sentence"])
        except Exception:
            field("text", p.get("text"))
        field("explicit", p.get("explicit", False))
        field("speaker", p.get("speaker"))
    elif operation == "pair":
        field("owner", p.get("owner_text"))
        if p.get("ai_text"):
            field("ai", p.get("ai_text"))
        field("relation", "%s_%s" % (p.get("by", "ai"), p.get("relation", "accepts")))
        if p.get("due_date"):
            field("리마인드 예정일", p.get("due_date"))
    elif operation == "deprecate":
        if p.get("_target_sentence"):
            field("기각 대상 문장", p.get("_target_sentence"))
        field("대상 #", p.get("index")); field("id8", p.get("id8")); field("사유", p.get("reason"))
    elif operation == "replace":
        if p.get("_target_sentence"):
            field("교체 대상 문장(기존)", p.get("_target_sentence"))
        field("새 문장", p.get("new_sentence"))
        field("대상 #", p.get("index")); field("id8", p.get("id8")); field("사유", p.get("reason"))
    elif operation in ("mark_hit", "mark_miss"):
        field("recall", p.get("recall_query")); field("#", p.get("index"))
        field("결과", "적중" if operation == "mark_hit" else "빗나감")
    elif operation == "harvest_add":
        field("kind", p.get("kind")); field("url", p.get("url"))
    elif operation == "harvest_remove":
        field("source_id", p.get("source_id"))
    elif operation in ("accept", "unaccept"):
        if p.get("_target_sentence"):
            field("대상 문장", p.get("_target_sentence"))
        field("대상 #", p.get("index")); field("id8", p.get("id8")); field("사유", p.get("reason"))
    elif operation == "due":
        if p.get("_target_sentence"):
            field("판단 문장", p.get("_target_sentence"))
        field("node_id", p.get("node_id")); field("검증 예정일", p.get("due_date"))
    elif operation == "resolve":
        if p.get("_target_sentence"):
            field("판단 문장", p.get("_target_sentence"))
        field("node_id", p.get("node_id")); field("결과", p.get("outcome")); field("사유", p.get("reason"))
    elif operation in ("confirm_edges", "import_edges"):
        for e in (p.get("edges") or []):
            field("엣지", "%s → %s (%s)" % (e.get("src"), e.get("dst"), e.get("rel")))
    elif operation == "hosted_bundle":
        # 묶음 전체 항목을 owner 가 검토(계약: 화면에 표시된 것만 승인 범위). raw 복제 아님 — 선택 문장 발췌만.
        for it in (p.get("items") or []):
            iid = str(it.get("intent_id") or "")[:8]
            try:
                from binggupack.capture import preview as cvp
                cands = cvp.capture_preview(it.get("text", ""), explicit=False)["candidates"]
                for i in (it.get("indices") or []):
                    if isinstance(i, int) and 1 <= i <= len(cands):
                        field("intent %s #%d" % (iid, i), cands[i - 1]["sentence"])
            except Exception:
                field("intent %s" % iid, (it.get("text") or "")[:60])
    return items


def write_review(home, request_id, operation, payload, payload_digest):
    """owner 검토용 실내용 기록(PII 게이트 후). 결정/만료 시 purge_review 로 제거."""
    os.makedirs(review_dir(home), exist_ok=True)
    body = {"request_id": request_id, "operation": operation, "payload_digest": payload_digest,
            "items": render_review(operation, payload)}
    tmp = _review_path(home, request_id) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False)
    os.replace(tmp, _review_path(home, request_id))


def read_review(home, request_id):
    p = _review_path(home, request_id)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def purge_review(home, request_id):
    p = _review_path(home, request_id)
    try:
        if os.path.exists(p):
            os.remove(p)
    except OSError:
        pass


# ── event store (owner-only 앵커 · append-only) ──────────────────────────────────
# ★ append_event 는 CLI/hook 전용. approval_gate(MCP)는 절대 호출하지 않는다(read 만).
def append_event(home, record):
    """trusted approval EVENT 1건 append(owner CLI/hook 전용)."""
    path = event_store_path(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_events(home):
    path = event_store_path(home)
    out = []
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def is_tombstoned(home, request_id):
    """revoke/reject tombstone 이 있으면 (True, reason). 없으면 (False, None)."""
    for e in read_events(home):
        if e.get("request_id") == request_id and e.get("record_type") in ("revoke", "reject"):
            return True, ("approval_revoked" if e["record_type"] == "revoke" else "approval_rejected")
    return False, None


def find_approve(home, request_id):
    for e in read_events(home):
        if e.get("request_id") == request_id and e.get("record_type") == "approve":
            return e
    return None


def mint_approval(home, request, ttl_seconds, now, channel="unverified_direct"):
    """owner CLI 가 승인 발행 — ≥128-bit nonce 생성 후 EVENT append. request = get_request dict.
    ★ 반드시 CLI(대화형 TTY 검증 후)에서만 호출(이 모듈은 발행 도구를 MCP 에 노출하지 않는다).
    channel 은 호출부가 검증 후 명시 전달한다(CLI TTY = 'cli_tty', 테스트 = 'test_double'). 기본값은
    'unverified_direct' — 직접 import 등 미검증 발행이 'cli_tty' 로 거짓 라벨되지 않게(P1-A.1 · AOB-1)."""
    nonce = secrets.token_hex(16)  # 128-bit
    # ★ float time.time() → int 고정(must_fix 2 · 서명 canonicalization 결정성 · float repr 의존 0).
    #   이 정수 값으로만 record 구성·서명·저장한다. verify_event 는 float() 로 파싱하므로 int/float 무관 정합.
    approved_at = int(now)
    expires_at = int(now) + int(ttl_seconds)
    record = {"request_id": request["request_id"], "protocol_version": request["protocol_version"],
              "operation": request["operation"], "payload_digest": request["payload_digest"],
              "ledger_id": request["ledger_id"], "approval_nonce": nonce,
              "approved_at": approved_at, "expires_at": expires_at,
              "approver_channel": channel, "record_type": "approve"}
    # 서명 계층(keychain 등) 구성 시 record 서명(sig 부여). local_owner/None 은 sig 키 미부여 = byte-identical(무회귀).
    prov = provider_for(home)
    if prov is not None:
        from binggupack.safety.signing_provider import SIGNING_KINDS as _SIGNING_KINDS
        if getattr(prov, "kind", None) in _SIGNING_KINDS:
            record = prov.sign_record(record)   # Keychain: Ed25519 sig(hex). Unavailable: raise(fail-closed·무서명 append 0).
    append_event(home, record)
    return record


def tombstone(home, request, record_type, now, channel="unverified_direct"):
    """reject/revoke tombstone append(owner CLI 전용).

    P1-A.1: 기본 channel 은 mint 와 동일하게 'unverified_direct' — reject/revoke 는 approve 와 달리 isatty
    검증이 없으므로 'cli_tty' 라벨은 정직하지 않다(pipe 로도 tombstone 가능). deny-direction(write 부여
    아님)이라 보안 중립이나 라벨 정직성 원칙(approver_channel 은 감사 메타 · verify_event 미검증)을 지킨다."""
    append_event(home, {"request_id": request["request_id"], "record_type": record_type,
                        "at": now, "approver_channel": channel})


def verify_event(home, request_id, operation, payload_digest, ledger_identity, now,
                 protocol_version=PROTOCOL_VERSION):
    """EVENT store 조회 → 바인딩/TTL/clock 검증. {ok, reason|nonce}."""
    tomb, treason = is_tombstoned(home, request_id)
    if tomb:
        return {"ok": False, "reason": treason}
    appr = find_approve(home, request_id)
    if not appr:
        return {"ok": False, "reason": "approval_not_found"}
    if appr.get("operation") != operation:
        return {"ok": False, "reason": "binding_mismatch:operation"}
    if appr.get("payload_digest") != payload_digest:
        return {"ok": False, "reason": "binding_mismatch:payload"}
    if str(appr.get("ledger_id")) != str(ledger_identity):
        return {"ok": False, "reason": "binding_mismatch:ledger"}
    if appr.get("protocol_version") != protocol_version:
        return {"ok": False, "reason": "binding_mismatch:protocol"}
    # 서명 계층(keychain 등) 구성 시에만 approve EVENT 서명 검증. local_owner/None 은 skip(byte-identical·무회귀).
    # ★정직(§6): config kind 는 모델-writable 평문이라 kind:local_owner 한 줄로 이 분기가 통째 skip 된다
    #   (R-B6·P1-B2 out-of-scope). L2 실질 값은 hosted/locked 배포에서만 — 로컬 위조 차단 아님.
    prov = provider_for(home)
    if prov is not None:
        from binggupack.safety.signing_provider import SIGNING_KINDS as _SIGNING_KINDS
        if getattr(prov, "kind", None) in _SIGNING_KINDS:
            if not prov.verify_signed(appr).get("ok"):
                return {"ok": False, "reason": "binding_mismatch:signature"}   # fail-closed(Unavailable 도 여기로)
    try:
        approved_at = float(appr.get("approved_at"))
        expires_at = float(appr.get("expires_at"))
    except (TypeError, ValueError):
        return {"ok": False, "reason": "approval_time_invalid"}
    if now < approved_at:
        return {"ok": False, "reason": "approval_time_invalid"}   # clock 역행/future-date 차단
    if now > expires_at:
        return {"ok": False, "reason": "approval_expired"}
    return {"ok": True, "nonce": appr["approval_nonce"]}


# ── consume: reserve / finalize / release (db.con · §7·§14) ───────────────────────
def reserve(con, nonce, now):
    """atomic single-winner. 반환 status ∈ {reserved, already_consumed(+receipt), in_progress}.
    §7 PK-collision 단일 규칙 + reserved_at lease(TAE-P2-01)."""
    import sqlite3
    try:
        con.execute("INSERT INTO approval_consumptions(approval_nonce,request_id,state,reserved_at)"
                    " VALUES(?,?, 'consuming', ?)", (nonce, None, str(now)))
        con.commit()
        return {"status": "reserved"}
    except sqlite3.IntegrityError:
        pass
    row = con.execute("SELECT state,reserved_at,receipt FROM approval_consumptions"
                      " WHERE approval_nonce=?", (nonce,)).fetchone()
    if row is None:
        # 극히 드문 race(삭제 사이) — 한 번 더 시도.
        try:
            con.execute("INSERT INTO approval_consumptions(approval_nonce,state,reserved_at)"
                        " VALUES(?, 'consuming', ?)", (nonce, str(now)))
            con.commit()
            return {"status": "reserved"}
        except sqlite3.IntegrityError:
            return {"status": "in_progress"}
    state, reserved_at, receipt = row
    if state == "consumed":
        return {"status": "already_consumed", "receipt": receipt}
    # state == 'consuming'
    try:
        age = now - float(reserved_at or 0)
    except (TypeError, ValueError):
        age = 0
    if age <= LEASE_SECONDS:
        return {"status": "in_progress"}
    # lease 만료 → atomic takeover
    cur = con.execute("UPDATE approval_consumptions SET reserved_at=? WHERE approval_nonce=?"
                      " AND state='consuming' AND reserved_at=?", (str(now), nonce, reserved_at))
    con.commit()
    if cur.rowcount == 1:
        return {"status": "reserved", "takeover": True}
    return {"status": "in_progress"}


def finalize_consumed(con, nonce, request_id, receipt, now, commit=True):
    """예약(consuming)을 consumed 로 확정 + receipt 기록. commit=False → con.commit() 생략
    (P1-B.1 crash-atomic bundle: mutation·finalize·audit 를 단일 COMMIT 경계 안에서 확정)."""
    con.execute("UPDATE approval_consumptions SET state='consumed', request_id=?, receipt=?, consumed_at=?"
                " WHERE approval_nonce=?", (request_id, json.dumps(receipt, ensure_ascii=False), str(now), nonce))
    if commit:
        con.commit()


def get_consumption(con, request_id):
    """★P1-B.1 Contract-8: request_id 로 consumed receipt 조회(source load 전 재시도 판정용).
    consumed 행이 있으면 {"receipt": <dict>} (nonce 미포함), 없으면 None. cross-ledger/unknown id 는
    이 ledger 의 approval_consumptions 에 없으므로 None → 호출자는 기존 fail-closed 경로 유지."""
    if not request_id:
        return None
    row = con.execute("SELECT receipt FROM approval_consumptions"
                      " WHERE request_id=? AND state='consumed'", (request_id,)).fetchone()
    if row is None:
        return None
    try:
        rc = json.loads(row[0]) if row[0] else {}
    except Exception:
        rc = {}
    if isinstance(rc, dict):
        rc.pop("nonce", None)
    return {"receipt": rc}


def release(con, nonce):
    """예약 해제(CONSUMING 만 삭제) — transient/hard-block 시 approval 재사용 가능하게(소각 0)."""
    con.execute("DELETE FROM approval_consumptions WHERE approval_nonce=? AND state='consuming'", (nonce,))
    con.commit()


def is_idempotent_done(reason, core_result):
    """core 반환이 '효과 이미 반영'인지(→CONSUMED) 판정. nothing_to_save 는 skipped_existing>0 만."""
    if reason in IDEMPOTENT_DONE_REASONS:
        return True
    if reason == "nothing_to_save":
        return (core_result.get("skipped_existing", 0) or 0) > 0 and not core_result.get("rejected")
    return False


def is_transient(reason):
    return reason in TRANSIENT_REASONS


def any_node_exists(con, node_ids):
    """node_ids 중 하나라도 ledger 에 실재하면 True(R2-02 duplicate 재조정용)."""
    for nid in (node_ids or []):
        try:
            if con.execute("SELECT 1 FROM nodes WHERE node_id=?", (nid,)).fetchone():
                return True
        except Exception:
            return True  # 조회 실패 시 보수적으로 존재로 간주(승인 소각 회피)
    return False


def derive_receipt(operation, payload, core_result, request_id):
    """replay 반환용 receipt. nonce 절대 미포함(TAE-6). APPLIED 시 core node_ids, 아니면 결정적 산출.
    항상 request_id 포함(비어있지 않은 안정값 보장 · TAE-P2-07)."""
    node_ids = core_result.get("node_ids") or []
    if not node_ids:
        nid = core_result.get("new_node_id") or core_result.get("node_id") or core_result.get("owner_node_id")
        if nid:
            node_ids = [nid]
    if not node_ids and operation in ("save_candidate",):
        node_ids = _derive_save_node_ids(payload)
    return {"request_id": request_id, "operation": operation,
            "node_ids": node_ids, "decision_id": core_result.get("decision_id")}


def _derive_save_node_ids(payload):
    try:
        from binggupack.capture import preview as cvp
        import sys
        _s = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts")
        if _s not in sys.path:
            sys.path.insert(0, _s)
        from openbinggu_conversation_candidate_save import _sent_hash
        cands = cvp.capture_preview(payload.get("text", ""),
                                    explicit=bool(payload.get("explicit", False)))["candidates"]
        out = []
        for i in (payload.get("indices") or []):
            if isinstance(i, int) and 1 <= i <= len(cands):
                out.append("node:CONV:" + _sent_hash(cands[i - 1]["sentence"]))
        return out
    except Exception:
        return []


# selftest 는 scripts/openbinggu_trusted_approval_boundary_selftest.py 참조(회귀 등재).
