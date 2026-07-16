# -*- coding: utf-8 -*-
"""binggu_cross_client_e2e.py — Track R: cross-client 읽기표면 drift 하니스 (로컬·무배포).

목적(한 줄): "두 모델이 같은 로컬 빌드를 놓고 서로 다른 버전을 보고 있는가?" 를 로컬에서만 확인.
  · Claude 는 hosted 커넥터(/mcp)로 hosted KV 에 실린 팩 스냅샷을 읽고,
  · ChatGPT 는 OpenCrab 커넥터로 OpenCrab 에 올린 팩 스냅샷을 읽는다.
  두 표면이 "같은 로컬 빌드(로컬 장부→개인 팩)" 를 반영하는지, 아니면 한쪽이 drift(구버전)인지
  비교한다. 로컬 빌드 = person_pack_sync.build_pack_text (정본 재사용).

경계 (Track R must_fix 준수 — 이 하니스가 하지 '않는' 것):
  · 실배포·live fetch 0 — hosted KV / OpenCrab 에 **네트워크로 붙지 않는다**. 이미 디스크에 있는
    스냅샷 산출물(owner 가 export 했거나 이전 파이프라인이 남긴 파일)만 **소비**한다. requests/
    urllib/socket import 0 (egress 게이트).
  · 새 fetch/serving 경로 신설 0 — 스냅샷은 '경로 입력'으로만 받는다. 없으면 정직하게 UNSUPPORTED
    (억지 PASS 금지). OpenCrab read 중복 재판 0 · Anywhere serving import 0(봉인 유지).
  · binggupack/ 미변경 — 이 파일은 scripts/ 전용. 정본(person_pack_sync·storage.schema)을 **읽기**
    재사용만 한다. cli/daily.py 및 sync_anywhere_vendor 대상 모듈 편집 0.
  · 운영 홈 불변 — 전 구간 운영 장부(mtime) sentinel 을 before/after 로 대조(hard gate). 전파
    dry-run 은 격리 임시 홈(BINGGU_HOME override)에서만 돌고 confirm/upload 0.

CLI:
  python scripts/binggu_cross_client_e2e.py --selftest
  python scripts/binggu_cross_client_e2e.py \
      --hosted-snapshot <path> --opencrab-snapshot <path>   # 실제 스냅샷 2종 diff
  python scripts/binggu_cross_client_e2e.py --propagation-only  # 격리홈 save→장부→sync dry-run 만
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import unicodedata

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ─────────────────────────────────────────────────────────────────────────────
# 정규화 — 어떤 표면의 팩이든 '원칙 문장 집합'으로 환원해 layout 무관하게 비교 가능하게.
# 등급 꼬리표 "(등급: 후보)"/"(등급: 정본)"(person_pack_sync 렌더)는 표기 전용이라
# 비교 전 strip — 꼬리표 유무/승격 플립이 영구 DRIFT 오판을 만들지 않게 한다.
# ─────────────────────────────────────────────────────────────────────────────
_GRADE_TAIL_RX = re.compile(r"\s*\(등급:\s*[^)]*\)\s*$")


def _strip_grade_tail(s: str) -> str:
    return _GRADE_TAIL_RX.sub("", s or "").strip()


def _norm_sentence(s: str) -> str:
    return _strip_grade_tail(unicodedata.normalize("NFC", (s or "")).strip())


def _digest(sentences) -> str:
    """원칙 문장 집합의 순서 무관 content digest(정렬 후 sha256). 두 표면이 같은 로컬 빌드를
    반영하면 digest 가 일치한다(문장 순서·layout·파일형식·등급 꼬리표와 무관)."""
    norm = sorted({_norm_sentence(s) for s in sentences if _norm_sentence(s)})
    blob = "\n".join(norm).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _sentences_from_md(text: str):
    """개인 팩 .md 텍스트 → '- ' 불릿 원칙 문장(등급 꼬리표 strip)."""
    out = []
    for line in (text or "").splitlines():
        st = line.strip()
        if st.startswith("- "):
            out.append(_strip_grade_tail(st[2:].strip()))
    return out


def _sentences_from_jsonl(text: str):
    """graph/nodes.jsonl(또는 flat nodes.jsonl) → 노드 문장(properties.sentence > sentence > label)."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        props = obj.get("properties") if isinstance(obj.get("properties"), dict) else {}
        sent = props.get("sentence") or obj.get("sentence") or obj.get("label")
        if sent:
            out.append(str(sent))
    return out


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _find_nodes_file(dirpath: str):
    """canonical(graph/nodes.jsonl) → flat(nodes.jsonl) 순으로 존재하는 노드 파일 반환."""
    for rel in ("graph/nodes.jsonl", "nodes.jsonl"):
        p = os.path.join(dirpath, rel)
        if os.path.isfile(p):
            return p
    return None


def snapshot_view(path: str) -> dict:
    """디스크의 기존 스냅샷 산출물(read-only) → 정규화 팩 뷰.

    지원 입력(모두 '이미 존재하는 파일' 소비 — 새 fetch 0):
      · 디렉터리: canonical pack(graph/nodes.jsonl) 또는 flat(nodes.jsonl)
      · *.jsonl : 노드 라인
      · *.md    : 개인 팩 텍스트('- ' 불릿)
      · *.json  : {"principles":[...]} 또는 {"sentences":[...]} 또는 nodes 배열
    반환 {available, source_kind, principle_count, version_digest, path} / 부재 시 available=False.
    """
    if not path:
        return {"available": False, "reason": "no_path"}
    if not os.path.exists(path):
        return {"available": False, "reason": "missing", "path": path}

    sents = None
    kind = None
    try:
        if os.path.isdir(path):
            nf = _find_nodes_file(path)
            if nf:
                sents, kind = _sentences_from_jsonl(_read_text(nf)), "pack_dir"
            else:
                # md fallback (개인 팩 스냅샷 디렉터리)
                for name in ("pack.md", "snapshot.md"):
                    mp = os.path.join(path, name)
                    if os.path.isfile(mp):
                        sents, kind = _sentences_from_md(_read_text(mp)), "pack_dir_md"
                        break
        else:
            ext = os.path.splitext(path)[1].lower()
            raw = _read_text(path)
            if ext == ".jsonl":
                sents, kind = _sentences_from_jsonl(raw), "jsonl"
            elif ext == ".md":
                sents, kind = _sentences_from_md(raw), "md"
            elif ext == ".json":
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    arr = obj.get("principles") or obj.get("sentences")
                    if arr is None and isinstance(obj.get("nodes"), list):
                        sents = [str((n.get("properties") or {}).get("sentence")
                                     or n.get("sentence") or n.get("label") or "")
                                 for n in obj["nodes"]]
                    else:
                        sents = [str(x) for x in (arr or [])]
                elif isinstance(obj, list):
                    sents = _sentences_from_jsonl("\n".join(json.dumps(o, ensure_ascii=False)
                                                            for o in obj))
                kind = "json"
            else:
                sents, kind = _sentences_from_md(raw), "text"
    except (OSError, ValueError, TypeError) as ex:
        return {"available": False, "reason": "parse_error:%s" % type(ex).__name__, "path": path}

    if sents is None:
        return {"available": False, "reason": "unrecognized_layout", "path": path}
    sents = [s for s in sents if _norm_sentence(s)]
    return {
        "available": True,
        "source_kind": kind,
        "principle_count": len(set(_norm_sentence(s) for s in sents)),
        "version_digest": _digest(sents),
        "path": path,
    }


def local_build_view(ledger=None) -> dict:
    """로컬 장부 → 개인 팩 정본 빌드(person_pack_sync) → 정규화 뷰. 이 값이 drift 비교의 기준선.
    read-only(mode=ro) — 장부 write 0."""
    from binggupack.pack import person_pack_sync as PPS
    sents, blocked = PPS._owner_sentences(ledger=ledger)
    return {
        "available": True,
        "source_kind": "local_build",
        "principle_count": len(set(_norm_sentence(s) for s in sents)),
        "version_digest": _digest(sents),
        "blocked": blocked,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 비교 — 각 표면이 로컬 빌드와 같은 버전인지 + 두 표면이 서로 일치하는지.
# ─────────────────────────────────────────────────────────────────────────────
def compare(local: dict, hosted: dict, opencrab: dict) -> dict:
    ld = local.get("version_digest")

    def surface(v):
        if not v.get("available"):
            return {"state": "MISSING", "reason": v.get("reason"), "path": v.get("path")}
        st = "IN_SYNC" if v.get("version_digest") == ld else "DRIFT"
        return {"state": st, "version_digest": v.get("version_digest"),
                "principle_count": v.get("principle_count"), "source_kind": v.get("source_kind")}

    hs, os_ = surface(hosted), surface(opencrab)
    # 두 모델이 서로 다른 버전을 보는가? (둘 다 available 일 때만 판정)
    cross = "UNKNOWN"
    if hosted.get("available") and opencrab.get("available"):
        cross = ("AGREE" if hosted.get("version_digest") == opencrab.get("version_digest")
                 else "MODELS_DIVERGE")
    any_missing = hs["state"] == "MISSING" or os_["state"] == "MISSING"
    any_drift = hs["state"] == "DRIFT" or os_["state"] == "DRIFT" or cross == "MODELS_DIVERGE"
    if any_missing:
        decision = "UNSUPPORTED"      # 정직: 스냅샷 부재 → 판정 불가(억지 PASS 금지)
    elif any_drift:
        decision = "DRIFT"
    else:
        decision = "IN_SYNC"
    return {
        "local_version_digest": ld,
        "local_principle_count": local.get("principle_count"),
        "hosted": hs,
        "opencrab": os_,
        "cross_client": cross,
        "decision": decision,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 전파 dry-run — 격리 홈에서 save→로컬 장부→sync 전파를 dry-run(confirm/upload/network 0).
# ─────────────────────────────────────────────────────────────────────────────
def propagation_dry_run(root=None) -> dict:
    """격리 임시 홈에서 owner 원칙을 장부에 넣고 person_pack_sync 로 '무엇이 전파될지' 를
    dry-run 으로 산출한다. confirm/upload/network 0 · 운영 홈 미접촉.

    검증 포인트:
      · 첫 sync = UPDATE_NEEDED (아직 baseline 없음) → 전파 대상 존재.
      · baseline 후 NO_CHANGE → 전파 안정.
      · 새 owner 노드 추가 → sync_delta 가 '신규 문장만' delta 로 산출(중복 append 방지 배선).
      · sync 는 장부 mtime 을 바꾸지 않는다(read-only).
    """
    import sqlite3

    from binggupack.pack import person_pack_sync as PPS
    from binggupack.storage.schema import apply_schema

    created_root = root is None
    base = root or tempfile.mkdtemp(prefix="xclient_prop_")
    saved_home = os.environ.get("BINGGU_HOME")
    saved_ledger = os.environ.get("BINGGU_LEDGER")
    checks = []

    def ck(cond, msg):
        checks.append({"ok": bool(cond), "msg": msg})
        return bool(cond)

    try:
        home = os.path.join(base, ".binggupack")
        os.makedirs(home, exist_ok=True)
        ledger = os.path.join(home, "ledger.sqlite")
        os.environ["BINGGU_HOME"] = home
        os.environ["BINGGU_LEDGER"] = ledger

        con = sqlite3.connect(ledger)
        apply_schema(con)

        def add(nid, sent, speaker="owner", state="active"):
            con.execute("INSERT INTO nodes(node_id,node_type,sentence,state,semantic_subtype,speaker)"
                        " VALUES(?,?,?,?,?,?)", (nid, "judgment", sent, state, "선호", speaker))
            con.commit()

        # save→장부: owner 원칙 2개(사람축) + AI 발화 1개(팩 제외 대상).
        add("o1", "결론부터 짧게 답한다")
        add("o2", "대안을 직접 찾아 가져온다")
        add("a1", "AI 발화는 개인 온톨로지 팩에서 제외된다", speaker="ai")

        r1 = PPS.sync()
        ck(r1["status"] == "UPDATE_NEEDED" and r1["count"] == 2,
           "save→장부 후 첫 sync = UPDATE_NEEDED(전파 대상 owner 2문장·AI 제외)")

        m0 = os.path.getmtime(ledger)
        PPS.sync(baseline=True)
        r2 = PPS.sync()
        ck(r2["status"] == "NO_CHANGE", "baseline 후 sync = NO_CHANGE(전파 안정)")
        ck(os.path.getmtime(ledger) == m0, "sync 후 장부 mtime 불변(read-only · write 0)")

        # 새 owner 원칙 추가 → 델타는 '신규 문장만'(중복 append 방지 배선 = 전파 정본).
        add("o3", "유연함이 능력이다")
        rd = PPS.sync_delta()
        # sync_delta 는 uploaded 기록이 없으면 첫 호출에서 BASELINE_SET(현재 전량 흡수).
        # 여기선 baseline sync 만 했고 sync_delta 첫 호출이라 흡수 후 다음 추가부터 델타가 뜬다.
        if rd["status"] == "DELTA_BASELINE_SET":
            add("o4", "빨리 시작하고 같이 수정한다")
            rd = PPS.sync_delta()
        ck(rd["status"] == "DELTA_UPDATE" and rd["delta_count"] >= 1
           and all("결론부터" not in s for s in rd.get("delta_sentences", [])),
           "새 owner 원칙 추가 → DELTA_UPDATE(신규 문장만 · 기존 재전파 0)")

        con.close()
    finally:
        # 격리 홈 env 원복(운영 홈 오염 방지) + 우리가 만든 root 만 정리.
        for k, v in (("BINGGU_HOME", saved_home), ("BINGGU_LEDGER", saved_ledger)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if created_root:
            import shutil
            shutil.rmtree(base, ignore_errors=True)

    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "checks": checks,
            "note": "confirm/upload/network 0 · 격리홈 · 운영 장부 미접촉"}


# ─────────────────────────────────────────────────────────────────────────────
# 운영 홈 sentinel — 하니스 전 구간 운영 장부 mtime 불변(hard gate).
# ─────────────────────────────────────────────────────────────────────────────
def _operating_ledger_path():
    saved_home = os.environ.get("BINGGU_HOME")
    saved_ledger = os.environ.get("BINGGU_LEDGER")
    # 운영 기준 경로를 얻기 위해 override 를 잠시 걷어낸다.
    try:
        os.environ.pop("BINGGU_HOME", None)
        os.environ.pop("BINGGU_LEDGER", None)
        import importlib

        import binggu_paths as BP
        importlib.reload(BP)
        return BP.ledger()
    except Exception:
        return None
    finally:
        if saved_home is not None:
            os.environ["BINGGU_HOME"] = saved_home
        if saved_ledger is not None:
            os.environ["BINGGU_LEDGER"] = saved_ledger


def _operating_sentinel():
    p = _operating_ledger_path()
    try:
        return {"path": p, "mtime": os.path.getmtime(p)} if p and os.path.isfile(p) else \
               {"path": p, "mtime": None}
    except OSError:
        return {"path": p, "mtime": None}


# ─────────────────────────────────────────────────────────────────────────────
# run — 스냅샷 diff + 전파 dry-run + 운영 홈 불변을 한 번에.
# ─────────────────────────────────────────────────────────────────────────────
def run(hosted_snapshot=None, opencrab_snapshot=None, ledger=None,
        propagation=True, root=None) -> dict:
    before = _operating_sentinel()

    local = local_build_view(ledger=ledger)
    hosted = snapshot_view(hosted_snapshot) if hosted_snapshot else \
        {"available": False, "reason": "not_provided"}
    opencrab = snapshot_view(opencrab_snapshot) if opencrab_snapshot else \
        {"available": False, "reason": "not_provided"}
    drift = compare(local, hosted, opencrab)

    prop = propagation_dry_run(root=root) if propagation else {"ok": True, "skipped": True}

    after = _operating_sentinel()
    op_unchanged = (before.get("mtime") == after.get("mtime"))

    # 최종 판정: drift 판정 + 전파 dry-run + 운영 불변 종합.
    if not op_unchanged or not prop.get("ok"):
        decision = "FAIL"
    else:
        decision = drift["decision"]     # IN_SYNC / DRIFT / UNSUPPORTED
    return {
        "harness": "cross_client_read_drift",
        "spec": "track-R-v0.1",
        "decision": decision,
        "drift": drift,
        "propagation_dry_run": prop,
        "operating_home_unchanged": op_unchanged,
        "operating_sentinel": {"before": before, "after": after},
        "boundary": "no live fetch · existing snapshots only · network 0 · binggupack/ unchanged",
    }


# ─────────────────────────────────────────────────────────────────────────────
# selftest — 합성 스냅샷 픽스처(same→IN_SYNC · different→DRIFT)로 로직 결정적 증명 + 전파 dry-run.
# ─────────────────────────────────────────────────────────────────────────────
def _selftest() -> int:
    import shutil

    ok = True

    def ck(cond, msg):
        nonlocal ok
        if not cond:
            ok = False
        print("  [%s] %s" % ("PASS" if cond else "FAIL", msg))

    tmp = tempfile.mkdtemp(prefix="xclient_selftest_")
    try:
        principles = ["결론부터 짧게 답한다", "대안을 직접 찾아 가져온다", "유연함이 능력이다"]
        local_dig = _digest(principles)

        # (1) hosted KV 스냅샷 = canonical pack dir(graph/nodes.jsonl) · 같은 내용.
        hdir = os.path.join(tmp, "hosted_pack")
        os.makedirs(os.path.join(hdir, "graph"))
        with open(os.path.join(hdir, "graph", "nodes.jsonl"), "w", encoding="utf-8") as f:
            for i, s in enumerate(principles):
                f.write(json.dumps({"id": "node:%d" % i, "properties": {"sentence": s}},
                                   ensure_ascii=False) + "\n")
        hv = snapshot_view(hdir)
        ck(hv["available"] and hv["version_digest"] == local_dig,
           "hosted pack_dir(nodes.jsonl) 파싱 · 로컬 digest 일치")

        # (2) OpenCrab 스냅샷 = 개인 팩 .md('- ' 불릿) · 같은 내용(순서만 다름) → 여전히 IN_SYNC.
        omd = os.path.join(tmp, "opencrab_pack.md")
        with open(omd, "w", encoding="utf-8") as f:
            f.write("# owner 원칙\n\n" + "\n".join("- " + s for s in reversed(principles)))
        ov = snapshot_view(omd)
        ck(ov["available"] and ov["version_digest"] == local_dig,
           "opencrab .md 파싱 · 순서 무관 digest 로컬 일치")

        # (2b) 등급 꼬리표 부착 .md — strip 정규화로 여전히 로컬 digest 일치
        #      (등급 도입/승격 플립이 영구 DRIFT 오판을 만들지 않음).
        omd_g = os.path.join(tmp, "opencrab_pack_graded.md")
        with open(omd_g, "w", encoding="utf-8") as f:
            f.write("\n".join("- %s (등급: %s)" % (s, "후보" if i % 2 else "정본")
                              for i, s in enumerate(principles)))
        ovg = snapshot_view(omd_g)
        ck(ovg["available"] and ovg["version_digest"] == local_dig,
           "등급 꼬리표 .md → strip 정규화로 digest 로컬 일치(DRIFT 오판 차단)")

        # (3) 두 표면 모두 로컬과 같음 → IN_SYNC · cross=AGREE.
        local = {"available": True, "version_digest": local_dig, "principle_count": 3,
                 "source_kind": "local_build"}
        c1 = compare(local, hv, ov)
        ck(c1["decision"] == "IN_SYNC" and c1["cross_client"] == "AGREE",
           "두 표면=로컬 → decision=IN_SYNC · cross_client=AGREE")

        # (4) OpenCrab 이 구버전(원칙 1개 누락) → DRIFT · MODELS_DIVERGE.
        omd2 = os.path.join(tmp, "opencrab_stale.md")
        with open(omd2, "w", encoding="utf-8") as f:
            f.write("\n".join("- " + s for s in principles[:2]))   # 1개 빠짐
        ov2 = snapshot_view(omd2)
        c2 = compare(local, hv, ov2)
        ck(c2["decision"] == "DRIFT" and c2["opencrab"]["state"] == "DRIFT"
           and c2["hosted"]["state"] == "IN_SYNC" and c2["cross_client"] == "MODELS_DIVERGE",
           "opencrab 구버전 → DRIFT · hosted IN_SYNC · MODELS_DIVERGE(두 모델 다른 버전)")

        # (5) 스냅샷 부재 → UNSUPPORTED(억지 PASS 금지 · 정직).
        c3 = compare(local, {"available": False, "reason": "not_provided"}, ov)
        ck(c3["decision"] == "UNSUPPORTED" and c3["hosted"]["state"] == "MISSING",
           "스냅샷 부재 → decision=UNSUPPORTED(정직)")

        # (6) 전파 dry-run(격리홈 save→장부→sync).
        prop = propagation_dry_run()
        for c in prop["checks"]:
            ck(c["ok"], "전파 dry-run: " + c["msg"])

        # (7) 운영 홈 sentinel 불변 — selftest 전 구간 운영 장부 mtime 변화 0.
        before = _operating_sentinel()
        propagation_dry_run()
        after = _operating_sentinel()
        ck(before.get("mtime") == after.get("mtime"),
           "운영 장부 mtime 불변(격리홈만 write · 운영 미접촉)")

        # (8) egress 게이트 — 이 모듈이 네트워크 라이브러리를 import 하지 않음.
        # (토큰을 조각내 검사문 자신의 리터럴이 오탐되지 않게 한다.)
        src = _read_text(os.path.abspath(__file__))
        net_mods = ("requests", "urllib", "socket", "http.client")
        ck(not any(("im" + "port " + m) in src for m in net_mods),
           "네트워크 import 0(egress 게이트 · live fetch 경로 신설 없음)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("cross_client_e2e selftest: %s" % ("GO" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="binggu_cross_client_e2e")
    ap.add_argument("--hosted-snapshot", default=None,
                    help="Claude 가 읽는 hosted KV 팩 스냅샷 경로(기존 산출물 · dir/jsonl/md/json)")
    ap.add_argument("--opencrab-snapshot", default=None,
                    help="ChatGPT 가 읽는 OpenCrab 팩 스냅샷 경로(기존 산출물 · dir/jsonl/md/json)")
    ap.add_argument("--ledger", default=None, help="로컬 빌드 기준 장부(기본 운영 장부 · read-only)")
    ap.add_argument("--propagation-only", action="store_true",
                    help="스냅샷 diff 없이 격리홈 save→장부→sync dry-run 만")
    ap.add_argument("--no-propagation", action="store_true", help="전파 dry-run 생략")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return _selftest()

    if a.propagation_only:
        r = propagation_dry_run()
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r["ok"] else 1

    receipt = run(hosted_snapshot=a.hosted_snapshot, opencrab_snapshot=a.opencrab_snapshot,
                  ledger=a.ledger, propagation=not a.no_propagation)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    d = receipt["decision"]
    # IN_SYNC/UNSUPPORTED = 정상 종료(UNSUPPORTED 는 스냅샷 부재 안내), DRIFT/FAIL = 비정상.
    return 0 if d in ("IN_SYNC", "UNSUPPORTED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
