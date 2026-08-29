# -*- coding: utf-8 -*-
"""person_crab_sync — owner 개인 온톨로지를 CrabAgent 스키마 팩으로 빌드·동기화.

기존 person_pack_sync(ingest 경로·텍스트 델타)의 스키마판 후속. owner 화자 확정
문장(T3 통과분)을 문장당 1문서로 수출해 crab_pack_wire 로 개념/주장/증거 계층
팩을 빌드하고, 같은 pack_name 재업로드 = 제자리 교체(package_update 실증·2026-07-06)
로 서버 팩을 통째 갱신한다. 델타 추적이 필요 없어 상태는 content_hash 하나로 충분.
제자리 교체(전체 재빌드)라 pack_update 재파싱 중복 노드 위험이 없다 — ingest 경로
(person_pack_sync)와 달리 승격 플립 재업로드가 안전. 등급(후보/봉인 정본)은 문서
본문 독립 줄로 병기하고 meta_sig 에 포함해 승격 시 재업로드를 트리거한다.

안전 불변 (전부 _selftest 로 증명):
  - 수집은 person_pack_sync._owner_sentences_graded 재사용(T3 하드제외 동일 경계).
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
# 표시 문구 기본값(중립) — 사용자별 override 는 _crab_meta(env > person_pack.json > 기본).
# 신규 사용자에게 owner 개인 호칭("사장님")이 노출되지 않도록 중립값을 기본으로 둔다.
DEFAULT_PACK_TITLE = "개인 의사결정 원칙 온톨로지 (CrabAgent)"
# 실제 내용물 정합 문구 — 후보(SAVE 도장)와 봉인정본(승격 완료)이 등급 표기와 함께 실린다.
# 운영 person_pack.json 에 crab_pack_purpose override 가 있으면 이 기본값 정정은 전파되지
# 않는다(override 우선) — 운영 config 갱신은 owner 몫.
DEFAULT_PACK_PURPOSE = ("빙구팩 사용자 개인 온톨로지 — 사람 도장(SAVE)으로 저장된 원칙(후보)과 "
                        "승격 완료된 봉인정본을 등급 표기와 함께 개념/주장/증거 계층으로 구조화 "
                        "(T3 하드제외 통과분·스키마 경로).")
DEFAULT_OWNER_LABEL = "사용자"                    # export_docs 문서 본문의 화자 호칭(개인화 override 가능)
# 하위호환 별칭(기존 import 참조 보존) — 실제 사용은 _crab_meta().
PACK_TITLE = DEFAULT_PACK_TITLE
PACK_PURPOSE = DEFAULT_PACK_PURPOSE
EXTRA_SOURCES_DIR = "person_pack_sources"       # <home>/ 하위 — 사용자가 승인해 넣은 보조 문서
_PATH_MASK_RX = re.compile(r"[A-Za-z]:\\Users\\[^\s\\/:*?\"<>|]+")  # 경로 자동 마스킹(leak 게이트 선제)


def _home(home=None):
    return str(home) if home else binggu_paths.home()


def _crab_meta(home=None, env=None):
    """팩 표시 문구를 사용자별로 해석: env > <home>/person_pack.json > 중립 기본값.

    person_pack_sync._pack_config 와 동일 시맨틱(개인 도구 → 배포 제품 일반화).
      title       : BINGGU_CRAB_PACK_TITLE   > crab_pack_title
      purpose     : BINGGU_CRAB_PACK_PURPOSE > crab_pack_purpose
      owner_label : BINGGU_OWNER_LABEL       > owner_label   (문서 본문 화자 호칭)
      pack_name   : BINGGU_CRAB_PACK_NAME    > crab_pack_name (ASCII 유지)
    owner 는 person_pack.json 에 현행 값을 명시해 회귀 0 을 유지할 수 있다.
    표시 문구는 프로세스 환경(os.environ)/config 로 결정 — 업로드 게이트 env 와 분리."""
    e = os.environ if env is None else env
    cfg = {}
    try:
        with open(os.path.join(_home(home), PACK_CONFIG_FILE), encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:  # 부재/손상 → 기본값 폴백
        cfg = {}

    def pick(env_key, cfg_key, default):
        return e.get(env_key) or cfg.get(cfg_key) or default

    return {
        "title": pick("BINGGU_CRAB_PACK_TITLE", "crab_pack_title", DEFAULT_PACK_TITLE),
        "purpose": pick("BINGGU_CRAB_PACK_PURPOSE", "crab_pack_purpose", DEFAULT_PACK_PURPOSE),
        "owner_label": pick("BINGGU_OWNER_LABEL", "owner_label", DEFAULT_OWNER_LABEL),
        "pack_name": pick("BINGGU_CRAB_PACK_NAME", "crab_pack_name", DEFAULT_PACK_NAME),
    }


def _state_path(home=None):
    return os.path.join(_home(home), STATE_FILE)


def load_state(home=None):
    p = _state_path(home)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:  # 손상 → 빈 상태(재빌드로 자연 복구)
            return {}
    return {}


def save_state(state, home=None):
    with open(_state_path(home), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    return state


def _content_hash(sentences):
    return hashlib.sha256("\n".join(sentences).encode("utf-8")).hexdigest()[:16]


# 등급 독립 줄 — candidate 1=후보(SAVE 도장·승격 전)/0=봉인 정본(승격 완료).
# '봉인 정본' 띄어쓰기는 의도: 등급 줄에 4자+ 연속 한글 토큰(crab_pack_wire.TOKEN_RX)을
# 남기지 않아 derive_claims(30자 하한으로 이미 제외)/derive_queries(토큰 부재)에
# 구조적으로 미채택 — 등급은 표기 전용.
_GRADE_LINES = {1: "등급: 후보(사람 확정 전)", 0: "등급: 봉인 정본(사람 승인 완료)"}


def export_docs(sentences, out_dir, owner_label=DEFAULT_OWNER_LABEL):
    """문장당 1문서 수출 — 파일명(=주제)이 문장 자체라 개념/질의 파생이 자기참조로 성립.

    sentences 항목은 str 또는 (문장, candidate) 튜플 — 튜플이면 본문에 등급 독립 줄
    (_GRADE_LINES)을 병기한다(str 은 기존 형식 유지·등급 미상은 표기 안 함).
    owner_label — 문서 본문의 화자 호칭. 신규 사용자엔 '사용자'(중립), owner 는
    person_pack.json 의 owner_label 로 개인 호칭 유지 가능."""
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for i, item in enumerate(sentences, 1):
        s, cand = item if isinstance(item, tuple) else (item, None)
        safe = re.sub(r"\s+", "_", re.sub(r'[\\/:*?"<>|]', "_", s.strip()))
        if len(safe) > 60:
            safe = safe[:60] + "_" + hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]
        # 본문 단언은 등급과 자기모순이 없게 분기 — 후보(candidate=1)에 "확정 저장" 단언 금지.
        if cand == 0:
            claim_line = "위 문장은 %s(owner) 화자로 확정 저장된(봉인 정본) 의사결정 원칙·판단이다." % owner_label
        elif cand is not None:
            claim_line = "위 문장은 %s(owner) 화자가 사람 도장(SAVE)으로 저장한 원칙·판단(후보)이다." % owner_label
        else:
            claim_line = "위 문장은 %s(owner) 화자로 확정 저장된 의사결정 원칙·판단이다." % owner_label
        lines = [
            "# %s 판단 원칙" % owner_label,
            "%s 판단: %s" % (owner_label, s),
            claim_line,
        ]
        if cand is not None:
            lines.append(_GRADE_LINES[0 if cand == 0 else 1])
        (out / ("%s_%d.txt" % (safe, i))).write_text("\n".join(lines), encoding="utf-8")
    return len(sentences)


def _extra_dir(home=None):
    return Path(_home(home)) / EXTRA_SOURCES_DIR


def _iter_extra(home=None):
    """보조 문서 순회 — (안전 파일명, 세척 본문). 세척: 사용자 경로 마스킹(<PATH>) 후에도
    PII/secret 패턴이 남는 문서는 통째 제외(fail-closed). yield 순서 결정적."""
    from binggupack.pack.crab_pack_wire import LEAK_PATTERNS
    src = _extra_dir(home)
    skipped = [0]
    if not src.is_dir():
        return iter(()), skipped

    def gen():
        for p in sorted(src.rglob("*"), key=lambda x: str(x).lower()):
            if not p.is_file() or p.suffix.lower() not in (".md", ".txt") or p.stat().st_size > 2_000_000:
                continue
            text = _PATH_MASK_RX.sub("<PATH>", p.read_text(encoding="utf-8", errors="replace"))
            if any(rx.search(text) for _, rx in LEAK_PATTERNS):
                skipped[0] += 1
                continue
            safe = re.sub(r"\s+", "_", re.sub(r'[\\/:*?"<>|]', "_", p.stem))
            if len(safe) > 60:
                safe = safe[:60] + "_" + hashlib.sha256(p.stem.encode("utf-8")).hexdigest()[:8]
            yield "x_%s.txt" % safe, text
    return gen(), skipped


def extra_signature(home=None):
    """보조 문서 병합분의 결정적 서명 — 변경 감지(NO_CHANGE)용. 없으면 None."""
    it, _ = _iter_extra(home)
    sig = ["%s:%s" % (n, hashlib.sha256(t.encode("utf-8")).hexdigest()[:12]) for n, t in it]
    return hashlib.sha256("\n".join(sig).encode("utf-8")).hexdigest()[:16] if sig else None


def merge_extra_sources(data_dir, home=None, max_bundle_docs=None):
    """보조 문서를 세척 후 데이터 폴더에 병합. 반환 {"merged", "skipped_leak", "bundled"}.

    max_bundle_docs 지정 시 그 개수 이하의 '묶음 문서'로 병합(내용 무손실 —
    서버 finalize delete 비용이 유입 문서 수에 비례하는 한도 대응·2026-07-06 실측
    108문서 통과/357 실패). 묶음 안에서 원본은 "## [원본: 파일명]" 섹션으로 보존.
    """
    it, skipped = _iter_extra(home)
    items = list(it)
    out = {"merged": 0, "skipped_leak": skipped[0], "bundled": False}
    if not items:
        return out
    if max_bundle_docs is None or len(items) <= max_bundle_docs:
        for name, text in items:
            (Path(data_dir) / name).write_text(text, encoding="utf-8")
        out["merged"] = len(items)
        return out
    n_bundles = max(1, int(max_bundle_docs))
    per = -(-len(items) // n_bundles)  # ceil — 정렬 순서 유지(파일명 prefix 그룹 보존)
    for bi in range(0, len(items), per):
        group = items[bi:bi + per]
        body = ["# 사용자 온톨로지 자료 묶음 %02d" % (bi // per + 1), ""]
        for name, text in group:
            body.append("## [원본: %s]" % name)
            body.append(text)
            body.append("")
        (Path(data_dir) / ("bundle_%02d.txt" % (bi // per + 1))).write_text(
            "\n".join(body), encoding="utf-8")
        out["merged"] += len(group)
    out["bundled"] = True
    return out


def _chunk_cap(home=None):
    """청크 크기 — person_pack.json "crab_chunk_cap" > 기본 2400.
    finalize 한도가 총 청크 수(~350선 실측)에 걸릴 때 키워서 1팩 유지."""
    try:
        with open(os.path.join(_home(home), PACK_CONFIG_FILE), encoding="utf-8") as handle:
            cfg = json.load(handle)
        v = int(cfg.get("crab_chunk_cap") or 0)
        return v if 500 <= v <= 8000 else 2400
    except Exception:
        return 2400


def sync(*, dry_run=True, confirm=False, force=False, env=None, ledger=None, home=None,
         config_path=None, work_dir=None, transport=None, put_fn=None, post_fn=None,
         sleep_fn=None, max_tries=8, max_docs=90):
    """owner 문장 → 스키마 팩 빌드 → 업로드(제자리 교체). 반환(raise 0): typed dict.

    {status: NO_CHANGE|PLAN|DONE|BUILD_FAIL|UPLOAD_FAIL, count, blocked, content_hash,
     grade, package_id, pack_name, tries, reason}
    """
    out = {"status": None, "count": 0, "blocked": 0, "content_hash": None, "grade": None,
           "package_id": None, "pack_name": None, "tries": 0, "reason": None}
    try:
        graded, blocked = PPS._owner_sentences_graded(ledger=ledger)
    except Exception as ex:  # noqa — 장부 부재/손상도 typed
        out.update({"status": "LEDGER_ERROR", "reason": type(ex).__name__})
        return out
    sentences = [s for s, _ in graded]
    out.update({"count": len(sentences), "blocked": blocked})
    if not sentences:
        out.update({"status": "NO_SENTENCES"})
        return out
    xsig = extra_signature(home)
    meta = _crab_meta(home, env)
    # content_hash 에 표시 메타 서명 포함 — 제목/설명/화자 호칭을 바꾸면 재빌드·재업로드가
    # 트리거되도록(표시명만 바꿔도 서버 팩에 반영). 문장 불변 + 메타 불변이면 NO_CHANGE.
    # 등급 서명도 포함 — 승격 플립(candidate 1→0)이 문장 불변이어도 재업로드를 트리거해
    # 서버 팩 등급 표기가 장부 승격 상태를 따라가게 한다.
    grade_sig = hashlib.sha256(
        "\n".join("%d|%s" % (c, s) for s, c in graded).encode("utf-8")).hexdigest()[:12]
    meta_sig = "meta:%s|%s|%s|%s|grades:%s" % (meta["title"], meta["purpose"],
                                               meta["owner_label"], meta["pack_name"], grade_sig)
    ch = _content_hash(sentences + ([xsig] if xsig else []) + [meta_sig])
    out["content_hash"] = ch
    st = load_state(home)
    if not force and st.get("content_hash") == ch and st.get("package_id"):
        out.update({"status": "NO_CHANGE", "package_id": st.get("package_id"),
                    "pack_name": st.get("pack_name")})
        return out

    work = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="person_crab_"))
    data_dir = work / "data"
    zip_path = work / "person_crab_pack.zip"
    export_docs(graded, data_dir, owner_label=meta["owner_label"])
    budget = max(10, int(max_docs) - len(sentences))  # 총 문서 수 ≤ max_docs (finalize 한도)
    out["extra"] = merge_extra_sources(data_dir, home, max_bundle_docs=budget)
    b = build_crab_pack(data_dir, zip_path, meta["title"], meta["purpose"], min_queries=4,
                        chunk_cap=_chunk_cap(home))
    out["grade"] = b.get("grade")
    if not b.get("ok"):
        out.update({"status": "BUILD_FAIL", "reason": b.get("reason") or str(b.get("failed_gates"))})
        return out
    if dry_run:
        out.update({"status": "PLAN", "reason": "dry_run — live 는 --live --confirm"})
        return out

    pack_name = st.get("pack_name") or meta["pack_name"]
    u = upload_crab_pack(zip_path, pack_name, meta["purpose"], pack_category="personal-ontology",
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
        if os.path.exists(cfg_path):
            with open(cfg_path, encoding="utf-8") as handle:
                cfg = json.load(handle)
        else:
            cfg = {}
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
    # candidate 컬럼 포함(운영 스키마 정합) — graded SELECT 경로의 치명 회귀 방어.
    # 기본 1(후보) = SAVE 직후 상태. 승격(0) 플립은 selftest 에서 UPDATE 로 검증.
    import sqlite3
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY, sentence TEXT, speaker TEXT,"
                " state TEXT, candidate INTEGER DEFAULT 1)")
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

        # ── 보조 소스 계층 (person_pack_sources) ──
        xdir = Path(home) / EXTRA_SOURCES_DIR
        xdir.mkdir()
        (xdir / "사용자_판단_보조문서.md").write_text(
            "# [출처: ai정리] 사용자 판단 보조\n"
            "사장님은 결론부터 짧게 듣는 방식을 선호하며 대안 제시를 중시한다.\n"
            "작업 위치는 %s 에 있었다.\n" % r"C:\Users\fixture-user\work", encoding="utf-8")
        (xdir / "누출문서.md").write_text(
            "# 누출\n연락처는 %s 입니다.\n" % ("x" + "@" + "y" + ".com"), encoding="utf-8")
        s8 = sync(ledger=led, home=home, work_dir=work, dry_run=False, confirm=True, env=live_env,
                  transport=transport, put_fn=lambda u, b, **k: 200,
                  post_fn=lambda u, h, **k: fin, sleep_fn=lambda s: None)
        chk("S8 보조 문서 병합(경로 마스킹) + 누출 문서 자동 제외",
            s8["status"] == "DONE" and s8["extra"]["merged"] == 1 and s8["extra"]["skipped_leak"] == 1)
        merged = (Path(work) / "data" / "x_사용자_판단_보조문서.txt").read_text(encoding="utf-8")
        chk("S9 병합본에 사용자 경로 원문 미포함(<PATH> 마스킹)",
            "<PATH>" in merged and "tester" not in merged)
        s10 = sync(ledger=led, home=home, work_dir=work, dry_run=False, confirm=True, env=live_env,
                   transport=transport, put_fn=lambda u, b, **k: 200,
                   post_fn=lambda u, h, **k: fin, sleep_fn=lambda s: None)
        chk("S10 보조 문서 변화 없음 → NO_CHANGE(서명 해시 통합)", s10["status"] == "NO_CHANGE")

        for i in range(30):  # 예산 초과 유도 → 묶음 병합
            (xdir / ("추가문서_%02d.md" % i)).write_text(
                "# 추가 자료 %02d\n이 문서는 묶음 병합 검증용 보조 자료이며 추천 코스 설명이 이어진다.\n" % i,
                encoding="utf-8")
        s11 = sync(ledger=led, home=home, work_dir=work, dry_run=False, confirm=True, env=live_env,
                   transport=transport, put_fn=lambda u, b, **k: 200,
                   post_fn=lambda u, h, **k: fin, sleep_fn=lambda s: None, max_docs=20)
        n_docs = len(list((Path(work) / "data").glob("*.txt")))
        chk("S11 문서 예산 초과 → 묶음 병합(총 문서 ≤ max_docs·무손실 merged 31)",
            s11["status"] == "DONE" and s11["extra"]["bundled"] and s11["extra"]["merged"] == 31
            and n_docs <= 20)

        # ── 표시 문구 일반화(개인 도구 → 배포 제품·신규 사용자 owner 호칭 미노출) ──
        m_def = _crab_meta(home=home, env={})
        chk("S12 기본 메타 = 중립값('사장님' 미노출)",
            m_def["title"] == DEFAULT_PACK_TITLE and m_def["owner_label"] == "사용자"
            and "사장님" not in m_def["title"] and "사장님" not in m_def["purpose"])
        m_env = _crab_meta(home=home, env={"BINGGU_CRAB_PACK_TITLE": "T1", "BINGGU_OWNER_LABEL": "사장님"})
        chk("S13 env override 최우선(title·owner_label)",
            m_env["title"] == "T1" and m_env["owner_label"] == "사장님")
        with open(os.path.join(home, PACK_CONFIG_FILE), "w", encoding="utf-8") as f:
            json.dump({"crab_pack_title": "C-TITLE", "owner_label": "대표님"}, f, ensure_ascii=False)
        m_cfg = _crab_meta(home=home, env={})
        chk("S14 config override(env 부재 시 person_pack.json)",
            m_cfg["title"] == "C-TITLE" and m_cfg["owner_label"] == "대표님")
        ed1 = os.path.join(tmp, "ed1"); export_docs(_FIX_SENTENCES[:2], ed1)
        b_def = (Path(ed1) / sorted(os.listdir(ed1))[0]).read_text(encoding="utf-8")
        chk("S15 export 기본 본문 = 중립 '사용자'('사장님' 미포함)",
            "사용자 판단" in b_def and "사장님" not in b_def)
        ed2 = os.path.join(tmp, "ed2"); export_docs(_FIX_SENTENCES[:2], ed2, owner_label="사장님")
        b_ov = (Path(ed2) / sorted(os.listdir(ed2))[0]).read_text(encoding="utf-8")
        chk("S16 export owner_label override → 개인 호칭 반영", "사장님 판단" in b_ov)

        # S14 에서 config owner_label='대표님' 로 바뀐 상태 → 문장 불변이라도 메타 변경 →
        # content_hash 변경 → 재업로드(표시명 변경이 서버 팩에 반영되는 제품 정합성).
        s17 = sync(ledger=led, home=home, work_dir=work, dry_run=False, confirm=True, env=live_env,
                   transport=transport, put_fn=lambda u, b, **k: 200,
                   post_fn=lambda u, h, **k: fin, sleep_fn=lambda s: None)
        chk("S17 표시 메타 변경(문장 불변) → content_hash 변경으로 재업로드",
            s17["status"] == "DONE")
        s18 = sync(ledger=led, home=home, work_dir=work, dry_run=False, confirm=True, env=live_env,
                   transport=transport, put_fn=lambda u, b, **k: 200,
                   post_fn=lambda u, h, **k: fin, sleep_fn=lambda s: None)
        chk("S18 메타·문장 모두 불변 → NO_CHANGE(메타 서명 안정)", s18["status"] == "NO_CHANGE")

        # ── 등급 표기(candidate) — 승격 플립 = 재업로드 트리거 · 파생 미채택 ──
        con = sqlite3.connect(led)
        con.execute("UPDATE nodes SET candidate=0 WHERE sentence=?", (_FIX_SENTENCES[0],))
        con.commit()
        con.close()
        s19 = sync(ledger=led, home=home, work_dir=work, dry_run=False, confirm=True, env=live_env,
                   transport=transport, put_fn=lambda u, b, **k: 200,
                   post_fn=lambda u, h, **k: fin, sleep_fn=lambda s: None)
        chk("S19 승격 플립(candidate 1→0·문장 불변) → 등급 서명 변경으로 재업로드",
            s19["status"] == "DONE")
        bodies = "\n".join(p.read_text(encoding="utf-8")
                           for p in sorted((Path(work) / "data").glob("*.txt")))
        chk("S20 문서 본문 등급 독립 줄(후보/봉인 정본 병기)",
            _GRADE_LINES[0] in bodies and _GRADE_LINES[1] in bodies)
        s19b = sync(ledger=led, home=home, work_dir=work, dry_run=False, confirm=True, env=live_env,
                    transport=transport, put_fn=lambda u, b, **k: 200,
                    post_fn=lambda u, h, **k: fin, sleep_fn=lambda s: None)
        chk("S19b 승격 반영 후 재실행 → NO_CHANGE(등급 서명 안정)", s19b["status"] == "NO_CHANGE")
        # 등급 줄은 개념/주장/질의 파생에 미채택(표기 전용) — 4자+ 한글 토큰 부재로 구조 보장.
        from binggupack.pack.crab_pack_wire import derive_claims, derive_queries, scan_data
        ed3 = os.path.join(tmp, "ed3")
        export_docs([(s, i % 2) for i, s in enumerate(_FIX_SENTENCES)], ed3)
        docs3 = scan_data(ed3)
        blob = json.dumps({"claims": derive_claims(docs3), "queries": derive_queries(docs3)},
                          ensure_ascii=False)
        chk("S21 등급 줄 derive_claims/derive_queries 미채택('등급'/'봉인' 토큰 0)",
            "등급" not in blob and "봉인" not in blob)

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
