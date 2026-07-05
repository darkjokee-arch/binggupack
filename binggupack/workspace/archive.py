# -*- coding: utf-8 -*-
"""binggupack.workspace.archive — 장부 백업·내보내기 (데이터 주권).

"승인한 것만 저장"의 짝 = "언제든 꺼내고(export) 되돌린다(backup)". 전수 감사 기능 갭 P0.

불변:
  - read-only + 복사만 — 운영 ledger 는 mode=ro 로만 읽는다. write 0(백업 대상 파일만 신규 생성).
  - 격리 존중 — 기본 백업 위치는 ledger 옆 <home>/_backup (home 미지정 시 dirname(ledger)).
  - 원문 보존 — export 는 사용자 자신의 장부를 자신이 꺼내는 것(PII 마스킹 없음 — 로컬 소유자 전용).
    (cloud publish 경로의 PII 게이트와 다름: 여기는 외부 전송 0, owner 로컬 파일로만.)
  - 방어적 스키마 — 구/신 ledger 컬럼 차이를 PRAGMA 로 흡수(누락 컬럼은 빈 값).
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime


def _connect_ro(ledger_path):
    return sqlite3.connect("file:%s?mode=ro" % ledger_path, uri=True)


def _cols(cur, table):
    try:
        return [c[1] for c in cur.execute("PRAGMA table_info(%s)" % table)]
    except sqlite3.OperationalError:
        return []


def read_all(ledger_path):
    """ledger → {nodes, edges, evidence} 전량(state 무관 — deprecated 포함). read-only.

    회상(active 만)과 달리 export 는 사용자 소유 데이터 전부를 꺼낸다(주권). 파일 부재 → 빈 세트.
    """
    empty = {"nodes": [], "edges": [], "evidence": []}
    if not ledger_path or not os.path.exists(ledger_path):
        return empty
    conn = _connect_ro(ledger_path)
    cur = conn.cursor()
    try:
        ncols = _cols(cur, "nodes")
        want_n = ["node_id", "node_type", "sentence", "state", "semantic_subtype",
                  "speaker", "created_at", "use_count", "candidate"]
        sel_n = [c if c in ncols else "NULL AS %s" % c for c in want_n]
        nodes = [dict(zip(want_n, r))
                 for r in cur.execute("SELECT %s FROM nodes" % ",".join(sel_n))]
        ecols = _cols(cur, "edges")
        edges = []
        if ecols:
            want_e = ["edge_id", "relation", "source", "target", "state"]
            sel_e = [c if c in ecols else "NULL AS %s" % c for c in want_e]
            edges = [dict(zip(want_e, r))
                     for r in cur.execute("SELECT %s FROM edges" % ",".join(sel_e))]
        evidence = []
        if _cols(cur, "evidence"):
            evidence = [{"evidence_id": r[0], "sentence": r[1]}
                        for r in cur.execute("SELECT evidence_id,sentence FROM evidence")]
    finally:
        conn.close()
    return {"nodes": nodes, "edges": edges, "evidence": evidence}


def _stamp(ts=None):
    return (ts or datetime.now()).strftime("%Y%m%d_%H%M%S")


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def backup_ledger(ledger_path, out_path=None, home=None, ts=None):
    """ledger 를 일관 스냅샷으로 복사(sqlite backup API — WAL 중에도 안전). 운영 ledger write 0.

    반환: {status, out_path, sha256, size, nodes, edges}. 파일 부재 → status='NO_LEDGER'.
    out_path 미지정 → <home|dirname>/_backup/ledger_<ts>.sqlite (디렉터리 자동 생성).
    """
    if not ledger_path or not os.path.exists(ledger_path):
        return {"status": "NO_LEDGER", "ledger": ledger_path}
    base = home or os.path.dirname(ledger_path)
    if out_path is None:
        bdir = os.path.join(base, "_backup")
        os.makedirs(bdir, exist_ok=True)
        out_path = os.path.join(bdir, "ledger_%s.sqlite" % _stamp(ts))
    else:
        d = os.path.dirname(out_path)
        if d:
            os.makedirs(d, exist_ok=True)
    # 일관 복사 — 원본은 mode=ro, sqlite 온라인 backup 으로 WAL 반영분까지 정합 스냅샷.
    src = _connect_ro(ledger_path)
    dst = sqlite3.connect(out_path)
    try:
        src.backup(dst)
        # 단일 파일 스냅샷 — WAL/shm 부산물 제거(백업은 이식·보관용이라 자족적이어야).
        dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        dst.execute("PRAGMA journal_mode=DELETE")
    finally:
        dst.close()
        src.close()
    data = read_all(out_path)
    return {"status": "OK", "out_path": out_path, "sha256": _sha256_file(out_path),
            "size": os.path.getsize(out_path),
            "nodes": len(data["nodes"]), "edges": len(data["edges"])}


def restore_ledger(backup_path, ledger_path, home=None, ts=None, confirm=None):
    """백업 스냅샷으로 운영 ledger 를 교체(파괴적 · confirm 정확 일치 게이트).

    confirm 미지정/불일치 → 검증 결과만 반환(write 0). 정확 일치("RESTORE <백업파일명>") 시:
      ① 백업 유효성(sqlite + nodes 테이블) ② 현 ledger 를 _backup/pre_restore_<ts>.sqlite
      자동 스냅샷(복구의 복구) ③ os.replace 원자 교체 + 낡은 -wal/-shm 제거
      (직전 스냅샷이 WAL 반영분 포함이므로 안전).
    반환 status: NO_BACKUP / INVALID_BACKUP / DRY_RUN / CONFIRM_MISMATCH /
                 PRE_SNAPSHOT_FAIL / BUSY / OK.
    """
    import shutil
    if not backup_path or not os.path.exists(backup_path):
        return {"status": "NO_BACKUP", "backup": backup_path}
    try:
        con = _connect_ro(backup_path)
        try:
            tables = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        finally:
            con.close()
    except sqlite3.DatabaseError:
        return {"status": "INVALID_BACKUP", "backup": backup_path}
    if "nodes" not in tables:
        return {"status": "INVALID_BACKUP", "backup": backup_path, "reason": "no_nodes_table"}
    bdata = read_all(backup_path)
    cur = read_all(ledger_path) if os.path.exists(ledger_path) else {"nodes": [], "edges": []}
    expected = "RESTORE " + os.path.basename(backup_path)
    info = {"backup": backup_path, "backup_nodes": len(bdata["nodes"]),
            "backup_edges": len(bdata["edges"]), "current_nodes": len(cur["nodes"]),
            "current_edges": len(cur["edges"]), "expected_confirm": expected}
    if confirm != expected:
        info["status"] = "CONFIRM_MISMATCH" if confirm else "DRY_RUN"
        return info
    pre = None
    if os.path.exists(ledger_path):
        base = home or os.path.dirname(ledger_path)
        pre_path = os.path.join(base, "_backup", "pre_restore_%s.sqlite" % _stamp(ts))
        b = backup_ledger(ledger_path, out_path=pre_path, home=base, ts=ts)
        if b["status"] != "OK":
            info["status"] = "PRE_SNAPSHOT_FAIL"
            return info
        pre = b["out_path"]
    d = os.path.dirname(ledger_path) or "."
    os.makedirs(d, exist_ok=True)
    tmp_path = os.path.join(d, ".restore_tmp_%s" % _stamp(ts))
    shutil.copy2(backup_path, tmp_path)
    try:
        os.replace(tmp_path, ledger_path)
    except OSError as e:  # 다른 프로세스(MCP/auto-pull)가 잡고 있으면 교체 실패 — 원본 무손상
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        info.update({"status": "BUSY", "error": str(e)})
        return info
    for suf in ("-wal", "-shm"):
        try:
            os.remove(ledger_path + suf)
        except OSError:
            pass
    after = read_all(ledger_path)
    info.update({"status": "OK", "pre_snapshot": pre,
                 "nodes": len(after["nodes"]), "edges": len(after["edges"])})
    return info


_TYPE_ORDER = ["judgment", "판단", "concept", "개념", "state", "상태",
               "evidence", "증거", "document", "문서"]


def _node_sort_key(n):
    t = n.get("node_type") or ""
    order = _TYPE_ORDER.index(t) if t in _TYPE_ORDER else len(_TYPE_ORDER)
    return (order, str(n.get("created_at") or ""), str(n.get("node_id") or ""))


def render_markdown(data, ts=None):
    """장부 → 사람이 읽는 markdown. node_type 그룹 + 화자/subtype/상태 + 관계 섹션."""
    nodes = sorted(data["nodes"], key=_node_sort_key)
    lines = ["# 빙구팩 장부 내보내기",
             "> 생성: %s · 노드 %d · 엣지 %d · 근거 %d"
             % (_stamp(ts), len(nodes), len(data["edges"]), len(data["evidence"])), ""]
    cur_type = object()
    for n in nodes:
        t = n.get("node_type") or "(무분류)"
        if t != cur_type:
            cur_type = t
            lines.append("\n## %s" % t)
        tags = []
        if n.get("semantic_subtype"):
            tags.append(str(n["semantic_subtype"]))
        if n.get("speaker"):
            tags.append("화자:%s" % n["speaker"])
        st = n.get("state")
        if st and st not in ("active", "confirmed"):
            tags.append(st)
        tag = (" [%s]" % " · ".join(tags)) if tags else ""
        nid = str(n.get("node_id") or "")[:12]
        lines.append("- %s%s `(%s)`" % (n.get("sentence") or "", tag, nid))
    if data["edges"]:
        lines.append("\n## 관계(edges)")
        for e in data["edges"]:
            st = e.get("state")
            mark = "" if st in (None, "active", "confirmed") else " [%s]" % st
            lines.append("- `%s` --%s--> `%s`%s"
                         % (str(e.get("source") or "")[:12], e.get("relation") or "?",
                            str(e.get("target") or "")[:12], mark))
    return "\n".join(lines) + "\n"


def export_ledger(ledger_path, fmt="md", out=None, ts=None):
    """장부를 markdown 또는 json 으로 내보낸다. read-only.

    반환: {status, format, out_path|text, nodes, edges}. out 미지정 → text 반환(stdout 용).
    """
    data = read_all(ledger_path)
    if fmt == "json":
        payload = {"meta": {"generated_at": _stamp(ts), "nodes": len(data["nodes"]),
                            "edges": len(data["edges"]), "evidence": len(data["evidence"])},
                   "nodes": data["nodes"], "edges": data["edges"], "evidence": data["evidence"]}
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        text = render_markdown(data, ts=ts)
    res = {"status": "OK", "format": fmt,
           "nodes": len(data["nodes"]), "edges": len(data["edges"])}
    if out:
        d = os.path.dirname(out)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        res["out_path"] = out
    else:
        res["text"] = text
    return res


# ---------------- selftest (temp 전용 · 운영 ledger 무접촉) ----------------
def _selftest():
    import tempfile
    import shutil
    ok = True

    def ck(c, m):
        nonlocal ok
        ok = ok and bool(c)
        print("  [%s] %s" % ("PASS" if c else "FAIL", m))

    tmp = tempfile.mkdtemp(prefix="bg_archive_")
    try:
        led = os.path.join(tmp, "ledger.sqlite")
        conn = sqlite3.connect(led)
        conn.execute("CREATE TABLE nodes(node_id TEXT, node_type TEXT, sentence TEXT, "
                     "state TEXT, semantic_subtype TEXT, speaker TEXT, created_at TEXT, "
                     "use_count INTEGER, candidate INTEGER)")
        conn.execute("CREATE TABLE edges(edge_id TEXT, relation TEXT, source TEXT, target TEXT, state TEXT)")
        conn.execute("CREATE TABLE evidence(evidence_id TEXT, sentence TEXT)")
        conn.execute("INSERT INTO nodes VALUES('n1','judgment','백업 없이 큰 변경은 위험','active','교훈','owner','t0',0,1)")
        conn.execute("INSERT INTO nodes VALUES('n2','concept','데이터 주권','active',NULL,'ai','t1',0,1)")
        conn.execute("INSERT INTO edges VALUES('e1','supports','n2','n1','active')")
        conn.execute("INSERT INTO evidence VALUES('ev1','근거 문장')")
        conn.commit()
        conn.close()
        led_mtime = os.path.getmtime(led)

        # 1. read_all
        d = read_all(led)
        ck(len(d["nodes"]) == 2 and len(d["edges"]) == 1 and len(d["evidence"]) == 1,
           "read_all — 노드2/엣지1/근거1")

        # 2. backup — 일관 복사 + 원본 무접촉
        b = backup_ledger(led)
        ck(b["status"] == "OK" and os.path.exists(b["out_path"]) and b["nodes"] == 2,
           "backup — 복사본 생성 + 노드 수 일치")
        ck(abs(os.path.getmtime(led) - led_mtime) < 1e-6, "backup 후 원본 ledger mtime 불변(write 0)")
        # 복사본이 실제로 열리고 동일 데이터
        ck(read_all(b["out_path"])["nodes"][0]["node_id"] == "n1", "복사본 읽기 정합")

        # 3. export md
        em = export_ledger(led, fmt="md")
        ck(em["status"] == "OK" and "데이터 주권" in em["text"] and "judgment" in em["text"],
           "export md — 문장/타입 포함")
        ck("화자:owner" in em["text"], "export md — 화자 축 표기")

        # 4. export json (파일)
        jout = os.path.join(tmp, "out.json")
        ej = export_ledger(led, fmt="json", out=jout)
        parsed = json.load(open(jout, encoding="utf-8"))
        ck(ej["status"] == "OK" and parsed["meta"]["nodes"] == 2 and len(parsed["edges"]) == 1,
           "export json — 구조/카운트 정합")

        # 5. 빈/부재 ledger graceful
        ck(read_all(os.path.join(tmp, "none.sqlite"))["nodes"] == [], "부재 ledger → 빈 세트(graceful)")
        ck(backup_ledger(os.path.join(tmp, "none.sqlite"))["status"] == "NO_LEDGER",
           "부재 ledger backup → NO_LEDGER(에러 0)")

        # 6. restore dry-run — 안내만 · write 0
        conn = sqlite3.connect(led)
        conn.execute("INSERT INTO nodes VALUES('n3','state','복원 시험','active',NULL,'ai','t2',0,1)")
        conn.commit()
        conn.close()
        r = restore_ledger(b["out_path"], led)
        ck(r["status"] == "DRY_RUN" and r["backup_nodes"] == 2 and r["current_nodes"] == 3
           and len(read_all(led)["nodes"]) == 3, "restore dry-run — 검증만 · 교체 0")

        # 7. confirm 불일치 → 거부 + write 0
        r = restore_ledger(b["out_path"], led, confirm="RESTORE wrong.sqlite")
        ck(r["status"] == "CONFIRM_MISMATCH" and len(read_all(led)["nodes"]) == 3,
           "restore confirm 불일치 → 거부 + 교체 0")

        # 8. 정확 confirm → 교체 + 직전 상태 pre_restore 스냅샷(복구의 복구)
        r = restore_ledger(b["out_path"], led,
                           confirm="RESTORE " + os.path.basename(b["out_path"]))
        ck(r["status"] == "OK" and len(read_all(led)["nodes"]) == 2
           and r["pre_snapshot"] and os.path.exists(r["pre_snapshot"])
           and len(read_all(r["pre_snapshot"])["nodes"]) == 3,
           "restore 정확 confirm — 교체 성공 + pre_restore 스냅샷 3노드 보존")

        # 9. 비 sqlite 백업 → INVALID + 원본 무접촉
        bad = os.path.join(tmp, "bad.sqlite")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("not a database")
        r = restore_ledger(bad, led, confirm="RESTORE bad.sqlite")
        ck(r["status"] == "INVALID_BACKUP" and len(read_all(led)["nodes"]) == 2,
           "restore 비정상 백업 → INVALID + 원본 무손상")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nGATE=%s" % ("GO" if ok else "NO-GO"))
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
