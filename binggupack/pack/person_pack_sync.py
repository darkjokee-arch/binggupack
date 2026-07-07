# -*- coding: utf-8 -*-
"""사람축(owner) 온톨로지 팩 자동 동기화 — 세션 자동 갱신용.

owner 결정(2026-07-02): 사용자 온톨로지를 opencrab 팩으로 올리고 지속 자동 업데이트.
Agent 경유 업로드가 harness 통과(무인 스케줄러/시크릿파일은 auto mode 차단) →
매 세션 시작/handoff 때 이 스크립트로 변경 감지 후 변경 시에만 Claude 가 Agent 로
opencrab_pack_update 를 호출한다. 실제 클라우드 호출은 MCP(Agent) — hook 은 불가.

파이프:
  1. ledger 의 speaker=owner active 노드 → T3 필터(PII·과거사 하드제외) → pack text.
  2. 이전 스냅샷(person_pack_last.json)의 content_hash 와 비교.
  3. NO_CHANGE / UPDATE_NEEDED / BASELINE_SET 판정(JSON) 반환.
  4. Agent 가 업로드 성공하면 --confirm 으로 baseline 갱신(다음 변경 감지 기준).

read-only(ledger mode=ro). 상태파일만 write(운영 ledger 불변)."""
import argparse
import hashlib
import json
import os
import sqlite3
import sys

# strangler 정본: 경로/필터 정본을 패키지 경로로 직접 참조(모든 dep MIGRATED).
# binggu_paths → binggupack.paths(facade) · binggu_t3_filter → binggupack.safety.t3_filter.
# 두 정본이 자기 위치에서 scripts/ sys.path 를 자체 관리하므로 이 모듈의 __file__ 경유
# sys.path 삽입은 불필요(제거). 위임 심볼(home/ledger/state_path/filter_uploadable) 동일.
from binggupack import paths as binggu_paths
from binggupack.safety.t3_filter import filter_uploadable

# owner 온톨로지 팩(2026-07-02 최초 업로드) — 갱신 대상.
# 사용자별 일반화(우선순위): env BINGGU_PACK_ID/BINGGU_PACK_TITLE > <home>/person_pack.json
# ({"pack_id":..., "title":...}) > person_pack_last.json(운영 마커) 의 pack_id > sentinel(None).
# 하드코딩 owner UUID 를 제거 — owner 는 운영 마커(person_pack_last.json)에서 기존 pack_id 를
# 읽어 회귀 0, 신규 사용자(마커 부재)는 sentinel → pack_create_required/auto_create 경로로 위임.
STATE_FILE = "person_pack_last.json"
PACK_CONFIG_FILE = "person_pack.json"
DEFAULT_OWNER_LABEL = "사용자"   # 중립 화자 호칭(person_crab_sync._crab_meta 패턴) — 신규 사용자 기본


def _owner_label():
    """제목/헤더 화자 호칭: env BINGGU_OWNER_LABEL > person_pack.json owner_label > 중립 '사용자'.
    owner 는 person_pack.json owner_label='사장님' 이라 표시 유지, 신규 사용자는 중립값 노출."""
    lbl = os.environ.get("BINGGU_OWNER_LABEL")
    if not lbl:
        try:
            with open(binggu_paths.state_path(PACK_CONFIG_FILE), encoding="utf-8") as f:
                lbl = (json.load(f).get("owner_label") or "").strip() or None
        except Exception:  # 부재/손상 → 중립 기본값
            lbl = None
    return lbl or DEFAULT_OWNER_LABEL


def _pack_config():
    """pack_id/title 해석: env > person_pack.json(config) > person_pack_last.json(운영 마커) >
    sentinel(None). 하드코딩 owner UUID 제거 — owner 회귀 0 은 운영 마커의 pack_id 로,
    신규 사용자(마커 부재)는 sentinel 로 pack_create_required/auto_create 경로에 위임한다.
    title 기본값은 owner_label 기반 중립 문구('<호칭> 의사결정 원칙 온톨로지')."""
    pid = os.environ.get("BINGGU_PACK_ID")
    title = os.environ.get("BINGGU_PACK_TITLE")
    if not (pid and title):
        try:
            with open(binggu_paths.state_path(PACK_CONFIG_FILE), encoding="utf-8") as f:
                c = json.load(f)
            pid = pid or (c.get("pack_id") or None)
            title = title or (c.get("title") or None)
        except Exception:  # 부재/손상 → 다음 폴백
            pass
    if not pid:  # config 에 없으면 운영 마커(person_pack_last.json)의 pack_id 로 폴백(owner 회귀 0)
        try:
            with open(binggu_paths.state_path(STATE_FILE), encoding="utf-8") as f:
                pid = (json.load(f).get("pack_id") or "").strip() or None
        except Exception:  # 부재/손상 → sentinel(None)
            pass
    return (pid, title or ("%s 의사결정 원칙 온톨로지" % _owner_label()))


PACK_ID, PACK_TITLE = _pack_config()


def pack_create_required():
    """신규 사용자 흐름 — env 미설정 + config 파일이 auto_create=true & pack_id 빈값이면
    '팩 생성 필요'(owner 기본값으로 업로드하는 오타겟 차단). 파일은 매 호출 fresh 읽기
    (import 캐시와 무관 — 온보딩 직후/기록 직후 상태를 즉시 반영).
    반환: False | {"title": <생성할 팩 제목>}"""
    if os.environ.get("BINGGU_PACK_ID"):
        return False
    try:
        with open(binggu_paths.state_path(PACK_CONFIG_FILE), encoding="utf-8") as f:
            c = json.load(f)
    except Exception:
        return False
    if bool(c.get("auto_create")) and not (c.get("pack_id") or "").strip():
        return {"title": c.get("title") or "개인 의사결정 온톨로지"}
    return False


def record_pack_id(pack_id, title=None, baseline=True, ledger=None):
    """Agent 가 opencrab_ingest_text 로 팩 생성 후 UUID 기록(온보딩 auto_create 완결).
    baseline=True — 생성 시 전체 텍스트를 이미 업로드했으므로 현재 전체 문장을
    uploaded 로 흡수(이후 sync_delta 는 신규만). 별도 프로세스 호출 전제(import 상수는
    다음 프로세스부터 새 pack_id 반영)."""
    pid = (pack_id or "").strip()
    if not pid:
        return {"status": "INVALID", "reason": "empty_pack_id"}
    path = binggu_paths.state_path(PACK_CONFIG_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            c = json.load(f)
    except Exception:
        c = {}
    c["pack_id"] = pid
    if title:
        c["title"] = title
    c["auto_create"] = False
    with open(path, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=2)
    absorbed = 0
    if baseline:
        sentences, _b = _owner_sentences(ledger=ledger)
        cur = [_sent_hash(s) for s in sentences]
        save_state(_hash(_render_pack(sentences)), len(sentences),
                   uploaded_hashes=cur, pack_id=pid)
        absorbed = len(cur)
    return {"status": "OK", "pack_id": pid, "config": path, "absorbed": absorbed}


def _home():
    return binggu_paths.home()


def _ledger():
    return binggu_paths.ledger()


def _state_path():
    return binggu_paths.state_path(STATE_FILE)


def _pack_header(label=None):
    """팩 헤더 — 화자 호칭 파라미터화(owner_label). 둘째 줄의 '사용자 개인 온톨로지'는
    일반 서술어(호칭 아님)라 그대로 둔다."""
    lbl = label or _owner_label()
    return ["# %s(owner) 의사결정 원칙·판단 온톨로지" % lbl, "",
            "사용자 개인 온톨로지 — owner 화자 확정 원칙/판단 (T3 하드제외 통과분).", ""]


# 하위호환 별칭(wrapper 명시 re-export 보존) — import 시점 라벨 스냅샷. 실제 렌더는 _pack_header() 동적.
_PACK_HEADER = _pack_header()


def _owner_sentences(ledger=None):
    """owner active 노드 → T3 통과 uniq 문장 리스트. 반환 (uniq_sentences, blocked_count)."""
    L = ledger or _ledger()
    con = sqlite3.connect("file:%s?mode=ro" % str(L).replace("\\", "/"), uri=True)
    try:
        rows = con.execute(
            "SELECT sentence FROM nodes WHERE speaker='owner' AND state='active'").fetchall()
    finally:
        con.close()
    items = [{"sentence": r[0]} for r in rows if r[0]]
    fr = filter_uploadable(items)
    seen, uniq = set(), []
    for it in fr["ok"]:
        s = (it["sentence"] or "").strip()
        if s and s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq, len(fr["blocked"])


def _render_pack(sentences, label=None):
    return "\n".join(_pack_header(label) + ["- " + s for s in sentences])


def build_pack_text(ledger=None):
    """owner active 노드 → T3 통과분 → pack text. 반환 (text, ok_count, blocked_count)."""
    uniq, blocked = _owner_sentences(ledger=ledger)
    return _render_pack(uniq), len(uniq), blocked


def load_state():
    p = _state_path()
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(content_hash, count, uploaded_hashes=None, pack_id=None):
    """상태 저장. uploaded_hashes=None 이면 기존 값을 보존한다(어느 경로에서도 silent
    wipe 금지 — 불변식). full/delta 모드 혼용 시 baseline·confirm 이 델타 추적 상태를
    지워 재-baseline(신규 문장 무업로드 흡수=데이터 손실)을 유발하던 결함 차단.
    pack_id — record_pack_id(신규 생성 직후)가 모듈 상수(구 값) 대신 새 UUID 를 기록."""
    st = {"pack_id": pack_id or PACK_ID, "content_hash": content_hash, "count": count}
    if uploaded_hashes is None:
        prev = load_state().get("uploaded_hashes")
        if prev is not None:
            st["uploaded_hashes"] = sorted(set(prev))
    else:
        st["uploaded_hashes"] = sorted(set(uploaded_hashes))
    with open(_state_path(), "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    return st


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# 문장 hash 길이 — 64bit(16자). 과거 48bit(12자)은 충돌 시 신규 문장을 기존으로 오인해
# skip(silent 누락)할 위험이 있어 확대. 저장된 12자 hash 는 16자 hash 의 접두사라 호환.
_SENT_HASH_LEN = 16


def _sent_hash(s):
    """문장 단위 짧은 hash — 델타(신규 문장) 추적용."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:_SENT_HASH_LEN]


def sync(baseline=False, write_text=None, ledger=None):
    """변경 감지. baseline=True → 현재 상태를 기준으로 기록(업로드 안 함).
    반환 {status, pack_id, count, blocked, hash, ...}."""
    req = pack_create_required()
    if req:
        text, count, blocked = build_pack_text(ledger=ledger)
        if write_text:
            with open(write_text, "w", encoding="utf-8") as f:
                f.write(text)
        return {"status": "PACK_CREATE_REQUIRED", "pack_id": None, "title": req["title"],
                "count": count, "blocked": blocked, "text": text,
                "hint": "MCP Agent: opencrab_ingest_text(title, text)로 팩 생성 → "
                        "person_pack_sync.record_pack_id('<uuid>') 호출(이후 델타만 업로드)"}
    text, count, blocked = build_pack_text(ledger=ledger)
    h = _hash(text)
    if write_text:
        with open(write_text, "w", encoding="utf-8") as f:
            f.write(text)
    st = load_state()
    if baseline:
        save_state(h, count)
        return {"status": "BASELINE_SET", "pack_id": PACK_ID, "count": count,
                "blocked": blocked, "hash": h}
    if st.get("content_hash") == h:
        return {"status": "NO_CHANGE", "pack_id": PACK_ID, "count": count, "hash": h}
    return {"status": "UPDATE_NEEDED", "pack_id": PACK_ID, "count": count, "blocked": blocked,
            "hash": h, "prev_hash": st.get("content_hash"), "prev_count": st.get("count")}


def confirm(content_hash, count):
    """Agent 가 opencrab 업로드 성공 후 호출 — baseline 갱신(다음 변경 감지 기준)."""
    return save_state(content_hash, count)


# ---------------- 델타 업로드 (pack_update append 중복 방지) ----------------
# opencrab pack_update(content) 는 content 를 재파싱→그래프 append 한다. 매번 전체 pack
# text 를 던지면 같은 문장이 반복 파싱돼 유사 노드가 중복 누적된다(E2E: 노드 13→26).
# → baseline 이후 신규 문장만 델타 텍스트로 던지면 신규 노드만 추가된다(중복 0).


def _render_delta(sentences, label=None):
    """델타 업로드 텍스트 — 신규 원칙 문장만(기존 노드 재파싱·중복 방지)."""
    lbl = label or _owner_label()
    return "\n".join(["# (추가) %s 의사결정 원칙 — 신규 반영분" % lbl, ""]
                     + ["- " + s for s in sentences])


def sync_delta(write_text=None, ledger=None):
    """델타(신규 문장만) 감지. uploaded_hashes 로 이미 업로드된 문장을 추적한다.
    status:
      DELTA_BASELINE_SET — 첫 델타 실행(uploaded 기록 없음). 현재 전체를 이미 업로드된
                           것으로 흡수(업로드 스킵). 클라우드엔 이미 full 반영돼 있다는 가정.
      DELTA_UPDATE       — 신규 문장 있음. delta_sentences/delta_hashes/delta_text 제공.
      NO_CHANGE          — 신규 없음.
    write_text: DELTA_UPDATE 시 델타 텍스트(신규만)를 파일로 출력(Agent 업로드용)."""
    req = pack_create_required()
    if req:
        text, count, blocked = build_pack_text(ledger=ledger)
        if write_text:
            with open(write_text, "w", encoding="utf-8") as f:
                f.write(text)
        return {"status": "PACK_CREATE_REQUIRED", "pack_id": None, "title": req["title"],
                "count": count, "blocked": blocked, "text": text, "delta_count": 0,
                "hint": "MCP Agent: opencrab_ingest_text(title, text)로 팩 생성 → "
                        "person_pack_sync.record_pack_id('<uuid>') 호출(이후 델타만 업로드)"}
    sentences, blocked = _owner_sentences(ledger=ledger)
    cur = [(_sent_hash(s), s) for s in sentences]
    full_hash = _hash(_render_pack(sentences))
    st = load_state()
    uploaded = st.get("uploaded_hashes")
    if uploaded is None:
        # 마이그레이션: 현재 전체를 uploaded 로 흡수(재업로드 방지). 이후 신규만 델타.
        save_state(full_hash, len(sentences), uploaded_hashes=[h for h, _ in cur])
        return {"status": "DELTA_BASELINE_SET", "pack_id": PACK_ID, "count": len(sentences),
                "blocked": blocked, "hash": full_hash, "absorbed": len(cur), "delta_count": 0}
    uploaded = set(uploaded)
    # 레거시 hash 호환: 과거 12자 hash 는 현재 16자 hash 의 접두사이므로 길이별 접두사로
    # 매칭한다. hash 폭 확대(12→16) 시 저장분을 신규로 오인→전량 재업로드(pack_update
    # 재파싱 중복 노드 누적)를 막는다. 무손실(접두사 일치는 동일 문장 보장).
    ulens = sorted({len(x) for x in uploaded}) or [_SENT_HASH_LEN]

    def _seen(h):
        return any(h[:n] in uploaded for n in ulens)

    new = [(h, s) for h, s in cur if not _seen(h)]
    if not new:
        return {"status": "NO_CHANGE", "pack_id": PACK_ID, "count": len(sentences),
                "hash": full_hash, "delta_count": 0}
    delta_text = _render_delta([s for _, s in new])
    if write_text:
        with open(write_text, "w", encoding="utf-8") as f:
            f.write(delta_text)
    return {"status": "DELTA_UPDATE", "pack_id": PACK_ID, "count": len(sentences),
            "blocked": blocked, "hash": full_hash, "delta_count": len(new),
            "delta_hashes": [h for h, _ in new], "delta_sentences": [s for _, s in new],
            "delta_text": delta_text}


def confirm_delta(new_hashes, content_hash, count):
    """델타 업로드 성공 후 — 신규 문장 hash 를 uploaded set 에 병합, baseline 갱신."""
    st = load_state()
    up = set(st.get("uploaded_hashes") or [])
    up.update(new_hashes)
    return save_state(content_hash, count, uploaded_hashes=list(up))


# ---------------- selftest ----------------
def _selftest():
    import shutil
    import tempfile
    ok = True

    def check(cond, msg):
        nonlocal ok
        if not cond:
            ok = False
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")

    tmp = tempfile.mkdtemp(prefix="person_sync_")
    try:
        home = os.path.join(tmp, ".binggupack")
        os.makedirs(home)
        ledger = os.path.join(home, "ledger.sqlite")
        os.environ["BINGGU_HOME"] = home
        os.environ["BINGGU_LEDGER"] = ledger
        con = sqlite3.connect(ledger)
        from binggupack.storage.schema import apply_schema  # 정본 스키마 위임(인라인 CREATE TABLE 제거)
        apply_schema(con)

        def add(nid, sent, speaker="owner", state="active"):
            con.execute("INSERT INTO nodes(node_id,node_type,sentence,state,semantic_subtype,speaker)"
                        " VALUES(?,?,?,?,?,?)", (nid, "judgment", sent, state, "선호", speaker))
            con.commit()

        add("o1", "결론부터 짧게 답한다")
        add("o2", "유연함이 능력이다")
        add("p1", "이건 PII 010-" "1234-5678 포함", speaker="owner")  # T3 차단 대상
        add("a1", "AI 발화는 온톨로지 팩에서 제외", speaker="ai")   # 사람축 아님

        # T1 build: owner 만·T3 차단 제외
        text, cnt, blk = build_pack_text()
        check(cnt == 2 and blk == 1 and "결론부터" in text and "010-1234" not in text,
              "T1 build: owner 2노드·T3 차단 1·PII 미포함")
        # T2 첫 sync = UPDATE_NEEDED(상태 없음)
        r1 = sync()
        check(r1["status"] == "UPDATE_NEEDED" and r1["count"] == 2, "T2 상태 없음 → UPDATE_NEEDED")
        # T3 baseline 기록 → NO_CHANGE
        rb = sync(baseline=True)
        check(rb["status"] == "BASELINE_SET", "T3 baseline 기록")
        r2 = sync()
        check(r2["status"] == "NO_CHANGE", "T4 baseline 후 변경 없음 → NO_CHANGE")
        # T5 owner 노드 추가 → UPDATE_NEEDED
        add("o3", "새 판단 하나 추가됨")
        r3 = sync()
        check(r3["status"] == "UPDATE_NEEDED" and r3["count"] == 3, "T5 노드 추가 → UPDATE_NEEDED(count 3)")
        # T6 confirm 후 다시 NO_CHANGE
        confirm(r3["hash"], r3["count"])
        r4 = sync()
        check(r4["status"] == "NO_CHANGE", "T6 confirm(업로드 성공 기록) 후 → NO_CHANGE")
        # T7 write_text 파일 생성
        wt = os.path.join(tmp, "pack.txt")
        sync(write_text=wt)
        check(os.path.exists(wt) and "새 판단" in open(wt, encoding="utf-8").read(),
              "T7 write_text 팩 파일 생성")
        # T8 ledger read-only(mtime 불변)
        m0 = os.path.getmtime(ledger)
        sync()
        check(os.path.getmtime(ledger) == m0, "T8 sync 후 ledger mtime 불변(read-only)")

        # ---- 델타 업로드 (append 중복 방지) ----
        # 현재 owner active = o1,o2,o3 (p1=T3차단, a1=ai 제외) = 3문장. uploaded 기록 없음.
        rd0 = sync_delta()
        check(rd0["status"] == "DELTA_BASELINE_SET" and rd0["absorbed"] == 3
              and rd0["delta_count"] == 0,
              "Td1 첫 델타 → BASELINE_SET(현재 3문장 흡수·업로드 스킵)")
        rd1 = sync_delta()
        check(rd1["status"] == "NO_CHANGE" and rd1["delta_count"] == 0,
              "Td2 변경 없음 → NO_CHANGE")
        add("o4", "새 원칙: 대안을 직접 찾아 가져온다")
        rd2 = sync_delta()
        check(rd2["status"] == "DELTA_UPDATE" and rd2["delta_count"] == 1
              and rd2["delta_sentences"] == ["새 원칙: 대안을 직접 찾아 가져온다"],
              "Td3 노드 1 추가 → DELTA_UPDATE(신규 1만)")
        check("새 원칙" in rd2["delta_text"] and "결론부터" not in rd2["delta_text"],
              "Td4 델타 텍스트 = 신규만(기존 문장 미포함)")
        wd = os.path.join(tmp, "delta.txt")
        sync_delta(write_text=wd)
        dtxt = open(wd, encoding="utf-8").read()
        check("새 원칙" in dtxt and "결론부터" not in dtxt and "유연함" not in dtxt,
              "Td5 write_text 델타 파일 = 신규만")
        # confirm_delta → uploaded 병합 → NO_CHANGE
        confirm_delta(rd2["delta_hashes"], rd2["hash"], rd2["count"])
        rd3 = sync_delta()
        check(rd3["status"] == "NO_CHANGE" and rd3["delta_count"] == 0,
              "Td6 confirm_delta(업로드 성공 기록) 후 → NO_CHANGE")
        # 델타 confirm 후에도 ledger 불변
        m1 = os.path.getmtime(ledger)
        sync_delta()
        check(os.path.getmtime(ledger) == m1, "Td7 델타 sync 후 ledger mtime 불변(read-only)")

        # ---- 결함 회귀 (Fix D) ----
        # Tf1 (defect 3): 문장 hash 폭 64bit(16자↑) — 48bit 충돌 시 신규 문장 skip 방지
        check(len(_sent_hash("아무 문장이든")) >= 16, "Tf1 _sent_hash 16자(64bit) 이상")

        # Tf2 (defect 1·2): 델타 운용 중 full baseline 이 uploaded_hashes 를 wipe 하지 않음
        up_before = set(load_state().get("uploaded_hashes") or [])
        check(len(up_before) == 4, "Tf2a 델타 상태 uploaded_hashes 4개(o1~o4)")
        sync(baseline=True)  # full baseline(업로드 안 함) — 예전엔 여기서 uploaded_hashes 소실
        up_after = set(load_state().get("uploaded_hashes") or [])
        check(up_after == up_before, "Tf2b full baseline 후 uploaded_hashes 보존(wipe 0)")

        # Tf3 (defect 1): baseline 이후 추가된 문장이 재-baseline 흡수(무업로드=손실) 없이 델타 검출
        add("o5", "새 원칙: 위험을 먼저 말한다")
        rf = sync_delta()
        check(rf["status"] == "DELTA_UPDATE" and rf["delta_count"] == 1
              and rf["delta_sentences"] == ["새 원칙: 위험을 먼저 말한다"],
              "Tf3 baseline 후 신규 문장 델타 검출(흡수·손실 없음)")

        # Tf4 (defect 2): full 모드 confirm 도 uploaded_hashes 를 wipe 하지 않음
        prev_up = load_state().get("uploaded_hashes")
        confirm(rf["hash"], rf["count"])  # full confirm — 예전엔 델타 추적 상태 소실
        cur_up = load_state().get("uploaded_hashes")
        check(cur_up is not None and set(cur_up) == set(prev_up),
              "Tf4 full confirm 후 uploaded_hashes 보존(재-baseline·재업로드 방지)")

        # Tf5 (defect 3 마이그레이션): 저장된 12자 레거시 hash 도 무손실 매칭(전량 재업로드 방지)
        sents, _b = _owner_sentences()
        legacy = [hashlib.sha256(s.encode("utf-8")).hexdigest()[:12] for s in sents]
        save_state(_hash(_render_pack(sents)), len(sents), uploaded_hashes=legacy)
        rmig = sync_delta()
        check(rmig["status"] == "NO_CHANGE" and rmig.get("delta_count") == 0,
              "Tf5a 레거시 12자 hash 접두사 매칭 → 재업로드 없음(NO_CHANGE)")
        add("o6", "새 원칙: 방법은 무조건 있다")
        rmig2 = sync_delta()
        check(rmig2["status"] == "DELTA_UPDATE" and rmig2["delta_count"] == 1
              and rmig2["delta_sentences"] == ["새 원칙: 방법은 무조건 있다"],
              "Tf5b 레거시 상태에서도 신규 문장은 정상 검출")

        # ---- 신규 사용자 auto_create (온보딩 config → 생성 필요 신호 → record_pack_id) ----
        cfgp = os.path.join(home, "person_pack.json")
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump({"pack_id": "", "title": "테스트 개인 온톨로지", "auto_create": True}, f)
        rq = pack_create_required()
        check(bool(rq) and rq["title"] == "테스트 개인 온톨로지",
              "Tn1 auto_create+빈 pack_id → 팩 생성 필요 감지")
        rn = sync_delta()
        check(rn["status"] == "PACK_CREATE_REQUIRED" and rn["pack_id"] is None
              and "결론부터" in rn["text"] and rn["delta_count"] == 0,
              "Tn2 sync_delta → PACK_CREATE_REQUIRED(전체 텍스트 동봉·owner UUID 오타겟 차단)")
        rn2 = sync()
        check(rn2["status"] == "PACK_CREATE_REQUIRED", "Tn3 sync 도 동일 신호")
        rr = record_pack_id("11111111-2222-3333-4444-555555555555", baseline=True)
        check(rr["status"] == "OK" and rr["absorbed"] == 6,
              "Tn4 record_pack_id → config 기록 + 현재 6문장 전량 흡수(생성 시 full ingest 가정)")
        cfg = json.load(open(cfgp, encoding="utf-8"))
        check(cfg["pack_id"].startswith("11111111") and cfg["auto_create"] is False,
              "Tn5 config pack_id 기록·auto_create 해제")
        rn3 = sync_delta()
        check(rn3["status"] == "NO_CHANGE" and rn3["delta_count"] == 0,
              "Tn6 기록 직후 델타 NO_CHANGE(흡수 반영)")
        check((load_state().get("pack_id") or "").startswith("11111111"),
              "Tn7 상태 pack_id = 신규 UUID(모듈 상수 아님)")
        check(record_pack_id("")["status"] == "INVALID", "Tn8 빈 pack_id 거부")
        os.remove(cfgp)
        check(pack_create_required() is False,
              "Tn9 config 부재 → 생성 신호 없음(owner 기본값 경로 회귀 0)")

        # ---- _pack_config 폴백 사슬 + owner_label 중립화 (트랙 C) ----
        # 격리: 팩/라벨 env 를 비우고 config·운영 마커 파일만으로 폴백을 검증.
        for _ev in ("BINGGU_PACK_ID", "BINGGU_PACK_TITLE", "BINGGU_OWNER_LABEL"):
            os.environ.pop(_ev, None)
        statep = _state_path()  # person_pack_last.json (BINGGU_HOME 격리)
        for _p in (cfgp, statep):
            if os.path.exists(_p):
                os.remove(_p)
        # (c) config·운영 마커 모두 부재 → pack_id sentinel(None)·하드코딩 owner UUID 제거 확인
        pid_c, title_c = _pack_config()
        check(pid_c is None, "Tc1 config·마커 모두 부재 → pack_id sentinel(None)·하드코딩 UUID 미노출")
        check(title_c == "사용자 의사결정 원칙 온톨로지",
              "Tc2 title 기본값 = 중립 owner_label('사용자') 기반")
        # (b) config 부재 + person_pack_last.json 에 pack_id → 그 값(=owner 회귀 시나리오)
        with open(statep, "w", encoding="utf-8") as f:
            json.dump({"pack_id": "4da76877-e286-449f-8116-569be4056838",
                       "content_hash": "x", "count": 1}, f, ensure_ascii=False)
        pid_b, _tb = _pack_config()
        check(pid_b == "4da76877-e286-449f-8116-569be4056838",
              "Tc3 config 부재+운영 마커 pack_id → 마커값 반환(owner 회귀 0)")
        # (a) config 에 pack_id 있으면 그것(운영 마커보다 우선)
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump({"pack_id": "99999999-aaaa-bbbb-cccc-dddddddddddd", "title": "명시 팩 제목"},
                      f, ensure_ascii=False)
        pid_a, title_a = _pack_config()
        check(pid_a == "99999999-aaaa-bbbb-cccc-dddddddddddd" and title_a == "명시 팩 제목",
              "Tc4 config pack_id·title 최우선(운영 마커보다 우선)")
        os.remove(cfgp)
        os.remove(statep)
        # (d) owner_label override — env·config·기본값 + 헤더/델타 반영
        os.environ["BINGGU_OWNER_LABEL"] = "대표님"
        check(_owner_label() == "대표님", "Tc5 env BINGGU_OWNER_LABEL 최우선")
        check("# 대표님(owner)" in _render_pack(["문장"])
              and "대표님 의사결정 원칙" in _render_delta(["신규"]),
              "Tc6 owner_label 이 팩 헤더·델타 텍스트에 반영")
        os.environ.pop("BINGGU_OWNER_LABEL", None)
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump({"owner_label": "사장님"}, f, ensure_ascii=False)
        check(_owner_label() == "사장님" and "# 사장님(owner)" in _render_pack(["문장"]),
              "Tc7 config owner_label(env 부재) → owner 개인 호칭('사장님') 표시 유지")
        os.remove(cfgp)

        con.close()
        print(f"\nGATE={'GO' if ok else 'NO-GO'}")
        return 0 if ok else 1
    finally:
        os.environ.pop("BINGGU_HOME", None)
        os.environ.pop("BINGGU_LEDGER", None)
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="binggu_person_pack_sync")
    ap.add_argument("--baseline", action="store_true", help="현재 상태를 기준으로 기록(업로드 안 함)")
    ap.add_argument("--write-text", default=None, help="pack text 를 파일로 출력(Agent 업로드용)")
    ap.add_argument("--confirm-hash", default=None, help="Agent 업로드 성공 후 baseline 갱신용 hash")
    ap.add_argument("--confirm-count", type=int, default=None)
    ap.add_argument("--delta", action="store_true",
                    help="델타(신규 문장만) 감지 모드 — append 중복 방지")
    ap.add_argument("--confirm-delta-hashes", default=None,
                    help="델타 업로드 성공한 문장 hash(콤마구분) — uploaded set 에 병합")
    ap.add_argument("--record-pack-id", dest="record_pack_id", default=None,
                    help="Agent 팩 생성 후 UUID 기록(auto_create 완결·현재 전량 흡수 baseline)")
    ap.add_argument("--title", default=None, help="--record-pack-id 와 함께 팩 제목 갱신(선택)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if a.record_pack_id:
        print(json.dumps(record_pack_id(a.record_pack_id, title=a.title), ensure_ascii=False))
        return 0
    if (a.confirm_delta_hashes is not None and a.confirm_hash is not None
            and a.confirm_count is not None):
        hashes = [x for x in a.confirm_delta_hashes.split(",") if x]
        print(json.dumps(confirm_delta(hashes, a.confirm_hash, a.confirm_count), ensure_ascii=False))
        return 0
    if a.confirm_hash is not None and a.confirm_count is not None:
        print(json.dumps(confirm(a.confirm_hash, a.confirm_count), ensure_ascii=False))
        return 0
    if a.delta:
        print(json.dumps(sync_delta(write_text=a.write_text), ensure_ascii=False))
        return 0
    r = sync(baseline=a.baseline, write_text=a.write_text)
    print(json.dumps(r, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
