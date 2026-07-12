# -*- coding: utf-8 -*-
"""Studio Approval Center read model — approval request 목록/상세를 read-only 로 해석한다.

데이터 소스(전부 read-only): ledger.sqlite(approval_requests · approval_consumptions · nodes) +
<home>/approvals.jsonl(EVENT store · read_events) + <home>/approval_review/<rid>.json(owner review).
어떤 write 도 하지 않는다 — open_g3/apply_schema/migration/makedirs/append_event/mint_approval/
tombstone/purge_review/reserve/finalize_consumed/release/audit append 0. SQLite 는 daily 의 mode=ro
helper 만 쓴다. 승인 semantics 는 기존 trusted approval verifier/consumption 을 그대로 해석(신규 0).

시각은 now 주입 가능(deterministic test) · wall-clock magic offset(±N) 없음.
nonce/raw event/절대경로/provider config/payload 전문은 응답에 절대 포함하지 않는다.
"""
import calendar
import json
import os
import re
import stat
import time

from binggupack.cli import daily                     # _ro_connect · _table_exists · safe_excerpt · _home_of
from binggupack.studio import read_model             # node_id8 · normalize_text · ValidationError · parse_int
from binggupack.safety import trusted_approval as ta  # read_events/is_tombstoned/find_approve/_review_path(순수 read)

SCHEMA_VERSION = 1
LIST_LIMIT_MAX = 100
LIST_LIMIT_DEFAULT = 30
REQUEST_ID_MAX = 300

_REVIEW_MAX_BYTES = 256 * 1024        # review 파일 크기 상한(oversized 거부)
_LABEL_CAP = 80
_VALUE_CAP = 160
_DIGEST_FP = 12                        # payload_digest 표시용 short fingerprint 길이
# request_id 형식(경로 순회 문자 배제) — DB canonical id 를 filesystem path 에 넣기 전 방어.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_:.\-]+$")

LIST_STATES = ("pending", "approved", "consuming", "consumed", "rejected", "revoked", "expired", "all")
_COUNT_STATES = ("pending", "approved", "consuming", "consumed", "rejected", "revoked", "expired")
# consumption.state → effective 우선순위(consumed/consuming 만 effective 로 승격 · reserved 는 미승격).
_RECEIPT_ALLOWED = ("request_id", "operation", "node_ids", "decision_id", "consumed_at")


def _now(now):
    return time.time() if now is None else now


def _as_ts(v):
    """epoch 숫자(approve event) 또는 ISO UTC 문자열(approval_requests._iso) → epoch float. 불명 → None."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    try:
        return float(calendar.timegm(time.strptime(str(v), "%Y-%m-%dT%H:%M:%SZ")))
    except (TypeError, ValueError):
        return None


def validate_list_params(state, limit, offset):
    if state not in LIST_STATES:
        raise read_model.ValidationError("state", "state must be one of %s" % "|".join(LIST_STATES))
    if not (1 <= limit <= LIST_LIMIT_MAX):
        raise read_model.ValidationError("limit", "limit must be 1..%d" % LIST_LIMIT_MAX)
    if offset < 0:
        raise read_model.ValidationError("offset", "offset must be >= 0")


def validate_request_id(raw):
    rid = read_model.normalize_text(raw)
    if "\x00" in (raw or ""):
        raise read_model.ValidationError("request_id", "must not contain NUL")
    if not rid:
        raise read_model.ValidationError("request_id", "request_id is required")
    if len(rid) > REQUEST_ID_MAX:
        raise read_model.ValidationError("request_id", "request_id too long")
    return rid


# ── ledger(mode=ro) 조회 ───────────────────────────────────────────────────────────
def _get_request(con, request_id):
    """approval_requests exact 조회(get_request 와 동일 컬럼 · mode=ro con)."""
    try:
        r = con.execute(
            "SELECT request_id,protocol_version,operation,payload_digest,ledger_id,summary,"
            "state,created_at,expires_at FROM approval_requests WHERE request_id=?",
            (request_id,)).fetchone()
    except Exception:
        return None
    if not r:
        return None
    return {"request_id": r[0], "protocol_version": r[1], "operation": r[2], "payload_digest": r[3],
            "ledger_id": r[4], "summary": r[5], "state": r[6], "created_at": r[7], "expires_at": r[8]}


def _get_consumption(con, request_id):
    if not daily._table_exists(con, "approval_consumptions"):
        return None
    try:
        r = con.execute(
            "SELECT state, receipt, consumed_at FROM approval_consumptions WHERE request_id=? "
            "ORDER BY (state='consumed') DESC, (state='consuming') DESC LIMIT 1",
            (request_id,)).fetchone()
    except Exception:
        return None
    if not r:
        return None
    return {"state": r[0], "receipt": r[1], "consumed_at": r[2]}


def _node_exists(con, node_id):
    try:
        return con.execute("SELECT 1 FROM nodes WHERE node_id=? LIMIT 1", (node_id,)).fetchone() is not None
    except Exception:
        return False


# ── EVENT store(read-only) 안전 projection ─────────────────────────────────────────
def _events_for(events, request_id):
    approve = None
    tomb_kind = None
    for e in events:
        if not isinstance(e, dict) or e.get("request_id") != request_id:
            continue
        rt = e.get("record_type")
        if rt == "approve" and approve is None:
            approve = e
        elif rt in ("reject", "revoke"):
            tomb_kind = rt
    return approve, tomb_kind


def _effective_state(now, req, approve, tomb_kind, consumption):
    """기존 verifier/consumption 을 read-only 해석(신규 semantics 0). 우선순위: consumed → consuming →
    rejected/revoked → approved(미소비·미만료) → expired → pending. consumed 는 expiry 로 덮지 않는다."""
    cstate = consumption.get("state") if consumption else None
    if cstate == "consumed":
        return "consumed"
    if cstate == "consuming":
        return "consuming"
    if tomb_kind == "reject":
        return "rejected"
    if tomb_kind == "revoke":
        return "revoked"
    req_exp = _as_ts(req.get("expires_at"))
    if approve:
        # 실제 소비 게이트(verify_event)는 approve EVENT 의 expires_at 만 검사한다 — request-row 만료는
        # 소비 semantics 에 영향 0(CLI approve 가 만료 request 도 fresh TTL 로 mint). request-row 만료로
        # 승인건을 expired 로 표기하면 소비 가능한 승인을 owner 가 죽은 것으로 오인하므로, approve event
        # 의 expires_at 만으로 approved/expired 를 판정한다(request-row 만료는 pending 단계에만 적용).
        appr_exp = _as_ts(approve.get("expires_at"))
        if appr_exp is not None and now > appr_exp:
            return "expired"
        return "approved"
    if req_exp is not None and now > req_exp:
        return "expired"
    return "pending"


def _has_review_file(home, request_id):
    if not _REQUEST_ID_RE.match(request_id):
        return False
    p = ta._review_path(home, request_id)
    try:
        return os.path.isfile(p) and not os.path.islink(p)
    except OSError:
        return False


def _count_from_summary(summary):
    if not summary:
        return None
    m = re.search(r"(\d+)\s*(intent|item|edge|엣지|건)", str(summary))
    return int(m.group(1)) if m else None


# ── snapshots ────────────────────────────────────────────────────────────────────
def collect_approval_list_snapshot(ledger, state="all", operation=None,
                                  limit=LIST_LIMIT_DEFAULT, offset=0, now=None):
    now = _now(now)
    home = daily._home_of(ledger)
    out = {"schema_version": SCHEMA_VERSION, "total": 0, "offset": offset, "limit": limit,
           "items": [], "filters": {"state": state, "operation": operation},
           "summary_counts": {s: 0 for s in _COUNT_STATES}}
    con = daily._ro_connect(ledger)
    if con is None:
        return out
    try:
        if not daily._table_exists(con, "approval_requests"):
            return out
        clause, params = "", []
        if operation:
            clause = " WHERE operation=?"
            params = [operation]
        try:
            rows = con.execute(
                "SELECT request_id,protocol_version,operation,payload_digest,ledger_id,summary,"
                "state,created_at,expires_at FROM approval_requests" + clause +
                " ORDER BY created_at DESC, request_id", params).fetchall()
        except Exception:
            return out
        events = _safe_events(home)
        all_items = []
        for r in rows:
            req = {"request_id": r[0], "protocol_version": r[1], "operation": r[2], "payload_digest": r[3],
                   "ledger_id": r[4], "summary": r[5], "state": r[6], "created_at": r[7], "expires_at": r[8]}
            approve, tomb_kind = _events_for(events, req["request_id"])
            consumption = _get_consumption(con, req["request_id"])
            eff = _effective_state(now, req, approve, tomb_kind, consumption)
            if eff in out["summary_counts"]:
                out["summary_counts"][eff] += 1
            req_exp = _as_ts(req["expires_at"])
            all_items.append({
                "request_id": req["request_id"], "display_id": read_model.node_id8(req["request_id"]),
                "operation": req["operation"], "summary": daily.safe_excerpt(req["summary"], 96),
                "db_state": req["state"], "effective_state": eff,
                "created_at": req["created_at"], "expires_at": req["expires_at"],
                "expired": bool(req_exp is not None and now > req_exp),
                "item_count": _count_from_summary(req["summary"]),
                "ledger_display_id": read_model.node_id8(str(req["ledger_id"] or "")),
                "has_review": _has_review_file(home, req["request_id"]),
                "has_receipt": bool(consumption and consumption.get("receipt")),
            })
        if state != "all":
            all_items = [it for it in all_items if it["effective_state"] == state]
        out["total"] = len(all_items)
        out["items"] = all_items[offset:offset + limit]
    finally:
        con.close()
    return out


def _safe_events(home):
    """approvals.jsonl read-only 파싱(malformed line skip · raw line 미노출). 파일 부재 → 빈 목록."""
    try:
        return ta.read_events(home)
    except Exception:
        return []


def _safe_review(home, req):
    """review 파일을 안전하게 읽는다(§8): DB canonical rid 만 사용 · symlink/비정규파일/oversize 거부 ·
    JSON object 만 · request_id/operation/payload_digest == DB 무결성. 위반 시 items 미반환."""
    rid = req["request_id"]
    if not _REQUEST_ID_RE.match(rid):
        return {"available": False, "integrity": "invalid_id"}
    p = ta._review_path(home, rid)
    review_dir = os.path.realpath(ta.review_dir(home))
    try:
        if not os.path.exists(p):
            return {"available": False, "integrity": "unavailable_after_decision"}
        st = os.lstat(p)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            return {"available": False, "integrity": "mismatch"}
        if st.st_size > _REVIEW_MAX_BYTES:
            return {"available": False, "integrity": "mismatch"}
        # 경로 순회 방어(정규식 + realpath 가 review_dir 하위인지)
        if os.path.commonpath([os.path.realpath(p), review_dir]) != review_dir:
            return {"available": False, "integrity": "mismatch"}
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {"available": False, "integrity": "mismatch"}
    if not isinstance(data, dict):
        return {"available": False, "integrity": "mismatch"}
    if (data.get("request_id") != req["request_id"]
            or data.get("operation") != req["operation"]
            or data.get("payload_digest") != req["payload_digest"]):
        return {"available": False, "integrity": "mismatch"}
    items = []
    for it in (data.get("items") or []):
        if not isinstance(it, dict):
            continue
        items.append({"label": daily.safe_excerpt(str(it.get("label", "")), _LABEL_CAP),
                      "value": daily.safe_excerpt(str(it.get("value", "")), _VALUE_CAP)})
    return {"available": True, "integrity": "matched", "items": items}


def _timeline(req, events):
    """request_created + approve/reject/revoke event(허용 필드만 · nonce/digest/ledger_id 제거)."""
    tl = [{"kind": "request_created", "at": req["created_at"], "channel": None}]
    for e in events:
        if not isinstance(e, dict) or e.get("request_id") != req["request_id"]:
            continue
        rt = e.get("record_type")
        if rt == "approve":
            tl.append({"kind": "approved", "at": e.get("approved_at"), "channel": e.get("approver_channel")})
        elif rt == "reject":
            tl.append({"kind": "rejected", "at": e.get("at"), "channel": e.get("approver_channel")})
        elif rt == "revoke":
            tl.append({"kind": "revoked", "at": e.get("at"), "channel": e.get("approver_channel")})
    return tl


def _consumption_view(con, consumption):
    """approval_consumptions 를 안전 해석(§9): receipt 는 whitelist projection(nonce/actor/unknown 제거) ·
    node_ids 는 full+display+dangling. 파싱 실패 → receipt_available=false(raw 미노출)."""
    if consumption is None:
        return {"state": None, "consumed_at": None, "receipt_available": False}
    out = {"state": consumption.get("state"), "consumed_at": consumption.get("consumed_at"),
           "receipt_available": False}
    raw = consumption.get("receipt")
    if not raw:
        return out
    try:
        rec = raw if isinstance(raw, dict) else json.loads(raw)
    except Exception:
        return out
    if not isinstance(rec, dict):
        return out
    proj = {k: rec.get(k) for k in _RECEIPT_ALLOWED if k in rec}
    nodes = []
    for nid in (rec.get("node_ids") or []):
        if not isinstance(nid, str):
            continue
        nodes.append({"node_id": nid, "display_id": read_model.node_id8(nid),
                      "dangling": not _node_exists(con, nid)})
    out["receipt_available"] = True
    out["receipt"] = {"operation": proj.get("operation"), "decision_id": proj.get("decision_id"),
                      "consumed_at": proj.get("consumed_at"), "nodes": nodes}
    return out


def collect_approval_detail_snapshot(ledger, request_id, now=None):
    """full request_id exact 상세(mode=ro). 없으면 None(→404). nonce/raw event/절대경로/provider config
    /payload 전문 미노출. review 무결성 검증 + receipt whitelist projection."""
    now = _now(now)
    home = daily._home_of(ledger)
    con = daily._ro_connect(ledger)
    if con is None:
        return None
    try:
        if not daily._table_exists(con, "approval_requests"):
            return None
        req = _get_request(con, request_id)
        if req is None:
            return None
        events = _safe_events(home)
        approve, tomb_kind = _events_for(events, req["request_id"])
        consumption = _get_consumption(con, req["request_id"])
        eff = _effective_state(now, req, approve, tomb_kind, consumption)
        req_exp = _as_ts(req["expires_at"])
        digest = str(req["payload_digest"] or "")
        detail = {
            "schema_version": SCHEMA_VERSION,
            "request": {
                "request_id": req["request_id"], "display_id": read_model.node_id8(req["request_id"]),
                "protocol_version": req["protocol_version"], "operation": req["operation"],
                "payload_digest_display": (digest[:_DIGEST_FP] + "…") if len(digest) > _DIGEST_FP else digest,
                "ledger_display_id": read_model.node_id8(str(req["ledger_id"] or "")),
                "summary": daily.safe_excerpt(req["summary"], 96), "db_state": req["state"],
                "effective_state": eff, "created_at": req["created_at"], "expires_at": req["expires_at"],
                "expired": bool(req_exp is not None and now > req_exp),
            },
            "review": _safe_review(home, req),
            "timeline": _timeline(req, events),
            "consumption": _consumption_view(con, consumption),
            "commands": {
                "show": "binggu approval show %s" % req["request_id"],
                "approve": "binggu approval approve %s" % req["request_id"],
                "reject": "binggu approval reject %s" % req["request_id"],
                "revoke": "binggu approval revoke %s" % req["request_id"],
            },
        }
    finally:
        con.close()
    return detail
