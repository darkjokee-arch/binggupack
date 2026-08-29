# -*- coding: utf-8 -*-
"""person_pack_split_upload — 사용자 개인 온톨로지 소스를 ~90문서 스키마 팩으로 분할 업로드.

근거: OpenCrab finalize "existing documents delete" 비용이 유입 문서 수에 비례 —
108문서까지 1차 통과 실측, 357문서는 항상 timeout. 90문서 AND ~700KB 상한 sticky 분할.
빌드 = binggupack.pack.crab_pack_wire(A게이트), 업로드 = upload_crab_pack(재시도 8).

표시 문구(화자 호칭)는 person_crab_sync._crab_meta 로 사용자별 해석 —
owner_label: env BINGGU_OWNER_LABEL > <home>/person_pack.json "owner_label" > 중립 "사용자".
owner 는 person_pack.json 에 owner_label 을 명시해 개인 호칭("사장님")을 유지(회귀 0).

소스: <home>/.binggupack/person_split_sources/*.{md,txt}
      (person_pack_assemble 로 ~/.claude/memory 에서 조립하거나 사용자가 직접 배치)

모드:
  (기본, 1회 적재) ledger(<home>/.claude/state/person_split_ledger.jsonl) DONE 스킵.
  (--daily 일 1회 갱신) 버킷별 소스 content_hash 를 state 와 대조 —
      안 바뀐 버킷은 NO_CHANGE 스킵(네트워크 0), 바뀐 버킷만 재빌드·재업로드
      (같은 pack_name = 제자리 교체·package_id 유지). state 에 hash/package_id 기록.
"""
from contextlib import suppress
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

try:  # pip 설치 환경
    from binggupack.pack.crab_pack_wire import LEAK_PATTERNS, build_crab_pack, upload_crab_pack
    from binggupack.pack.person_crab_sync import _PATH_MASK_RX, _crab_meta
except ImportError:  # repo 안에서 직접 실행 — repo 루트(= scripts/ 의 부모)를 경로에 추가
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from binggupack.pack.crab_pack_wire import LEAK_PATTERNS, build_crab_pack, upload_crab_pack
    from binggupack.pack.person_crab_sync import _PATH_MASK_RX, _crab_meta

SRC = Path.home() / ".binggupack" / "person_split_sources"
LEDGER = Path.home() / ".claude" / "state" / "person_split_ledger.jsonl"
STATE = Path.home() / ".claude" / "state" / "person_split_state.json"
DOC_CAP = 90          # sticky 분할: 파트당 문서 상한
BYTE_CAP = 700_000    # sticky 분할: 파트당 원본 총 바이트 상한(서버 finalize ~1MB 한도 대응·2026-07-09 실측)
DEFAULT_SCOPE_PREFIX = "Binggu Person"   # cloud_search 기본 스코프(팩 title 접두어) — 업로드 성공 시 config 기록
PROJECT_ID = None  # 업로드는 project 연결 없이(project_id 동봉 시 chunks insert HTML 에러 관측 2026-07-07).
#                    프로젝트 연결은 업로드 후 MCP opencrab_project_manage(add_packs)로 분리.


def _load_assign():
    a = load_state().get("_assign")
    return dict(a) if isinstance(a, dict) else {}


def _save_assign(assign):
    st = load_state()
    st["_assign"] = assign
    save_state(st)


def buckets():
    # sticky 분할(2026-07-09): 서버 finalize 규모 한도(~1MB) 재발로 1팩(2.25MB) 업로드가 delete
    # timeout 8회 실패 → 파일→파트 영구 매핑으로 90문서 AND ~700KB 파트 분할. 멤버십 불변(신규
    # 파일만 마지막 미만석 파트에 append·초과 시 새 파트) → NO_CHANGE 정상·중복 오염 0.
    # pack_name "Binggu Person Unified P%d" = 라이브 4파트 이름과 일치 → 제자리 교체.
    files = sorted((p for p in SRC.glob("*") if p.suffix.lower() in (".md", ".txt")),
                   key=lambda x: x.name)
    assign = _load_assign()             # {filename: part_no} — 영구 매핑
    live = {f.name for f in files}
    for k in [k for k in assign if k not in live]:
        del assign[k]                   # 삭제된 파일 정리
    parts, pdocs, pbytes = {}, {}, {}
    for f in files:                     # 기존 배정 먼저(멤버십 불변)
        pno = assign.get(f.name)
        if pno is None:
            continue
        parts.setdefault(pno, []).append(f)
        pdocs[pno] = pdocs.get(pno, 0) + 1
        pbytes[pno] = pbytes.get(pno, 0) + f.stat().st_size
    cur = max(parts) if parts else 1
    for f in files:                     # 미배정(신규) → 마지막 미만석 파트·초과 시 새 파트
        if f.name in assign:
            continue
        sz = f.stat().st_size
        if pdocs.get(cur, 0) >= DOC_CAP or pbytes.get(cur, 0) + sz > BYTE_CAP:
            cur = (max(parts) if parts else 0) + 1
        parts.setdefault(cur, []).append(f)
        pdocs[cur] = pdocs.get(cur, 0) + 1
        pbytes[cur] = pbytes.get(cur, 0) + sz
        assign[f.name] = cur
    _save_assign(assign)
    return [("Unified P%d" % pno, "통합 온톨로지 P%d" % pno,
             sorted(parts[pno], key=lambda x: x.name)) for pno in sorted(parts)]


def bucket_hash(files):
    """버킷 소스 파일들의 결정적 해시(파일명+내용) — 변경 감지용."""
    h = hashlib.sha256()
    for p in sorted(files, key=lambda x: x.name):
        h.update(p.name.encode("utf-8"))
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()[:16]


def ledger_done():
    if not LEDGER.exists():
        return set()
    done = set()
    for ln in LEDGER.read_text(encoding="utf-8").splitlines():
        with suppress(json.JSONDecodeError):
            r = json.loads(ln)
            if r.get("status") == "DONE":
                done.add(r["bucket"])
    return done


def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_state(st):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_data(files, work):
    """세척(경로 마스킹 + 누출 문서 제외) → data 폴더. 반환 skipped_leak."""
    data = work / "data"
    data.mkdir()
    skipped_leak = 0
    for f in files:
        text = _PATH_MASK_RX.sub("<PATH>", f.read_text(encoding="utf-8", errors="replace"))
        if any(rx.search(text) for _, rx in LEAK_PATTERNS):
            skipped_leak += 1
            continue
        (data / f.name.replace(".md", ".txt")).write_text(text, encoding="utf-8")
    return data, skipped_leak


def _titles(slug, korean, owner_label):
    """팩 표시 문구 — owner_label(사용자별 화자 호칭)로 개인화. 신규 사용자는 중립 '사용자'."""
    title = "%s %s (CrabAgent)" % (owner_label, korean)
    purpose = ("빙구팩 사용자 개인 온톨로지 보조 자료 — %s. AI 정리 자료(%s 발화 아님)를 "
               "출처 라벨과 함께 개념/주장/증거 계층으로 구조화." % (korean, owner_label))
    return title, purpose


def _ensure_default_scope():
    """업로드 성공 시 person_pack.json 에 cloud_search_default_pack_query 를 1회 기록(멱등).

    cloud_search 미지정 호출이 이 접두어("Binggu Person")로 자동 스코프됨 → 사용자 온톨로지가
    세션 중 자동으로 검색에 붙는다. id 가 아닌 접두어라 파트 증설·재업로드에도 갱신 불필요(stale 0).
    기존 config 키는 로드 후 보존(다른 키 무손상). 반환: 기록 여부(bool)."""
    cfg_path = Path.home() / ".binggupack" / "person_pack.json"
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    except Exception:
        cfg = {}
    if cfg.get("cloud_search_default_pack_query") == DEFAULT_SCOPE_PREFIX:
        return False
    cfg["cloud_search_default_pack_query"] = DEFAULT_SCOPE_PREFIX
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def main(daily=False):
    os.environ.setdefault("BINGGU_CRAB_UPLOAD", "1")
    owner_label = _crab_meta()["owner_label"]
    if not SRC.is_dir():
        print("소스 없음: %s — person_pack_assemble 먼저 실행(또는 직접 배치)" % SRC)
        return 1
    done = ledger_done() if not daily else set()
    state = load_state() if daily else {}
    bs = buckets()
    print("모드=%s / 버킷 %d개 / %s" % (
        "daily" if daily else "ledger", len(bs),
        ("state %d" % len(state)) if daily else ("완료 %d" % len(done))))
    ok = changed = skipped = 0
    for slug, korean, files in bs:
        title, purpose = _titles(slug, korean, owner_label)
        if daily:
            cur = bucket_hash(files)
            prev = state.get(slug, {})
            if prev.get("content_hash") == cur and prev.get("package_id"):
                skipped += 1
                print("[%s] NO_CHANGE (docs=%d)" % (slug, len(files)))
                continue
        elif slug in done:
            continue

        work = Path(tempfile.mkdtemp(prefix="psplit_"))
        data, skipped_leak = _build_data(files, work)
        if skipped_leak:
            print("[%s] 누출 제외 %d건" % (slug, skipped_leak))
        b = build_crab_pack(data, work / "pack.zip", title, purpose, min_queries=4)
        print("[%s] build grade=%s docs=%s" % (slug, b.get("grade"), b.get("counts", {}).get("documents")))
        if not b.get("ok"):
            with open(LEDGER, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"bucket": slug, "status": "BUILD_FAIL",
                                     "reason": b.get("reason"), "ts": time.strftime("%FT%T")}) + "\n")
            continue
        u = upload_crab_pack(work / "pack.zip", "Binggu Person %s" % slug, purpose,
                             pack_category="personal-ontology", project_id=PROJECT_ID,
                             dry_run=False, confirm=True, max_tries=8)
        row = {"bucket": slug, "status": "DONE" if u.get("ok") else "UPLOAD_FAIL",
               "package_id": u.get("package_id"), "tries": u.get("tries"),
               "reason": u.get("reason"), "title": title, "ts": time.strftime("%FT%T")}
        with open(LEDGER, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print("[%s] %s package=%s tries=%s %s" % (slug, row["status"], u.get("package_id"),
                                                  u.get("tries"), u.get("reason") or ""))
        if u.get("ok"):
            ok += 1
            changed += 1
            if daily:
                state[slug] = {"content_hash": bucket_hash(files), "package_id": u.get("package_id"),
                               "title": title, "ts": row["ts"]}
                save_state(state)
    if ok > 0 and _ensure_default_scope():
        print("[config] cloud_search_default_pack_query = %s 기록(자동 스코프)" % DEFAULT_SCOPE_PREFIX)
    if daily:
        print("== daily 완료 — 갱신 %d / NO_CHANGE %d / 버킷 %d ==" % (changed, skipped, len(bs)))
    else:
        print("== 완료 버킷 %d ==" % ok)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(daily="--daily" in sys.argv))
