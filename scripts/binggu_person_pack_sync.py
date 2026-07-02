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
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sqlite3  # noqa: E402
from binggu_t3_filter import filter_uploadable  # noqa: E402

# owner 온톨로지 팩(2026-07-02 최초 업로드) — 갱신 대상 고정.
PACK_ID = "4da76877-e286-449f-8116-569be4056838"
STATE_FILE = "person_pack_last.json"
PACK_TITLE = "사장님 의사결정 원칙 온톨로지"


def _home():
    return os.environ.get("BINGGU_HOME") or os.path.join(os.path.expanduser("~"), ".binggupack")


def _ledger():
    return os.environ.get("BINGGU_LEDGER") or os.path.join(_home(), "ledger.sqlite")


def _state_path():
    return os.path.join(_home(), STATE_FILE)


_PACK_HEADER = ["# 사장님(owner) 의사결정 원칙·판단 온톨로지", "",
                "사용자 개인 온톨로지 — owner 화자 확정 원칙/판단 (T3 하드제외 통과분).", ""]


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


def _render_pack(sentences):
    return "\n".join(_PACK_HEADER + ["- " + s for s in sentences])


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


def save_state(content_hash, count, uploaded_hashes=None):
    st = {"pack_id": PACK_ID, "content_hash": content_hash, "count": count}
    if uploaded_hashes is not None:
        st["uploaded_hashes"] = sorted(set(uploaded_hashes))
    with open(_state_path(), "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    return st


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _sent_hash(s):
    """문장 단위 짧은 hash — 델타(신규 문장) 추적용."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def sync(baseline=False, write_text=None, ledger=None):
    """변경 감지. baseline=True → 현재 상태를 기준으로 기록(업로드 안 함).
    반환 {status, pack_id, count, blocked, hash, ...}."""
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


def _render_delta(sentences):
    """델타 업로드 텍스트 — 신규 원칙 문장만(기존 노드 재파싱·중복 방지)."""
    return "\n".join(["# (추가) 사장님 의사결정 원칙 — 신규 반영분", ""]
                     + ["- " + s for s in sentences])


def sync_delta(write_text=None, ledger=None):
    """델타(신규 문장만) 감지. uploaded_hashes 로 이미 업로드된 문장을 추적한다.
    status:
      DELTA_BASELINE_SET — 첫 델타 실행(uploaded 기록 없음). 현재 전체를 이미 업로드된
                           것으로 흡수(업로드 스킵). 클라우드엔 이미 full 반영돼 있다는 가정.
      DELTA_UPDATE       — 신규 문장 있음. delta_sentences/delta_hashes/delta_text 제공.
      NO_CHANGE          — 신규 없음.
    write_text: DELTA_UPDATE 시 델타 텍스트(신규만)를 파일로 출력(Agent 업로드용)."""
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
    new = [(h, s) for h, s in cur if h not in uploaded]
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
    import tempfile
    import shutil
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
        con.execute("CREATE TABLE nodes(node_id TEXT PRIMARY KEY, node_type TEXT, sentence TEXT,"
                    " state TEXT, semantic_subtype TEXT, speaker TEXT)")

        def add(nid, sent, speaker="owner", state="active"):
            con.execute("INSERT INTO nodes(node_id,node_type,sentence,state,semantic_subtype,speaker)"
                        " VALUES(?,?,?,?,?,?)", (nid, "judgment", sent, state, "선호", speaker))
            con.commit()

        add("o1", "결론부터 짧게 답한다")
        add("o2", "유연함이 능력이다")
        add("p1", "이건 PII 010-1234-5678 포함", speaker="owner")  # T3 차단 대상
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
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
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
