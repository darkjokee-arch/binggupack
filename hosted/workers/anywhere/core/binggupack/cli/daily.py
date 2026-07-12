# -*- coding: utf-8 -*-
"""Daily console — `binggu` 홈 화면 + 통합 `binggu inbox` (read-only presentation).

읽기 전용 표현 계층. ledger/보조 DB/staging 을 **mode=ro URI 또는 파일 존재 확인**으로만 조회하고
어떤 write 도 하지 않는다 — makedirs·schema migration·snapshot·audit append·use_count++·capture purge·
네트워크 fetch 0. 저장/승인/교체/폐기/동기화의 mutation core 는 기존 명령이 그대로 담당한다.

향후 Binggu Studio/TUI 가 동일 snapshot(collect_*_snapshot)을 재사용할 수 있도록 순수 함수로 분리했다.

읽기 소스(전부 로컬·네트워크 0):
  - 노드/감사/승인/검토: ledger.sqlite (mode=ro · 없으면 온보딩)
  - 자동 수집 후보: capture_buffer.sqlite (mode=ro · render_preview 의 purge 경로 미사용)
  - 원격 저장 의도: <home>/hosted_inbox/*.json (binggu_hosted_inbox.summarize · fetch 0)
  - capture/provider/preflight 상태: 로컬 플래그 파일 + trusted_approval.json(화이트리스트)
"""
import json
import os
import re
import sqlite3
import time
import unicodedata

SCHEMA_VERSION = 1

_EXCERPT = 72        # 홈/인박스 발췌 상한(원문 전문 미출력)
_DUE_EXCERPT = 50    # 검토 예정 항목 발췌 상한

# package 모듈에서 scripts/ 재사용(trusted_approval 관례와 동일). 지연 import 전 1회만 path 주입.
_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCRIPTS = os.path.join(_PKG_ROOT, "scripts")


def _ensure_scripts_path():
    import sys
    if _SCRIPTS not in sys.path:
        sys.path.insert(0, _SCRIPTS)


# ── read-only primitives ─────────────────────────────────────────────────────────
def _home_of(ledger):
    """ledger 경로 → home 디렉토리(= MCP _operating_home / _approval_home 과 동일 규약)."""
    return os.path.dirname(os.path.abspath(ledger))


def _ro_uri(path):
    return "file:%s?mode=ro" % os.path.abspath(path).replace("\\", "/")


def _ro_connect(path):
    """존재하는 파일만 mode=ro 로 연다(write 시 OperationalError). 부재/불가 → None(생성 0)."""
    if not os.path.exists(path):
        return None
    try:
        return sqlite3.connect(_ro_uri(path), uri=True)
    except Exception:
        return None


def _table_exists(con, name):
    try:
        return con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None
    except Exception:
        return False


def _scalar(con, sql, params=()):
    try:
        row = con.execute(sql, params).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except Exception:
        return 0


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _today(now):
    return time.strftime("%Y-%m-%d", time.localtime(now))


# ── text safety: 제어/bidi 제거 + 공백 정규화 + 절단(§10) ─────────────────────────
def safe_excerpt(text, cap=_EXCERPT):
    """제어/format(bidi 포함) codepoint 제거 · 공백 정규화 · cap 절단(…). 원문 전문/raw control 미출력."""
    if not text:
        return ""
    s = unicodedata.normalize("NFC", str(text))
    out = []
    for ch in s:
        if ch in ("\t", "\n", "\r"):
            out.append(" ")
            continue
        if unicodedata.category(ch) in ("Cc", "Cf"):   # control/format(bidi override·isolate 포함)
            continue
        out.append(ch)
    s = re.sub(r"\s+", " ", "".join(out)).strip()
    if len(s) > cap:
        s = s[:cap].rstrip() + "…"
    return s


# ── audit chain 무결성(read-only · StagingDB.verify_chain 로직 재사용) ─────────────
class _RoShim:
    """StagingDB.verify_chain 은 self.con 의 SELECT 만 쓴다. mode=ro con 을 주입해
    __init__(schema apply·commit) 없이 chain 검증 로직만 그대로 재사용한다."""

    def __init__(self, con):
        self.con = con


def _audit_status(con):
    """audit_log chain 무결성. 테이블/앵커 없음(신규·레거시) → INTACT. 검증 불가 → UNKNOWN."""
    if not _table_exists(con, "audit_log"):
        return "INTACT"
    try:
        _ensure_scripts_path()
        from openbinggu_staging_write_selftest import StagingDB
        return "INTACT" if StagingDB.verify_chain(_RoShim(con)) else "BROKEN"
    except Exception:
        return "UNKNOWN"


# ── 서비스 상태(로컬 플래그 · write 0) ─────────────────────────────────────────────
def _capture_status(home):
    """capture_enabled 존재 AND paused 없음 AND sticky-disabled 없음 → on. (CaptureScope.enabled 규약)"""
    if os.path.exists(os.path.join(home, "capture_disabled")):
        return "off"
    on = (os.path.exists(os.path.join(home, "capture_enabled"))
          and not os.path.exists(os.path.join(home, "capture_paused")))
    return "on" if on else "off"


def _provider_status(home):
    """trusted approval provider 구성 여부(load_config 화이트리스트 · 시크릿 미노출)."""
    try:
        _ensure_scripts_path()
        from binggupack.safety import trusted_approval as ta
        return "on" if ta.load_config(home) is not None else "off"
    except Exception:
        # 폴백: 파일 존재 + enabled True (원문/키 미출력)
        try:
            p = os.path.join(home, "trusted_approval.json")
            if not os.path.exists(p):
                return "off"
            with open(p, "r", encoding="utf-8") as f:
                return "on" if json.load(f).get("enabled") is True else "off"
        except Exception:
            return "off"


def _preflight_status(home):
    return "on" if os.path.exists(os.path.join(home, "preflight_enabled")) else "off"


# ── 큐 카운트/아이템(전부 read-only) ───────────────────────────────────────────────
def _capture_ttl_days():
    try:
        _ensure_scripts_path()
        from binggu_capture_persist import DEFAULT_TTL_DAYS
        return DEFAULT_TTL_DAYS
    except Exception:
        return 7   # 정본 DEFAULT_TTL_DAYS 와 동일(import 실패 시 폴백)


def _capture_items(home, now):
    """capture_buffer.sqlite mode=ro. render_preview 의 생존자(비만료) 정렬(pinned DESC, id ASC)을
    그대로 재현 — inbox 번호 == `binggu capture preview` 번호. purge(DELETE) 경로 미사용."""
    db_path = os.path.join(home, "capture_buffer.sqlite")
    con = _ro_connect(db_path)
    if con is None:
        return []
    try:
        if not _table_exists(con, "capture_candidates"):
            return []
        cutoff = now - _capture_ttl_days() * 86400
        try:
            rows = con.execute(
                "SELECT text, pinned FROM capture_candidates WHERE captured_at >= ? "
                "ORDER BY pinned DESC, id ASC", (cutoff,)).fetchall()
        except Exception:
            return []
    finally:
        con.close()
    items = []
    for i, (text, pinned) in enumerate(rows, 1):
        items.append({"idx": i, "preview": safe_excerpt(text), "pinned": bool(pinned)})
    return items


def _hosted_summary(home, now):
    """<home>/hosted_inbox/*.json read-only 요약(fetch 0). summarize 는 os.path.isdir 가드·write 0."""
    try:
        _ensure_scripts_path()
        from binggu_hosted_inbox import summarize, staging_dir_for
        return summarize(staging_dir_for(home), int(now))
    except Exception:
        return {"count": 0, "items": [], "total": 0}


def _hosted_items(home, now):
    summ = _hosted_summary(home, now)
    items = []
    for it in summ.get("items", []):
        iid = it.get("intent_id") or ""
        items.append({
            "idx": it.get("idx"),
            "intent_id": iid[:8],
            "preview": safe_excerpt(it.get("excerpt") or ""),
            "text_sha": it.get("text_sha"),
            "age_days": it.get("age_days"),
            "expired": bool(it.get("expired")),
            "pii_secret": bool(it.get("pii_secret")),
            "candidates": it.get("n_candidates", 0),
        })
    return items


def _approval_items(con):
    """approval_requests 의 PENDING 만(payload-agnostic summary · raw payload 0 · nonce 0)."""
    if con is None or not _table_exists(con, "approval_requests"):
        return []
    try:
        rows = con.execute(
            "SELECT request_id, operation, summary, expires_at FROM approval_requests "
            "WHERE state='pending' ORDER BY created_at DESC").fetchall()
    except Exception:
        return []
    # request_id 는 공개 digest(nonce 아님) — 후속 명령(approval show/approve)에 필요하므로 전문 유지.
    return [{"request_id": r[0], "operation": r[1],
             "summary": safe_excerpt(r[2], 96), "expires_at": r[3]} for r in rows]


def _due_items(con, now):
    """judgment_reviews 의 due 경과(pending·due_date<=today) — list_due_reminders 정본 재현(read-only)."""
    if con is None or not _table_exists(con, "judgment_reviews"):
        return []
    try:
        _ensure_scripts_path()
        from openbinggu_candidate_list_view import node_id8
    except Exception:
        node_id8 = lambda x: (x or "")[:8]  # noqa: E731
    try:
        rows = con.execute(
            "SELECT r.node_id, r.due_date, n.sentence FROM judgment_reviews r "
            "JOIN nodes n ON n.node_id = r.node_id "
            "WHERE r.status='pending' AND r.due_date <= ? ORDER BY r.due_date",
            (_today(now),)).fetchall()
    except Exception:
        return []
    return [{"id": node_id8(nid), "due_date": due, "preview": safe_excerpt(sent, _DUE_EXCERPT)}
            for nid, due, sent in rows]


# ── 스냅샷(순수 read model) ────────────────────────────────────────────────────────
def collect_home_snapshot(ledger, now=None):
    """홈 화면 read model. ledger 없으면 온보딩(생성 0). 어떤 write 도 하지 않는다."""
    now = time.time() if now is None else now
    home = _home_of(ledger)
    exists = os.path.exists(ledger)
    snap = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _iso(now),
        "ledger": {"exists": exists, "active": 0, "deprecated": 0, "audit": "UNKNOWN"},
        "services": {
            "capture": _capture_status(home),
            "approval_provider": _provider_status(home),
            "preflight": _preflight_status(home),
        },
        "queues": {"capture": 0, "hosted": 0, "approvals": 0, "due": 0},
        "next_actions": [],
    }
    if exists:
        con = _ro_connect(ledger)
        if con is not None:
            try:
                if _table_exists(con, "nodes"):
                    snap["ledger"]["active"] = _scalar(
                        con, "SELECT COUNT(*) FROM nodes WHERE state='active'")
                    snap["ledger"]["deprecated"] = _scalar(
                        con, "SELECT COUNT(*) FROM nodes WHERE state='deprecated'")
                snap["ledger"]["audit"] = _audit_status(con)
                if _table_exists(con, "approval_requests"):
                    snap["queues"]["approvals"] = _scalar(
                        con, "SELECT COUNT(*) FROM approval_requests WHERE state='pending'")
                snap["queues"]["due"] = len(_due_items(con, now))
            finally:
                con.close()
        else:
            snap["ledger"]["audit"] = "UNKNOWN"
    else:
        snap["ledger"]["audit"] = "NONE"
    snap["queues"]["capture"] = len(_capture_items(home, now))
    snap["queues"]["hosted"] = _hosted_summary(home, now).get("total", 0)
    # Local Fresh Index 상태(읽기전용 peek · 생성/write 0). stale = 색인 노드수 != ledger active.
    try:
        from binggupack.pack import fresh_index as _FI
        ix = _FI.peek(home)
    except Exception:
        ix = {"exists": False}
    if ix.get("exists"):
        ix["stale"] = ix.get("active") != snap["ledger"]["active"]
    else:
        ix["stale"] = exists  # ledger 는 있는데 색인이 없으면 갱신 필요
    snap["index"] = ix
    snap["next_actions"] = _next_actions(snap)
    return snap


def collect_inbox_snapshot(ledger, sections=None, now=None):
    """통합 inbox read model. sections=None → 전 섹션. 로컬 스냅샷만(네트워크 fetch 0)."""
    now = time.time() if now is None else now
    home = _home_of(ledger)
    want = set(sections) if sections else {"capture", "hosted", "approvals", "due"}
    out = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _iso(now),
        "ledger": {"exists": os.path.exists(ledger)},
        "sections": {},
    }
    if "capture" in want:
        items = _capture_items(home, now)
        out["sections"]["capture"] = {"count": len(items), "items": items}
    if "hosted" in want:
        items = _hosted_items(home, now)
        out["sections"]["hosted"] = {"count": len(items), "items": items}
    if "approvals" in want or "due" in want:
        con = _ro_connect(ledger)
        try:
            if "approvals" in want:
                items = _approval_items(con)
                out["sections"]["approvals"] = {"count": len(items), "items": items}
            if "due" in want:
                items = _due_items(con, now)
                out["sections"]["due"] = {"count": len(items), "items": items}
        finally:
            if con is not None:
                con.close()
    return out


def _next_actions(snap):
    """우선순위: audit BROKEN → 승인 → 원격 → 후보 → 검토. (홈 배너/JSON 공용)"""
    acts = []
    if snap["ledger"]["audit"] == "BROKEN":
        acts.append({"kind": "audit", "count": 1, "command": "binggu doctor"})
    q = snap["queues"]
    order = [("approvals", "approval"), ("hosted", "hosted"),
             ("capture", "capture"), ("due", "due")]
    for qkey, kind in order:
        if q.get(qkey, 0) > 0:
            acts.append({"kind": kind, "count": q[qkey],
                         "command": "binggu inbox --%s" % qkey})
    return acts


# ── 렌더링(외부 UI dependency 0 · box + plain fallback) ────────────────────────────
def _disp_width(s):
    w = 0
    for ch in s:
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def _pad(s, width):
    return s + " " * max(0, width - _disp_width(s))


def _boxed(title, content_lines, unicode_ok):
    inner = max([_disp_width(t) for t in content_lines] + [_disp_width(title) + 4]) + 2
    if unicode_ok:
        dash = "─" * (inner - _disp_width(title) - 3)
        top = "╭─ %s %s╮" % (title, dash)
        body = ["│ %s │" % _pad(t, inner - 2) for t in content_lines]
        bot = "╰%s╯" % ("─" * inner)
    else:
        dash = "-" * (inner - _disp_width(title) - 3)
        top = "+- %s %s+" % (title, dash)
        body = ["| %s |" % _pad(t, inner - 2) for t in content_lines]
        bot = "+%s+" % ("-" * inner)
    return [top] + body + [bot]


def _warn(msg, unicode_ok):
    return ("⚠ " if unicode_ok else "! ") + msg


def _arrow(unicode_ok):
    return "→" if unicode_ok else "->"


_KIND_LABEL = {
    "approval": "승인 요청 %d개",
    "hosted": "원격 저장 의도 %d개",
    "capture": "자동 수집 후보 %d개",
    "due": "검토 예정 %d개",
}


def _render_onboarding():
    return "\n".join([
        "BingguPack 에 오신 것을 환영합니다.",
        "",
        "아직 로컬 기억 장부가 없습니다.",
        "",
        "시작:",
        "  binggu start",
        "",
        "60초 체험:",
        "  binggu demo",
    ])


def render_home_text(snap, unicode_ok=True):
    if not snap["ledger"]["exists"]:
        return _render_onboarding()
    L, Q, S = snap["ledger"], snap["queues"], snap["services"]
    counts = "기억 %d  후보 %d  원격 %d  승인 %d  검토 %d" % (
        L["active"], Q["capture"], Q["hosted"], Q["approvals"], Q["due"])
    status = "장부 %s  capture %s  provider %s" % (
        L["audit"], S["capture"].upper(), S["approval_provider"].upper())
    ix = snap.get("index", {})
    if ix.get("exists"):
        ix_line = "색인 %s  (기억 %d 색인됨)" % (
            "갱신필요" if ix.get("stale") else "최신", ix.get("active", 0))
    else:
        ix_line = "색인 없음 (첫 회상 시 자동 생성)"
    lines = _boxed("BingguPack", [counts, status, ix_line], unicode_ok)
    lines.append("")
    ar = _arrow(unicode_ok)
    if L["audit"] == "BROKEN":
        lines.append(_warn("장부 무결성 점검 필요", unicode_ok))
        lines.append("  %s binggu doctor" % ar)
        lines.append("")
    if ix.get("stale"):
        lines.append(_warn("Local Fresh Index 갱신 필요(변경분 미반영)", unicode_ok))
        lines.append("  %s binggu index update" % ar)
        lines.append("")
    queue_acts = [a for a in snap["next_actions"] if a["kind"] != "audit"]
    if queue_acts:
        lines.append("다음 할 일")
        lines.append("")
        for i, a in enumerate(queue_acts, 1):
            lines.append("%d. %s" % (i, _KIND_LABEL[a["kind"]] % a["count"]))
            lines.append("   %s %s" % (ar, a["command"]))
        lines.append("")
    else:
        lines.append("지금 처리할 항목이 없습니다. 장부는 최신 상태예요.")
        lines.append("")
    lines.append("기억 찾기:")
    lines.append('  binggu recall "질문"')
    lines.append("")
    lines.append("전체 검토함:")
    lines.append("  binggu inbox")
    return "\n".join(lines)


def _render_capture_section(sec, unicode_ok):
    ar = _arrow(unicode_ok)
    out = ["자동 수집 후보 %d개" % sec["count"]]
    for it in sec["items"]:
        pin = " [PINNED]" if it.get("pinned") else ""
        out.append("  [%d] %s%s" % (it["idx"], it["preview"], pin))
    out.append("  %s 확인·저장:  binggu capture preview" % ar)
    return out


def _render_hosted_section(sec, unicode_ok):
    ar = _arrow(unicode_ok)
    out = ["원격 저장 의도 %d개 (로컬 staging · fetch 0)" % sec["count"]]
    for it in sec["items"]:
        flag = ""
        if it.get("pii_secret"):
            flag += " ⚠PII/secret" if unicode_ok else " !PII/secret"
        if it.get("expired"):
            flag += " ⚠만료" if unicode_ok else " !만료"
        age = ("%.1f일 전" % it["age_days"]) if it.get("age_days") is not None else "?"
        out.append("  [%d] %s | sha %s | %s | 후보 %d%s" % (
            it["idx"], it["preview"], it.get("text_sha") or "?", age,
            it.get("candidates", 0), flag))
    out.append("  %s 새로 가져오기:  binggu hosted inbox" % ar)
    out.append("  %s 저장(선택):     binggu hosted pull --select <번호>" % ar)
    return out


def _render_approvals_section(sec, unicode_ok):
    ar = _arrow(unicode_ok)
    out = ["승인 요청 %d개 (PENDING)" % sec["count"]]
    for it in sec["items"]:
        out.append("  · %s | %s | %s | 만료 %s" % (
            it["request_id"], it["operation"], it["summary"], it.get("expires_at") or "?"))
    out.append("  %s 내용 확인:  binggu approval show <요청ID>" % ar)
    out.append("  %s 승인:       binggu approval approve <요청ID>" % ar)
    return out


def _render_due_section(sec, unicode_ok):
    ar = _arrow(unicode_ok)
    out = ["검토 예정 %d개" % sec["count"]]
    for it in sec["items"]:
        out.append("  [%s] (%s 예정) %s" % (it["id"], it["due_date"], it["preview"]))
    # 안내 명령은 인자 없이 동작하는 정본(reminders) — 표시 short id 를 그대로 소비할 명령이 없으므로
    # 구체 id ↔ 잘못된 명령을 짝짓지 않는다(reminders 가 due 목록 + 결과 입력 방법을 안내).
    out.append("  %s 검토 목록:  binggu reminders" % ar)
    return out


_SECTION_RENDER = {
    "capture": _render_capture_section,
    "hosted": _render_hosted_section,
    "approvals": _render_approvals_section,
    "due": _render_due_section,
}
_SECTION_ORDER = ["capture", "hosted", "approvals", "due"]


def render_inbox_text(snap, unicode_ok=True):
    secs = snap.get("sections", {})
    total = sum(secs.get(k, {}).get("count", 0) for k in secs)
    if total == 0:
        return "\n".join(["검토할 항목이 없습니다.", "장부는 최신 상태입니다."])
    blocks = []
    for key in _SECTION_ORDER:
        if key in secs:
            blocks.append("\n".join(_SECTION_RENDER[key](secs[key], unicode_ok)))
    return "\n\n".join(blocks)


# ── stdout 어댑터(binggu.py dispatch 에서 1줄 호출) ────────────────────────────────
def _stdout_unicode_ok():
    import sys
    enc = (getattr(sys.stdout, "encoding", None) or "").lower()
    if not enc:
        return False
    try:
        "╭─╮│╰╯⚠→✓".encode(enc)
        return True
    except Exception:
        return False


def print_home(ledger, as_json=False, now=None):
    snap = collect_home_snapshot(ledger, now=now)
    if as_json:
        print(json.dumps(snap, ensure_ascii=False, indent=2))
    else:
        print(render_home_text(snap, unicode_ok=_stdout_unicode_ok()))
    return 0


def print_inbox(ledger, sections=None, as_json=False, now=None):
    snap = collect_inbox_snapshot(ledger, sections=sections, now=now)
    if as_json:
        print(json.dumps(snap, ensure_ascii=False, indent=2))
    else:
        print(render_inbox_text(snap, unicode_ok=_stdout_unicode_ok()))
    return 0
