# -*- coding: utf-8 -*-
"""person_crab_sync — owner 개인 온톨로지를 CrabAgent 스키마 팩으로 빌드·동기화.

기존 person_pack_sync(ingest 경로·텍스트 델타)의 스키마판 후속. owner 화자 확정
문장(T3 통과분)을 문장당 1문서로 수출해 crab_pack_wire 로 개념/주장/증거 계층
팩을 빌드하고, 같은 pack_name 재업로드 = 제자리 교체(package_update 실증·2026-07-06)
로 서버 팩을 통째 갱신한다. 델타 추적이 필요 없어 상태는 content_hash 하나로 충분.

안전 불변 (전부 _selftest 로 증명):
  - 수집은 person_pack_sync._owner_sentences 재사용(T3 하드제외 동일 경계).
  - 기본 dry_run. live 는 crab_pack_wire.upload_crab_pack 게이트 전부 상속
    (ENABLE env + confirm + cloud config + ZIP release_ready).
  - --auto 는 <home>/person_pack.json 의 "crab_auto_sync": true 가 있어야만 live
    (owner 파일 옵트인 — 없으면 DISABLED_AUTO). 변화 없으면 NO_CHANGE 로 네트워크 0.
  - 상태 파일(person_crab_last.json)은 live 성공 시에만 갱신.

CLI: python -m binggupack.pack.person_crab_sync --selftest | [--live --confirm] | --auto
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from binggupack.pack import person_pack_sync as PPS
from binggupack.pack.crab_pack_wire import ENABLE_ENV, build_crab_pack, upload_crab_pack
from binggupack import paths as binggu_paths

STATE_FILE = "person_crab_last.json"
PACK_CONFIG_FILE = "person_pack.json"           # 기존 온보딩 config 재사용(crab_auto_sync 키 추가)
DEFAULT_PACK_NAME = "Binggu Person Ontology"    # ASCII 강제(서버 한글 키 버그) — 표시명은 별도
PACK_TITLE = "사장님 의사결정 원칙 온톨로지 (CrabAgent)"
PACK_PURPOSE = ("빙구팩 사용자 개인 온톨로지 — owner 화자로 확정 저장된 의사결정 원칙·판단을 "
                "개념/주장/증거 계층으로 구조화 (T3 하드제외 통과분·스키마 경로).")


def _home(home=None):
    return str(home) if home else binggu_paths.home()


def _state_path(home=None):
    return os.path.join(_home(home), STATE_FILE)


def load_state(home=None):
    p = _state_path(home)
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:  # 손상 → 빈 상태(재빌드로 자연 복구)
            return {}
    return {}


def save_state(state, home=None):
    with open(_state_path(home), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    return state


def _content_hash(sentences):
    return hashlib.sha256("\n".join(sentences).encode("utf-8")).hexdigest()[:16]


def export_docs(sentences, out_dir):
    """문장당 1문서 수출 — 파일명(=주제)이 문장 자체라 개념/질의 파생이 자기참조로 성립."""
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for i, s in enumerate(sentences, 1):
        safe = re.sub(r"\s+", "_", re.sub(r'[\\/:*?"<>|]', "_", s.strip()))
        if len(safe) > 60:
            safe = safe[:60] + "_" + hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]
        body = "\n".join([
            "# 사장님 판단 원칙",
            "사장님 판단: %s" % s,
            "위 문장은 사장님(owner) 화자로 확정 저장된 의사결정 원칙·판단이다.",
        ])
        (out / ("%s_%d.txt" % (safe, i))).write_text(body, encoding="utf-8")
    return len(sentences)


def sync(*, dry_run=True, confirm=False, force=False, env=None, ledger=None, home=None,
         config_path=None, work_dir=None, transport=None, put_fn=None, post_fn=None,
         sleep_fn=None, max_tries=8):
    """owner 문장 → 스키마 팩 빌드 → 업로드(제자리 교체). 반환(raise 0): typed dict.

    {status: NO_CHANGE|PLAN|DONE|BUILD_FAIL|UPLOAD_FAIL, count, blocked, content_hash,
     grade, package_id, pack_name, tries, reason}
    """
    out = {"status": None, "count": 0, "blocked": 0, "content_hash": None, "grade": None,
           "package_id": None, "pack_name": None, "tries": 0, "reason": None}
    try:
        sentences, blocked = PPS._owner_sentences(ledger=ledger)
    except Exception as ex:  # noqa — 장부 부재/손상도 typed
        out.update({"status": "LEDGER_ERROR", "reason": type(ex).__name__})
        return out
    out.update({"count": len(sentences), "blocked": blocked})
    if not sentences:
        out.update({"status": "NO_SENTENCES"})
        return out
    ch = _content_hash(sentences)
    out["content_hash"] = ch
    st = load_state(home)
    if not force and st.get("content_hash") == ch and st.get("package_id"):
        out.update({"status": "NO_CHANGE", "package_id": st.get("package_id"),
                    "pack_name": st.get("pack_name")})
        return out

    work = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="person_crab_"))
    data_dir = work / "data"
    zip_path = work / "person_crab_pack.zip"
    export_docs(sentences, data_dir)
    b = build_crab_pack(data_dir, zip_path, PACK_TITLE, PACK_PURPOSE, min_queries=4)
    out["grade"] = b.get("grade")
    if not b.get("ok"):
        out.update({"status": "BUILD_FAIL", "reason": b.get("reason") or str(b.get("failed_gates"))})
        return out
    if dry_run:
        out.update({"status": "PLAN", "reason": "dry_run — live 는 --live --confirm"})
        return out

    pack_name = st.get("pack_name") or DEFAULT_PACK_NAME
    u = upload_crab_pack(zip_path, pack_name, PACK_PURPOSE, pack_category="personal-ontology",
                         env=env, config_path=config_path, home=home, transport=transport,
                         put_fn=put_fn, post_fn=post_fn, sleep_fn=sleep_fn,
                         dry_run=False, confirm=confirm, max_tries=max_tries)
    out.update({"tries": u.get("tries"), "pack_name": u.get("pack_name_used")})
    if not u.get("ok"):
        out.update({"status": "UPLOAD_FAIL", "reason": u.get("reason")})
        return out
    out.update({"status": "DONE", "package_id": u.get("package_id")})
    save_state({"package_id": u.get("package_id"), "pack_name": u.get("pack_name_used"),
                "content_hash": ch, "count": len(sentences)}, home)
    return out


def sync_auto(env=None, ledger=None, home=None, config_path=None, **inject):
    """무인 동기화 — <home>/person_pack.json 의 crab_auto_sync=true 옵트인 시에만 live.

    옵트인 파일이 owner 승인 증거라 ENABLE env 를 자체 주입한다(다른 게이트는 전부 유지).
    """
    cfg_path = os.path.join(_home(home), PACK_CONFIG_FILE)
    try:
        cfg = json.load(open(cfg_path, encoding="utf-8")) if os.path.exists(cfg_path) else {}
    except Exception:
        cfg = {}
    if cfg.get("crab_auto_sync") is not True:
        return {"status": "DISABLED_AUTO", "reason": "%s 에 crab_auto_sync:true 없음" % PACK_CONFIG_FILE}
    e = dict(os.environ if env is None else env)
    e[ENABLE_ENV] = "1"
    return sync(dry_run=False, confirm=True, env=e, ledger=ledger, home=home,
                config_path=config_path, **inject)


# ───────────────────────────── selftest ─────────────────────────────
def _fixture_ledger(path, sentences):
    import sqlite3
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY, sentence TEXT, speaker TEXT, state TEXT)")
    for s in sentences:
        con.execute("INSERT INTO nodes (sentence, speaker, state) VALUES (?, 'owner', 'active')", (s,))
    con.execute("INSERT INTO nodes (sentence, speaker, state) VALUES ('AI 요약 문장이라 제외 대상이다', 'ai', 'active')")
    con.commit()
    con.close()


_FIX_SENTENCES = [
    "결론부터 짧게 보고하고 상세는 물어볼 때 보충하는 방식이 낫다",
    "안 됩니다 대신 가능한 대안을 직접 찾아서 제시해야 한다",
    "파괴적 작업은 실물 대조 후 최소 범위로만 실행한다",
    "직감이 절반을 넘으면 먼저 실행하고 돌아가며 보완한다",
    "외부 서비스 결함은 두둔하지 말고 객관적으로 판정한다",
    "반복 패턴이 세 번 쌓이면 자동화 스킬로 승격을 검토한다",
    "저장과 승격은 사람 승인 게이트를 우선한다",
    "같은 실수를 반복하지 않도록 기록을 먼저 조회한다",
]


def _selftest():
    checks = []

    def chk(name, cond):
        checks.append((name, bool(cond)))
        print("[%s] %s" % ("PASS" if cond else "FAIL", name))

    with tempfile.TemporaryDirectory() as tmp:
        home = os.path.join(tmp, "home")
        os.makedirs(home)
        led = os.path.join(tmp, "ledger.sqlite")
        _fixture_ledger(led, _FIX_SENTENCES)
        work = os.path.join(tmp, "work")

        s1 = sync(ledger=led, home=home, work_dir=work)
        chk("S1 dry_run 기본 → PLAN + grade A + owner 8건(T3 통과)",
            s1["status"] == "PLAN" and s1["grade"] == "A" and s1["count"] == 8)

        s2 = sync(ledger=led, home=home, work_dir=work, dry_run=False, confirm=True,
                  env={ENABLE_ENV: "1"})
        chk("S2 cloud config 부재 → UPLOAD_FAIL 차단(fail-closed)",
            s2["status"] == "UPLOAD_FAIL" and "NO_CLOUD_CONFIG" in str(s2["reason"]))

        canned = json.dumps({"saas_ingest_handoff": {
            "upload_session_id": "sess-p1",
            "upload_url": "https://storage.example/fake-signed/p.zip?token=dummysig",
            "upload_finalize_url": "https://api.example/finalize/sess-p1",
            "upload_command": "curl -H 'X-OpenCrab-Upload-Token: dummytokp1'"}})

        def transport(payload):
            if payload["method"] == "initialize":
                return {"result": {"protocolVersion": "2025-03-26"}}
            return {"result": {"content": [{"type": "text", "text": canned}]}}

        fin = {"status": "ok", "package": {"package_id": "aaaabbbb-cccc-dddd-eeee-ffff00001111"}}
        live_env = {ENABLE_ENV: "1", "BINGGU_CLOUD_MCP_URL": "https://mcp.example/x"}
        s3 = sync(ledger=led, home=home, work_dir=work, dry_run=False, confirm=True, env=live_env,
                  transport=transport, put_fn=lambda u, b, **k: 200,
                  post_fn=lambda u, h, **k: fin, sleep_fn=lambda s: None)
        chk("S3 mock live → DONE + package_id + 상태 저장",
            s3["status"] == "DONE" and s3["package_id"] == "aaaabbbb-cccc-dddd-eeee-ffff00001111"
            and load_state(home).get("content_hash") == s3["content_hash"])

        s4 = sync(ledger=led, home=home, work_dir=work, dry_run=False, confirm=True, env=live_env,
                  transport=transport, put_fn=lambda u, b, **k: 200,
                  post_fn=lambda u, h, **k: fin, sleep_fn=lambda s: None)
        chk("S4 변화 없음 → NO_CHANGE(재빌드·네트워크 0)", s4["status"] == "NO_CHANGE")

        import sqlite3
        con = sqlite3.connect(led)
        con.execute("INSERT INTO nodes (sentence, speaker, state) VALUES "
                    "('새로 확정된 판단은 다음 동기화에서 자동 반영되어야 한다', 'owner', 'active')")
        con.commit()
        con.close()
        s5 = sync(ledger=led, home=home, work_dir=work, dry_run=False, confirm=True, env=live_env,
                  transport=transport, put_fn=lambda u, b, **k: 200,
                  post_fn=lambda u, h, **k: fin, sleep_fn=lambda s: None)
        chk("S5 신규 문장 → 재빌드·재업로드(제자리 교체 계약)",
            s5["status"] == "DONE" and s5["count"] == 9)

        a1 = sync_auto(ledger=led, home=home, work_dir=work, transport=transport,
                       put_fn=lambda u, b, **k: 200, post_fn=lambda u, h, **k: fin,
                       sleep_fn=lambda s: None, env={})
        chk("S6 auto 옵트인 없음 → DISABLED_AUTO", a1["status"] == "DISABLED_AUTO")

        with open(os.path.join(home, PACK_CONFIG_FILE), "w", encoding="utf-8") as f:
            json.dump({"crab_auto_sync": True}, f)
        a2 = sync_auto(ledger=led, home=home, work_dir=work, transport=transport,
                       put_fn=lambda u, b, **k: 200, post_fn=lambda u, h, **k: fin,
                       sleep_fn=lambda s: None, env={"BINGGU_CLOUD_MCP_URL": "https://mcp.example/x"})
        chk("S7 auto 옵트인 → live 진행(NO_CHANGE=최신 상태)", a2["status"] in ("NO_CHANGE", "DONE"))

    ok = all(c for _, c in checks)
    print("\nGATE=%s (%d/%d)" % ("GO" if ok else "NO-GO", sum(1 for _, c in checks if c), len(checks)))
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser(prog="person_crab_sync")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--auto", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return 0 if _selftest() else 1
    if a.auto:
        r = sync_auto()
    else:
        r = sync(dry_run=not a.live, confirm=a.confirm, force=a.force)
    print(json.dumps(r, ensure_ascii=False))
    return 0 if r.get("status") in ("DONE", "PLAN", "NO_CHANGE") else 1


if __name__ == "__main__":
    raise SystemExit(main())
